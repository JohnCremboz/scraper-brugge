# TODO — Besluitendatabank Scraper

## Huidige status (19 maart 2026)

**Dekking:** 429/555 gemeenten hebben een werkende scraper (77%)

| Type | Aantal | Scraper | Status |
|------|--------|---------|--------|
| Deliberations.be | 180 | scraper_deliberations.py | ✅ Klaar |
| CipalSchaubroeck / CSEcho | 85 | scraper_menen.py | ✅ Klaar |
| SmartCities / Besluitvorming | 71 | scraper_halle.py | ✅ Klaar |
| LBLOD | 48 | scraper_lblod.py | ✅ Klaar |
| MeetingBurger | 41 | scraper_ranst.py | ✅ Klaar |
| iBabs | 2 | scraper_ibabs.py | ✅ Klaar |
| Vlaams-Brabant | 1 | scraper_vlaamsbrabant.py | ✅ Klaar |
| Ingelmunster | 1 | scraper_ingelmunster.py | ✅ Klaar |
| **Irisnet (Brussel)** | **8** | — | ❌ Geen scraper |
| **Overig (individueel)** | **118** | — | ❌ Geen scraper |

---

## Openstaande taken

### 1. Irisnet-scraper (8 Brusselse gemeenten)
- Platform: `publi.irisnet.be`
- Gemeenten: Anderlecht, Berchem-Sainte-Agathe, Brussel-Bruxelles, Forest, Molenbeek-Saint-Jean, Saint-Josse-ten-Noode, Schaerbeek, Woluwe-Saint-Lambert
- **Actie:** Onderzoek de API/HTML-structuur van publi.irisnet.be en bouw scraper

### 2. Overige 118 gemeenten onderzoeken
- Dit zijn allemaal individuele websites (geen gedeeld platform)
- Veel Waalse gemeenten met eigen website (www.amay.be, www.anhee.be, etc.)
- Enkele Vlaamse: Blankenberge, Boechout, Bilzen-Hoeselt, Baarle-Hertog
- **Actie:** Onderzoek per gemeente of er een publicatieplatform is; groepeer waar mogelijk

### 3. Code-kwaliteit (🟡 nice-to-have)
- [ ] Refactor naar class-based scrapers i.p.v. module-level globals (SESSION, _config)
- [ ] HTML-generatie centraliseren in een gedeelde output-module
- [ ] Playwright timeout-handling verbeteren in scraper.py en scraper_halle.py
- [ ] Download-resumability toevoegen (file-size check vóór skip)

### 4. Testing
- [ ] Unit tests schrijven voor base_scraper.py (sanitize_filename, robust_get, download_document)
- [ ] Integratietests per scraper-type (mock HTTP responses)

### 5. Provincie Antwerpen
- URL: `echo.provincieantwerpen.be` → 421 Misdirected Request (SNI-probleem)
- Programmatisch niet bereikbaar; mogelijk handmatig of via alternatief pad

### 6. Pubcon-gemeenten (Laakdal, Oudsbergen)
- Platform: `*.azurewebsites.net/pubcon`
- Vereist authenticatie via Tobibus-portaal → niet scrapbaar zonder login

---

## Projectstructuur

```
scraper-brugge/
├── base_scraper.py          # Gedeelde logica: sessie, download, sanitize, robust_get
├── start.py                 # Interactieve TUI (wizard)
├── scraper_groep.py         # Batch-scraper per type + type-detectie
├── scraper.py               # Brugge (Playwright, dedicated)
├── scraper_leuven.py        # Leuven (Playwright, dedicated)
├── scraper_halle.py         # SmartCities/Besluitvorming (71 gem., Playwright)
├── scraper_menen.py         # CipalSchaubroeck/Echo/CSEcho (85 gem., REST)
├── scraper_ranst.py         # MeetingBurger (41 gem., REST)
├── scraper_lblod.py         # LBLOD (48 gem., HTML)
├── scraper_deliberations.py # Deliberations.be (180 gem., HTML)
├── scraper_ibabs.py         # iBabs (2 gem., HTML)
├── scraper_vlaamsbrabant.py # Prov. Vlaams-Brabant (1, HTML)
├── scraper_ingelmunster.py  # Ingelmunster (1, HTML)
├── simba-source.csv         # Bronlijst 555 gemeenten (gemeente;url)
├── pyproject.toml           # Dependencies
└── README.md                # Documentatie
```

## Technische notities

- Python 3.14, uv als package manager
- `$env:PYTHONIOENCODING = "utf-8"` nodig op sommige Windows-systemen
- Playwright alleen nodig voor SmartCities-type (71 gemeenten + Brugge + Leuven)
- `base_scraper.robust_get()` is de gedeelde retry-helper (3 pogingen, exponentiële backoff)
- `scraper_groep.py` detecteert automatisch het type op basis van de URL in de CSV
