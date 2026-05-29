# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Install Playwright browser (only needed for SmartCities scrapers)
uv run python -m playwright install chromium

# Run interactive wizard
uv run python start.py

# Run tests
uv run python -m pytest tests/
# or single test
uv run python -m unittest tests.test_base_scraper.TestBaseScraperHelpers.test_sanitize_filename_blocks_traversal_patterns

# Health check (CSV integrity + type detection)
uv run python health_check.py
uv run python health_check.py --url-check  # also checks HTTP reachability

# Batch scraper — see all groups
uv run python scraper_groep.py --toon-groepen
```

## Architecture

**Central data source:** `simba-source.csv` — 575 rows (10 provinces + 565 municipalities) with URLs. `scraper_groep.py` reads this CSV and dispatches to individual scrapers via `detecteer_type()` (URL → scraper type mapping).

**Scraper hierarchy:**
- `start.py` — interactive TUI entry point, wraps `scraper_groep.py`
- `scraper_groep.py` — batch orchestrator; calls individual scrapers as subprocesses; also handles type detection from URL patterns
- `scraper.py` — dedicated Brugge scraper (Playwright, SmartCities)
- `scraper_*.py` — 22 individual scrapers, one per website platform

**Base layer** (`base_scraper.py`):
- `ScraperConfig` dataclass — all tunable params (timeouts, parallelism, rate limiting)
- `create_session()` + `rate_limited_get()` — HTTP with retry (3×, exponential backoff) and rate limiting (200ms min between requests)
- `robust_get()` — simpler retry wrapper returning `None` on permanent failure
- `download_document()` — atomic write (tmp file → rename), PDF magic-byte validation, path traversal protection
- `download_documents_parallel()` — `ThreadPoolExecutor` with `max_parallel_downloads=5`
- `sanitize_filename()` / `safe_output_path()` — security: blocks path traversal, Windows reserved names, invalid chars

**Output structure:** `pdfs/<gemeente>/<orgaan>/<zittingsdatum>/filename.pdf`

**Platform types and scrapers:**

| Scraper | Platform | Method |
|---------|----------|--------|
| `scraper_onlinesmartcities.py` | SmartCities / Besluitvorming (70 entities) | Playwright |
| `scraper_menen.py` | CipalSchaubroeck / CSEcho (79 entities) | REST API |
| `scraper_ranst.py` | MeetingBurger (46 entities) | REST API |
| `scraper_deliberations.py` | Deliberations.be (167 entities) | HTML |
| `scraper_lblod.py` | LBLOD (62 entities) | HTML |
| `scraper_idelibe.py` | iDélibé / conseilcommunal.be (31 entities) | REST API |
| `scraper_imio.py` | iMio / Plone (31 entities, 3 structs) | HTML |
| `scraper_wordpress.py` | WordPress / TYPO3 / Plone (36 entities) | HTML |
| `scraper_drupal.py` | Drupal / TYPO3 (15 entities) | HTML |

## Key technical notes

- **Package manager:** `uv` — always prefix with `uv run python`
- **Playwright:** only needed for SmartCities type (70 entities + Brugge + Büllingen + Orp-Jauche); after `uv` update run `uv run playwright install` if `chrome-headless-shell` is missing
- **Type detection:** `scraper_groep.py::detecteer_type()` maps URL patterns to scraper types; all Waalse iMio hosts listed in `_IMIO_HOSTS`, LetsGoCity/WordPress in `_WAALSE_WP_HOSTS`
- **iMio ajax_load:** Plone SPA sites require `?ajax_load=1`; configured per-gemeente with `ajax_load: True`
- **Plone `/view` suffix:** links ending in `.pdf/view` → `plone_folder_listing: True` strips the suffix
- **Date extraction:** `datum_uit_pad()` in `scraper_wordpress.py` handles 8 date patterns (YYYYMMDD, YYYY-MM-DD, DD.MM.YYYY, French month names, etc.)
- **WordPress 403:** many sites block HEAD/GET from health_check but scrapers work with proper User-Agent + session
- **LBLOD 403:** scrapers work; `health_check.py` treats 403 as acceptable for LBLOD (`_TYPE_VERWACHT_403`)
- **Deliberations.be:** HEAD returns 404 but GET returns 200 (Plone/Zope behaviour); health_check falls back to GET
- **Herstappe:** only blocked municipality (DNS failure, ~85 residents, no working website)
- **Windows:** set `$env:PYTHONIOENCODING = "utf-8"` if terminal shows encoding errors

## Adding a new scraper

1. Add gemeente entry to `simba-source.csv` with URL
2. Create `scraper_<type>.py` importing from `base_scraper`
3. Add type entry to `TYPES` dict in `scraper_groep.py`
4. Add URL pattern recognition to `detecteer_type()` in `scraper_groep.py`
5. Run `uv run python health_check.py` to verify coverage
