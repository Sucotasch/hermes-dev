"""Tests for the feed fast path (RSS 2.0 / Atom / JSON Feed) in vwe.

The void-<link> regression case matters most: without the <link>→<rsslink>
rewrite an HTML parser drops every RSS item URL. No network — _fetch is
monkeypatched for the integration cases. Run from plugins/web-tools/ddg/::

    python -m pytest test_feed_parse.py -q
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import visit_website_enhanced as vwe  # noqa: E402

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>RSS Example Feed</title>
<link>https://example.com/</link><description>test</description>
<item><title>First post</title><link>https://example.com/first</link>
<guid>https://example.com/first</guid>
<pubDate>Wed, 02 Oct 2026 10:00:00 GMT</pubDate>
<description>Hello <b>world</b> of feeds</description></item>
<item><title>Second</title><link>https://example.com/second</link>
<pubDate>Tue, 01 Oct 2026 09:00:00 GMT</pubDate>
<description>Another item body</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Example</title>
<entry><title>Atom item</title>
<link rel="alternate" href="https://example.com/atom/1"/>
<published>2026-10-02T10:00:00Z</published>
<summary>Atom summary text</summary></entry></feed>"""

JSON_FEED = """{
 "version": "https://jsonfeed.org/version/1.1",
 "title": "JSON Feed Example", "feed_url": "https://example.com/feed.json",
 "items": [{"url": "https://example.com/jf/1", "title": "JSON item",
            "content_text": "Plain text body", "date_published": "2026-10-02"}]
}"""


# ── detection ───────────────────────────────────────────────────────────────

def test_is_feed_detects_rss_atom_and_json():
    assert vwe._is_feed(RSS) is True
    assert vwe._is_feed(ATOM) is True
    assert vwe._is_feed(JSON_FEED) is True


def test_is_feed_rejects_html_xhtml_and_late_mentions():
    assert vwe._is_feed("<!doctype html><html><body>hello</body></html>") is False
    xhtml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<html xmlns="http://www.w3.org/1999/xhtml"><body>x</body></html>')
    assert vwe._is_feed(xhtml) is False
    # "<rss" mentioned LATE in an HTML page must not fire (window guard).
    late = ("<html><body>" + "x" * 2000 +
            "<code><rss version=2></code></body></html>")
    assert vwe._is_feed(late) is False
    assert vwe._is_feed("") is False
    assert vwe._is_feed(None) is False


# ── rendering ───────────────────────────────────────────────────────────────

def test_rss_renders_items_links_dates_and_strips_tags():
    page = vwe._feed_to_page(RSS, "https://example.com/rss")
    assert page["source"] == "feed"
    assert page["title"] == "RSS Example Feed"
    assert "First post" in page["content"]
    assert "Second" in page["content"]
    assert "<b>" not in page["content"]           # tags stripped
    assert "world of feeds" in page["content"]    # entities decoded
    # THE void-<link> regression: item URLs must survive parsing
    urls = [l["url"] for l in page["links"]]
    assert "https://example.com/first" in urls
    assert "https://example.com/second" in urls
    assert page["published"] == "Wed, 02 Oct 2026 10:00:00 GMT"


def test_atom_renders_link_href_published_summary():
    page = vwe._feed_to_page(ATOM, "https://example.com/atom")
    assert page["title"] == "Atom Example"
    assert page["links"] == [{"url": "https://example.com/atom/1",
                              "text": "Atom item"}]
    assert page["published"] == "2026-10-02T10:00:00Z"
    assert "Atom summary text" in page["content"]


def test_json_feed_renders():
    page = vwe._feed_to_page(JSON_FEED, "https://example.com/feed.json")
    assert page["title"] == "JSON Feed Example"
    assert page["links"][0]["url"] == "https://example.com/jf/1"
    assert "Plain text body" in page["content"]
    assert page["published"] == "2026-10-02"


def test_truncation_at_60_items_with_honest_note():
    items = "".join(
        f"<item><title>Item {i}</title><link>https://e.com/{i}</link>"
        f"<description>body {i}</description></item>"
        for i in range(70))
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
           f'<title>Big</title>{items}</channel></rss>')
    page = vwe._feed_to_page(rss, "https://e.com/rss")
    assert page["content"].count("## ") == vwe._FEED_MAX_ITEMS
    assert "feed truncated" in page["content"]
    assert "Item 69" not in page["content"]


def test_max_chars_cap():
    items = "".join(
        f"<item><title>T{i}</title><description>{'z' * 900}</description></item>"
        for i in range(30))
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
           f'<title>Cap</title>{items}</channel></rss>')
    page = vwe._feed_to_page(rss, "https://e.com/rss", max_chars=500)
    assert len(page["content"]) <= 500


def test_unparseable_feed_returns_none():
    assert vwe._feed_to_page("<rss version='2.0'></rss>", "https://e.com/x") is None
    assert vwe._feed_to_page("<feed></feed>", "https://e.com/x") is None


# ── visit_website integration ────────────────────────────────────────────────

def test_visit_website_takes_feed_fast_path(monkeypatch):
    monkeypatch.setattr(vwe, "_fetch", lambda url, referrer=None, cookies=None: RSS)
    page = vwe.visit_website("http://feeds.example/rss")
    assert page["source"] == "feed"
    assert "First post" in page["content"]
    assert page["links"][0]["url"] == "https://example.com/first"


def test_visit_website_normal_html_unaffected(monkeypatch):
    html = "<html><body><p>" + "word " * 200 + "</p></body></html>"
    monkeypatch.setattr(vwe, "_fetch", lambda url, referrer=None, cookies=None: html)
    page = vwe.visit_website("http://plain.example/page")
    assert page.get("source") in ("direct", None)   # not the feed path
    assert page["source"] != "feed"
