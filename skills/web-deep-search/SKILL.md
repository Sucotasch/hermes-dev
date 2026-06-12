---
name: web-deep-search
description: "Deep web search in Hermes with zero external API dependency. Multi-engine collection (DDG/Yahoo/Yandex/Mojeek), HEAD-first validation, relevance scoring, Level 2 expansion via `web_expand_and_fetch`, and optional image search. Wrapper supports raw JSON output for agent-side synthesis or `compose=True` for Markdown. Native `web_search` fallback included. Distinctive features: dynamic query variants, multi-engine pool enlargement, no Felo/SearchAPI/SerpApi keys required, recoverable wrapper after Hermes updates."
version: 3.2.0
tags: [web-search, deep-search, validation, raw-json, agent-synthesis, expansion, deep-level, wrapper, fallback, web_expand, multi-engine]
platforms: [linux, macos, windows]
---

# Web Deep Search — Глубокий поиск без внешних API

## Activation triggers
Use this skill when the task requires multi-source evidence, cross-referencing, comprehensive coverage, or narrative synthesis. Trigger for complex research questions, not simple lookups.

### WHEN to use
- Language: match the user's conversation language. Russian query → Russian report; do not auto-translate unless asked.
- Comparative or analytical queries (e.g., "compare X vs Y for Z use case")
- Topic overviews requiring 10+ sources (e.g., "deep dive into recent advances in X")
- Fact-checking with source attribution across multiple domains
- Research for reports, audits, or reviews with evidence requirements
- Visual/people topics where images are needed (auto-triggers `image_search`)
- Historical/general topics where automatic categorization produces noisy bullet lists (use agent-side synthesis mode)

### When NOT to use (use `web_search` instead)
- Simple factual lookups: "what is X", "who is Y", "when did Z happen"
- Single-source verification with one authoritative answer expected
- Time-critical queries where speed matters more than coverage
- Queries clearly answerable from training data without web evidence

### Explicit commands
`web_search_deep`, `/web_search_deep`, `deep research`, `deep search`, `research mode`, `comprehensive web search`, `multi-source research`

### Provider behavior
- `web_search_deep` returns raw validated JSON (no markdown, no classification).
- `web_deep_research` returns a unified evidence pack: `pages`, `images`, `panel`.
- If markdown synthesis is required, the local agent builds it from the raw evidence pack using this skill's Minimal Output Skeleton.
- All types of research: requested by the user, the agent decides the appropriate `query_type` and passes it to the tool. Backend supports the label via wrapper and backend without raising.
- **Do NOT expect `web_search_deep` or `web_deep_research` to call an external/auxiliary LLM.** They do not.\n- If markdown synthesis is required, the local agent builds it from the raw evidence pack using this skill's Minimal Output Skeleton.\n- Treat `compose=True` as a local markdown-formatting only instruction. If an environment-specific issue ever prevents its use, fall back to manual synthesis but do not attribute the failure to a separate nonexistent auxiliary LLM call.\n
## Оптимизация валидации
- **HEAD-first**: Dead/blocked URL отсекаются на HEAD; GET делается только для 2xx/3xx.
- **timeout_per_url=3s** (снижен с 10s). При быстром канале (25 Мбит/с) большинство живых сайтов отвечают за 1–2.5с; 3с — достаточный бюджет, а медленные/мертвые отсекаются быстро.
- **Параллельность увеличена до max_workers=10** (было 5). Краткий таймаут резко сокращает задержки без потери живых ссылок.
- **max_validate=100** по умолчанию. Это даёт ~2x ускорение при умеренной потере живых ссылок; при недостатке coverage автоматически триггерится Level 2 (`alive<15` или `not _is_coverage_sufficient`).
- Прокси опционален; без него alive-rate часто выше.
- **Composite deadline = 360с (6 мин)** в `web_deep_research`. При нормальной работе 4 варианта × проверка 100 URL укладываются в этот бюджет; при задержках пайплайн корректно завершается на текущем evidence pack. Ранее дедлайн был 300с, но прогоны по сети 25 Мбит/с часто выходили за 315с.

## Анти-бот и повторные вызовы
- **Прокси опционален**. Локальный NECOBOX (127.0.0.1:2080) доступен, но не должен применяться автоматически. В `visit_website_enhanced.py` по умолчанию `USE_PROXY=False`. Подключать его стоит только при явном низком alive-rate или массовом blocked.
- **Rotate**: при повторных прогонах сравнивай показатели alive/blocked с прокси и без. Поведение зависит от конкретного выхода.
- **Risk-quarantine доменов**: YouTube/RealPython/W3Schools/Cloudflare-сайты часто дают blocked. Принимай blocked status и двигайся дальше.
- **Сначала**: попробуй direct; если blocked >60%, включи прокси.

## Универсальный паттерн многоформулькового поиска (multi-query reformulation)

### Входная структура запроса
Декомпозируй пользовательский запрос на **Core** + **Constraint** + **Variants**.

| Компонент | Роль |
|---|---|
| **Core** | неизменная сущность: имя человека, продукт, тема |
| **Constraint** | жёсткое условие: `gallery`, `forum`, `images`, `official`, `manual`, `error code` |
| **Variants** | 2-5 близких по смыслу обёрток того же intent |

Правило: **Core + Constraint должны присутствовать в КАЖДОМ варианте запроса**.

### Шаблоны контекстных вариаций
- **Глагол/действие**: `how to fix / meaning / causes / reset / install / setup / find / access`
- **Тип ресурса**: `guide / tutorial / manual / forum thread / wiki / blog`
- **Платформа/источник**: `official documentation / community / Reddit / StackOverflow / support page`
- **Аспект проблемы**: `symptoms / troubleshooting / error code / self-clean / reset procedure`

**media-расширение**: если запрос явно про персону, музыку, фильм, сериал, клип — добавь YouTube-контекст: `... youtube`, `... official video`, `... youtube music`.

### Использование в `search_deep`
```python\nsearch_deep(\n    query="Vaillant boiler F28 error",\n    query_variants=[\n        "Vaillant boiler F28 error code causes fix",\n        "Vaillant F28 fault reset procedure official",\n        "Vaillant boiler F28 self-clean ignition repair",\n        "Vaillant F28 error forum threads community",\n    ],\n    validate=True,\n    max_validate=40,\n    timeout_per_url=3,\n)\n```\n\n**Dynamic variants:** if no explicit `query_variants` are provided and the initial pool falls below `max_validate`, `search_deep` may auto-generate coverage-extending reformulations from first-pass results. That auto-gen is pure token heuristics; it does not call an external LLM and still does not infer intent classes.\n
**Динамические query_variants (auto-mode):** если не передавать `query_variants` и сырой пул оказался меньше `max_validate`, `search_deep` сам генерирует 1-3 дополнительных варианта на основе первого прохода. Режим полезен, когда тема широкая и первый проход уловил только часть аспектов.

