"""Evidence ranking helpers ported from TinySearch (hybrid chunk ranking) and
agent-reach (Jina antibot detection).

This module lets the pipeline replace blind ``text[:N]`` evidence truncation
with:

- ``chunk_text`` — structure-aware chunking with overlap (no tiktoken
  dependency; tokens are approximated from character length).
- ``rank_chunks_bm25`` — BM25 lexical ranking, fail-open: uses ``rank_bm25``
  when installed, otherwise a small built-in BM25Okapi implementation.
- ``select_evidence_chunks`` — chunk → rank → Jaccard-dedupe → assemble up to
  ``max_chars``.
- ``select_chunks_with_quota_and_fill`` — per-source URL quota + Jaccard
  dedupe + fill toward a target count (TinySearch chunk_pool_selection).
- ``is_jina_antibot`` — recognize "Just a moment…"/Cloudflare challenge pages
  returned by r.jina.ai so they are not treated as content.

Adaptations vs upstream: Unicode-aware tokenization (the corpus here is
often Russian), no hard dependency on rank_bm25/tiktoken, and chunk sizes
are configurable in characters with a default token-equivalent.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

# Unicode-safe tokenization: \w covers Cyrillic and other scripts, while
# ./:#- keep URL-like tokens (paths, ids) intact for both BM25 and Jaccard.
_TOKEN_RE = re.compile(r"[\w./:#-]+", re.UNICODE)

# Rough characters-per-token for chunk-size math (no tiktoken dependency).
# 4 chars/token approximates English; mixed-Cyrillic corpora average ~3.
_CHARS_PER_TOKEN = 4.0


# ── Tokenization ───────────────────────────────────────────────────────────


def tokenize_for_retrieval(text: str) -> list[str]:
    """Same tokenization used by BM25 and Jaccard dedupe."""
    return _TOKEN_RE.findall(str(text or "").lower())


def approx_tokens(text: str) -> int:
    return max(1, int(len(str(text or "")) / _CHARS_PER_TOKEN))


# ── Chunking ───────────────────────────────────────────────────────────────


def _split_long_text(text: str, max_chunk_chars: int, overlap_chars: int) -> list[str]:
    """Split a single over-long block on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?…])\s+|\n+", text.strip())
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not current:
            current = sentence
        elif len(current) + len(sentence) + 1 <= max_chunk_chars:
            current += " " + sentence
        else:
            if current:
                pieces.append(current)
            # Carry the tail of the previous chunk as overlap.
            overlap = current[-overlap_chars:] if overlap_chars and current else ""
            current = (overlap + " " + sentence).strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(
    text: str,
    max_chunk_tokens: int = 500,
    overlap_tokens: int = 80,
) -> list[dict[str, Any]]:
    """Structure-aware text chunking with overlap.

    Paragraph breaks (blank lines) are preserved as chunk boundaries; a chunk
    is never split mid-paragraph unless the paragraph alone exceeds the cap,
    in which case it is split on sentence boundaries. ``overlap_tokens`` worth
    of trailing characters from a previous chunk are carried into the next one
    so retrieval does not lose context across boundaries. Note: overlap only
    applies when a single oversized paragraph is split on sentence boundaries
    (matching TinySearch); paragraph-to-paragraph boundaries carry no overlap.

    Returns chunks shaped like TinySearch's::

        {"chunk_id": int, "text": str, "tokens": int}
    """
    text = str(text or "").strip()
    if not text:
        return []

    max_chars = max(1, int(max_chunk_tokens * _CHARS_PER_TOKEN))
    overlap_chars = max(0, int(overlap_tokens * _CHARS_PER_TOKEN))
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    chunks: list[dict[str, Any]] = []
    current_blocks: list[str] = []

    def flush() -> None:
        nonlocal current_blocks
        body = "\n\n".join(current_blocks).strip()
        if not body:
            current_blocks = []
            return
        chunks.append({"chunk_id": len(chunks), "text": body, "tokens": approx_tokens(body)})
        current_blocks = []

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            for piece in _split_long_text(paragraph, max_chars, overlap_chars):
                chunks.append(
                    {"chunk_id": len(chunks), "text": piece, "tokens": approx_tokens(piece)}
                )
            continue

        candidate = "\n\n".join(current_blocks + [paragraph]).strip()
        if len(candidate) <= max_chars:
            current_blocks.append(paragraph)
        else:
            flush()
            current_blocks.append(paragraph)

    flush()
    return chunks


