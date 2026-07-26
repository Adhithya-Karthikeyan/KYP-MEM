"""BM25 keyword index.

Replaces the previous scorer, which had three problems:

  1. It scanned the whole vocabulary for every query word
     (``for idx_word in word_index: if word in idx_word``), so search cost grew
     with the size of the vault rather than the size of the query.
  2. It intersected every query word, so any natural-language question
     returned nothing unless one note contained all of the words.
  3. It scored by raw term count over document length, with no IDF, so common
     words outranked the rare ones that actually carry the query's meaning.

This module fixes all three: a real inverted index with postings, OR matching
with a coverage bonus, and BM25 with proper IDF.
"""

import re
from bisect import bisect_left
from collections import defaultdict
from math import log

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Deliberately small. An aggressive list hurts a technical vault, where words
# like "no", "not" and "all" change the meaning of a sentence.
STOPWORDS = frozenset(
    """a an and are as at be by for from has have how in is it its of on or
    that the their then there these this to was were what when where which
    who will with""".split()
)

K1 = 1.5  # term-frequency saturation
B = 0.75  # document-length normalisation

# Weight multipliers applied when a term appears in a high-signal field.
TITLE_WEIGHT = 3.0
TAG_WEIGHT = 2.0
PATH_WEIGHT = 2.0

# How strongly to favour documents matching more of the query terms. Applied as
# coverage ** COVERAGE_EXP, so a doc matching 2 of 3 terms keeps most of its
# score while a doc matching 1 of 3 falls well behind.
COVERAGE_EXP = 1.5

# Cap on how many vocabulary terms a single prefix expansion may contribute, so
# a short prefix can't drag the whole vocabulary into one query.
MAX_PREFIX_EXPANSIONS = 24

# How much a prefix hit counts relative to a literal one, for both ranking and
# the coverage gate.
PREFIX_MATCH_WEIGHT = 0.45


def _split_identifier(token: str) -> list:
    """Break ``snake_case`` / ``camelCase`` into parts.

    A vault about code is full of identifiers. Searching "content hash" should
    find ``_content_hash``, and searching "session memory" should find
    ``SessionMemory``.
    """
    parts = []
    for piece in token.split("_"):
        if not piece:
            continue
        parts.extend(p for p in CAMEL_RE.split(piece) if p)
    return parts


def tokenize(text: str, keep_stopwords: bool = False) -> list:
    """Lowercase word tokens, with identifiers additionally split into parts."""
    tokens = []
    for raw in TOKEN_RE.findall(text or ""):
        low = raw.lower()
        if not keep_stopwords and low in STOPWORDS:
            continue
        tokens.append(low)
        parts = _split_identifier(raw)
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts if p.lower() != low)
    return tokens