## Query type tagging (LLM-decided, not code-decided)
Вместо keyword-списков в коде, получай тип запроса из доменной классификации агента без запуска инструментов. Код больше не решает technical/visual/historical по словарю — принимает только уже помеченный запрос.
Разрешённые метки: `visual`, `technical`, `news`, `historical`, `comparison`, `general`.
Если метка не задана явно, пайплайн использует `general`.
Передача метки в первый вызов делает всю дальнейшую логику дистрибутивной и не зависит от пополняемого набора слов.

**LLM intent->variant mapping (контракт):**
- `visual` → force image search, ≥8 sources, ≥1 image result, relaxed image tolerance
- `technical` → ≤4 variants в строго intent-реформулировках, без generic-суффиксов `history/trends/examples/resources`, bias official/github/docs
- `news` → bias recent sources, не блокировать короткие pages
- `historical` → broaden variants, tolerate lower alive
- `comparison` → min 3 sources на сторону
- `general` → balanced defaults; bot-challenge tagger is metadata-only, images are not forced

Пример:
```
web_search_deep(
    query="...",
    query_type="technical",
    validate=True,
    classify=False,
    max_validate=35,
    compose=False
)
```

## PR-1

Сначала выводи синтезированный ответ (narrative, checklist, ranked list — смотря что уместно). Если тема явно историческая/общая — избегай шаблонной категоризации; делай связный повествовательный отчёт, а не bullet-список.
Hermes tool registration for `web_search_deep` считается активным только если:
- `discover_builtin_tools()` возвращает модуль обёртки;
- `registry.get_tool_names_for_toolset('web')` фактически содержит `web_search_deep`.
Если одно из этих условий не выполнено — инструмент не зарегистрирован, даже если файл `ddg_search_tool.py` на диске существует.

### Coverage extension via `ddgs`
- Внутри `search_deep` после HTML/JSON стратегий опционально запускается расширенный проход через `ddgs.text` для увеличения сырого пула.
- **Важно:** `DDGS().text()` возвращает около 10 результатов на один запрос. Чтобы набрать широкий пул, используй **несколько query_variants** (3–6 формулировок) и суммируй уникальные URL.

### Single-query coverage cap
- `web_search(..., count=100)` лишь запрашивает до 100 результатов, но DuckDuckGo HTML часто возвращает 10-30. Если одиночный запрос дал мало сырых URL, это нормально и не означает поломку.
- Поэтому один прогон `search_deep` **не гарантирует** широкий coverage. Ожидаемый практический предел одного запроса — обычно 10-30 сырых + ~20-34 через `ddgs.text`.
- Для расширения покрытия используй multi-query (`query_variants`): разные формулировки одного и того же запроса позволят набрать 50-100+ уникальных сырых URL.
- **Динамические query_variants:** если после первого прохода по каким-то аспектам темы мало живых источников, `search_deep` сам сгенерирует второй виток вариантов, нацеленный на пробел, и расширит пул. Это уменьшает ручную доработку под конкретную тему.

## Query Type Policy

Agent decides `query_type` from the user's goal before calling the tool.
Code must never infer intent from keywords, length, or content-derived signals.
Allowed values: `visual`, `technical`, `news`, `historical`, `comparison`, `general`.
Default when omitted: `general`.

### Intent → query_type mapping

| Intent category    | user goal / ask                                             | query_type   |
|--------------------|-------------------------------------------------------------|--------------|
| Visual material    | картинки/арт/дизайн/фото/персоны/персонажи                  | visual       |
| Technical details  | код/API/конфиг/ошибка/архитектура/инструмент                | technical    |
| Recent events      | что произошло / сейчас / сегодня / хроника                  | news         |
| Comparison         | сравнивает X и Y; достоинства/недостатки; A vs B           | comparison   |
| Background         | история/предпосылки/происхождение/контекст events           | historical   |
| Default            | общее/смешанный запрос без явной категории                  | general      |

If `query_type` is omitted the wrapper/router must assume `general`.
This table is the expansion surface; add rows only when a new caller-visible behavior needs tagging. Do not add code-side keyword classification alongside it.

### Hard contract — no keyword detection in plugins

Adding keyword lists, visual-signal dictionaries, length-based gates, or any content-derived intent detection to `ddg_search.py`, `ddg_search_tool.py`, `query_variants.py`, or `visit_website_enhanced.py` is prohibited.
This logic drifts over time, duplicates the agent's decision, and creates a second classification layer the skill explicitly removed.
Backend receives `query_type` only as a routing label, not as a classification source.

### Verified wrapper wiring

Call pattern for the updated wrapper:

```python
web_deep_research(query="...", query_type="technical", max_validate=100)
```

`web_deep_research` forwards `query_type` and `compose` into `_safe_deep_research`; backend `search_deep()` accepts `query_type=None` without raising.
`compose=True` is supported as a local markdown-formatting path only; the tool stack does not call external/auxiliary LLMs.
Observed failure mode to avoid: routing composed markdown through a provider chat endpoint and treating the resulting HTTP 400 as a “missing auxiliary LLM” issue. The correct behavior is local agent synthesis from the evidence pack, or local `compose._build_markdown_answer` formatting.

### Implementation notes
- `query_variants` backend module exposes `generate(query)`; do not import `_suggest_query_variants`; it may be absent and causes `AttributeError`.
- After wrapper changes, validate with `discover_builtin_tools()` and `registry.get_tool_names_for_toolset('web')` containing `web_deep_research`.
- Do not change proxy or HTTP fallback behavior while adjusting routing fields. Registry + schema checks are the correct validation surface.
- Newer `httpx` builds support `httpx.Proxy(url)`; older builds used `proxies={...}`. If backend fallback logs `Client.__init__() got an unexpected keyword argument 'proxies'`, the active venv expects the newer proxy API path. Wrap proxy creation in try/except and prefer the already-patched backend wrapper instead of editing `ddg_search.py` blindly.
- See `references/wrapper-validation-2026-06-11.md
references/pool-size-regression-2026-06-12.md` and `references/tested-backend-compatibility.md` for captured validation checklists and known-good backend behavior.