# ── BM25 ranking (fail-open) ───────────────────────────────────────────────


def _bm25_scores_builtin(
    corpus_tokens: Sequence[Sequence[str]],
    query_tokens: Sequence[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Small BM25Okapi implementation used when rank_bm25 is unavailable."""
    if not corpus_tokens or not query_tokens:
        return [0.0 for _ in corpus_tokens]
    doc_count = len(corpus_tokens)
    doc_lengths = [len(doc) for doc in corpus_tokens]
    avg_length = sum(doc_lengths) / doc_count if doc_count else 0.0

    df: dict[str, int] = {}
    for doc in corpus_tokens:
        for token in set(doc):
            df[token] = df.get(token, 0) + 1

    scores: list[float] = []
    for doc, length in zip(corpus_tokens, doc_lengths):
        score = 0.0
        for token in set(query_tokens):
            freq = doc.count(token)
            if not freq:
                continue
            idf = math.log(
                1.0 + (doc_count - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5)
            )
            denom = freq + k1 * (1.0 - b + b * (length / avg_length if avg_length else 1.0))
            score += idf * (freq * (k1 + 1.0)) / denom
        scores.append(score)
    return scores


def _bm25_scores_rank_bm25(
    corpus_tokens: Sequence[Sequence[str]],
    query_tokens: Sequence[str],
) -> list[float]:
    try:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi(list(corpus_tokens))
        return [float(s) for s in bm25.get_scores(list(query_tokens))]
    except Exception:
        # rank_bm25 present but broken — degrade to builtin.
        return _bm25_scores_builtin(corpus_tokens, query_tokens)


def rank_chunks_bm25(query: str, chunks: Sequence[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    """Rank chunks for a query by BM25. Fail-open: rank_bm25 optional."""
    if not query or not chunks:
        return []
    corpus = [tokenize_for_retrieval(str(c.get("text") or "")) for c in chunks]
    query_tokens = tokenize_for_retrieval(query)
    if not query_tokens:
        return []

    # _bm25_scores_rank_bm25 already falls back to the builtin on any error.
    scores = _bm25_scores_rank_bm25(corpus, query_tokens)

    ranked = sorted(zip(chunks, scores), key=lambda item: item[1], reverse=True)
    out: list[dict[str, Any]] = []
    for chunk, score in ranked[: max(0, top_k)]:
        entry = dict(chunk)
        entry["bm25_score"] = float(score)
        out.append(entry)
    return out


# ── Jaccard dedupe + per-source quota + fill (TinySearch port) ─────────────


def jaccard_similarity_tokens(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return float(inter) / float(union) if union else 0.0


def _token_set(text: str) -> frozenset[str]:
    return frozenset(tokenize_for_retrieval(text))


def dedupe_chunks_by_token_jaccard(
    ranked_chunks: Sequence[dict[str, Any]],
    *,
    threshold: float,
    text_key: str = "text",
) -> list[dict[str, Any]]:
    """Keep chunks in order; drop any whose token Jaccard to an earlier kept chunk is >= threshold."""
    if threshold >= 1.0:
        return list(ranked_chunks)

    accepted: list[dict[str, Any]] = []
    accepted_sets: list[frozenset[str]] = []

    for chunk in ranked_chunks:
        text = str(chunk.get(text_key) or "").strip()
        tokens = _token_set(text)
        if not tokens:
            if any(str(c.get(text_key) or "").strip() == text for c in accepted):
                continue
            accepted.append(chunk)
            accepted_sets.append(frozenset())
            continue
        if accepted_sets and max(
            (jaccard_similarity_tokens(tokens, s) for s in accepted_sets if s), default=0.0
        ) >= threshold:
            continue
        accepted.append(chunk)
        accepted_sets.append(tokens)

    return accepted


def select_chunks_with_quota_and_fill(
    ranked_chunks: Sequence[dict[str, Any]],
    *,
    final_limit: int,
    max_per_source_url: int,
    dedupe_jaccard_threshold: float,
    source_key: str = "source_url",
    text_key: str = "text",
) -> list[dict[str, Any]]:
    """Dedupe globally, enforce per-source cap in a first pass, then fill toward
    ``final_limit`` while still rejecting near-duplicates. TinySearch port."""
    limit = max(0, final_limit)
    if limit == 0:
        return []

    ranked = dedupe_chunks_by_token_jaccard(
        ranked_chunks, threshold=dedupe_jaccard_threshold, text_key=text_key
    )
    if max_per_source_url <= 0:
        return ranked[:limit]

    url_counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    accepted_sets: list[frozenset[str]] = []
    chosen_keys: set[Any] = set()

    def accepts(chunk: dict[str, Any]) -> bool:
        text = str(chunk.get(text_key) or "").strip()
        tokens = _token_set(text)
        if not tokens:
            return not any(str(c.get(text_key) or "").strip() == text for c in out)
        if not accepted_sets:
            return True
        return max(
            (jaccard_similarity_tokens(tokens, s) for s in accepted_sets if s), default=0.0
        ) < dedupe_jaccard_threshold

    def chunk_key(chunk: dict[str, Any]) -> Any:
        # chunk_id alone is not unique across sources, so pair it with the
        # source URL; fall back to identity when neither is present.
        cid = chunk.get("chunk_id")
        url = str(chunk.get(source_key) or "")
        if cid is not None:
            return ("id", url, cid)
        return ("obj", id(chunk))

    def append_chunk(chunk: dict[str, Any]) -> None:
        chosen_keys.add(chunk_key(chunk))
        out.append(chunk)
        accepted_sets.append(_token_set(str(chunk.get(text_key) or "").strip()))

    for chunk in ranked:
        if len(out) >= limit:
            break
        if chunk_key(chunk) in chosen_keys:
            continue
        url = str(chunk.get(source_key) or "")
        if url_counts.get(url, 0) >= max_per_source_url:
            continue
        if not accepts(chunk):
            continue
        url_counts[url] = url_counts.get(url, 0) + 1
        append_chunk(chunk)

    if len(out) < limit:
        for chunk in ranked:
            if len(out) >= limit:
                break
            if chunk_key(chunk) in chosen_keys:
                continue
            if not accepts(chunk):
                continue
            append_chunk(chunk)

    return out[:limit]


# ── High-level evidence assembly ───────────────────────────────────────────


def select_evidence_chunks(
    query: str,
    text: str,
    max_chars: int = 4000,
    max_chunk_tokens: int = 500,
    overlap_tokens: int = 80,
    jaccard_threshold: float = 0.92,
) -> str:
    """Chunk → BM25-rank → Jaccard-dedupe → assemble up to ``max_chars``.

    Replacement for blind ``text[:max_chars]`` truncation: the most relevant
    passages win even when they sit mid-page, and near-duplicate passages are
    dropped so the LLM sees diverse evidence.
    """
    text = str(text or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    chunks = chunk_text(text, max_chunk_tokens=max_chunk_tokens, overlap_tokens=overlap_tokens)
    if not chunks:
        return text[:max_chars]

    ranked = rank_chunks_bm25(query, chunks, top_k=len(chunks))
    ranked = dedupe_chunks_by_token_jaccard(ranked, threshold=jaccard_threshold)

    out: list[str] = []
    total = 0
    for chunk in ranked:
        body = str(chunk.get("text") or "").strip()
        if not body:
            continue
        if total + len(body) > max_chars and out:
            break
        out.append(body)
        total += len(body)
        if total >= max_chars:
            break

    if not out:
        return text[:max_chars]
    return "\n\n".join(out)[:max_chars]


# ── Jina antibot detection (agent-reach port) ──────────────────────────────


def is_jina_antibot(body: str, sample_chars: int = 4096) -> bool:
    """Recognize high-confidence Jina/Cloudflare challenge responses.

    Ported from agent-reach ``_is_antibot_page``. When r.jina.ai returns a
    CAPTCHA or Cloudflare challenge instead of the target page, callers must
    NOT treat it as content — this lets them fall through to other backends.
    """
    sample = str(body or "")[:sample_chars].casefold()

    # Jina's real warning phrasing varies ("requiring captcha" / "requires captcha").
    jina_captcha_warning = "warning:" in sample and (
        "requiring captcha" in sample or "requires captcha" in sample
    )
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = "title: attention required! | cloudflare" in sample and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (jina_captcha_warning and challenge_structure) or cloudflare_block
