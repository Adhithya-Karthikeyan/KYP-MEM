"""Text embedding backends.

kyp-mem used to get embeddings implicitly from ChromaDB, which meant the
choice of model was buried inside a 430 MB dependency tree. The store now owns
its vectors, so this module owns the models — as a ladder of tiers, each an
explicit install away:

  Tier 0  BM25 only            no extra deps, no model download (KYP_NO_VECTOR,
                               or simply not installing the [vector] extra)
  Tier 1  [vector] (default)   all-MiniLM-L6-v2 via onnxruntime — the exact
                               model (and on-disk cache) Chroma bundled, so
                               upgrading from the chroma-backed store changes
                               retrieval quality not at all
  Tier 2  [vector-lite]        model2vec potion-retrieval-32M — numpy-only,
                               ~32 MB model, encodes in microseconds
  Tier 3  [st]                 any sentence-transformers model — pulls torch

Why MiniLM stays the default even though the static tier is lighter: it was
measured (2026-08-02, real vault, 4.6k chunks). potion's relevant and
off-topic similarity ranges overlap so badly that no floor separates them —
an off-topic query peaked at 0.469 while relevant paraphrases sat at
0.14-0.22 — which breaks the "bad matches return nothing" guarantee. MiniLM's
overlap is narrow enough for the two-tier floor scheme to work. The static
tier remains the right choice when install size beats the occasional
confident-looking stray hit, and BM25 corroboration masks much of the gap.

Every backend returns L2-normalised float32 rows, so cosine similarity is a
plain dot product everywhere downstream.

Similarity floors travel with the model: a floor measured for one model's
score distribution is meaningless for another's. Calibration procedure lives
in tests/test_retrieval_quality.py — off-topic peak sets the strict floor,
weakest keyword-corroborated relevant hit sets the lenient one.
"""

import os
import sys
from pathlib import Path

# The static tier's model. Best retrieval-tuned static embedder available;
# ~82% of MiniLM's dense-retrieval quality at a fraction of the size and none
# of the runtime. See the module docstring for why it is not the default.
DEFAULT_STATIC_MODEL = "minishlab/potion-retrieval-32M"

# Chroma's standalone ONNX artifact for all-MiniLM-L6-v2. Same URL and cache
# location Chroma itself uses, so an existing ~/.cache/chroma download is
# reused instead of fetched again.
ONNX_MINILM_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
ONNX_MINILM_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
ONNX_MAX_TOKENS = 256

# Spec prefixes accepted in the ``embedding_model`` config key. A bare
# unprefixed name keeps its historical meaning: a sentence-transformers model.
STATIC_PREFIX = "model2vec:"
ST_PREFIX = "st:"
ONNX_SPECS = ("onnx", "onnx-minilm", "minilm")
STATIC_SPECS = ("static", "lite", "model2vec")


def _log(msg: str):
    print(f"[kyp-mem embed] {msg}", file=sys.stderr)


class EmbedderUnavailable(RuntimeError):
    """A backend's dependencies or model files cannot be loaded.

    Carries the install hint shown to the user; semantic search degrades to
    keyword-only rather than failing the query.
    """


