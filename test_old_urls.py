"""Test URLs from old report to see which are still alive."""
import sys; sys.path.insert(0, "plugins/web-tools/ddg")
import ddg_search as ddg

urls = [
    "https://viewmaze17.blogspot.com/2020/04/pc-itw-sara-stjames-004.html",
    "https://suze.net/tour/models/sarastjames.html",
    "https://www.liveinternet.ru/users/xubuciki45/post403541719/",
    "https://grafics-allinone.blogspot.com/2008/08/sara-stjames-honey.html",
    "https://anotherbabe.com/sara-stjames-in-a-classic-j-stephen-hicks-photo-series",
    "https://terapatrickxx.blogspot.com/2011/02/sara-st-james.html",
    "https://sportina.fashion/naked+sarah+saint+james",
    "https://bedroomwallbabesthree.blogspot.com/2018/10/a-sara-st-james-mega-collection.html",
]
for url in urls:
    r = ddg._check_url_live(url, timeout=8)
    body = r.get("body", "")
    body_len = len(body) if body else 0
    text_len = 0
    if body:
        import re
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"\s+", " ", text).strip()
        text_len = len(text)
    print(f"alive={r.get('alive')} status={r.get('status')} proxy={r.get('proxy_used')} body={body_len} text={text_len} | {url[:70]}")
