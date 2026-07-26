from dataclasses import dataclass

from kyp_mem.search import best_snippet, hybrid_search, reciprocal_rank_fusion


@dataclass
class FakeHit:
    doc_path: str
    similarity: float
    text: str = "chunk body"
    heading: str = "Sec"
    title: str = "T"
    chunk_id: str = "c"


@dataclass
class FakeNote:
    title: str
    content: str


def test_rrf_rewards_agreement():
    scores = reciprocal_rank_fusion([(1.0, ["a", "b"]), (1.0, ["b", "a"])])
    # Both rank a and b symmetrically, so they tie.
    assert scores["a"] == scores["b"]

    scores = reciprocal_rank_fusion([(1.0, ["a", "b"]), (1.0, ["a", "c"])])
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_respects_weights():
    equal = reciprocal_rank_fusion([(1.0, ["a"]), (1.0, ["b"])])
    assert equal["a"] == equal["b"]
    skewed = reciprocal_rank_fusion([(2.0, ["a"]), (1.0, ["b"])])
    assert skewed["a"] > skewed["b"]


def test_hybrid_prefers_note_found_by_both_rankers():
    hits = hybrid_search(
        "session log",
        keyword_hits=[("both.md", 5.0), ("kw.md", 4.9)],
        vector_hits=[FakeHit("sem.md", 0.8), FakeHit("both.md", 0.7)],
    )
    assert hits[0].path == "both.md"
    assert set(hits[0].sources) == {"keyword", "semantic"}


def test_hybrid_keeps_keyword_only_and_semantic_only_results():
    hits = hybrid_search(
        "x",
        keyword_hits=[("kw.md", 1.0)],
        vector_hits=[FakeHit("sem.md", 0.9)],
    )
    paths = {h.path for h in hits}
    assert paths == {"kw.md", "sem.md"}


def test_hybrid_degrades_to_keyword_only_when_no_vectors():
    hits = hybrid_search("x", keyword_hits=[("a.md", 3.0), ("b.md", 1.0)], vector_hits=[])
    assert [h.path for h in hits] == ["a.md", "b.md"]
    assert all(h.sources == ["keyword"] for h in hits)


def test_hybrid_degrades_to_semantic_only_when_no_keywords():
    hits = hybrid_search("x", keyword_hits=[], vector_hits=[FakeHit("s.md", 0.9)])
    assert [h.path for h in hits] == ["s.md"]
    assert hits[0].sources == ["semantic"]


def test_hybrid_returns_nothing_when_both_empty():
    assert hybrid_search("x", [], []) == []


def test_chunks_collapse_to_best_scoring_chunk_per_note():
    hits = hybrid_search(
        "x",
        keyword_hits=[],
        vector_hits=[
            FakeHit("n.md", 0.4, text="weak chunk"),
            FakeHit("n.md", 0.9, text="strong chunk"),
        ],
    )
    assert len(hits) == 1
    assert hits[0].snippet == "strong chunk"
    assert hits[0].similarity == 0.9


def test_semantic_snippet_wins_over_note_lookup():
    notes = {"n.md": FakeNote("T", "irrelevant note body")}
    hits = hybrid_search(
        "x", [], [FakeHit("n.md", 0.9, text="the matching chunk")], note_lookup=notes.get
    )
    assert hits[0].snippet == "the matching chunk"


def test_keyword_only_hit_gets_snippet_from_note():
    notes = {"n.md": FakeNote("T", "prelude " * 40 + "the answer is a lock ordering bug " + "tail " * 40)}
    hits = hybrid_search("lock ordering bug", [("n.md", 1.0)], [], note_lookup=notes.get)
    assert "lock ordering bug" in hits[0].snippet


def test_limit_is_respected():
    kw = [(f"n{i}.md", 1.0) for i in range(20)]
    assert len(hybrid_search("x", kw, [], limit=3)) == 3


def test_ranking_is_deterministic_for_ties():
    kw = [("b.md", 1.0), ("a.md", 1.0)]
    first = [h.path for h in hybrid_search("x", kw, [])]
    second = [h.path for h in hybrid_search("x", kw, [])]
    assert first == second


def test_best_snippet_picks_densest_window_not_first_match():
    content = "lock appears here alone. " + "filler " * 60 + " the real passage discusses lock ordering and lock contention together."
    snip = best_snippet(content, "lock ordering contention")
    assert "ordering" in snip and "contention" in snip


def test_best_snippet_handles_no_match():
    snip = best_snippet("completely unrelated prose", "kubernetes")
    assert snip.startswith("completely unrelated")


def test_best_snippet_handles_empty_content():
    assert best_snippet("", "x") == ""


def test_embedded_breadcrumb_is_stripped_from_snippets():
    from kyp_mem.search import strip_embedded_header

    text = "Session s1 > LEARNED\n\nChroma never reclaims tombstoned slots."
    assert strip_embedded_header(text) == "Chroma never reclaims tombstoned slots."


def test_stripping_keeps_multiline_bodies_intact():
    from kyp_mem.search import strip_embedded_header

    text = "Note > Sec\n\nfirst line\n\nsecond para"
    assert strip_embedded_header(text) == "first line\n\nsecond para"


def test_stripping_leaves_text_without_a_breadcrumb_alone():
    from kyp_mem.search import strip_embedded_header

    assert strip_embedded_header("just a body with no header") == "just a body with no header"
    # A multi-line first block is a body, not a breadcrumb.
    assert strip_embedded_header("line one\nline two\n\nmore") == "line one\nline two\n\nmore"


def test_hybrid_snippet_has_no_duplicated_breadcrumb():
    hits = hybrid_search(
        "slots",
        [],
        [FakeHit("n.md", 0.9, text="Session s1 > LEARNED\n\nChroma never reclaims slots.", heading="Session s1 > LEARNED")],
    )
    assert hits[0].snippet == "Chroma never reclaims slots."
    assert hits[0].heading == "Session s1 > LEARNED"
