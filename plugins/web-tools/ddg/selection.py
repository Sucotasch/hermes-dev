"""Evidence selection algorithms shared by the Hermes wrapper and standalone.

Brings in three techniques researched from the RAG / agentic-search space
(RAG-Fusion RRF, MMR diversification, novelty/saturation stopping), with the
same Unicode-aware tokenization used by ``evidence_rank`` so Russian/mixed
corpora behave consistently:

- ``reciprocal_rank_fusion`` — merge per-variant result lists so pages found
  by several query variants rank above single-hit pages (RAG-Fusion, k=60).
- ``mmr_select`` — Maximal Marginal Relevance: relevance minus similarity to
  already-selected pages. Replaces hard per-domain quotas: several genuinely
  distinct threads from one domain survive, near-duplicate syndicated copies
  are penalised. Optionally biases toward under-represented ``aspect`` tags.
- ``title_normalize`` / ``dedupe_by_normalized_title`` — catch syndicated
  copies (same article re-published under different URLs/site suffixes).
- ``novelty`` / ``is_redundant`` — saturation signal for stopping Level-2
  expansion when new pages add little new information.

All functions are dependency-free (stdlib) on top of ``evidence_rank``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from evidence_rank import jaccard_similarity_tokens as _jaccard
    from evidence_rank import tokenize_for_retrieval as _tokenize
except Exception:  # pragma: no cover - evidence_rank is always shipped together
    _TOKEN_RE = re.compile(r"[\w./:#-]+", re.UNICODE)

    def _tokenize(text: str) -> list[str]:  # type: ignore[misc]
        return _TOKEN_RE.findall(str(text or "").lower())

    def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:  # type: ignore[misc]
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0


def token_set(text: str) -> frozenset[str]:
    return frozenset(_tokenize(text))


# ── Reciprocal Rank Fusion (RAG-Fusion) ───────────────────────────────────

_RRF_K = 60


def reciprocal_rank_fusion(
    url_rankings: Iterable[Iterable[str]],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Merge several ranked URL lists (best first) into url -> RRF score.

    A page found by two variants at ranks 3 and 5 scores 1/63 + 1/65 while a
    single-hit page at rank 1 scores only 1/61 — cross-query agreement is a
    strong relevance signal the pipeline previously discarded.
    """
    scores: dict[str, float] = {}
    for ranking in url_rankings:
        for rank, url in enumerate(ranking, start=1):
            if not url:
                continue
            scores[url] = scores.get(url, 0.0) + 1.0 / (k + rank)
    return scores


def combine_score(relevance: float, rrf_norm: float, w: float = 0.6) -> float:
    """Blend semantic relevance with the cross-query RRF signal (both 0-1)."""
    return w * relevance + (1.0 - w) * rrf_norm


# ── Title normalization / syndication dedup ───────────────────────────────

# A trailing segment after a separator is stripped only when it looks like a
# short site/brand suffix: "Article | Brand News", "X — Official Site".
# Requirements: whitespace BEFORE the separator (so "Qwen-27B" survives) and a
# short tail (<= 24 chars, no nested separators). Colon is excluded so
# "Name: Biography" vs "Name: Filmography" stay distinct.
_SITE_SUFFIX_RE = re.compile(r"\s+[\|\-–—·•]\s*[\w .'’]{1,24}$")


