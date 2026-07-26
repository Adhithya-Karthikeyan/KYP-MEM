"""Markdown-aware chunking.

Embedding a whole note as one vector averages away the detail you actually
want back. A session note with ``## Summary``, ``## LEARNED`` and
``## COMPLETED`` collapses into a single point that is close to everything and
precise about nothing.

So we split on markdown headings instead. Each chunk keeps a breadcrumb of the
headings above it plus the note title, so an embedded fragment still carries
the context needed to rank it.
"""

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Chunks larger than this get split again on paragraph boundaries. Sized so a
# chunk stays well inside the embedding model's window while still holding a
# complete thought.
MAX_CHUNK_CHARS = 1200

# An *unlabelled* fragment with fewer real words than this is structural noise
# — a horizontal rule or a stray marker floating above the first heading — and
# is dropped. Fragments under a heading are always kept, however short.
MIN_CHUNK_WORDS = 4

WORD_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass
class Chunk:
    """One embeddable fragment of a note."""

    doc_path: str
    ordinal: int
    heading_path: list = field(default_factory=list)
    body: str = ""

    @property
    def chunk_id(self) -> str:
        return f"{self.doc_path}#{self.ordinal}"

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path)

    def embed_text(self, title: str) -> str:
        """The text actually handed to the embedding model.

        Prefixing the title and heading breadcrumb means a fragment like
        "fixed the race in the stop hook" still embeds near its project and
        section, instead of floating free.
        """
        header = title
        if self.heading_path:
            header = f"{title} > {self.heading}"
        return f"{header}\n\n{self.body}".strip()


def _split_oversized(text: str, max_chars: int) -> list:
    """Break a too-long section on paragraph boundaries, then on lines.

    Never splits mid-word; a single paragraph longer than ``max_chars`` is
    emitted whole rather than cut at an arbitrary character.
    """
    if len(text) <= max_chars:
        return [text]

    out = []
    buf = []
    size = 0
    for para in text.split("\n\n"):
        para_len = len(para) + 2
        if size + para_len > max_chars and buf:
            out.append("\n\n".join(buf).strip())
            buf, size = [], 0
        buf.append(para)
        size += para_len
    if buf:
        out.append("\n\n".join(buf).strip())
    return [p for p in out if p]


def chunk_note(
    doc_path: str,
    content: str,
    max_chars: int = MAX_CHUNK_CHARS,
    min_words: int = MIN_CHUNK_WORDS,
) -> list:
    """Split note content into heading-scoped chunks.

    Returns at least one chunk for any non-empty note, so nothing silently
    drops out of the index.
    """
    if not content or not content.strip():
        return []

    sections = []  # (heading_path, body_lines)
    stack = []
    current = []

    for line in content.split("\n"):
        m = HEADING_RE.match(line)
        if not m:
            current.append(line)
            continue

        if current and "\n".join(current).strip():
            sections.append((list(stack), current))
        current = []

        level = len(m.group(1))
        text = m.group(2).strip()
        # Pop siblings and deeper headings so the breadcrumb reflects nesting.
        stack = stack[: level - 1]
        stack.append(text)
        sections.append((list(stack), []))

    if current and "\n".join(current).strip():
        sections.append((list(stack), current))

    # A heading with no prose under it contributes its breadcrumb to the next
    # chunk but produces no chunk of its own.
    collapsed = [
        (heading_path, "\n".join(lines).strip())
        for heading_path, lines in sections
        if "\n".join(lines).strip()
    ]

    if not collapsed:
        # Headings only, no prose. Index the headings themselves so the note
        # is still findable.
        headings = [" > ".join(hp) for hp, _ in sections if hp]
        if not headings:
            return []
        return [Chunk(doc_path=doc_path, ordinal=0, heading_path=[], body="\n".join(headings))]

    # Drop only fragments that are both tiny and unlabelled — a stray rule or
    # stub line before the first heading. A short *titled* section is kept:
    # "## COMPLETED\nPushed v0.9.0" is brief but it is exactly what a later
    # search needs to find, and its breadcrumb makes it addressable.
    packed = [
        (heading_path, body)
        for heading_path, body in collapsed
        if heading_path or len(WORD_RE.findall(body)) >= min_words
    ]
    if not packed:
        packed = collapsed

    chunks = []
    ordinal = 0
    for heading_path, body in packed:
        for piece in _split_oversized(body, max_chars):
            chunks.append(
                Chunk(doc_path=doc_path, ordinal=ordinal, heading_path=heading_path, body=piece)
            )
            ordinal += 1
    return chunks