class BM25Index:
    """Inverted index over notes, scored with Okapi BM25."""

    def __init__(self):
        self._postings: dict = defaultdict(dict)  # term -> {doc_id: weighted tf}
        self._doc_len: dict = {}
        self._avg_len: float = 0.0
        self._vocab: list = []  # sorted, for prefix expansion
        self._vocab_stale = True

    # --- building -------------------------------------------------------------

    def add(self, doc_id: str, title: str = "", body: str = "", tags=None, path: str = ""):
        """Index one document. Replaces any previous entry for ``doc_id``."""
        self.remove(doc_id)

        counts: dict = defaultdict(float)
        for term in tokenize(body):
            counts[term] += 1.0
        for term in tokenize(title):
            counts[term] += TITLE_WEIGHT
        for tag in tags or []:
            for term in tokenize(str(tag)):
                counts[term] += TAG_WEIGHT
        # Path terms make "kyp-mem sessions" find notes under KYP-MEM/Sessions/.
        for term in tokenize(path.replace("/", " ").replace("-", " ").replace(".md", "")):
            counts[term] += PATH_WEIGHT

        if not counts:
            return

        for term, tf in counts.items():
            self._postings[term][doc_id] = tf

        self._doc_len[doc_id] = sum(counts.values())
        self._vocab_stale = True
        self._recompute_avg()

    def remove(self, doc_id: str):
        """Drop a document from the index."""
        if doc_id not in self._doc_len:
            return
        empty = []
        for term, docs in self._postings.items():
            if doc_id in docs:
                del docs[doc_id]
                if not docs:
                    empty.append(term)
        for term in empty:
            del self._postings[term]
        del self._doc_len[doc_id]
        self._vocab_stale = True
        self._recompute_avg()

    def _recompute_avg(self):
        self._avg_len = (sum(self._doc_len.values()) / len(self._doc_len)) if self._doc_len else 0.0

    def _ensure_vocab(self):
        if self._vocab_stale:
            self._vocab = sorted(self._postings)
            self._vocab_stale = False

    # --- querying -------------------------------------------------------------

    def _expand_prefix(self, term: str) -> list:
        """Vocabulary terms starting with ``term``, found by binary search.

        This is the fast replacement for the old full-vocabulary substring
        scan: O(log V + k) instead of O(V) per query word.
        """
        if len(term) < 3:
            return []
        self._ensure_vocab()
        start = bisect_left(self._vocab, term)
        out = []
        for i in range(start, len(self._vocab)):
            candidate = self._vocab[i]
            if not candidate.startswith(term):
                break
            if candidate != term:
                out.append(candidate)
            if len(out) >= MAX_PREFIX_EXPANSIONS:
                break
        return out

    def _idf(self, df: int, n_docs: int) -> float:
        return log(1 + (n_docs - df + 0.5) / (df + 0.5))

    def search(self, query: str, limit: int = 20, candidates=None, min_idf_coverage: float = 0.0) -> list:
        """Return ``[(doc_id, score)]`` ranked by BM25, best first.

        Matching is OR — a document need not contain every query term — but
        documents covering more of the query are boosted, so the best matches
        still rise to the top.

        ``min_idf_coverage`` is an absolute relevance gate: the fraction of the
        query's *information content* (summed IDF, not word count) that a
        document must actually match. Without it, a four-word query like
        "thai green curry recipe" matches a note called "GreenLeaf" on the
        single word "green" and — being the only candidate — ranks first.
        Weighting by IDF rather than term count is what makes this safe for
        long natural-language queries, where most words are common filler and
        only a few rare terms carry the question.
        """
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or not self._doc_len:
            return []

        n_docs = len(self._doc_len)
        scores: dict = defaultdict(float)
        matched_terms: dict = defaultdict(set)

        # Information content of the query. A term absent from the vault counts
        # at full weight: a document cannot match it, and pretending otherwise
        # is how off-topic queries sneak through.
        term_idf = {t: self._idf(len(self._postings.get(t, {})), n_docs) for t in terms}
        total_idf = sum(term_idf.values())

        # Best match strength per (doc, term): 1.0 for a literal hit, less for a
        # prefix hit. The coverage gate weighs by this, so a loose stem match
        # ("return" landing on "returns") cannot masquerade as hard evidence
        # that the document is about the query.
        match_weight: dict = defaultdict(dict)

        for term in terms:
            # An exact hit counts fully; a prefix hit is discounted, so
            # "sess" still finds "sessions" without outranking a literal match.
            variants = [(term, 1.0)] + [(v, PREFIX_MATCH_WEIGHT) for v in self._expand_prefix(term)]
            for variant, weight in variants:
                docs = self._postings.get(variant)
                if not docs:
                    continue
                idf = self._idf(len(docs), n_docs)
                for doc_id, tf in docs.items():
                    if candidates is not None and doc_id not in candidates:
                        continue
                    dl = self._doc_len.get(doc_id, 0.0)
                    norm = 1 - B + B * (dl / self._avg_len if self._avg_len else 1.0)
                    scores[doc_id] += weight * idf * (tf * (K1 + 1)) / (tf + K1 * norm)
                    matched_terms[doc_id].add(term)
                    prev = match_weight[doc_id].get(term, 0.0)
                    if weight > prev:
                        match_weight[doc_id][term] = weight

        if not scores:
            return []

        total = len(terms)
        ranked = []
        for doc_id, score in scores.items():
            matched = matched_terms[doc_id]
            if min_idf_coverage > 0.0 and total_idf > 0:
                weights = match_weight[doc_id]
                matched_idf = sum(term_idf[t] * weights.get(t, 1.0) for t in matched)
                if matched_idf / total_idf < min_idf_coverage:
                    continue
            coverage = len(matched) / total
            ranked.append((doc_id, score * (coverage ** COVERAGE_EXP)))

        ranked.sort(key=lambda x: (-x[1], x[0]))
        return ranked[:limit]

    def __len__(self) -> int:
        return len(self._doc_len)
