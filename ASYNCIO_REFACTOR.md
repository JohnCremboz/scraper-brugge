# Asyncio Refactor Plan

## Doel
`requests` + `ThreadPoolExecutor` → `aiohttp` + `asyncio.gather`
Winst: concurrent HTTP requests binnen elk scraper-subprocess.

## Architectuur-context
Scrapers draaien als subprocessen via `scraper_groep.py`.
Asyncio-winst zit binnen elk subprocess, niet tussen gemeenten.
`scraper_groep.py` ThreadPoolExecutor voor subprocessen blijft — geen GIL-probleem.

## Status

### Stap 0 — Branch + dependencies [x]
- [x] `git checkout -b asyncio-refactor`
- [x] `aiohttp` toevoegen aan `pyproject.toml`
- [x] `uv sync`

### Stap 1 — `base_scraper.py` kern [x]
Async versies toegevoegd naast sync (sync blijft voor niet-gemigreerde scrapers):
- [x] `create_async_session()` → `aiohttp.ClientSession` met TCPConnector
- [x] `async_rate_limit()` → asyncio.Lock + asyncio.sleep
- [x] `async_download_document()` → aiohttp streaming, executor voor content filter
- [x] `async_download_documents_parallel()` → asyncio.gather + Semaphore
- [x] `_extract_filename()` refactored → accepteert `Mapping[str,str]` i.p.v. Response
- [x] `max_parallel_downloads` default verhoogd 5 → 8

### Stap 2 — Pure HTTP scrapers (geen Playwright)
Patroon per bestand: `session.get()` → `await session.get()`, `def main()` → `async def main()` + `asyncio.run(main())`

- [ ] 2a `scraper_drupal.py`
- [ ] 2b `scraper_ibabs.py`
- [ ] 2c `scraper_lblod.py`
- [ ] 2d `scraper_imio.py`
- [ ] 2e `scraper_brussel.py`
- [ ] 2f `scraper_docodis.py`
- [ ] 2g `scraper_gelinktnotuleren.py`
- [ ] 2h `scraper_icordis.py`
- [ ] 2i `scraper_idelibe.py`
- [ ] 2j `scraper_irisnet.py`
- [ ] 2k `scraper_ixelles.py`
- [ ] 2l `scraper_linkebeek.py`
- [ ] 2m `scraper_menen.py`
- [ ] 2n `scraper_molenbeek.py`
- [ ] 2o `scraper_provantwerpen.py`
- [ ] 2p `scraper_pubcon.py`
- [ ] 2q `scraper_ranst.py`
- [ ] 2r `scraper_schaerbeek.py`
- [ ] 2s `scraper_vlaamsbrabant.py`
- [ ] 2t `scraper_waalse_provincies.py`

### Stap 3 — Playwright scrapers (sync → async_playwright)
- [ ] 3a `scraper.py`
- [ ] 3b `scraper_deliberations.py` ← grootste impact (128 gemeenten)
- [ ] 3c `scraper_onlinesmartcities.py`
- [ ] 3d `scraper_wordpress.py`

### Stap 4 — `scraper_groep.py` (optioneel)
- [ ] Platform-bewuste batching (deliberations.be apart groeperen)

## Notities
- deliberations.be: 128 gemeenten op 1 server → rate limit risico bij parallel
- conseilcommunal.be: 31 gemeenten
- Elke stap = 1 bestand, 1 commit
- Test na elke stap met 1 gemeente voor acceptatie
