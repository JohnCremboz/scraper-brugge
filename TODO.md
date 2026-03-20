# TODO — Besluitendatabank Scraper

## Huidige status (20 maart 2026)

**Dekking:** ~490/565 gemeenten hebben een werkende scraper (~87%)
**CSV:** 575 rijen = 10 provincies + 565 gemeenten (compleet, matcht Wikipedia)

| Type | Aantal | Scraper | Status |
|------|--------|---------|--------|
| Deliberations.be | 201 | scraper_deliberations.py | ✅ Klaar |
| CipalSchaubroeck / CSEcho | 79 | scraper_menen.py | ✅ Klaar |
| SmartCities / Besluitvorming | 69 | scraper_halle.py | ✅ Klaar |
| LBLOD | 52 | scraper_lblod.py | ✅ Klaar |
| MeetingBurger | 45 | scraper_ranst.py | ✅ Klaar |
| Drupal directe PDFs | 12 | scraper_drupal.py | ✅ Klaar |
| Icordis CMS (LCP nv) | 11 | scraper_icordis.py | ✅ Klaar |
| Irisnet (Brussel — 10 gem.) | 10 | scraper_irisnet.py | ✅ Klaar |
| WordPress/Plone (Duitstalige gem.) | 7 | scraper_wordpress.py | ✅ Klaar |
| iBabs | 2 | scraper_ibabs.py | ✅ Klaar |
| **Pubcon (Tobibus LBLOD)** | **1** | scraper_pubcon.py | ✅ Klaar |
| Ixelles / Elsene | 1 | scraper_ixelles.py | ✅ Klaar |
| Prov. Antwerpen | 1 | scraper_provantwerpen.py | ✅ Klaar |
| Vlaams-Brabant | 1 | scraper_vlaamsbrabant.py | ✅ Klaar |
| Ingelmunster | 1 | scraper_ingelmunster.py | ✅ Klaar |
| Brussel | 1 | scraper_brussel.py | ✅ Klaar |
| Forest | 1 | scraper_forest.py | ✅ Klaar |
| Molenbeek-Saint-Jean | 1 | scraper_molenbeek.py | ✅ Klaar |
| Schaerbeek | 1 | scraper_schaerbeek.py | ✅ Klaar |
| **Overig (individueel)** | **~75** | — | ❌ Geen scraper |

**Brusselse status:**
| Gemeente | Platform | Status |
|----------|----------|--------|
| Anderlecht, Berchem-Sainte-Agathe, Etterbeek, Evere, Ganshoren, Jette, Saint-Gilles, Saint-Josse-ten-Noode, Watermael-Boitsfort, Woluwe-Saint-Lambert | publi.irisnet.be | ✅ irisnet |
| Brussel | bruxelles.be | ✅ scraper_brussel.py |
| Forest | forest.brussels | ✅ scraper_forest.py |
| Molenbeek | molenbeek.irisnet.be | ✅ scraper_molenbeek.py |
| Schaerbeek | 1030.be | ✅ scraper_schaerbeek.py |
| Ixelles / Elsene | ixelles.be | ✅ scraper_ixelles.py |
| Uccle | uccle.be (Drupal) | ✅ scraper_drupal.py |
| Auderghem | auderghem.be (Drupal) | ✅ scraper_drupal.py |
| Koekelberg | koekelberg.be (MediaWiki) | ❌ JS-rendered |
| Woluwe-Saint-Pierre | woluwe1150.be (WordPress) | ❌ Geen documenten |

**Duitstalige gemeenten (DG) — status:**
| Gemeente | Platform | Status |
|----------|----------|--------|
| Bütgenbach | WordPress | ✅ scraper_wordpress.py |
| Kelmis | WordPress | ✅ scraper_wordpress.py |
| Lontzen | WordPress | ✅ scraper_wordpress.py |
| Raeren | WordPress + static.raeren.be CDN | ✅ scraper_wordpress.py |
| Burg-Reuland | WordPress | ✅ scraper_wordpress.py |
| Eupen | WordPress | ✅ scraper_wordpress.py |
| Sankt Vith | Plone (www.st.vith.be) | ✅ scraper_wordpress.py |
| Büllingen | Sucuri WAF (JS-challenge) | ❌ Niet scrapbaar |
| Amel | TYPO3 (1 doc per type zichtbaar) | ❌ Overig |

