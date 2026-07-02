# PROJECT_CONTEXT.md — Deep Research Pipeline

Полный контекст проекта. Документ создан для:
1. Восстановления работы при потере контекста сессии
2. Переноса улучшений из standalone-версии обратно в Hermes-интеграцию

---

## 1. Архитектура

### Две версии пайплайна

| Версия | Файлы | Назначение |
|--------|-------|------------|
| **Hermes** | `hermes-agent/tools/ddg_search_tool.py` + `plugins/web-tools/ddg/*` | Встроена в Hermes Agent, работает через `tools.registry` |
| **Standalone** | `standalone/deep_research.py` + `standalone/orchestrator.py` + `standalone/llm_client.py` | CLI-приложение, подключается к llama.cpp server |

### Файловая структура

```
hermes-dev/
├── hermes-agent/tools/
│   ├── ddg_search_tool.py        ← wrapper: registry.register(), query_type routing
│   └── browser_dialog_tool.py    ← stub, не используется
├── plugins/web-tools/ddg/
│   ├── ddg_search.py             ← backend: search, validation, blocklist, images
│   ├── visit_website_enhanced.py ← fetcher: curl_cffi, httpx, Jina
│   ├── query_variants.py         ← генератор вариантов запросов
│   └── compose.py                ← markdown formatter
├── standalone/
│   ├── deep_research.py          ← CLI точка входа
│   ├── orchestrator.py           ← пайплайн: search→validate→Level2→deep-read→synthesize
│   └── llm_client.py             ← HTTP клиент для llama.cpp
├── skills/
│   ├── restore-context/SKILL.md  ← recovery skill
│   └── web-deep-search/SKILL.md  ← deep research skill (очищен: 706 строк)
├── restore.ps1                   ← scripted restore
├── CONTEXT.md                    ← Hermes-специфичный контекст
├── AGENTS.md                     ← quick reference для агентов
├── README.md                     ← developer docs
└── PROJECT_CONTEXT.md            ← ЭТОТ ФАЙЛ
```

### Зависимости

| Пакет | Версия | Назначение |
|-------|--------|------------|
| Python | 3.11+ | Основной runtime |
| httpx | 0.28.1 | HTTP client (**`proxy=` не `proxies=`**) |
| curl_cffi | 0.14.0 | Anti-detection TLS fingerprinting |
| ddgs | 9.14.4 | Multi-engine search (DDG, Yahoo, Yandex, Mojeek) |
| bs4 | 4.13.4 | HTML parsing |
| lxml | 6.0.2 | XML/HTML parser |

---

## 2. Критические исправления (2026-06-12)

### httpx proxy API (СЛОМАНО → ИСПРАВЛЕНО)

httpx 0.28.1 убрал `proxies=` (мн. ч.), теперь только `proxy=` (ед. ч.).

```python
# БЫЛО (ломается с TypeError):
httpx.Client(..., proxies={"http://": PROXY_URL, "https://": PROXY_URL})

# СТАЛО (работает):
kwargs = {"http2": True, "follow_redirects": True, "timeout": 15}
if proxy:
    kwargs["proxy"] = proxy
httpx.Client(**kwargs)
```

**Файлы:** `ddg_search.py:197-211`, `visit_website_enhanced.py:152-162`

### query_type pipeline (НЕ РАБОТАЛО → ИСПРАВЛЕНО)

**Было:** `_is_visual_topic()` с关键词-списком в коде.
**Стало:** `query_type` в схемах, handlers, `_safe_deep_research`.

- Схемы: `query_type` с enum в `_schema_search_deep()` и `_schema_deep_research()`
- Handlers: `args.get("query_type")` пробрасывается в обе функции
- `_safe_deep_research`: `query_type == "visual"` вместо `_is_visual_topic(query)`
- `_is_visual_topic()` **удалён** из кода

**Файл:** `ddg_search_tool.py`

### final_limit 40→80

**Было:** `final_limit = 40` в `_apply_post_retrieval_filter`
**Стало:** `final_limit = 80`

**Файл:** `ddg_search_tool.py:293`

---

## 3. Anti-bot механизмы

### curl_cffi impersonation

- `IMPERSONATE_POOL = ["chrome110", "chrome116", "chrome120", "chrome124"]`
- Session cache key: `(PROXY_URL, impersonation_version)` — отдельные сессии
- UA ротация: 18 UA в пуле, re-select на retry

### DNS circuit breaker

```python
# В web_search и _fetch:
if "getaddrinfo" in str(e).lower():
    break  # Пропускаем оставшиеся стратегии
```

### Proxy retry для blocked URLs

`_check_url_live`: при 403/429/451/503 автоматический retry через прокси.

### JS-block detection

Индикаторы: `"javascript is disabled"`, `"enable javascript and then reload"`, `"requires javascript"`

### Domain blocklist

~90 доменов: analytics, ads, search engines, aggregators, Russian portals.
`VISUAL_ALLOWLIST`: pinterest, deviantart, artstation — не блокируются для visual.

### Overlay bypass

ID-based (`age-gate`, `cookie-consent`), text-based (`"Are you over 18?"`), button removal (`"Accept all"`, `"I agree"`).

---

## 4. Content Relevance Scoring

```python
content_relevance_score(query, text) → 0.0-1.0
```

**Person query gate:** если все слова query ≤5 символов → entity phrase (все слова кроме последнего) должно быть substring в тексте. Предотвращает "Sara James" (другой человек) от scoring для "Sara St James".

**Topic queries:** word-overlap scoring без phrase gate.

**Penalty:** text < 200 chars → 0.3x multiplier.
**Bonus:** words appearing 3+ times → +0.2.

---

## 5. Image Extraction Pipeline

### Извлечение из страниц (primary)