def title_normalize(title: str) -> str:
    """Normalize a title into a stable dedup key.

    Lowercases, strips trailing site suffixes ("Article | Brand News") and
    collapses punctuation to spaces, so "Sara St. James — Official Site" and
    "sara st james | official site" map to the same key.
    """
    t = str(title or "").strip()
    t = _SITE_SUFFIX_RE.sub("", t)
    t = re.sub(r"[^\w]+", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def dedupe_by_normalized_title(
    items: Iterable[dict[str, Any]],
    title_key: str = "title",
) -> list[dict[str, Any]]:
    """Keep the first item per normalized title (syndicated-copy dedup)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = title_normalize(str(item.get(title_key) or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ── Maximal Marginal Relevance (MMR) ──────────────────────────────────────

_MMR_LAM = 0.7


def mmr_select(
    candidates: Iterable[dict[str, Any]],
    k: int,
    lam: float = _MMR_LAM,
    rel_key: str = "relevance",
    text_keys: tuple[str, ...] = ("title", "text", "snippet"),
    aspect_key: str | None = None,
    aspect_weight: float = 0.15,
    aspect_target: int = 2,
) -> list[dict[str, Any]]:
    """Select up to ``k`` candidates by relevance minus redundancy.

    score(d) = lam * rel(d) + aspect_bonus(d) - (1-lam) * max sim(d, selected)

    Similarity is token-Jaccard over ``text_keys`` — no embeddings needed.
    With ``aspect_key`` set, candidates whose aspect is under-represented in
    the selection get a small bonus, biasing coverage toward uncovered facets.
    """
    cands = [c for c in candidates if c is not None]
    if k <= 0 or not cands:
        return []

    n = min(k, len(cands))
    rels = [float(c.get(rel_key) or 0.0) for c in cands]
    token_sets = [token_set(" ".join(str(c.get(tk) or "") for tk in text_keys)) for c in cands]
    aspects = [str(c.get(aspect_key) or "") if aspect_key else "" for c in cands]

    selected_idx: list[int] = []
    remaining = set(range(len(cands)))

    def aspect_bonus(i: int) -> float:
        if not aspect_key or not aspects[i]:
            return 0.0
        covered = sum(1 for j in selected_idx if aspects[j] == aspects[i])
        if covered >= aspect_target:
            return 0.0
        return aspect_weight * (1.0 - covered / aspect_target)

    for _ in range(n):
        if not remaining:
            break
        best_i: int | None = None
        best_score = float("-inf")
        for i in remaining:
            if not selected_idx:
                score = rels[i] + aspect_bonus(i)
            else:
                redundancy = max(
                    (_jaccard(token_sets[i], token_sets[j]) for j in selected_idx if token_sets[j]),
                    default=0.0,
                )
                score = lam * rels[i] + aspect_bonus(i) - (1.0 - lam) * redundancy
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is None:
            break
        selected_idx.append(best_i)
        remaining.discard(best_i)

    return [cands[i] for i in selected_idx]


# ── Optional cross-encoder rerank (Tier 3, guarded) ───────────────────────
# Enabled only when DDG_RERANK=1 AND sentence-transformers is installed — keeps
# the production Hermes venv free of a multi-GB torch stack until real runs
# prove the extra precision is worth it.
_CROSS_ENCODER: Any = None  # None=not probed, False=unavailable, model=ready


def _load_cross_encoder() -> Any:
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    _CROSS_ENCODER = False
    try:
        import os

        if os.environ.get("DDG_RERANK", "0") != "1":
            return _CROSS_ENCODER
        from sentence_transformers import CrossEncoder

        _CROSS_ENCODER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    except Exception:
        _CROSS_ENCODER = False
    return _CROSS_ENCODER


def cross_encoder_scores(
    query: str,
    candidates: Iterable[dict[str, Any]],
    text_keys: tuple[str, ...] = ("title", "text"),
    top_n: int = 40,
) -> dict[str, float]:
    """Rerank up to ``top_n`` candidates with a cross-encoder; url -> 0-1 score.

    No-op (returns {}) unless DDG_RERANK=1 and the model is available. Scores
    are min-max normalized across the scored batch.
    """
    model = _load_cross_encoder()
    cands = [c for c in candidates if c is not None][:top_n]
    if not model or not cands:
        return {}
    pairs = []
    for c in cands:
        text = " ".join(str(c.get(k) or "") for k in text_keys)[:4000]
        pairs.append((query, text))
    try:
        raw = list(model.predict(pairs))
    except Exception:
        return {}
    if not raw:
        return {}
    lo, hi = min(raw), max(raw)
    span = (hi - lo) or 1.0
    return {
        c["url"]: (float(s) - lo) / span
        for c, s in zip(cands, raw)
    }


# ── Novelty / saturation (Level-2 stopping) ───────────────────────────────

def novelty(text: str, accepted_sets: Iterable[frozenset[str]]) -> float:
    """1 - max Jaccard to already-accepted texts; 1.0 for the first item."""
    tokens = token_set(text)
    if not tokens:
        return 0.0
    worst = max((_jaccard(tokens, s) for s in accepted_sets if s), default=0.0)
    return 1.0 - worst


def is_redundant(text: str, accepted_sets: Iterable[frozenset[str]], threshold: float = 0.7) -> bool:
    """True when the page overlaps an accepted page by >= threshold (Jaccard)."""
    tokens = token_set(text)
    if not tokens:
        return True
    return any(
        _jaccard(tokens, s) >= threshold for s in accepted_sets if s
    )
