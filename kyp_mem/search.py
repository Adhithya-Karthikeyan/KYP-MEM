"""Hybrid retrieval: BM25 and semantic results fused with Reciprocal Rank Fusion.

Previously the vault exposed two unrelated search tools — ``kyp_search``
(keyword, whole notes) and ``kyp_session_search`` (semantic, sessions only) —
and the calling agent had to guess which one would find the thing. Worse, each
was blind to the other's strengths: keyword search misses paraphrase, semantic
search misses exact identifiers like ``_prune_stale_logs``.

RRF combines them without needing the two score scales to be comparable. Each
result is scored by its *rank* in each list, so a note that both rankers like
beats one that only a single ranker loved.
"""

from dataclasses import dataclass, field

from .textindex import TOKEN_RE, tokenize

# Standard RRF damping. Large enough that the top few ranks are close together,
# so agreement across rankers matters more than a one-position lead in either.
RRF_K = 60

DEFAULT_KEYWORD_WEIGHT = 1.0
DEFAULT_SEMANTIC_WEIGHT = 1.0

SNIPPET_WIDTH = 240


@dataclass
class SearchHit:
    path: str
    score: float
    snippet: str = ""
    heading: str = ""
    title: str = ""
    sources: list = field(default_factory=list)
    keyword_rank: int = None
    semantic_rank: int = None
    similarity: float = None


def strip_embedded_header(text: str) -> str:
    """Drop the title/heading breadcrumb a chunk carries for embedding.

    Chunks are embedded as "Title > Section\\n\\nbody" so the vector keeps its
    context (see chunking.Chunk.embed_text). That prefix is useful to the model
    and redundant on screen, where the heading is already shown beside the
    result — without this the breadcrumb appears twice in every hit.
    """
    if not text:
        return ""
    head, sep, body = text.partition("\n\n")
    if sep and body.strip() and "\n" not in head.strip():
        return body.strip()
    return text.strip()


def relative_heading(heading: str, title: str) -> str:
    """Drop the leading title from a heading breadcrumb.

    A note's H1 is normally also its title, so the raw breadcrumb reads
    "Session s1 > Session s1 > LEARNED". Callers display the title separately,
    so only the part below it is worth showing.
    """
    if not heading or not title:
        return heading or ""
    if heading == title:
        return ""
    prefix = f"{title} > "
    return heading[len(prefix):] if heading.startswith(prefix) else heading


def _head(content: str, width: int) -> str:
    text = content[:width].strip()
    return text + ("..." if len(content) > width else "")


def best_snippet(content: str, query: str, width: int = SNIPPET_WIDTH) -> str:
    """Pick the ``width``-character window of ``content`` densest in query terms.

    The old implementation returned the region around the *first* match of the
    first query word, which usually landed on a heading or a passing mention
    rather than the passage that actually made the note match.
    """
    if not content:
        return ""
    terms = set(tokenize(query))
    if not terms:
        return _head(content, width)

    # Character offsets of every word that matches a query term. Matching goes
    # through tokenize(), so a query for "content hash" also lands on
    # "_content_hash".
    positions = [
        m.start()
        for m in TOKEN_RE.finditer(content)
        if terms & set(tokenize(m.group()))
    ]
    if not positions:
        return _head(content, width)

    # Slide a width-wide window over the match positions and keep the one
    # covering the most matches. Both pointers only move forward.
    best_i, best_count, j = 0, 0, 0
    for i in range(len(positions)):
        if j < i:
            j = i
        while j < len(positions) and positions[j] < positions[i] + width:
            j += 1
        if j - i > best_count:
            best_count, best_i = j - i, i

    start = max(0, positions[best_i] - 40)
    end = min(len(content), start + width)
    segment = content[start:end]

    # Don't begin or end mid-word.
    if start > 0 and not content[start - 1].isspace() and " " in segment:
        segment = segment.split(" ", 1)[1]
    if end < len(content) and not content[end].isspace() and " " in segment:
        segment = segment.rsplit(" ", 1)[0]

    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{segment.strip()}{suffix}"


def reciprocal_rank_fusion(ranked_lists: list, k: int = RRF_K) -> dict:
    """Fuse ``[(weight, [doc_id, ...]), ...]`` into ``{doc_id: score}``."""
    scores = {}
    for weight, docs in ranked_lists:
        for rank, doc_id in enumerate(docs, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return scores


def hybrid_search(
    query: str,
    keyword_hits: list,
    vector_hits: list,
    note_lookup=None,
    limit: int = 10,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
) -> list:
    """Fuse keyword and semantic results into one ranking.

    ``keyword_hits`` is ``[(path, bm25_score)]``; ``vector_hits`` is a list of
    ``VectorHit``. ``note_lookup(path)`` returns the Note (used for snippets on
    keyword-only matches). Either list may be empty — with no vector store the
    result is plain BM25, in stable order.
    """
    keyword_paths = [p for p, _ in keyword_hits]

    # Collapse chunks to their parent note, keeping the strongest chunk as that
    # note's representative — that chunk is also the best snippet to show.
    best_chunk = {}
    semantic_paths = []
    for hit in vector_hits:
        if hit.doc_path not in best_chunk:
            best_chunk[hit.doc_path] = hit
            semantic_paths.append(hit.doc_path)
        elif hit.similarity > best_chunk[hit.doc_path].similarity:
            best_chunk[hit.doc_path] = hit

    fused = reciprocal_rank_fusion(
        [(keyword_weight, keyword_paths), (semantic_weight, semantic_paths)]
    )
    if not fused:
        return []

    kw_rank = {p: i + 1 for i, p in enumerate(keyword_paths)}
    sem_rank = {p: i + 1 for i, p in enumerate(semantic_paths)}

    hits = []
    for path, score in fused.items():
        sources = []
        if path in kw_rank:
            sources.append("keyword")
        if path in sem_rank:
            sources.append("semantic")

        chunk = best_chunk.get(path)
        note = note_lookup(path) if note_lookup else None

        if chunk:
            snippet = strip_embedded_header(chunk.text)
            if len(snippet) > SNIPPET_WIDTH * 2:
                snippet = snippet[: SNIPPET_WIDTH * 2].rstrip() + "..."
            title = chunk.title or (note.title if note else "")
            heading = relative_heading(chunk.heading, title)
        else:
            snippet = best_snippet(note.content, query) if note else ""
            heading = ""
            title = note.title if note else ""

        hits.append(
            SearchHit(
                path=path,
                score=score,
                snippet=snippet,
                heading=heading,
                title=title,
                sources=sources,
                keyword_rank=kw_rank.get(path),
                semantic_rank=sem_rank.get(path),
                similarity=chunk.similarity if chunk else None,
            )
        )

    hits.sort(key=lambda h: (-h.score, h.path))
    return hits[:limit]