def _l2_normalize(arr):
    import numpy as np

    arr = np.asarray(arr, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class StaticEmbedder:
    """model2vec static embeddings — the opt-in [vector-lite] tier.

    Static means no attention pass at all: a lookup plus a pooled sum, which
    is why encoding is effectively free and the runtime is numpy.
    """

    # Measured for potion-retrieval-32M (2026-08-02, test corpus + real vault,
    # 4.6k chunks): strong relevant hits 0.36-0.39, test-corpus off-topic peak
    # 0.289, weakest corroborated-relevant 0.144. 0.32/0.13 is the least-bad
    # cut — rare off-topic outliers (one real-vault query peaked 0.469) can
    # still leak, which is exactly why this tier is not the default.
    floor_alone = 0.32
    floor_corroborated = 0.13

    def __init__(self, model_name: str = DEFAULT_STATIC_MODEL):
        self.name = f"model2vec:{model_name}"
        try:
            # The hub progress bar writes carriage-return spam to stderr on
            # every cache check; this is a server, not a terminal.
            os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
            from model2vec import StaticModel

            self._model = StaticModel.from_pretrained(model_name)
        except ImportError as e:
            raise EmbedderUnavailable(
                f"model2vec is not installed ({e}); semantic search is off. "
                "Install it with: pip install 'kyp-mem[vector-lite]'"
            ) from e
        except Exception as e:
            raise EmbedderUnavailable(
                f"embedding model {model_name!r} could not be loaded ({e!r}). "
                "First use needs one ~32 MB download; check network access, "
                "or set KYP_NO_VECTOR=1 to silence this."
            ) from e
        self.dim = int(self._model.dim)

    def encode(self, texts: list):
        return _l2_normalize(self._model.encode(list(texts)))


class OnnxMiniLMEmbedder:
    """all-MiniLM-L6-v2 through onnxruntime — the default tier.

    Reimplements the ~80 lines Chroma wrapped around this artifact: tokenize,
    run the transformer, mean-pool over the attention mask, normalise. Same
    model, same scores, so the floors calibrated under the Chroma backend
    carry over unchanged.
    """

    # Measured on a real vault: off-topic queries peaked at 0.244, so the
    # strict floor sits just above; the weakest keyword-corroborated relevant
    # note scored 0.190, so the lenient floor sits just below.
    floor_alone = 0.28
    floor_corroborated = 0.15

    name = "onnx:all-MiniLM-L6-v2"
    dim = 384

    def __init__(self):
        try:
            import numpy as np  # noqa: F401
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as e:
            raise EmbedderUnavailable(
                f"semantic search needs onnxruntime and tokenizers ({e}). "
                "Install them with: pip install 'kyp-mem[vector]' — or set "
                "embedding_model=static for the numpy-only lite tier."
            ) from e

        model_dir = self._ensure_model()
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=ONNX_MAX_TOKENS)
        self._tokenizer.enable_padding()
        self._session = onnxruntime.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )

    def _ensure_model(self) -> Path:
        target = ONNX_MINILM_CACHE / "onnx"
        if (target / "model.onnx").is_file() and (target / "tokenizer.json").is_file():
            return target

        import tarfile
        import tempfile
        import urllib.request

        _log("downloading all-MiniLM-L6-v2 ONNX model (~80 MB, one time)")
        ONNX_MINILM_CACHE.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                with urllib.request.urlopen(ONNX_MINILM_URL, timeout=120) as resp:
                    while chunk := resp.read(1 << 20):
                        tmp.write(chunk)
                tmp_path = tmp.name
            with tarfile.open(tmp_path, "r:gz") as tar:
                self._safe_extract(tar, ONNX_MINILM_CACHE)
            os.unlink(tmp_path)
        except Exception as e:
            raise EmbedderUnavailable(
                f"could not download the ONNX embedding model ({e!r}). "
                "Check network access, or switch to the offline-friendly lite "
                "tier: kyp-mem config embedding_model static "
                "(needs: pip install 'kyp-mem[vector-lite]')"
            ) from e

        if not (target / "model.onnx").is_file():
            raise EmbedderUnavailable(
                "downloaded ONNX archive did not contain model.onnx — "
                "layout changed upstream?"
            )
        return target

    @staticmethod
    def _safe_extract(tar, dest: Path):
        """Extract refusing members that escape ``dest`` (tar path traversal).

        Only regular files and directories are allowed — that rejects
        symlinks and hardlinks (which can redirect later members outside
        ``dest``) and also devices/FIFOs, none of which belong in a model
        archive.
        """
        dest = dest.resolve()
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if dest != target and dest not in target.parents:
                raise EmbedderUnavailable(
                    f"ONNX archive contains an unsafe path: {member.name!r}"
                )
            if not (member.isfile() or member.isdir()):
                raise EmbedderUnavailable(
                    f"ONNX archive contains a non-regular member: {member.name!r}"
                )
        tar.extractall(dest)  # noqa: S202 — members validated above

    def encode(self, texts: list):
        import numpy as np

        encodings = self._tokenizer.encode_batch(list(texts))
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        outputs = self._session.run(
            None,
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            },
        )
        hidden = outputs[0]  # (batch, tokens, 384)
        expanded = mask[..., None].astype(np.float32)
        summed = (hidden * expanded).sum(axis=1)
        counts = np.clip(expanded.sum(axis=1), 1e-9, None)
        return _l2_normalize(summed / counts)


class SentenceTransformerEmbedder:
    """Any sentence-transformers model — the [st] tier. Heavy (torch)."""

    # Model-dependent; these are conservative values that behave sensibly for
    # the bge/gte families users actually configure here.
    floor_alone = 0.28
    floor_corroborated = 0.15

    def __init__(self, model_name: str):
        self.name = f"st:{model_name}"
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
        except ImportError as e:
            raise EmbedderUnavailable(
                f"sentence-transformers is not installed ({e}). "
                "Install it with: pip install 'kyp-mem[st]'"
            ) from e
        except Exception as e:
            raise EmbedderUnavailable(
                f"sentence-transformers model {model_name!r} failed to load ({e!r})"
            ) from e
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list):
        return _l2_normalize(
            self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        )


def resolve_spec(spec: str | None) -> str:
    """Normalise a config value into a canonical embedder spec string.

    The canonical string is what gets persisted in the index metadata, so two
    processes agree on whether the index matches the configured model.
    """
    spec = (spec or "").strip()
    if not spec:
        return "onnx:all-MiniLM-L6-v2"
    low = spec.lower()
    if low in ONNX_SPECS:
        return "onnx:all-MiniLM-L6-v2"
    if low in STATIC_SPECS:
        return f"{STATIC_PREFIX}{DEFAULT_STATIC_MODEL}"
    if low.startswith(STATIC_PREFIX) or low.startswith(ST_PREFIX) or low.startswith("onnx:"):
        return spec
    # Bare names keep their pre-1.2 meaning: a sentence-transformers model.
    return f"{ST_PREFIX}{spec}"


def floors_for_spec(spec: str | None) -> tuple:
    """(floor_alone, floor_corroborated) for a spec, without loading the model.

    The store needs floors before (and sometimes instead of) instantiating the
    embedder — e.g. to filter results when the model is already loaded, or to
    report configuration while the [vector] extra is missing.
    """
    canonical = resolve_spec(spec)
    if canonical.startswith(STATIC_PREFIX):
        cls = StaticEmbedder
    elif canonical.startswith("onnx:"):
        cls = OnnxMiniLMEmbedder
    else:
        cls = SentenceTransformerEmbedder
    return cls.floor_alone, cls.floor_corroborated


def load_embedder(spec: str | None):
    """Build the embedder for a canonical or raw spec.

    Raises EmbedderUnavailable (never ImportError) when the tier's
    dependencies or model files are missing.
    """
    canonical = resolve_spec(spec)
    if canonical.startswith(STATIC_PREFIX):
        return StaticEmbedder(canonical[len(STATIC_PREFIX):])
    if canonical.startswith("onnx:"):
        return OnnxMiniLMEmbedder()
    return SentenceTransformerEmbedder(canonical[len(ST_PREFIX):])