```python
extract_fullsize_images(html, base_url) → list of URLs
```

Источники (по надёжности):
1. `og:image` meta tag
2. `<a href="...jpg"><img>` gallery pattern
3. `srcset` (максимальный Nw)
4. `data-original` / `data-lazy-src`
5. JSON-LD `image` field
6. `<img>` fallback (с фильтрацией tracking pixels)

### Image URL upgrade

```python
upgrade_to_fullsize(url) → url
```

Regex: suffix removal (`-150x150`, `-small`), flickr tokens (`_s`→`_b`), path cleanup, CDN subdomains, query params.

### Не использовать image_search для deep research

`image_search` через Bing/Jina возвращает **страницы-прокладки**, не прямые URL. Изображения нужно извлекать из **raw HTML** скачанных страниц через `extract_fullsize_images`.

---

## 6. Context Optimization (96k models)

### Проблема

96k context − 20k Hermes − 8k skill − 10k history = ~58k доступно. Evidence pack 60KB = 15k tokens = 26% budget.

### Решения (в standalone)

| Параметр | Hermes | Standalone |
|----------|--------|------------|
| final_limit | 80 | 25 |
| text per page | 4,000 | 500 (summary) |
| images | 20 | 8 |
| max_result_size_chars | 60,000 | 20,000 (auto) |

### `_compact_evidence(pages, max_per_page=500)`

Берёт первый абзац (самый информативный) + metadata. Вместо 4,000 символов → 500.

### Что нужно перенести в Hermes version

1. `final_limit=25` в `_safe_deep_research` (сейчас 80)
2. `_compact_evidence` перед return
3. Images limit 8 (сейчас 20)

---

## 7. Standalone Pipeline (llama.cpp)

### Запуск

```bash
cd standalone
python deep_research.py "ваш запрос" --server http://localhost:8888
```

### Пайплайн

```
1. classify → query_type (person/visual/technical/fact/science/education/art/historical/news/comparison/general)
2. enrich → LLM добавляет aliases для person (Sara St James, Jackie Lovell)
3. search → 5 variants × 50 URLs с query-type aware suffixes
4. blocklist → ~90 доменов
5. validate → 100 URLs, 10 threads, timeout=5s
6. Level 2 → если alive < 20, расширение из ссылок страниц
7. deep-read → raw HTML через _fetch() + extract_fullsize_images
8. images → из og:image/img тегов скачанных страниц (не image_search!)
9. evidence → compact: 25 страниц × 500 символов
10. synthesis → 2 pass: facts extraction → comprehensive report
```

### Query-type aware suffixes

| Тип | Суффиксы |
|-----|----------|
| person | career biography, free gallery photos, personal life interview, aliases |
| technical | github repository, documentation guide, download installation |
| art | gallery exhibition, artist biography, high resolution images |
| fact | exact answer, scientific evidence, calculation how |
| science | research paper, experiment results, discoveries |
| education | tutorial course, textbook guide, online course |
| historical | history origins timeline, chronology evolution, archival sources |
| visual | free gallery, high resolution, wallpapers |
| news | latest news, current status, developments |
| comparison | vs alternative, pros cons, benchmark |
| general | detailed analysis, comprehensive guide, expert overview |

### LLM enrichment (person queries)

```python
enrich_query(query, "person") → "Jacqueline Lovell Sara St James Jackie Lovell actress"
```

LLM из training data добавляет aliases. Универсально — работает для любого человека.

---

## 8. Known Limitations

| Ограничение | Причина | Обход |
|-------------|---------|-------|
| 40-46% URL blocked (403/429) | Cloudflare/WAF | Proxy retry помогает 5-10% |
| IMDB/Wikipedia/Reddit blocked | JS-block / Cloudflare | JS-block detection flags these |
| `image_search` возвращает страницы | Bing pipeline | Используем `extract_fullsize_images` из HTML |
| LLM галлюцинирует факты | Small model limitation | Fact verification pass (TODO) |
| `query_variants` missing after restore | Hermes update | Static fallback в `_query_variants_wrapper` |
| `compose=True` не используется | LLM синтезирует из raw JSON | Acceptable для standalone |

---

## 9. Что перенести из standalone в Hermes

| Улучшение | Standalone | Hermes | Действие |
|-----------|-----------|--------|----------|
| Query-type aware suffixes | ✓ | `_query_variants` с generic suffixes | Обновить `_query_variants_wrapper` |
| LLM enrichment (aliases) | ✓ | Нет | Добавить `enrich_query` в wrapper |
| Level 2 blocklist check | ✓ | Нет | Добавить `is_blocked_domain` в Level 2 |
| `_compact_evidence` | ✓ | Нет | Добавить в `_safe_deep_research` |
| final_limit=25 | ✓ | 80 | Изменить дефолт |
| Images from page HTML | ✓ | `image_search` через Bing | Использовать `extract_fullsize_images` |
| Multi-pass synthesis | ✓ | single pass | Добавить facts extraction pass |
| 11 query categories | 7 categories | 7 categories | Расширить classify |

---

## 10. Тесты

### Hermes version
```bash
python -m pytest plugins/web-tools/ddg/test_query_variants.py
python -m pytest hermes-agent/test_coverage_gate.py
python -m py_compile plugins\web-tools\ddg\ddg_search.py
python -m py_compile plugins\web-tools\ddg\visit_website_enhanced.py
python -m py_compile hermes-agent\tools\ddg_search_tool.py
```

### Standalone version
```bash
cd standalone
python deep_research.py "test query" --validate 10 --output test.md
```

---

## 11. Git

- **master** — Hermes-интегрированная версия
- **standalone** — независимое CLI-приложение
- GitHub: https://github.com/Sucotasch/hermes-dev
