import json, re
from collections import defaultdict

_TRACKING_IMG_RE = re.compile(r'(?:google|gstatic|ebay|amazon-adsystem|doubleclick)', re.I)
_YOUTUBE_RE = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})')


def _clean_images(image_urls):
    out = []
    for img in image_urls:
        src = (img.get('src') or img.get('url') or '').strip()
        if not src:
            continue
        if _TRACKING_IMG_RE.search(src):
            continue
        out.append(src)
    return out[:3]


def _extract_youtube(text):
    yids = _YOUTUBE_RE.findall(text or '')
    seen = []
    for vid in yids:
        if vid not in seen:
            seen.append(vid)
    return [f'https://www.youtube.com/watch?v={vid}' for vid in seen[:3]]


def _group_by_category(results):
    groups = defaultdict(list)
    for item in results:
        cat = item.get('category') or 'other'
        groups[cat].append(item)
    return groups


_UI_NOISE_RE = re.compile(
    r'(?:select citation style|cookie|accept all|reject all|subscribe|sign up|log in|register|'
    r'search site|search form|navigation|menu|skip to content|back to top|'
    r'advertisement|ad\b|click here|read more|learn more|show more|load more|'
    r'buy now|add to cart|checkout|free shipping|sale|discount|promo code)',
    re.I,
)
_NAV_ONLY_RE = re.compile(r'^(?:home|about|contact|policy|terms|privacy|faq|help|news)\b', re.I)


def _clean_fact(text: str) -> str:
    t = text.strip()
    if not t:
        return ''
    if len(t) < 50:
        return ''
    if _NAV_ONLY_RE.search(t):
        return ''
    if _UI_NOISE_RE.search(t):
        return ''
    alpha = sum(c.isalpha() for c in t)
    if alpha < max(15, int(len(t) * 0.25)):
        return ''
    return t


_DATE_RE = re.compile(r'\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4})\b')
_PHONE_RE = re.compile(r'\+?\d[\d\s\-\(\)]{7,}\d')
_QUOTE_RE = re.compile(r'[\"\«\»]([^\"\«\»]{3,120})[\"\»\"]')
_MARKERS = {
    'error': re.compile(r'(?:ошиб(?:ка|ки|ок)|error|fault|code\s+\w+|fail(?:ed|ure)?)', re.I),
    'cause': re.compile(r'(?:причин(?:а|ы|ой)|cause|because|из-за|due to|since|поскольку)', re.I),
    'fix': re.compile(r'(?:исправ(?:ить|ление)|fix|repair|resolve|решение|устран(?:ить|ение|ения))', re.I),
    'definition': re.compile(r'(?:это\s+—|означает|means|definition|describe|описание|что такое)', re.I),
    'date': re.compile(r'(?:дата|date|year|год|since|established|основан)', re.I),
    'price': re.compile(r'(?:цена|price|стоимость|от\s+\d{2,6}|\d{2,6}\s*(?:руб|р\.|₽|\$|€|£))', re.I),
}


def _score_sentence(sentence, query):
    s = (sentence or '').lower()
    q_words = [w for w in query.lower().split() if len(w) > 2][:8]
    if not q_words:
        return 0.0
    matched = [w for w in q_words if w in s]
    score = len(matched) / len(q_words)
    for pat in _MARKERS.values():
        if pat.search(s):
            score += 0.15
    if _DATE_RE.search(s):
        score += 0.1
    if _PHONE_RE.search(s):
        score += 0.05
    if _QUOTE_RE.search(s):
        score += 0.05
    return min(score, 1.0)


def _extract_facts(text, query, max_facts=5):
    text = text or ''
    # Split by sentence end OR colon for technical bullet lines
    sentences = re.split(r'(?<=[.!?])\s+|:\s+', text)
    sentences = [s.strip() for s in sentences if 18 < len(s.strip()) < 400]
    scored = []
    for s in sentences:
        sc = _score_sentence(s, query)
        if sc >= 0.15:
            scored.append((sc, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    facts = [_clean_fact(s) for _, s in scored[:max_facts]]
    return [f for f in facts if f]


def _build_markdown_answer(query, deep_result, include_images=True):
    summary = deep_result.get('summary', {}) or {}
    results = deep_result.get('results', []) or []
    categories = deep_result.get('categories', {}) or {}

    alive = [x for x in results if x.get('alive')]
    alive_sorted = sorted(alive, key=lambda x: x.get('relevance', 0), reverse=True)

    if not alive_sorted:
        return (
            '# Результат глубокого поиска\n\n'
            f'## Запрос: {query}\n\n'
            '_По запросу не найдено живых источников._\n'
        )

    parts = [
        '# Результат глубокого поиска',
        f'\n## Запрос: {query}\n',
        f'Всего сырых: **{summary.get("total_raw", "?")}**, проверено: **{summary.get("validated", "?")}**, живых: **{summary.get("alive", "?")}**\n',
    ]

    all_text_chunks = []
    for item in alive_sorted[:3]:
        txt = (item.get('text') or '').strip()
        if not txt:
            txt = (item.get('body') or '').strip()
        if txt:
            all_text_chunks.append(txt)

    combined_text = ' '.join(all_text_chunks)
    facts = _extract_facts(combined_text, query, max_facts=4)

    if facts:
        parts.append('## Проверенные факты\n')
        for i, fact in enumerate(facts, 1):
            parts.append(f'{i}. {fact}')
        parts.append('')

    groups = _group_by_category(alive_sorted)
    for cat, items in sorted(groups.items()):
        parts.append(f'## Категория: {cat}')
        for item in items[:5]:
            title = (item.get('title') or '').strip() or item.get('url', '')
            url = item.get('url', '')
            relevance = item.get('relevance', 0)

            parts.append(f'### {title}')
            parts.append(f'- **Релевантность:** {relevance}/1.0')
            parts.append(f'- **Ссылка:** [{url}]({url})')

            body = (item.get('body') or '').strip()
            text = (item.get('text') or '').strip()
            body_for_facts = text if text else body
            item_facts = _extract_facts(body_for_facts, query, max_facts=2) if body_for_facts else []
            if item_facts:
                parts.append('\n**Проверенные факты:**')
                for fact in item_facts:
                    parts.append(f'- {fact}')

            youtube = _extract_youtube(body)
            if youtube:
                parts.append('\n**Видео:**')
                for yt in youtube[:2]:
                    parts.append(f'- [YouTube]({yt})')

            parts.append('')

    if include_images:
        seen = set()
        unique_img_urls = []
        for item in alive_sorted[:5]:
            for img in _clean_images(item.get('image_urls') or []):
                if img not in seen:
                    seen.add(img)
                    unique_img_urls.append(img)
            if len(unique_img_urls) >= 9:
                break
        if unique_img_urls:
            parts.append('## Иллюстрации\n')
            for img in unique_img_urls[:9]:
                label = img.split('/')[-1][:30]
                parts.append(f'![{label}]({img})')
            parts.append('')

    tag_counts = defaultdict(int)
    for cat_items in categories.values():
        for x in cat_items:
            tag_counts[x.get('category', 'other')] += 1
    if tag_counts:
        parts.append('\n---\n**Категории источников:** ' + ', '.join(
            f'{k}={v}' for k, v in sorted(tag_counts.items())
        ))
        parts.append('')

    return '\n'.join(parts)