## Source Priority & Fetch Policy

1. Source priority не должен быть «всем сдать». Дублирующиеся домены/очереди снижай в пользу уникальных хостов и authoritative источников.
2. После validation запускай fetch/visit только для живых и релевантных result-блоков.
3. Извлеченный контент систематизируй, а не скидывай как цитату: выделяй факты, контекст, source reference, media.
4. Если coverage gap по части запроса — сообщи явно, не добавляй текст из других прогонов/доменов.

## Structured Answer Framework

### Principles
- **Cite per fact.** Каждий факт = ссылка на источник.
- **Format follows query type.** Checklist для инструкций, narrative для биографий, ranked list для рекомендаций.
- **Mandatory media for person-query.** Если запрос явно про конкретное лицо (человек, персона, персонаж), ответ должен включать иллюстрацию: фото/изображение/портрет или явный блок `media` с доступными изображениями. Не оставляй человек-ответ без визуального якоря, если такие ссылки есть в живых результатах.
- **Honest gap report.** Если аспект не найден — сказать об этом.
- **No query contamination.** История/факты из предыдущих прогонов не утекают в текущий ответ.
- **Structured over raw.** Используй compose mode для готового ответа, а не сырого дампа.

### Minimal Output Skeleton (compose mode)
1. **Header** — запрос, total_raw, validated, alive, time.
2. **Per-facet blocks** — по блокам запроса: finding + source + media (опционально).
3. **Cross-cutting notes** — alias, alternate spellings, confidence.
4. **Not found** — явный список неприкрытых аспектов.
5. **Sources footer** — title, URL, category, relevance, inclusion reason.

### Bot-challenge exclusion from synthesis
Если страница содержит challenge-маркеры (`captcha`, `cloudflare`, `cloudflare-`, `are you a robot`, `are you not a robot`, `verify you are human`, `verify you are a human`, `verify your identity`, `checking your browser`, `attention required`, `access is denied`, `ddg verification`, `robot challenge`, `sorry, you have been blocked`, `please enable javascript`), она **не цитируется** и **не включается** в Sources footer, даже если `status=200` и `alive=True`. Валидатор должен помечать такие страницы `bot_challenge`, а синтез должен их игнорировать. Это предотвращает просачивание антибот-страниц в финальный ответ как авторитетных источников.

### Composed output contract
`search_deep(..., compose=True)` возвращает `str` (Markdown), не JSON dict.
- Каждая категория = Markdown секция `## Категория: <name>`.
- Каждый alive result = subsection с relevance, ссылкой, excerpt, images, YouTube.
- Tracking images (`gstatic`, `doubleclick`, `ebay`, `amazon-adsystem`) фильтруются.
- Если живых источников нет — short Markdown note о coverage gap.
- **Проверенные факты** выводятся из body/title/snippet источника, ключевые тезисы остаются под ссылкой, а не сжатый пересказ.

## Core principle: synthesized answer, not link list

The user's stated goal for deep research is not to return a list of links for the user to follow. The goal is to **find, study, extrapolate, synthesize, and present a complete answer** across the user's query. Links serve as **proof of existence for sources**, not as the deliverable.

This means:
- The final output must be a readable narrative/report, not a URL dump.
- Every claim needs an inline citation `[N]` pointing to a real source URL.
- Coverage gaps must be stated explicitly, not hidden.
- Raw pipeline stats (raw count, validated count, timing) are implementation details, not user-facing content — omit them unless the user explicitly asks for methodology.

## Chat Delivery Rule
1. **Answer first.** Deliver the synthesized narrative/report inline, not the pipeline process. Pipeline stats belong in a tiny optional methodology footer, never as the opening.
2. **Do not blame environment/network when the user reports stability.** If the user says the system is stable and used to work, treat wrapper state, post-retrieval filters, and registry packaging as the suspect set first; only after that consider external strategy health.
3. **No pipeline narrative as opener.** Start with the topic, not with "elapsed", "panel", "Strategy X failed", or countdown of steps. Going straight to the topic is a hard requirement, not a soft preference.
4. **Inline citations** per fact; footer with full source list. Raw JSON dumps are not deliverables.
5. **No auto-emitted pipeline stats** — if useful, fold into one line in a hidden/compact Methodology footer, never with headers like "Pipeline status".
6. **Do not start** with "I found N sources", "Passed X stages", "Deep research result:" — go straight to the topic.
7. "Tell me about X" is an answer request, not a process report. Include methodology only if the user explicitly asks "how did you find this" or "show the process".
8. **Language default = conversation language.** If the user writes in Russian, the answer must be Russian too. Never auto-switch to English for technical topics unless explicitly requested.

### External Deep-Research Frameworks — Pilot Protocol

When an external framework like `deep-research-pipeline` is proposed for integration with Hermes tooling, follow this pilot protocol instead of changing code or config immediately.

### Compatibility check (no code changes)
1. Map framework stages to existing Hermes tools:
   - Collection → `web_search` / `web_search_deep`
   - Validation / live-status → `web_search_deep(..., validate=True)`
   - Content extraction → `web_extract` / `visit_website_enhanced` / `browser_*`
   - Fact synthesis → `compose=True` or manual Markdown build
   - Quality scoring → currently unavailable as a built-in Hermes tool; must be provided outside the framework or simulated
2. Verify wrapper registration:
   - `discover_builtin_tools()` must find the module
   - `registry.get_tool_names_for_toolset('web')` must contain `web_search_deep`
   A status flag is not sufficient; run the CLI probe before scheduling runs.
3. Verify `compose`-fact layer availability:
   - `compose._extract_facts` is available only at runtime when `compose=True` is invoked;
   the wrapper registration does not require touching `compose.py`.

### Dry-run first (always)
- Generate a manifest for the topic, listing the expected Hermes tool calls per framework stage.
- Do not modify `config.yaml`, scripts, or skills until the manifest matches what the framework would ask Hermes to do.
- For Russian users, preserve Russian as the report language throughout all manifest stages unless explicitly asked otherwise.

### Backward compatibility rule
Any added step must be removable without breaking the existing `web_search_deep` flow. Do not make `web_search_deep(..., compose=True)` conditional on the presence of an external framework.

