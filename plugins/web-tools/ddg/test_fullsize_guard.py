"""Guard: a sieve rewrite must still be an image, not a page.

Found on a real visual run: the Imagus YouTube rule maps a thumbnail to
``https://www.youtube.com/embed/<id>?thumb`` (a page, with a doubled ``?``
when the original had a query). We keep the original thumbnail for such
rewrites instead of fetching a page as if it were an image.

Run from plugins/web-tools/ddg/::

    python -m pytest test_fullsize_guard.py -q
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import visit_website_enhanced as vwe  # noqa: E402

YT_THUMB = "https://i.ytimg.com/vi/8Q5UUuvdVWU/maxresdefault.jpg"


# ── the predicate ───────────────────────────────────────────────────────────

def test_rejects_embed_page_rewrite():
    cand = "https://www.youtube.com/embed/8Q5UUuvdVWU?thumb"
    assert vwe._plausible_image_upgrade(YT_THUMB, cand) is False


def test_rejects_double_query_artifact():
    original = "https://i.ytimg.com/vi/8Q5UUuvdVWU/hqdefault.jpg?sqp=-oaymwEqCKgBEF5IW"
    cand = "https://www.youtube.com/embed/8Q5UUuvdVWU?thumb?sqp=-oaymwEqCKgBEF5IW"
    assert vwe._plausible_image_upgrade(original, cand) is False


def test_rejects_unfetchable_schemes():
    assert vwe._plausible_image_upgrade(YT_THUMB, "imagus://extension") is False
    assert vwe._plausible_image_upgrade(YT_THUMB, "data:,abc") is False


def test_accepts_bigger_image_file():
    bigger = "https://live.staticflickr.com/4535/37841676975_bf7976cf5c_o.jpg"
    assert vwe._plausible_image_upgrade(YT_THUMB, bigger) is True


def test_accepts_extensionless_cdn_when_original_had_none():
    original = "https://cdn.example.com/img/thumb/123"
    cand = "https://cdn.example.com/img/full/123"
    assert vwe._plausible_image_upgrade(original, cand) is True


def test_rejects_extension_loss():
    original = "https://cdn.example.com/img/123.jpg"
    cand = "https://cdn.example.com/img/123"      # no extension -> ambiguous
    assert vwe._plausible_image_upgrade(original, cand) is False


def test_rejects_empty_candidate():
    assert vwe._plausible_image_upgrade(YT_THUMB, "") is False
    assert vwe._plausible_image_upgrade(YT_THUMB, None) is False


# ── integration through upgrade_to_fullsize ────────────────────────────────

def test_upgrade_keeps_original_when_sieve_returns_page(monkeypatch):
    import sieve
    monkeypatch.setattr(sieve, "apply",
                        lambda url, source_url="": "https://www.youtube.com/embed/x?thumb")
    out = vwe.upgrade_to_fullsize(YT_THUMB, YT_THUMB)
    assert out == YT_THUMB                      # page rewrite discarded


def test_upgrade_applies_real_image_rewrite(monkeypatch):
    import sieve
    target = "https://cdn.example.com/full/123.jpg"
    monkeypatch.setattr(sieve, "apply", lambda url, source_url="": target)
    src = "https://cdn.example.com/thumb/123.jpg"
    assert vwe.upgrade_to_fullsize(src, src) == target
