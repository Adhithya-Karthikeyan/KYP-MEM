"""KYP-MEM configuration — stored at ~/.kyp-mem/config.json"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".kyp-mem"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATS_FILE = CONFIG_DIR / "token_stats.json"
DEFAULT_VAULT = str(CONFIG_DIR / "vault")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            return {"vault_path": DEFAULT_VAULT}
    return {"vault_path": DEFAULT_VAULT}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_vault_path() -> str:
    env = os.environ.get("KYP_VAULT")
    if env:
        return env
    config = load_config()
    return config.get("vault_path", DEFAULT_VAULT)


def get_session_model() -> str:
    config = load_config()
    return config.get("session_model", "claude-sonnet-4-6")


def get_embedding_model() -> str:
    """Embedding model spec, or "" for the default tier.

    Accepted values (resolution lives in embedder.py):
      ""                        ONNX all-MiniLM-L6-v2 — the default, and the
                                exact model the old ChromaDB backend bundled
      "static" / "lite"         model2vec potion — numpy-only lite tier
      "model2vec:<hf-model>"    any model2vec static model
      "st:<name>" or bare name  a sentence-transformers model (needs [st])

    Changing the model triggers an automatic re-embed on next search, since
    vectors from different models are not comparable.
    """
    return (load_config().get("embedding_model") or "").strip()