## Fetcher and Level-2 contract (empirical, 2026-06-06)
- Preferred fetcher: `visit_website_tool`.
- `web_extract` is **best-effort only**. In 2026-06-06 tests it returned **0 chars** on multiple otherwise alive pages while `visit_website_tool` returned useful text.
- If returned text is < 500 chars, or contains challenge markers (`Checking your browser`, `captcha`, `cloudflare`, `Access is denied`), retry with proxy if available.
- Level 2 evidence must enter the synthesis pool. Use `web_expand_and_fetch` for Level 2 expansion + fetch.
- **Antiblocking**: In restricted environments, prefer direct fetch with light output (`visit_website_tool`) over browser-based extraction; buffer CAPTCHA risk on `browser_click` unless required by UI flow.
- **Minimum return discipline**: If workflow asks for a single minimal artifact, return exactly that file path and its content length + checksum. Do not print the newly created full prompt body in chat.
- `web_deep_research` now applies post-retrieval Jaccard dedup (`0.85` threshold), per-source URL quota (`4`), and global page cap (`80`) before returning the evidence pack. Treat `pages` as already filtered; do not re-run dedup unless explicitly requested.
- Preserve dedup awareness across Level 1 + Level 2 before synthesis; dedup across `pages` is already handled by the wrapper.
- Registry invariant: after a wrapper change, verify `web_expand_and_fetch`/`web_deep_research` remain in `registry.get_tool_names_for_toolset('web')` before chaining Level 2.

## Composite tool contract (2026-06-06)
- `web_deep_research(query, max_validate, max_new_links, max_chars, compose=False)` **replaces the manual orchestration pattern** over `web_search_deep` + `web_expand_and_fetch` + `image_search` for routine deep research.
- Behavior:
  1. Builds query variants via `_query_variants_wrapper(query)`.
  2. Runs Level 1 multi-query deep search, dedupes, collects live pages.
  3. Auto-triggers Level 2 (`web_expand_and_fetch`) if `alive < 15` or coverage gate fails.
  4. Auto-triggers `image_search` for visual/people/art topics.
  5. When `compose=False`, returns unified evidence pack: `pages` + `images` + `panel`.
  6. When `compose=True`, returns a ready Markdown synthesis instead of raw JSON.
- Schema must be exposed at top level in the wrapper; keep older tools registered too for backward compatibility.
- **Pitfall:** if `compose=True` is set but the wrapper does not forward it to `search_deep(..., compose=True)` or apply `compose._build_markdown_answer(...)` to the evidence pack, the tool silently returns raw JSON. Always verify the flag is plumbed through.
- **Pitfall:** `web_deep_research` schema exposes `compose` so the agent can request a ready answer, while `web_search_deep` schema deliberately leaves `compose` out because that tool stays in raw JSON mode.

### Agent-side synthesis mandate (user requirement)
When the user explicitly requires the agent itself to synthesize the final narrative answer (as opposed to receiving a pre-composed Markdown evidence pack), the agent **must not** rely on `compose=True`.

**Default behavior:**\n- Call `web_search_deep(query, validate=True, max_validate=100)` or `web_deep_research(...)`.\n- Backend returns raw validated JSON; the LLM synthesizes the final answer locally.\n- This is the standard operational mode for deep research: collect evidence, then synthesize.\n- Determine `query_type` from the user's intent before calling the tool. The label is agent-selected, not inferred by keyword lists, not delegated to an external LLM.\n\nRequired behavior:\n- If the goal is pictures, portraits, gallery material, or visual output → use `query_type=\"visual\"`.\n- Technical docs, fixes, APIs, error codes → `query_type=\"technical\"`.\n- News, announcements, brand-new releases → `query_type=\"news\"`.\n- Historical overview, archive, legacy context → `query_type=\"historical\"`.\n- A/B, old vs new, X versus Y → `query_type=\"comparison\"`.\n- Undetermined or mixed intent → `query_type=\"general\"`.\n- Do not pass `classify=True` to backend; `web_search_deep`/`web_deep_research` return evidence only.\n- Do not add keyword lists to `ddg_search.py`, `ddg_search_tool.py`, `query_variants.py`, or `visit_website_enhanced.py`.\n
### Verified wrapper contract (post-2026-06-12, current implementation)
The active wrapper now accepts an optional `query_type` argument on `web_search_deep` and `web_deep_research`.
Allowed values: `visual`, `technical`, `news`, `historical`, `comparison`, `general`.
Default when omitted: `general`.

Normalization path:
- `web_search_deep` handler forwards `query_type` to `_safe_search_deep(query, ..., query_type=args.get("query_type"))`.
- `web_deep_research` handler forwards `query_type` to `_safe_deep_research(query, ..., query_type=args.get("query_type"))`.
- `_safe_deep_research` signature: `_safe_deep_research(query, max_validate=200, max_new_links=20, max_chars=5000, query_type=None)`.
- Backend `search_deep` remains monotone and never infers intent from keywords.

Image search routing is controlled by `query_type` only:
- `query_type == "visual"` enables `image_search`.
- All other values leave `image_search` disabled.
- The previous `_is_visual_topic(query)` keyword gate in `_safe_deep_research` has been removed from the wrapper.

Do not reintroduce code-side keyword detection in `ddg_search.py`, `ddg_search_tool.py`, `query_variants.py`, or `visit_website_enhanced.py`. The backend must not branch on topic/coverage/image keywords; intent is set by the agent/caller before the tool runs.

Post-retrieval dedup configuration (current):
- `final_limit=80` (was 40) via parameterizable `_apply_post_retrieval_filter(evidence, query, final_limit=80, ...)`
- `max_per_source_url=4`
- `jaccard_threshold=0.85`

Runtime tool-result normalization (durable)
Provider-side 400s with `messages.N.content: Invalid input` can occur when tool results reach the wire with non-string/non-list content. Do not fix this in `tool_executor.py`: it is a churn surface and can be reverted by updates.

Durable fix is in `agent/tool_dispatch_helpers.py::make_tool_result_message`:
- `str` and `list` pass through unchanged.
- `None` becomes `""`.
- Other objects are normalized via `json.dumps(..., default=str)`.

Post-update regression check:
```python
from agent.tool_dispatch_helpers import make_tool_result_message
assert isinstance(make_tool_result_message('x', {'a':1}, 'id')['content'], str)
assert make_tool_result_message('x', None, 'id')['content'] == ''
```
If either assertion fails, re-patch `make_tool_result_message`; do not relocate this guard back to `tool_executor.py`.

### Post-update tool-result normalization (durable)
Provider-side 400s with `messages.N.content: Invalid input` occur when tool results reach the wire with non-string/non-list content.
Do not fix this in `agent/tool_executor.py`: it is a churn surface and is overwritten by updates.