---

## Openstaande taken

### 1. ~~Irisnet-scraper (8 Brusselse gemeenten)~~ ✅ Opgelost

### 2. ~~Brusselse gemeenten — alle gedaan~~ ✅ Opgelost

- **Irisnet** (10 gem.): Anderlecht, Berchem-Sainte-Agathe, Etterbeek, Evere, Ganshoren, Jette, Saint-Gilles, Saint-Josse-ten-Noode, Watermael-Boitsfort, Woluwe-Saint-Lambert → `scraper_irisnet.py`
- **Individuele scrapers**: Brussel, Forest, Molenbeek, Schaerbeek, Ixelles
- **Drupal**: Auderghem, Uccle → `scraper_drupal.py`
- **Niet scrapbaar**: Koekelberg (MediaWiki, JS), Woluwe-Saint-Pierre (WordPress, geen documenten)

### 3. Duitstalige gemeenten — **✅ Opgelost**

Scrapers gebouwd in `scraper_wordpress.py` (7/9 gemeenten):
- Bütgenbach, Kelmis, Lontzen, Raeren, Burg-Reuland, Eupen → WordPress
- Sankt Vith → Plone (www.st.vith.be), jaarpagina-navigatie
- Büllingen: Sucuri WAF, niet scrapbaar
- Amel: TYPO3, slechts 1 document per type zichtbaar, overig

### 4. Resterende gemeenten zonder scraper (75)

#### Vlaams (18 gemeenten)

**Cobra-platform** (2):
- Dendermonde — `dendermonde.be/cobra/gemeenteraad`
- Evergem — `evergem.be/cobra/Gemeenteraad`

**Drupal** (1):
- Liedekerke — `liedekerke.be/system/files`

**Nomatron** (1):
- Oosterzele — `oosterzele.cdn.nomatron.be`

**Mebosoft** (1):
- Zuienkerke — `mebosoft.be/zuienkerke_gemeente`

**Specifieke pagina gevonden** (4):
- Baarle-Hertog — `baarle-hertog.be/bekendmakingen`
- Vleteren — `vleteren.be/bestuur/informatie-en-inspraak/openbaarheid-van-bestuur`
- Voeren — `voeren.be/upload/pdf`
- Wielsbeke — `wielsbeke.be/agendas-en-verslagen`

**Nog te onderzoeken** (9):
- Boechout, Damme, Destelbergen, Essen, Stekene — alleen homepage-URL in CSV
- Heers — was OnlineSmartCities, platform lag eerder down
- Herstappe — kleinste gemeente van België (~85 inwoners)
- Linkebeek, Sint-Genesius-Rode — faciliteitengemeenten

#### Brussel (2 gemeenten)
- Koekelberg — MediaWiki, JS-rendered → niet scrapbaar
- Woluwe-Saint-Pierre — WordPress, geen documenten gevonden

#### Duitstalig (2 gemeenten)
- Amel — TYPO3, slechts 1 document per type zichtbaar
- Büllingen — Sucuri WAF (JS-challenge) → niet scrapbaar

#### Waals (53 gemeenten)

**Specifieke pagina gevonden** (3):
- Amay — `amay.be/ma-commune/vie-politique/conseil-communal/pv-et-resumes-du-conseil/proces-verbaux`
- Anhée — `anhee.be/ma-commune/vie-politique/conseil-communal`
- Ans — `ans-ville.be/ma-ville/vie-politique/conseil-communal/proces-verbaux`

**Homepage-only — nog te onderzoeken** (50):
Antoing, Aubange, Aywaille, Bassenge, Beauvechain, Bernissart, Bièvre,
Brugelette, Burdinne, Chaudfontaine, Chièvres, Crisnée, Esneux, Fernelmont,
Flobecq, Floreffe, Fosses-la-Ville, Gedinne, Gesves, Gouvy, Hastière,
Herbeumont, Herve, Hotton, Jodoigne, La Louvière, Leuze-en-Hainaut,
Libramont-Chevigny, Messancy, Modave, Mont-de-l'Enclus, Musson, Neufchâteau,
Neupré, Orp-Jauche, Ouffet, Pecq, Plombières, Rumes, Saint-Nicolas, Silly,
Soumagne, Tenneville, Tintigny, Trooz, Vaux-sur-Sûre, Verlaine,
Villers-le-Bouillet, Visé, Waterloo

