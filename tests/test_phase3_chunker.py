"""Phase 3 — Unit tests for the parent-child chunker."""

from __future__ import annotations

import uuid

from api.app.services.chunker import CHILD_TOKENS, ChunkRecord, make_chunks


def test_short_text_yields_one_parent() -> None:
    records = make_chunks("hello world", source="gh://test/test#1")
    parents = [r for r in records if r.chunk_type == "parent"]
    assert len(parents) == 1


def test_short_text_yields_at_least_one_child() -> None:
    records = make_chunks("hello world", source="gh://test/test#1")
    children = [r for r in records if r.chunk_type == "child"]
    assert len(children) >= 1


def test_child_references_parent() -> None:
    records = make_chunks("fix: null pointer in auth module", source="gh://test/test#1")
    parent = next(r for r in records if r.chunk_type == "parent")
    children = [r for r in records if r.chunk_type == "child"]
    for child in children:
        assert child.parent_id == parent.id


def test_source_propagated_to_all_chunks() -> None:
    source = "gh://pandas-dev/pandas#1234"
    records = make_chunks("some text", source=source)
    for r in records:
        assert r.source == source


def test_label_propagated_to_all_chunks() -> None:
    records = make_chunks("bugfix text", source="gh://x/y#1", label="bug")
    for r in records:
        assert r.label == "bug"


def test_label_none_when_not_provided() -> None:
    records = make_chunks("text", source="gh://x/y#1")
    for r in records:
        assert r.label is None


def test_chunk_records_have_unique_ids() -> None:
    records = make_chunks("text", source="gh://x/y#1")
    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))


def test_chunk_records_are_chunk_record_instances() -> None:
    records = make_chunks("text", source="gh://x/y#1")
    for r in records:
        assert isinstance(r, ChunkRecord)
        assert isinstance(r.id, uuid.UUID)


def test_long_text_produces_multiple_parents() -> None:
    # ~3000 words should produce more than one parent (PARENT_TOKENS=1024)
    long_text = " ".join(["word"] * 3000)
    records = make_chunks(long_text, source="gh://x/y#1")
    parents = [r for r in records if r.chunk_type == "parent"]
    assert len(parents) > 1


def test_parent_children_all_reference_correct_parent() -> None:
    long_text = " ".join(["word"] * 3000)
    records = make_chunks(long_text, source="gh://x/y#1")
    parents = {r.id: r for r in records if r.chunk_type == "parent"}
    children = [r for r in records if r.chunk_type == "child"]
    for child in children:
        assert child.parent_id in parents


def test_child_text_is_substring_of_parent() -> None:
    text = "The quick brown fox jumps over the lazy dog " * 50
    records = make_chunks(text, source="gh://x/y#1")
    parent = next(r for r in records if r.chunk_type == "parent")
    children = [r for r in records if r.chunk_type == "child" and r.parent_id == parent.id]
    for child in children:
        assert child.text in parent.text or len(child.text) <= CHILD_TOKENS * 5
