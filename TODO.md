# TODO — Besluitendatabank Scraper

## Huidige status (22 maart 2026)

**Dekking:** 574/575 rijen in simba-source.csv hebben een werkende scraper (**99.8%**)
**CSV:** 575 rijen = 10 provincies + 565 gemeenten (compleet, matcht Wikipedia)
**Geblokkeerd:** 1 gemeente (Herstappe — DNS-fout, ~85 inwoners, geen werkende website)
**Scrapers:** 21 standalone scrapers + 1 batch-orchestrator (`scraper_groep.py`)

| Type | Gemeenten/entiteiten | Scraper |
|------|---------------------|---------|
| Deliberations.be (Plone/IMIO) | 203 | scraper_deliberations.py |
| CipalSchaubroeck / CSEcho | 79 | scraper_menen.py |
| SmartCities / Besluitvorming | 72 | scraper_onlinesmartcities.py |
| LBLOD | 63 | scraper_lblod.py |
| MeetingBurger | 46 | scraper_ranst.py |
| iDélibé (conseilcommunal.be) | 39 | scraper_idelibe.py |
| WordPress / TYPO3 / Plone | 32 | scraper_wordpress.py |
| Drupal / TYPO3 directe PDFs | 13 | scraper_drupal.py |
| Icordis CMS (LCP nv) | 12 | scraper_icordis.py |
| Irisnet (Brusselse gem.) | 10 | scraper_irisnet.py |
| iBabs | 2 | scraper_ibabs.py |
| LCP agenda-notulen | 2 | scraper_linkebeek.py |
| Pubcon (Tobibus LBLOD) | 1 | scraper_pubcon.py |
| Ixelles / Elsene | 1 | scraper_ixelles.py |
| Docodis CMS | 1 | scraper_docodis.py |
| Prov. Antwerpen | 1 | scraper_provantwerpen.py |
| Prov. Vlaams-Brabant | 1 | scraper_vlaamsbrabant.py |
| Prov. Henegouwen / Luxemburg / Waals-Brabant | 3 | scraper_waalse_provincies.py |
| Brussel (bruxelles.be) | 1 | scraper_brussel.py |
| Forest | 1 | scraper_drupal.py |
| Molenbeek-Saint-Jean | 1 | scraper_molenbeek.py |
| Schaerbeek | 1 | scraper_schaerbeek.py |
| Leuven | 1 | scraper_onlinesmartcities.py |
| **Geblokkeerd (DNS)** | **1** | — |

---

## Provincies (10/10) ✅

Alle 10 Belgische provincies zijn nu gedekt:

| Provincie | Type | Scraper |
|-----------|------|---------|
| Antwerpen | provantwerpen | scraper_provantwerpen.py |
| Oost-Vlaanderen | smartcities | scraper_onlinesmartcities.py |
| West-Vlaanderen | lblod | scraper_lblod.py |
| Limburg | smartcities | scraper_onlinesmartcities.py |
| Vlaams-Brabant | vlaamsbrabant | scraper_vlaamsbrabant.py |
| Henegouwen | hainaut | scraper_waalse_provincies.py |
| Luxemburg | luxemburg | scraper_waalse_provincies.py |
| Waals-Brabant | brabantwallon | scraper_waalse_provincies.py |
| Luik | deliberations | scraper_deliberations.py |
| Namen | deliberations | scraper_deliberations.py |

---

## Brusselse gemeenten (19/19) ✅

| Gemeente | Platform | Scraper |
|----------|----------|---------|
| Anderlecht, Berchem-Sainte-Agathe, Etterbeek, Evere, Ganshoren, Jette, Saint-Gilles, Saint-Josse-ten-Noode, Watermael-Boitsfort, Woluwe-Saint-Lambert | publi.irisnet.be | scraper_irisnet.py |
| Brussel | bruxelles.be | scraper_brussel.py |
| Forest | forest.brussels | scraper_forest.py |
| Molenbeek-Saint-Jean | molenbeek.irisnet.be | scraper_molenbeek.py |
| Schaerbeek | 1030.be | scraper_schaerbeek.py |
| Ixelles / Elsene | ixelles.be | scraper_ixelles.py |
| Uccle | uccle.be (Drupal) | scraper_drupal.py |
| Auderghem | auderghem.be (Drupal) | scraper_drupal.py |
| Koekelberg | koekelberg.be (Docodis CMS) | scraper_docodis.py |
| Woluwe-Saint-Pierre | woluwe1150.be (WordPress) | scraper_wordpress.py |