Durable fix lives in `agent/tool_dispatch_helpers.py::make_tool_result_message`:
- `str` and `list` pass through unchanged.
- `None` becomes `""`.
- Other objects are normalized via `json.dumps(..., default=str)`.

Regression check after any Hermes update:
```python
from agent.tool_dispatch_helpers import make_tool_result_message
make_tool_result_message('x', {'a':1}, 'id')['content']
# Expected: '{"a": 1}' (a JSON string, not a dict)
make_tool_result_message('x', None, 'id')['content']
# Expected: ''
```

If either assertion fails, re-patch `make_tool_result_message`; do not relocate this guard back to `tool_executor.py`.

## Final report synthesis contract (agent-side, no extra searches)

When the user wants output improved from an existing evidence pack only:
- Reuse evidence pack or equivalent; **do not trigger additional `web_search` / `web_search_deep` calls** unless explicitly requested.
- Embed `image_url` entries directly into the report instead of footnote-only references.
- For galleries/sources, include **site descriptions** that distinguish authoritative/curated sources from generic social platforms or commercial links.
- The final markdown must be **self-contained**: images, links, and synthesis embedded directly, with no references requiring the user to parse external JSON files.
- Use narrative structure (intro + thematic sections + conclusion), not bullet dumps of links.
- **Citation format**: `[N]` inline pointing to a real source URL. Page-level citations are recommended when relevant.
- **Coverage gaps** must be stated explicitly if key facets are missing from the evidence pack.
- **Media block**: if `images` is non-empty in the evidence pack and the query is visual/people-related, embed a Media block with images in the final answer. If no images are available, continue without images instead of failing.
- **Final output must be a ready answer, not a link list.** Links serve as proof of existence for sources, not as the deliverable.

**CLI:** `python ~/.hermes/plugins/web-tools/ddg/ddg_search.py search-deep "<query>" --validate --max-validate 100 --timeout 3`

**Hermes tool (standard mode):**
```python
web_search_deep(query="<query>", validate=True, max_validate=100)
```
When a Markdown synthesis is required, build it locally from the raw JSON evidence pack using this skill's **Minimal Output Skeleton**. `web_search_deep` and `web_deep_research` are evidence collectors only; local synthesis is expected. Use `compose=True` only for local markdown formatting.

## Pipeline Architecture

### Автоматизированный (web_search_deep)
```
Stage 1: Collection    → web_search / search_deep          → 30-150 URLs
Stage 1a: Dynamic variants → auto-generated reformulations when pool is thin  → coverage extension
Stage 2: Validation    → HEAD-first (dead blocked early)   → alive/dead/blocked
Stage 3: Content       → body text analysis                → relevance score
Stage 4: Classification → categorize                      → grouped by category
Stage 5: Media         → extract <img>, YouTube links      → src + alt
Stage 6: Output        → Markdown (compose=True) или JSON  → ready to present
```

### Hard HTTP early return
403/404/405/410/429/451/500/502/503/504 — skip follow-up GET для этих статусов.

## Anti-bot and bot-challenge detection
### Bot challenge pages (HTTP 200 but challenge HTML)
Страницы sometimes возвращают 200, но содержат HTML-челленджи: CAPTCHA, Cloudflare challenge, "Are you not a robot?", "Verify you are human", "Checking your browser". Валидатор только по статус-коду их не отловит.
- После GET validator обязан сканировать текст на challenge-маркеры: `captcha`, `cloudflare`, `are you a robot`, `verify you are human`, `checking your browser`, `enable javascript`, `ddg verification`, `attention required`.
- При совпадении: ставить `status='bot_challenge'`, `alive=False`, не добавлять в pages.
- В panel вести счётчик `bot_challenge` для отладки.
- **Synthesis guard**: если страница содержит challenge-маркеры, она исключается из финального ответа и не цитируется, даже если прошла валидацию.

## Verified compose-forwarding pattern (current wrapper)

### Image integration note
`image_search` can complete successfully, but fetched page bodies may not contain direct `.jpg/.png` URLs. Toolkit must tolerate text-only evidence and not block on images.

The current `ddg_search_tool.py` wrapper builds the local compose payload from the evidence pack. The minimal working pattern:
```python
# evidence = list of dicts with url/title/text/snippet/alive
# images = list of dicts with url/title
compose_results = []
for item in evidence:
    compose_results.append({
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "text": item.get("text", ""),
        "snippet": item.get("snippet", ""),
        "alive": item.get("alive"),
        "relevance": 1.0,
        "category": "other",
        "image_urls": [],
    })
for idx, item in enumerate(compose_results[:5]):
    if idx < len(images):
        item["image_urls"] = [{"url": images[idx].get("url"), "src": images[idx].get("url")}]
compose_payload = {
    "summary": {
        "total_raw": raw_count,
        "validated": len(evidence),
        "alive": alive_count,
        "dead": 0,
        "blocked": 0,
        "classified_categories": 0,
        "max_validate": max_validate,
        "elapsed_seconds": round(time.time() - start, 2),
    },
    "results": compose_results,
    "categories": {},
}
return _build_markdown_answer(query, compose_payload, include_images=bool(images))
```

When `web_search_deep(..., compose=True)` is unavailable or insufficient, use `compose._extract_facts` directly to build a fact-dense synthesis input. This pattern converts raw HTML pages into minimal evidence packets for the LLM.
Preferred reusable helper: `~/.hermes/pilot-synthesis/pipeline.py` (`stage_extract_facts`, `stage_build_synthesis`, `render_synthesis_prompt`). Reuse it instead of hand-assembling facts.

### Verified input contract
- `compose._extract_facts(text, query, max_facts=5)` accepts **raw text**, not URL.
- Passing a URL returns metadata-only facts like the URL string itself.
- Always strip HTML first: `text = re.sub(r'<[^>]+>', ' ', html)` then normalize whitespace.

### Validated context window
After extracting facts, attach ±2 sentences around each fact from the original text. Empirical measurements on real pages:
- plumbingimmediately.co.uk: 132807 → 13288 chars with ±2s (~10%)
- iheat.co.uk: 15818 → 2434 chars with ±2s (~15%)

Recommendation: **±2 sentences** is the optimal default for most topics. It restores enough narrative to avoid "bullet-list-only" answers while keeping token cost ~10x lower than raw HTML.

### When to use this pattern
- The user's goal is **ready answer, not links**.
- Topic benefits from specific evidence (error codes, dates, names, numeric claims).
- You need citations with minimal context overhead.

