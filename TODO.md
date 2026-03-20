# TODO — Besluitendatabank Scraper

## Huidige status (19 maart 2026)

**Dekking:** 446/565 gemeenten hebben een werkende scraper (79%)
**CSV:** 575 rijen = 10 provincies + 565 gemeenten (compleet, matcht Wikipedia)

| Type | Aantal | Scraper | Status |
|------|--------|---------|--------|
| Deliberations.be | 199 | scraper_deliberations.py | ✅ Klaar |
| CipalSchaubroeck / CSEcho | 79 | scraper_menen.py | ✅ Klaar |
| SmartCities / Besluitvorming | 67 | scraper_halle.py | ✅ Klaar |
| LBLOD | 47 | scraper_lblod.py | ✅ Klaar |
| MeetingBurger | 43 | scraper_ranst.py | ✅ Klaar |
| iBabs | 2 | scraper_ibabs.py | ✅ Klaar |
| Prov. Antwerpen | 1 | scraper_provantwerpen.py | ✅ Klaar |
| Vlaams-Brabant | 1 | scraper_vlaamsbrabant.py | ✅ Klaar |
| Ingelmunster | 1 | scraper_ingelmunster.py | ✅ Klaar |
| **Irisnet (Brussel)** | **8** | — | ❌ Geen scraper |
| **Overig (individueel)** | **119** | — | ❌ Geen scraper |

---

## Openstaande taken

### 1. Irisnet-scraper (8 Brusselse gemeenten) — **8 SP**
- Platform: `publi.irisnet.be`
- Gemeenten: Anderlecht, Berchem-Sainte-Agathe, Brussel-Bruxelles, Forest, Molenbeek-Saint-Jean, Saint-Josse-ten-Noode, Schaerbeek, Woluwe-Saint-Lambert
- **Actie:** Onderzoek de API/HTML-structuur van publi.irisnet.be en bouw scraper

### 2. Overige 119 gemeenten onderzoeken — **13 SP**
- Dit zijn allemaal individuele websites (geen gedeeld platform)
- Veel Waalse gemeenten met eigen website (www.amay.be, www.anhee.be, etc.)
- Enkele Vlaamse: Blankenberge, Boechout, Bilzen-Hoeselt, Baarle-Hertog
- Duitstalige gemeenten: Amel, Burg-Reuland, Büllingen, Eupen, Kelmis, etc.
- **Actie:** Onderzoek per gemeente of er een publicatieplatform is; groepeer waar mogelijk

### 3. Code-kwaliteit (🟡 nice-to-have) — **8 SP**
- [ ] Refactor naar class-based scrapers i.p.v. module-level globals (SESSION, _config) — **3 SP**
- [ ] HTML-generatie centraliseren in een gedeelde output-module — **2 SP**
- [ ] Playwright timeout-handling verbeteren in scraper.py en scraper_halle.py — **1 SP**
- [ ] Download-resumability toevoegen (file-size check vóór skip) — **2 SP**

### 4. Testing — **8 SP**
- [ ] Unit tests schrijven voor base_scraper.py (sanitize_filename, robust_get, download_document) — **3 SP**
- [ ] Integratietests per scraper-type (mock HTTP responses) — **5 SP**

### 5. ~~Provincie Antwerpen~~ ✅ Opgelost
- Oude URL (echo.provincieantwerpen.be) was dood → nieuwe bron gevonden
- Scraper: `scraper_provantwerpen.py` (HTML-verslagen + PDF-notulen via provincieantwerpen.be)

### 6. ~~CSV completeren (565 gemeenten)~~ ✅ Opgelost
- CSV bijgewerkt naar 575 rijen (10 provincies + 565 gemeenten)
- 34 ontbrekende gemeenten toegevoegd, 13 fusie-namen bijgewerkt, 15 namen genormaliseerd
- Vergelijkingsscript `compare_wiki.py` toont 0 ontbrekend / 0 teveel

### 7. Pubcon-gemeenten (Laakdal, Oudsbergen)
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
├── scraper_provantwerpen.py  # Prov. Antwerpen (1, HTML/PDF)
├── scraper_vlaamsbrabant.py # Prov. Vlaams-Brabant (1, HTML)
├── scraper_ingelmunster.py  # Ingelmunster (1, HTML)
├── compare_wiki.py          # Vergelijking CSV vs Wikipedia (565 gem.)
├── simba-source.csv         # Bronlijst 575 rijen (10 provincies + 565 gemeenten)
├── pyproject.toml           # Dependencies
└── README.md                # Documentatie
```

## Technische notities

- Python 3.14, uv als package manager
- `$env:PYTHONIOENCODING = "utf-8"` nodig op sommige Windows-systemen
- Playwright alleen nodig voor SmartCities-type (71 gemeenten + Brugge + Leuven)
- `base_scraper.robust_get()` is de gedeelde retry-helper (3 pogingen, exponentiële backoff)
- `scraper_groep.py` detecteert automatisch het type op basis van de URL in de CSV
