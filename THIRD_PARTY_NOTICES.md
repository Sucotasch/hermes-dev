# Third-party notices

## Licensing scope of this repository

| Scope | License | Files |
|---|---|---|
| Everything by default | **MIT** (`LICENSE`) | plugins, bridge, orchestrator, skills, docs |
| GUI binding | **GPL-3.0-or-later** (`LICENSE-GPL-3.0.txt`) | `standalone/gui.py`, `standalone/test_gui_smoke.py`, `gui_launcher.bat` |

The GUI is GPL because **PyQt5 is GPL v3** (or commercial). MIT code can be
combined into a GPL work, but not the other way round, so the exception is
scoped to those three files. The rest of the project stays permissive.

## Runtime dependencies (installed by the user — not redistributed here)

| Package | License |
|---|---|
| `curl_cffi` | MIT |
| `ddgs` | MIT |
| `httpx` | BSD-3-Clause |
| `beautifulsoup4` | MIT |
| `lxml` | BSD-3-Clause |
| `trafilatura` | Apache-2.0 |
| `htmldate` | Apache-2.0 |
| `pypdf` | BSD |
| `certifi` | MPL-2.0 |
| `websocket-client` | Apache-2.0 |
| `PyQt5` | GPL v3 (GUI only) |

Nothing here bundles them: they are ordinary dependencies a user installs.

## Optional external programs (deliberately NOT in this repository)

| Component | License | Note |
|---|---|---|
| [Moli](https://github.com/lexmount/moli) | Apache-2.0 | headless browser used by `mfetch`; binary lives in gitignored `moli/` |
| [Deno](https://github.com/denoland/deno) | MIT | JS runtime for `render`; binary + cache in gitignored `deno/` |
| [happy-dom](https://github.com/capricorn86/happy-dom) | MIT | DOM used by `js_engine/render_worker.js`; npm cache is gitignored |

## Data fetched at runtime (not redistributed)

| Data | Source | Note |
|---|---|---|
| Imagus sieve rules | `kuzn123/Imagus-Sieve-RuBoard` (`master/update.txt`) | upstream declares **no license**, so no snapshot is committed. `sieve_update.py` fetches it into the local cache on demand |

## Ideas and prior art (no third-party code copied)

| Project | License | What we took |
|---|---|---|
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Apache-2.0 | sitemap seeding, best-first expansion, aspect coverage, PDF/table extraction — reimplemented |
| [DonSeTch](https://github.com/dondai44423/donsetch) | AGPL-3.0 | wall memory/backoff constants, probe mode, stable error codes, reference handles — ideas and measurements only, **no code copied** |
| [tinyfish-cookbook](https://github.com/tinyfish-io/tinyfish-cookbook) | MIT | doctor-skill pattern and landscape-report recipe (prompt text written here) |
| [hermes-plugin-web-local](https://github.com/enzo-adami/hermes-plugin-web-local) | MIT | SSRF-check pattern, reimplemented std-only; it also pointed us at Hermes' own `tools/url_safety` |
| [hermes-jina](https://github.com/Adolanium/hermes-jina) | MIT | the anonymous Jina concurrency limit (2) — a documented fact, not code |
| `soxoj/maigret`, `thezveroboy/reddit-dl`, `TheGP/untidetect-tools` | MIT / none / none | evaluated, not used |

## Trademarks

Product and project names are the property of their owners. Mentioning them
here does not imply endorsement or affiliation.