### 5. Code-kwaliteit (🟡 nice-to-have) — **8 SP**
- [ ] Refactor naar class-based scrapers i.p.v. module-level globals (SESSION, _config) — **3 SP**
- [ ] HTML-generatie centraliseren in een gedeelde output-module — **2 SP**
- [ ] Playwright timeout-handling verbeteren in scraper.py en scraper_halle.py — **1 SP**
- [ ] Download-resumability toevoegen (file-size check vóór skip) — **2 SP**

### 6. Testing — **8 SP**
- [ ] Unit tests schrijven voor base_scraper.py (sanitize_filename, robust_get, download_document) — **3 SP**
- [ ] Integratietests per scraper-type (mock HTTP responses) — **5 SP**

### 7. ~~Provincie Antwerpen~~ ✅ Opgelost

### 8. ~~CSV completeren (565 gemeenten)~~ ✅ Opgelost

### 8. ~~Pubcon-gemeenten (Laakdal, Oudsbergen)~~ ✅ Opgelost

- **Oudsbergen**: `scraper_pubcon.py` — crawlt publiek `/LBLOD` endpoint, PDFs via Azure Blob Storage
  (`stpubconoudsbergen.blob.core.windows.net/lblod/documents/`). Getest: 48/100 GR-docs (404s zijn
  niet-publieke inkomende stukken/processtukken).
- **Laakdal**: notulen staan op www.laakdal.be (PaddleCMS/Drupal) → `scraper_drupal.py`.
  Pubcon LBLOD-endpoint bevat alleen metadata zonder documenten in blob.

---

## Projectstructuur

```
scraper-brugge/
├── base_scraper.py          # Gedeelde logica: sessie, download, sanitize, robust_get
├── start.py                 # Interactieve TUI (wizard)
├── scraper_groep.py         # Batch-scraper per type + type-detectie
├── scraper.py               # Brugge (Playwright, dedicated)
├── scraper_leuven.py        # Leuven (Playwright, dedicated)
├── scraper_halle.py         # SmartCities/Besluitvorming (67 gem., Playwright)
├── scraper_menen.py         # CipalSchaubroeck/Echo/CSEcho (79 gem., REST)
├── scraper_ranst.py         # MeetingBurger (45 gem., REST)
├── scraper_lblod.py         # LBLOD (52 gem., HTML)
├── scraper_deliberations.py # Deliberations.be (199 gem., HTML)
├── scraper_ibabs.py         # iBabs (2 gem., HTML)
├── scraper_provantwerpen.py  # Prov. Antwerpen (1, HTML/PDF)
├── scraper_vlaamsbrabant.py # Prov. Vlaams-Brabant (1, HTML)
├── scraper_irisnet.py       # Irisnet/publi.irisnet.be (10 Brusselse gem.)
├── scraper_brussel.py       # Stad Brussel (bruxelles.be)
├── scraper_forest.py        # Forest/Vorst (forest.brussels)
├── scraper_molenbeek.py     # Molenbeek-Saint-Jean (molenbeek.irisnet.be)
├── scraper_schaerbeek.py    # Schaerbeek (1030.be, via sitemap)
├── scraper_icordis.py       # Icordis CMS — 7 Vlaamse gem. (*/file/download)
├── scraper_drupal.py        # Drupal direct PDF — 12 gem. (*/sites/*/files) incl. Auderghem, Uccle, Laakdal
├── scraper_wordpress.py     # WordPress/Plone — 7 Duitstalige gem. (*/wp-content/uploads*)
├── scraper_pubcon.py        # Pubcon/Tobibus LBLOD — 1 gem. (Oudsbergen, Azure Blob Storage)
├── scraper_ixelles.py       # Ixelles / Elsene (conseil communal ODJ + PV)
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
