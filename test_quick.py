import sys; sys.path.insert(0, 'plugins/web-tools/ddg')
import ddg_search as ddg
urls = [
    'https://viewmaze17.blogspot.com/2020/04/pc-itw-sara-stjames-004.html',
    'https://www.babepedia.com/babe/Sara_St._James',
    'https://penthousegold.com/models/sara-st-james.html',
]
for url in urls:
    r = ddg._check_url_live(url, timeout=8)
    body = r.get('body', '') or ''
    print(f"alive={r.get('alive')} status={r.get('status')} body={len(body)} | {url[:60]}")