### When NOT to use
- Topics requiring deep narrative/argument chains (history, analysis) without additional source diversity.
- Pages where blocked/soft-overlay status means `text` is empty — skip silently.
- When `compose=True` already returns the needed Markdown answer.

### Minimal flow
1. `ddg_search.py search-deep ... --max-validate N`
2. For each alive URL: `html = _fetch(url); text = strip_tags(html)`
3. `facts = _extract_facts(text, query, max_facts=4)`
4. `context = context_window(text, fact, before=2, after=2)`
5. Build prompt: `claim + context + source_url + [citation_id]`
6. LLM synthesis → Markdown answer with inline `[N]` citations
7. If no OpenAI-compatible endpoint is available, stop at step 5 and deliver the prompt/manifest for manual/host-side synthesis. Do not retry endlessly.

### Integration with visit_website_enhanced
`visit_website_enhanced.py visit` returns structured content. You can either:
- Pass its `content` directly into `_extract_facts`
- Or fetch HTML via `ddg_search._fetch` and extract facts, then use `visit_website_enhanced` for media/heading metadata when needed.

## Deep Research Workflow Pattern (class-level)

Use this pattern whenever the user asks for thorough research, report, or multi-source synthesis; not for simple fact lookups.
Use this skill only after explicitly loading it with `skill_view(name='web-deep-search')`; when doing deep research, your first move is to fully load this skill.

### Two-level coverage rule (must follow automatically)
- **Level 1** (`web_search_deep`): collect raw URLs, validate liveness, score relevance, and return the raw JSON pool.
- **Level 2** (`web_expand_and_fetch`): when the Level 1 pool lacks facts, missing details, or secondary sources, run a second-level expansion by visiting the live pages from Level 1 and extracting links from them, then fetch the top candidates. This resumes the previously lost second-level expansion capability after restore/update.
- **Auto-trigger conditions (no manual gate needed):**
  - `alive < 15`
  - key query facets are absent from the alive pool
  - topic is about people/art/visual culture — always follow up with expansion + images
- Registry invariant: `web_expand_and_fetch` must remain registered through the wrapper as a top-level `registry.register(...)` call. If Hermes update wipes the wrapper or removes it from `registry.get_tool_names_for_toolset('web')`, restore it from the dev repo before chaining Level 2.
- **Mandatory image search for visual topics:** If the query is about artists, visual culture, artworks, or people — call `image_search(query)` after Level 1 and include image results in the final answer. Do not leave a visual/people topic without at least one image reference.

### Coverage gates (empirical, 2026-06-06)
- Artists/visual topics: require ≥8 distinct authoritative/alive sources + ≥1 image_search result set.
- Gallery/tool topics: require ≥5 gallery-like sources from distinct hosts.
- Modern vs classic comparisons: require ≥3 sources each for modern and classic.
- If any gate fails after Level 1, trigger Level 2 and re-check.

### When to use
- Deep research on a person, company, topic, event, or product
- Multi-angle coverage required (history, current state, sources, opinions)
- Output should be a cited report, not just a URL list
- Disambiguation needed (pseudonyms, namesakes, similar entities)

### Phases
1. **Understand (30s)** — capture goal and constraints; if user says "just research it", use reasonable defaults and skip questions.
2. **Plan** — decompose into 3-5 sub-questions. Keep a stable **Core** + per-angle **Constraints**.
3. **Search** — run 2-3 keyword variations per sub-question via `web_search` or `web_search_deep(..., query_variants=[...])`. Mix web + news intent where relevant. Target 15-30 unique sources live. Use Deep tier when coverage matters.
4. **Deep-read** — fetch full content for 3-5 most promising URLs via `visit_website_enhanced` or browser tools. Do not rely on snippets alone.
5. **Synthesize** — produce a structured Markdown report with inline citations, per-theme sections, key takeaways, sources footer, and a one-line methodology note.
6. **Save & deliver** — save report to a known path when asked; in chat, deliver executive summary + key takeaways, full report as file only when long.

### Validated test case: Sara St James (2026-06-03)
This query is intentionally hard: pseudonym, namesake professor, paywalled/legacy sources, forum-only discussions.
- **Expected correct answer:** Sara St James = pseudonym of Jacqueline Lovell (b. 1974), 1990s Playboy/Penthouse model, softcore actress, retired ~2002.
- **Key signal sources:** Wikipedia (Jacqueline Lovell page), LPW Wiki, IAFD database, IMDb title page confirming "as Sara St. James", FreeOnes/Vintage Erotica/Reddit forums.
- **What must appear in output:** alias table, career timeline, forum evidence, disambiguation of 3 namesakes, note about paywalled sources.
- **What must NOT appear:** confusion with professor Sara St. James (Utah) or Jessie St. James (Golden Age) or Sarah Saint James (singer).
- **Coverage lesson:** For legacy/adult topics, expect many domains to be paywalled or bot-blocked. Multi-query collection is more important than aggressive validation. Accept lower alive counts; compensate with more query variants.

### Quality rules (must be explicit in output)
1. **Every claim needs a source.** No unsourced assertions.
2. **Cross-reference.** Single-source claims must be flagged unverified.
3. **Recency matters.** Prefer sources from the last 12 months unless historical context is requested.
4. **Acknowledge gaps.** If a sub-question has weak coverage, say so explicitly.
5. **No hallucination.** If data is insufficient, say "insufficient data found."
6. **Disambiguate proactively.** When a query involves names with known aliases or namesakes, include a disambiguation section even if the user didn't ask for it.

### Answer skeleton
```text
# <Topic>: Deep Research Report
Generated: <date> | Sources: <N> | Confidence: High/Medium/Low

## Executive Summary
<3-5 sentence overview>

## 1. <Major Theme>
<Findings with inline citations>

## 2. <Major Theme>
<Findings with inline citations>

## Key Takeaways
- ...

## Sources
1. [Title](url) — one-line summary

## Methodology
Searched <N> queries across web/news. Analyzed <M> sources.
Sub-questions: <list>
```

### Mapping to Hermes tools
- Collection: `web_search` / `web_search_deep` with `query_variants`
- Validation: `web_search_deep(..., validate=True)` — use `max_validate=20-30` for hard topics, not 50+
- Fetching: `web_extract`, `visit_website_enhanced`, `browser_navigate`
- Composition: `compose=True` or manual Markdown synthesis using the skeleton above
- Persistence: `write_file` under `~/.hermes/research/<slug>/report.md` if saving

