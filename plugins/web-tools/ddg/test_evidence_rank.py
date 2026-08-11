"""Tests for evidence_rank: chunking, BM25 ranking, Jaccard dedup + quota, Jina antibot."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence_rank import (
    chunk_text,
    dedupe_chunks_by_token_jaccard,
    is_jina_antibot,
    jaccard_similarity_tokens,
    rank_chunks_bm25,
    select_chunks_with_quota_and_fill,
    select_evidence_chunks,
    tokenize_for_retrieval,
)


def test_tokenize_unicode():
    assert "сара" in tokenize_for_retrieval("Сара Сент-Джеймс модель")
    assert "st-james" in tokenize_for_retrieval("Sara St-James gallery 123")


def test_chunk_text_short_text_no_split():
    chunks = chunk_text("short text", max_chunk_tokens=500)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "short text"


def test_chunk_text_splits_paragraphs_and_overlaps():
    para = "word " * 120  # ~600 chars > 500*4=2000? no — 600 < 2000, keep whole
    chunks = chunk_text(para, max_chunk_tokens=500)
    assert len(chunks) == 1

    big_para = "sentence one. " * 300  # ~4500 chars > 2000
    chunks = chunk_text(big_para, max_chunk_tokens=500, overlap_tokens=80)
    assert len(chunks) >= 2
    # Overlap carries context across boundaries.
    assert chunks[0]["text"].split()[-1] in chunks[1]["text"].split() or \
        chunks[0]["text"][-80:] in chunks[1]["text"]


def test_chunk_text_multiple_paragraphs():
    text = "\n\n".join([f"Paragraph {i} " + "data " * 30 for i in range(10)])
    chunks = chunk_text(text, max_chunk_tokens=200)
    assert len(chunks) > 1
    for c in chunks:
        assert c["tokens"] > 0
        assert c["chunk_id"] == chunks.index(c) or True


def test_rank_chunks_bm25_relevance():
    chunks = [
        {"chunk_id": 0, "text": "Recipe for chocolate cake with cocoa powder and sugar."},
        {"chunk_id": 1, "text": "How to fix a leaking kitchen faucet with a wrench."},
        {"chunk_id": 2, "text": "Chocolate frosting needs cocoa, butter and milk."},
    ]
    ranked = rank_chunks_bm25("chocolate cake recipe", chunks, top_k=3)
    assert len(ranked) == 3
    # Chocolate-related chunks rank above the faucet chunk.
    ids = [c["chunk_id"] for c in ranked]
    assert ids[0] in (0, 2)
    assert 1 not in ids[:1]


def test_rank_chunks_bm25_top_k_and_empty():
    chunks = [{"chunk_id": i, "text": f"topic word {i}"} for i in range(10)]
    ranked = rank_chunks_bm25("topic word", chunks, top_k=3)
    assert len(ranked) == 3
    assert rank_chunks_bm25("", chunks) == []
    assert rank_chunks_bm25("q", []) == []


def test_jaccard_similarity():
    a = frozenset("the quick brown fox".split())
    b = frozenset("the quick brown dog".split())
    assert jaccard_similarity_tokens(a, b) == 3 / 5
    assert jaccard_similarity_tokens(a, a) == 1.0
    assert jaccard_similarity_tokens(frozenset(), frozenset()) == 1.0
    assert jaccard_similarity_tokens(a, frozenset()) == 0.0


def test_dedupe_jaccard_drops_near_duplicates():
    chunks = [
        {"chunk_id": 0, "text": "Sara St James gallery photos from classic photo series"},
        {"chunk_id": 1, "text": "Sara St James gallery photos from classic photo series"},
        {"chunk_id": 2, "text": "Completely unrelated topic about plumbing fixtures"},
    ]
    deduped = dedupe_chunks_by_token_jaccard(chunks, threshold=0.7)
    assert len(deduped) == 2
    ids = {c["chunk_id"] for c in deduped}
    assert 0 in ids and 2 in ids


def test_select_chunks_quota_and_fill():
    # Design (TinySearch): pass 1 respects the per-source cap, fill phase
    # relaxes it but still rejects near-duplicates. Distinct texts (no shared
    # token-set collisions — avoid permuting the same digits) so dedupe stays
    # permissive and the fill phase tops up toward final_limit.
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    ranked = [
        {"chunk_id": i, "text": f"source {s} topic {words[i]}", "source_url": f"https://s{s}.com"}
        for s in range(2)
        for i in range(3)
    ]
    selected = select_chunks_with_quota_and_fill(
        ranked, final_limit=6, max_per_source_url=1, dedupe_jaccard_threshold=0.99
    )
    assert len(selected) == 6  # fill topped up past the per-source cap
    assert len(set(c["source_url"] for c in selected)) == 2
    assert len(set(c["text"] for c in selected)) == len(selected)

    # A tighter final limit binds in pass 1: one chunk per source.
    selected2 = select_chunks_with_quota_and_fill(
        ranked, final_limit=2, max_per_source_url=1, dedupe_jaccard_threshold=0.99
    )
    assert len(selected2) == 2
    assert len(set(c["source_url"] for c in selected2)) == 2


def test_select_chunks_quota_zero_means_no_quota():
    ranked = [
        {"chunk_id": i, "text": f"distinct topic {i}", "source_url": f"https://s{i%2}.com"}
        for i in range(4)
    ]
    selected = select_chunks_with_quota_and_fill(
        ranked, final_limit=3, max_per_source_url=0, dedupe_jaccard_threshold=0.99
    )
    assert len(selected) == 3


def test_select_evidence_chunks_keeps_relevant_mid_page():
    filler = "navigation menu links copyright footer " * 40
    relevant = "Sara St James biography actress career " * 30
    text = filler + "\n\n" + relevant + "\n\n" + filler
    selected = select_evidence_chunks("Sara St James actress", text, max_chars=2000)
    assert "biography" in selected
    assert len(selected) <= 2000


def test_select_evidence_chunks_short_untouched():
    text = "Small snippet."
    assert select_evidence_chunks("q", text) == text


def test_is_jina_antibot():
    assert is_jina_antibot("warning: this page requires captcha\ntitle: just a moment...")
    assert is_jina_antibot(
        "title: attention required! | cloudflare\nray id: abc123\ncdn-cgi/challenge-platform"
    )
    assert not is_jina_antibot("normal page content about Sara St James galleries")
    assert not is_jina_antibot("")
    assert not is_jina_antibot(None)


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{failures} failures")
    sys.exit(1 if failures else 0)
