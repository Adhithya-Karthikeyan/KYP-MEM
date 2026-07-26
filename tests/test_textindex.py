import pytest

from kyp_mem.textindex import BM25Index, tokenize


def test_tokenize_drops_stopwords_and_lowercases():
    assert tokenize("The Cat AND the mat") == ["cat", "mat"]


def test_tokenize_splits_snake_case_keeping_original():
    toks = tokenize("_content_hash")
    assert "content" in toks and "hash" in toks


def test_tokenize_splits_camel_case_keeping_original():
    toks = tokenize("SessionMemory")
    assert "sessionmemory" in toks
    assert "session" in toks and "memory" in toks


def test_negation_words_are_not_stopwords():
    # "not" flips meaning in a technical note; dropping it loses the point.
    assert "not" in tokenize("does not compile")


@pytest.fixture
def idx():
    ix = BM25Index()
    ix.add("hooks.md", title="Session Hooks", body="The stop hook compiles a session log into a vault note.", tags=["hooks"], path="KYP-MEM/hooks.md")
    ix.add("vector.md", title="Vector Store", body="Chroma persists embeddings for semantic session search.", tags=["search"], path="KYP-MEM/vector.md")
    ix.add("ui.md", title="Web UI", body="FastAPI serves the vault browser and the graph view.", tags=["ui"], path="KYP-MEM/ui.md")
    return ix


def test_finds_by_body_term(idx):
    assert idx.search("chroma")[0][0] == "vector.md"


def test_title_outranks_body(idx):
    ix = BM25Index()
    ix.add("a.md", title="Vector Store", body="unrelated filler text here")
    ix.add("b.md", title="Unrelated", body="this note merely mentions vector once")
    assert ix.search("vector")[0][0] == "a.md"


def test_multiword_query_does_not_require_all_terms(idx):
    # The old index ANDed every term, so this returned nothing.
    hits = idx.search("why did the semantic session search break")
    assert hits, "OR matching should still return results"
    assert hits[0][0] == "vector.md"


def test_more_query_coverage_ranks_higher():
    ix = BM25Index()
    ix.add("both.md", title="x", body="alpha beta")
    ix.add("one.md", title="x", body="alpha alpha alpha alpha alpha")
    ranked = [d for d, _ in ix.search("alpha beta")]
    assert ranked[0] == "both.md"


def test_idf_favours_the_rare_term():
    ix = BM25Index()
    for i in range(20):
        ix.add(f"common{i}.md", title="t", body="database database database")
    ix.add("rare.md", title="t", body="database quasar")
    assert ix.search("database quasar")[0][0] == "rare.md"


def test_prefix_match_finds_longer_term(idx):
    assert any(d == "vector.md" for d, _ in idx.search("embed"))


def test_exact_match_outranks_prefix_match():
    ix = BM25Index()
    ix.add("exact.md", title="t", body="session")
    ix.add("prefix.md", title="t", body="sessionization")
    assert ix.search("session")[0][0] == "exact.md"


def test_short_terms_do_not_prefix_expand():
    ix = BM25Index()
    ix.add("a.md", title="t", body="second sequence secret")
    assert idx_search_terms(ix, "se") == []


def idx_search_terms(ix, q):
    return ix.search(q)


def test_path_terms_are_searchable():
    ix = BM25Index()
    ix.add("p.md", title="Note", body="nothing relevant", path="GreenLeaf/Sessions/2026.md")
    assert ix.search("greenleaf")[0][0] == "p.md"


def test_tags_are_searchable(idx):
    assert idx.search("ui")[0][0] == "ui.md"


def test_remove_drops_document(idx):
    idx.remove("vector.md")
    assert all(d != "vector.md" for d, _ in idx.search("chroma"))
    assert len(idx) == 2


def test_add_replaces_existing_document(idx):
    before = len(idx)
    idx.add("vector.md", title="Vector Store", body="now about postgres instead")
    assert len(idx) == before
    assert not idx.search("chroma")
    assert idx.search("postgres")[0][0] == "vector.md"


def test_remove_unknown_document_is_a_noop(idx):
    idx.remove("nope.md")
    assert len(idx) == 3


def test_empty_query_returns_nothing(idx):
    assert idx.search("") == []
    assert idx.search("the and of") == []


def test_no_match_returns_nothing(idx):
    assert idx.search("kubernetes helm chart") == []


def test_candidate_filter_restricts_results(idx):
    hits = idx.search("session", candidates={"hooks.md"})
    assert [d for d, _ in hits] == ["hooks.md"]


def test_limit_is_respected():
    ix = BM25Index()
    for i in range(30):
        ix.add(f"n{i}.md", title="t", body="alpha")
    assert len(ix.search("alpha", limit=5)) == 5


def test_scores_are_positive_and_descending(idx):
    hits = idx.search("session search vault")
    scores = [s for _, s in hits]
    assert all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_empty_index_returns_nothing():
    assert BM25Index().search("anything") == []