### Pitfalls
- Single-query deep search often yields fewer than 10-20 raw URLs. Always prefer multi-query when depth matters.
- Validation drops dead/blocked results; expect live count to be much lower than raw. This is correct behavior, not failure. For paywalled/legacy topics, compensate with more query variants rather than raising `max_validate`.
- Do not inject facts from earlier research runs into a fresh query; each run must answer the current ask only.
- When only authoritative sources are needed, bias toward official docs, academic, and reputable publishers; keep blogs/forums secondary.
- IMDb and similar bot-protected sites may require `browser_navigate` instead of `web_extract`.
- Wikipedia pages are high-yield for alias/identity confirmation; prioritize them early in deep-read phase.
- Namesake disambiguation is not optional when the query contains a name with known homonyms. Include a "Namesakes" or "Disambiguation" block.

See `references/deep-research-workflow.md` for the condensed upstream summary and adaptation notes.

## Hard Constraints & User Rules

- **Do NOT expand `_classify_by_content` with arbitrary domain lists.** The classifier is intentionally generic. Adding hardcoded hosts (sports sites, wikis, news, etc.) creates arbitrary bias, degrades niche/rare/non-English queries, and contradicts the skill's universal scope. If classification coverage is weak, prefer query reformulation over classifier surgery.
- Default `search_deep` output is JSON. The Markdown scaffold exists for convenience, but do not hand-edit or "improve" it beyond the verified `_build_markdown_answer` helper unless explicitly asked.
- Image results are **not guaranteed** to be artist works; `image_search` returns broad topical images. Don't promise gallery-grade artwork unless sourced from a verified portfolio URL.
- After any Hermes update, perform the **wrapper existence check** before deep research:

```python
import tools.registry as r
from tools.registry import discover_builtin_tools, registry
discover_builtin_tools()
print('web tools:', registry.get_tool_names_for_toolset('web'))
# Must include web_search_deep, visit_website_tool, image_search
```

- **Dependency check**: if `search_deep` raises `ModuleNotFoundError` for `bs4`/`lxml`/`curl_cffi`, install them into the active Hermes venv: `"<venv>/python.exe" -m pip install beautifulsoup4 lxml curl-cffi`. Do not add them to the system Python; the wrapper uses the venv interpreter only.
- **Preserve custom wrapper**: `tools/ddg_search_tool.py` must remain registered at module level. Do not replace it with inline imports or lazy-loading tricks that lose the top-level `registry.register()` call.

## Принципы
- **Никакой самодеятельности в отборе** — не удалять ссылки без причины.
- **Результат — структурированный, а не сырой** — используй compose mode для готовых ответов.
- **Предпочитать web_search_deep** для автоматизированного глубокого анализа.
- Каждый прогон `search_deep(...)` — изолированная единица. Не переноси факты/URL из предыдущего запроса.

## Runtime tool-result content normalization (durable)
Provider-side 400s with `messages.N.content: Invalid input` can occur when tool results reach the wire with non-string/non-list content. Do not fix this in `tool_executor.py`: it is a churn surface and can be reverted by updates.

Durable fix is in `agent/tool_dispatch_helpers.py::make_tool_result_message`:
- `str` and `list` pass through unchanged.
- `None` becomes `""`.
- Other objects are normalized via `json.dumps(..., default=str)`.

Post-update regression check:
```python
from agent.tool_dispatch_helpers import make_tool_result_message
assert isinstance(make_tool_result_message('x', {'a':1}, 'id')['content'], str)
assert make_tool_result_message('x', None, 'id')['content'] == ''
```
If either assertion fails, re-patch `make_tool_result_message`; do not relocate this guard back to `tool_executor.py`.

## Invariant: keep compose.py edits minimal and verifiable
`compose.py` is small and regex-heavy. Each edit must:
1. Touch only one behavior at a time (`_clean_fact`, `_format_fact`, `_extract_facts`, regex constants).
2. Be syntactically valid immediately (`python -m py_compile compose.py`).
3. Be followed by a live `search-deep ... --compose` probe before changing anything else.

If a regex edit breaks the file, revert to the last known-good state and stop. Do not layer new edits on top of a broken `compose.py` — the noise will become unparseable and every subsequent run will be misleading.

### Verified wrapper contract (agent-side synthesis, post-2026-06-06)

When the user explicitly rejects automatic categorization/compose output for general/historical/narrative topics, activate **agent-side synthesis mode**:
- `web_search_deep` **must call backend with `classify=False` and `compose=False`**.
- Wrapper **must not** expose `classify` or `compose` in the tool schema.
- Default `max_validate` is 100 in the wrapper (was 200), keeping the pool wide while relying on the 3s per-URL timeout to avoid hangs.
- Backend returns **raw validated JSON only**; the LLM synthesizes the final answer.
- Native `web_search` remains available as a fallback when DDG raw pool is empty/degraded.
- Do **not** reinstate algorithmic categorization or compose behavior in `web_search_deep`.

### No external LLM fallback rule (agent-side synthesis mandatory)

If no OpenAI-compatible / external LLM endpoint is available for the compose step, **the agent must always synthesize the final answer from the raw evidence pack**:
- Do **not** rely on `compose=True` expecting external LLM post-processing.
- Call `web_search_deep(..., compose=False)` or `web_deep_research(..., compose=False)`.
- After receiving raw JSON, build a narrative Markdown answer using the evidence pack, inline citations, and the **Minimal Output Skeleton** from this skill.
- If `web_deep_research(..., compose=True)` is the only available entry but external LLM is unavailable, treat the returned structured pack as raw input and synthesize manually — do not wait for an LLM call that cannot happen.
- In this mode, only `web_search_deep` is used for collection/validation; synthesis is done entirely by the LLM using the returned JSON evidence.
- `visit_website_tool` is the primary page-reading channel and the source of links for Level 2 expansion.
- `image_search` is available as a separate tool; call it for visual/people queries.
- `web_deep_research` → composite tool: one-call orchestration over Level 1 → coverage gate → Level 2 → images → unified evidence pack or composed Markdown. **Use this for routine deep research.**
- Proxy in `visit_website_enhanced` is **opt-in only**; do not enable it automatically.

### Verified Level-2 tool
1. `web_expand_and_fetch`
   - Input: `query` + `source_urls` from Level 1.
   - Behavior: visits each source, extracts links, normalizes URLs, ranks candidates by token overlap with the query, then fetches the top candidates.
   - Output: `query`, `candidates_count`, `fetched_count`, `items` (`url`, `title`, `anchor`, `text`, `chars`).