---

## Duitstalige gemeenten (9/9) ✅

| Gemeente | Platform | Opmerkingen |
|----------|----------|-------------|
| Bütgenbach | WordPress | — |
| Kelmis | WordPress | — |
| Lontzen | WordPress | — |
| Raeren | WordPress | PDFs op static.raeren.be CDN → `extra_pdf_domeinen` |
| Burg-Reuland | WordPress | — |
| Eupen | WordPress | — |
| Sankt Vith | Plone (st.vith.be) | jaarpagina-navigatie |
| Büllingen | WordPress | Sucuri WAF omzeild via Playwright |
| Amel | TYPO3/fileadmin | datum uit linktekst |

---

## Waalse gemeenten

### WordPress / Plone / LetsGoCity (23 gemeenten) ✅

| Gemeente | Domein | Opmerkingen |
|----------|--------|-------------|
| La Louvière | lalouviere.be | jaarpagina-navigatie |
| Waterloo | waterloo.be | Plone, subfolder |
| Fernelmont | fernelmont.be | Plone |
| Chièvres | chievres.be | Plone |
| Verlaine | verlaine.be | Plone |
| Fosses-la-Ville | fosses-la-ville.be | Plone |
| Brugelette | brugelette.be | Plone |
| Pecq | pecq.be | Plone |
| Herbeumont | herbeumont.be | Plone, seances-YYYY subfolders |
| Amay | amay.be | Plone, datum in linktekst |
| Anhée | anhee.be | Plone, @@folder_listing |
| Floreffe | floreffe.be | WordPress |
| Bernissart | bernissart.be | WordPress |
| Rumes | rumes.be | WordPress |
| Woluwe-Saint-Pierre | woluwe1150.be | WordPress, datum in linktekst |
| Antoing | antoing.net | Plone, 2-niveau subfolder |
| Ans | ans-ville.be | Plone, jaar-subfolders |
| Aubange | aubange.be | WordPress, PDFs op ImageKit CDN |
| Burdinne | burdinne.be | Plone, jaar-subfolders |
| Crisnée | crisnee.be | Joomla, custom pdf_re |
| Gesves | gesves.be | WordPress, HTTP-only |
| Mont-de-l'Enclus | montdelenclus.be | WordPress, ssl_verify: False |
| Orp-Jauche | orp-jauche.be | WordPress + Playwright |
| Trooz | trooz.be | Plone imio.smartweb |
| Vaux-sur-Sûre | vaux-sur-sure.be | Plone imio.smartweb |
| Hastière | hastiere.be | LetsGoCity SPA, REST API |

### iDélibé / conseilcommunal.be (39 gemeenten) ✅

Alle 39 gemeenten via REST API (`/ApiCitoyen/public/v1/`) — `scraper_idelibe.py`

### Deliberations.be (1 gemeente) ✅

- Libramont-Chevigny → `www.deliberations.be/libramont`

---

## Geblokkeerde gemeenten

| Gemeente | Probleem |
|----------|----------|
| Herstappe | DNS-fout, ~85 inwoners, geen werkende website |

---

## Openstaande taken

### Code-kwaliteit (nice-to-have)

- [x] Halle + Leuven samenvoegen tot `scraper_onlinesmartcities.py` (95% identieke code) ✅
- [x] Forest integreren in `scraper_drupal.py` als 13e configuratie-entry ✅
- [ ] HTML-generatie centraliseren in een gedeelde output-module (nu duplicaat in waalse_provincies, brussel, ...)
- [ ] Refactor naar class-based scrapers i.p.v. module-level globals (SESSION, _config)
- [ ] Playwright timeout-handling verbeteren in scraper.py en scraper_onlinesmartcities.py
- [ ] Download-resumability toevoegen (file-size check vóór skip)

### Testing (nice-to-have)

- [ ] Unit tests voor base_scraper.py (sanitize_filename, robust_get, download_document)
- [ ] Integratietests per scraper-type (mock HTTP responses)

---

## Projectstructuur

