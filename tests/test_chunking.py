from kyp_mem.chunking import chunk_note

SESSION = """# Session 2026-07-21_170312

**Project:** `/Users/x/Projects/KYP-MEM`

## Summary
Fixed the stop hook race that let two sessions share one activity log.

## INVESTIGATED
kyp_mem/hooks.py; bin/cli.mjs; the shared current.jsonl path.

## LEARNED
All concurrent Claude sessions appended to a single current.jsonl, so the
stop handler summarised interleaved activity from unrelated projects.

## COMPLETED
Added per-session log files keyed by session id; added _prune_stale_logs.
"""


def test_splits_on_headings():
    chunks = chunk_note("KYP-MEM/Sessions/s.md", SESSION)
    headings = [c.heading for c in chunks]
    assert "Session 2026-07-21_170312 > Summary" in headings
    assert "Session 2026-07-21_170312 > LEARNED" in headings
    assert "Session 2026-07-21_170312 > COMPLETED" in headings


def test_chunk_bodies_do_not_bleed_across_sections():
    chunks = chunk_note("s.md", SESSION)
    learned = next(c for c in chunks if c.heading.endswith("LEARNED"))
    assert "interleaved activity" in learned.body
    assert "_prune_stale_logs" not in learned.body


def test_ids_are_stable_and_unique():
    chunks = chunk_note("a/b.md", SESSION)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("a/b.md#") for i in ids)
    assert [c.chunk_id for c in chunk_note("a/b.md", SESSION)] == ids


def test_embed_text_carries_title_and_breadcrumb():
    chunks = chunk_note("s.md", SESSION)
    learned = next(c for c in chunks if c.heading.endswith("LEARNED"))
    text = learned.embed_text("Session 2026-07-21")
    assert text.startswith("Session 2026-07-21 > Session 2026-07-21_170312 > LEARNED")
    assert "interleaved activity" in text


def test_nested_headings_build_breadcrumb():
    md = "# Top\n\nintro prose here\n\n## Bugs\n\n### Fixed\n\nthe null deref in parse_note\n"
    chunks = chunk_note("n.md", md)
    fixed = next(c for c in chunks if "null deref" in c.body)
    assert fixed.heading_path == ["Top", "Bugs", "Fixed"]


def test_sibling_heading_pops_stack():
    md = "# T\n\nx\n\n## A\n\n" + "alpha " * 30 + "\n\n## B\n\n" + "beta " * 30 + "\n"
    chunks = chunk_note("n.md", md)
    b = next(c for c in chunks if "beta" in c.body)
    assert b.heading_path == ["T", "B"]


def test_oversized_section_is_split_on_paragraphs():
    para = "word " * 120  # ~600 chars
    md = "# T\n\n## Big\n\n" + "\n\n".join([para] * 5)
    chunks = chunk_note("n.md", md, max_chars=800)
    big = [c for c in chunks if c.heading.endswith("Big")]
    assert len(big) > 1
    assert all(len(c.body) <= 1000 for c in big)
    # Splitting must not lose text.
    assert sum(c.body.count("word") for c in big) == 600


def test_short_titled_section_survives_with_its_own_breadcrumb():
    # A brief "## COMPLETED\nPushed v0.9.0" is short but is precisely what a
    # later search needs, so it must not be folded into the section above it.
    md = "# T\n\n" + "context " * 40 + "\n\n## Tiny\n\nok\n"
    chunks = chunk_note("n.md", md)
    tiny = next(c for c in chunks if c.body.strip() == "ok")
    assert tiny.heading_path == ["T", "Tiny"]
    assert "context" not in tiny.body


def test_untitled_runt_before_first_heading_is_dropped():
    md = "---\n\n# T\n\n" + "real content here " * 10
    chunks = chunk_note("n.md", md)
    assert all(c.body.strip() != "---" for c in chunks)


def test_content_before_any_heading_is_kept():
    md = "preamble text that matters quite a lot here\n\n# Later\n\nbody\n"
    chunks = chunk_note("n.md", md)
    assert any("preamble text" in c.body for c in chunks)


def test_empty_and_whitespace_notes_produce_nothing():
    assert chunk_note("n.md", "") == []
    assert chunk_note("n.md", "   \n\n  ") == []


def test_headings_only_note_still_indexed():
    chunks = chunk_note("n.md", "# A\n\n## B\n\n## C\n")
    assert len(chunks) == 1
    assert "B" in chunks[0].body


def test_plain_note_without_headings():
    chunks = chunk_note("n.md", "just some prose with no headings at all")
    assert len(chunks) == 1
    assert chunks[0].heading_path == []
    assert "just some prose" in chunks[0].body