### Level-2 auto-trigger rules
- Trigger Level 2 when **`alive < 15`**, or when key facets are absent, or for visual/people topics.
- Pass only high-quality alive URLs from Level 1 as `source_urls`.
- Preserve dedup across Level 1 + Level 2 before synthesis.

## User-directed agent-side synthesis mode (2026-06-06)
When the user explicitly rejects automatic categorization and wants the agent itself to judge relevance:
- Use the raw-json path: `web_search_deep(..., compose=False)` or `web_deep_research(..., compose=False)`.
- The raw-json tool schemas (`web_search_deep`) must not expose `classify` or `compose` to the LLM.
- Results come back as evidence JSON; synthesize them into narrative Markdown manually.
- `image_search` and `visit_website_tool` remain available for visual/person topics.
- Keep the compose-capable `web_deep_research` available separately; do not remove it or the registry entry for `web_search_deep`.
- Preferred defaults in this mode: `max_validate=100`, `timeout_per_url=3s`, `max_workers=10`.

## Fallback chain order
1. backend deep `search_deep(...)` 
2. wrapper composition via `compose._build_markdown_answer` if available
3. wrapper fallback `_build_fallback_markdown(...)`
4. native `web_search(...)` fallback via `_safe_native_fallback`
5. JSON error payload `{"error": "search backend unavailable"}`

When modifying output behaviour, edit the wrapper first. Keep backend `ddg_search.search_deep` in JSON mode.

## Добавлено 2026-06-04
Падение `search-deep` иногда происходит из-за отсутствия импорта `from bs4 import BeautifulSoup` в `plugins/web-tools/ddg/ddg_search.py`; тогда прямые CLI-вызовы дают `NameError` внутри функции `_extract_image_urls`. Исправление: добавить этот импорт и заново прогонить CLI.

## Fact extraction from live page bodies (compose mode, 2026-06-04)
Текущая реализация `compose=True` извлекает конкретные проверенные факты из текста живых страниц, а не общий пересказ.
- HTML → readable text через `BeautifulSoup.get_text(separator=' ', strip=True)` — скрипты/стили/шаблоны не попадают в текст.
- `_extract_facts()` в `compose.py`: режет на предложения/bullets, скорит по маркерам (error/cause/fix/definition/date/price) и query-совпадениям, чистит UI-шум (`_UI_NOISE_RE`, `_NAV_ONLY_RE`, алфавитный порог).
- Пороги длины: факты <30 символов отбрасываются; короткие технические bullet-строки проходят, если проходят фильтр.
- Формат вывода:
  - глобально: блок `## Проверенные факты` (до 4 фактов из топ-3 живых источников);
  - на уровне источника: блок `**Проверенные факты:**` с 1-2 фактами из текста страницы.
- Каждый факт должен быть привязан к источнику через ссылку. Не найденные аспекты — явно.
- Если `text`/`body` у живого результата пустой (например, `m.imdb.com/name/.../bio/` без readable body), факт-блок для этого источника не формируется — это не баг, а отсутствие извлекаемого текста.
- `_get_item_text()` гарантирует безопасное чтение полей `text/body/content/snippet/description/title` и не падает, если элемент не dict. Это важно после валидации, где встречаются слабые/странные записи.

## Динамические query_variants (auto-mode, 2026-06-04)
Если `query_variants` не переданы и сырой пул `all_raw` меньше `max_validate`, `search_deep` автоматически генерирует до 3 уточняющих вариантов на основе первого прохода через `query_variants._suggest_query_variants`. Это расширяет coverage без ручной доработки под конкретную тему.
- Режим работает только когда caller явно не задал variants, чтобы не дублировать пользовательские формулировки.
- Генерация не вызывает внешние LLM/API, опирается только на уже собранные сырые результаты.

## Dependencies & compatibility

- `ddgs` is an optional coverage-backend. Recent `httpx` builds did not accept `proxies=...` in `httpx.Client(...)` from `ddg_search.py`. Verify with `python -c "import httpx, inspect; print(inspect.signature(httpx.Client.__init__))"`. If `proxy` is present but `proxies` is absent, patch `plugins/web-tools/ddg/ddg_search.py` to use `httpx.Proxy(url)` and pass it via the `proxy=` keyword.
- `lxml.etree` must be importable from the same venv that imports `bs4`. Missing or corrupted `lxml` wheels show up as `cannot import name 'etree' from 'lxml'` inside `ddgs`/`bs4`. Reinstall either globally or via `--target=<venv>/Lib/site-packages lxml` into the Hermes venv.
- The `curl_cffi` session path caches one session per proxy setting. If proxy env changes at runtime, the cached session is stale and returns empty bodies. Best practice: restart Hermes after changing proxy env vars.
- `ddgs.text()` caps at ~10 hits per query. Use 3-6 `query_variants` for wide coverage; do not expect single-query `count=100` to return 100 results.

## Environment fix: missing `curl_cffi` / `ddgs` / `bs4`
Если `search-deep` падает с `ModuleNotFoundError` для `curl_cffi`, `ddgs` или `beautifulsoup4`, установи их в тот же venv, из которого запускается `python`:
```bash
"C:\Users\<user>\.hermes\hermes-agent\venv\Scripts\python.exe" -m pip install curl_cffi ddgs beautifulsoup4
```
`beautifulsoup4` подтягивает парсер `lxml` через зависимости окружения.
После установки перезапусти CLI/инструмент.

## Repo hygiene
Плагин в `~/.hermes/plugins/web-tools/ddg/` имеет отдельный git-репозиторий. Родительская папка `~/.hermes/` — не в git. Это значит:
- фиксировать изменения в плагине своей командой `cd .../plugins/web-tools/ddg && git add/commit`;
- фиксировать изменения в hermes-agent из `~/.hermes/hermes-agent/` отдельно.
Храните изменения обеих частей recovery-дружественно: plugin — одна история, core — другая.

## Прочее
- `web_search_deep` в Hermes инструментах зарегистрирован только если wrapper (`tools/ddg_search_tool.py`) вызвал `register()` и `registry` стартовал. Проверка: `registry.get_tool_names_for_toolset('web')` должен содержать `web_search_deep`.

## Пример
Пользователь: "Vaillant boiler F28 error, causes, how to reset, self-clean procedure"
Запрос в tool:
```json
web_search_deep(
    query="Vaillant boiler F28 error",
    validate=True, classify=False, max_validate=50,
    compose=False
)
```
Результат: сырой JSON-пакет; агент собирает narrative Markdown с инлайн-цитатами из полученных страниц.