```
scraper-brugge/
├── base_scraper.py              # Gedeelde logica: sessie, download, sanitize, robust_get
├── start.py                     # Interactieve TUI (wizard)
├── scraper_groep.py             # Batch-scraper per type + type-detectie (URL → scraper)
├── scraper.py                   # Brugge (Playwright, dedicated)
├── scraper_onlinesmartcities.py # SmartCities/Besluitvorming (72 gem. + Leuven, Playwright)
├── scraper_menen.py             # CipalSchaubroeck/CSEcho (79 gem., REST)
├── scraper_ranst.py             # MeetingBurger (46 gem., REST)
├── scraper_lblod.py             # LBLOD (63 gem., HTML)
├── scraper_deliberations.py     # Deliberations.be/IMIO (203 gem., HTML)
├── scraper_ibabs.py             # iBabs (2 gem., HTML)
├── scraper_irisnet.py           # Irisnet/publi.irisnet.be (10 Brusselse gem.)
├── scraper_brussel.py           # Stad Brussel (bruxelles.be)
├── scraper_forest.py            # Forest/Vorst (forest.brussels)
├── scraper_molenbeek.py         # Molenbeek-Saint-Jean (molenbeek.irisnet.be)
├── scraper_schaerbeek.py        # Schaerbeek (1030.be, via sitemap)
├── scraper_ixelles.py           # Ixelles/Elsene
├── scraper_icordis.py           # Icordis CMS/LCP — 12 gem. (*/file/download)
├── scraper_linkebeek.py         # LCP agenda-notulen — 2 gem. (Linkebeek, SGR)
├── scraper_docodis.py           # Docodis CMS — 1 gem. (Koekelberg)
├── scraper_drupal.py            # Drupal/TYPO3 direct PDF — 13 gem. incl. Ingelmunster, Forest
├── scraper_wordpress.py         # WordPress/TYPO3/Plone — 32 gem.
├── scraper_idelibe.py           # iDélibé/conseilcommunal.be — 39 Waalse gem. (REST API)
├── scraper_pubcon.py            # Pubcon/Tobibus LBLOD — 1 gem. (Oudsbergen)
├── scraper_provantwerpen.py     # Prov. Antwerpen
├── scraper_vlaamsbrabant.py     # Prov. Vlaams-Brabant
├── scraper_waalse_provincies.py # Prov. Henegouwen + Luxemburg + Waals-Brabant
├── compare_wiki.py              # Vergelijking CSV vs Wikipedia (565 gem.)
├── investigate.py               # Hulpscript voor onderzoek nieuwe gemeenten
├── simba-source.csv             # Bronlijst: 575 rijen (10 provincies + 565 gemeenten)
└── pyproject.toml               # Dependencies (Python 3.12+, uv)
```

## Technische notities

- **Package manager**: uv — `uv run python scraper_wordpress.py --gemeente burdinne`
- **Encoding**: `$env:PYTHONIOENCODING = "utf-8"` nodig op Windows bij sommige terminals
- **Playwright**: alleen nodig voor SmartCities-type (72 gem. + Leuven) + Brugge + Büllingen + Orp-Jauche
- **Type-detectie**: `scraper_groep.py detecteer_type()` bepaalt de scraper op basis van de URL in de CSV
- **Datumextractie**: `datum_uit_pad()` in scraper_wordpress.py kent 8 patronen (YYYYMMDD, YYYY-MM-DD, YYYY.MM.DD, DD.MM.YYYY, YY.MM.DD, DD-MM-YY, Frans maandnaam, DDMMYYYY)
- **Plone-gemeenten**: links eindigen op `.pdf/view` → `plone_folder_listing: True` strip de `/view`-suffix
- **ImageKit CDN** (Aubange): `extra_pdf_domeinen: ["ik.imagekit.io"]` voor externe PDF-links
- **TYPO3** (Ingelmunster): `pdf_re: _TYPO3_PDF_RE` + `vereist_datum: True` in scraper_drupal.py config
- **Forest** (www.forest.brussels): `kaart_klassen: ["publication", "conseil"]` triggert kaart-gebaseerde parsing; datum via `<time class="datetime" datetime="...">` ipv URL
- **Waalse provincies**: `scraper_waalse_provincies.py --provincie hainaut|luxemburg|brabantwallon`; auto-detectie via `--base-url`

