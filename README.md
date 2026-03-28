# Besluitendatabank Scraper
Automatische downloader voor PDF-documenten (notulen, besluitenlijsten, agenda's, besluiten) van Belgische gemeenten.
**Dekking:** 574/575 entiteiten (gemeenten + provincies) hebben een werkende scraper (**99,8%**).
**Scrapers:** 22 standalone scrapers + 1 batch-orchestrator (`scraper_groep.py`).
**Geblokkeerd:** 1 gemeente (Herstappe — DNS-fout, ~85 inwoners).
## Installatie
1. Zorg dat [uv](https://docs.astral.sh/uv/) geïnstalleerd is
2. Installeer dependencies:
   ```powershell
   uv sync
   ```
3. Installeer de browser (alleen nodig voor SmartCities-gemeenten):
   ```powershell
   uv run python -m playwright install chromium
   ```
## Snelstart
### Interactieve wizard (gemakkelijkste manier)
```powershell
uv run python start.py
```
Kies: 📥 Enkele gemeente scrapen | 📦 Batch scrapen per type | 📋 Organen bekijken | 🚪 Afsluiten
### Commando regel (directe methode)
```powershell
# Brugge - Gemeenteraad, notulen, 36 maanden (standaard)
uv run python scraper.py --orgaan "Gemeenteraad" --notulen --maanden 36
# Batch scrapen via scraper_groep.py
uv run python scraper_groep.py --gemeente Beernem --alle --maanden 6
```
## Websitetypes
| Type | Aantal | Voorbeelden | Scraper | Browser |
|------|--------|------------|---------|---------|
| SmartCities / Besluitvorming | 70 | Brugge, Leuven, Kortrijk | scraper_onlinesmartcities.py | Ja (Playwright) |
| CipalSchaubroeck / CSEcho | 79 | Beernem, Menen, Ieper | scraper_menen.py | Nee (REST API) |
| MeetingBurger | 46 | Ranst, Hulshout | scraper_ranst.py | Nee (REST API) |
| LBLOD | 62 | Gistel, Bredene, Assenede | scraper_lblod.py | Nee (HTML) |
| iDélibé (conseilcommunal.be) | 31 | Anderlecht, Namur, Spa | scraper_idelibe.py | Nee (HTML) |
| Deliberations.be | 167 | Liège, Charleroi, Awans | scraper_deliberations.py | Nee (HTML) |
| iMio / Plone directe PDFs | 31 | Arlon, Viroinval, Herstal | scraper_imio.py | Nee (HTML) |
| WordPress / TYPO3 / Plone | 36 | Hastière, Courcelles, Waterloo | scraper_wordpress.py | Nee (HTML) |
| Icordis CMS (LCP nv) | 12 | Vlaamse Icordis-gemeenten | scraper_icordis.py | Nee (HTML) |
| Drupal / TYPO3 | 15 | Diverse sites | scraper_drupal.py | Nee (HTML) |
| iBabs | 2 | Kalmthout, Stabroek | scraper_ibabs.py | Nee (HTML) |
| Irisnet (Brusselse gem.) | 10 | Anderlecht, Etterbeek, Jette | scraper_irisnet.py | Nee (HTML) |
| LCP agenda-notulen | 2 | Linkebeek, Rhode-Saint-Genèse | scraper_linkebeek.py | Nee (HTML) |
| Pubcon (Tobibus LBLOD) | 1 | Sint-Lievens-Houtem | scraper_pubcon.py | Nee (HTML) |
| Ixelles / Elsene | 1 | Ixelles | scraper_ixelles.py | Nee (HTML) |
| Docodis CMS | 1 | Koekelberg | scraper_docodis.py | Nee (HTML) |
| Provinciaal Antwerpen | 1 | Provincie Antwerpen | scraper_provantwerpen.py | Nee (HTML) |
| Provinciaal Vlaams-Brabant | 1 | Provincie Vlaams-Brabant | scraper_vlaamsbrabant.py | Nee (HTML) |
| Waalse provincies | 3 | Henegouwen, Luxemburg, Waals-Brabant | scraper_waalse_provincies.py | Nee (HTML) |
| Brussel (bruxelles.be) | 1 | Brussel stad | scraper_brussel.py | Nee (HTML) |
| Molenbeek-Saint-Jean | 1 | Molenbeek-Saint-Jean | scraper_molenbeek.py | Nee (HTML) |
| Schaerbeek | 1 | Schaerbeek | scraper_schaerbeek.py | Nee (HTML) |
| Overig / Geblokkeerd | 1 | Herstappe | — | — |
## Commando regel voorbeelden
### Brugge / Leuven (SmartCities)
```powershell
uv run python scraper.py --orgaan "Gemeenteraad" --maanden 36
uv run python scraper.py --alle --maanden 12
uv run python scraper.py --orgaan "Gemeenteraad" --agendapunten --maanden 6
```
### Andere SmartCities-gemeenten
```powershell
uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-kortrijk.onlinesmartcities.be --orgaan "Gemeenteraad" --maanden 12
uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-roeselare.onlinesmartcities.be --alle --maanden 6
```
### CipalSchaubroeck / CSEcho-gemeenten
```powershell
uv run python scraper_menen.py --base-url https://beernem-echo.cipalschaubroeck.be --orgaan "Gemeenteraad" --maanden 12
```
### MeetingBurger-gemeenten
```powershell
uv run python scraper_ranst.py --base-url https://ranst.meetingburger.net --orgaan "Gemeenteraad" --maanden 12
```
### LBLOD-gemeenten (62 gemeenten)
```powershell
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --notulen --maanden 36
```
### Deliberations.be (167 gemeenten)
```powershell
uv run python scraper_deliberations.py --gemeente liege --maanden 6
uv run python scraper_deliberations.py --alle --maanden 3
```
### iBabs (Kalmthout, Stabroek)
```powershell
uv run python scraper_ibabs.py --gemeente kalmthout --maanden 6
uv run python scraper_ibabs.py --alle
```
### Provincie Vlaams-Brabant
```powershell
uv run python scraper_vlaamsbrabant.py --maanden 6
```
### Ingelmunster
```powershell
uv run python scraper_drupal.py --base-url https://www.ingelmunster.be --orgaan "Gemeenteraad" --maanden 12
```
### Batch scrapen (meerdere gemeenten)
```powershell
# Alle SmartCities, Gemeenteraad, notulen, 36 maanden
uv run python scraper_groep.py --type smartcities --orgaan "Gemeenteraad" --maanden 36 --notulen
# Alle CipalSchaubroeck-gemeenten
uv run python scraper_groep.py --type cipalschaubroeck --orgaan "Gemeenteraad" --maanden 12
# Alle deliberations.be gemeenten
uv run python scraper_groep.py --type deliberations --alle --maanden 3
# Alle LBLOD-gemeenten
uv run python scraper_groep.py --type lblod --orgaan "Gemeenteraad" --maanden 12
# Waalse provincies
uv run python scraper_groep.py --type hainaut --alle --maanden 6
# Toon groepsoverzicht
uv run python scraper_groep.py --toon-groepen
# Interactieve TUI (wizard)
uv run python scraper_groep.py
```
## Opties
| Optie | Korte | Beschrijving |
|-------|-------|--------------|
| --orgaan NAAM | -o | Filter op orgaannaam |
| --alle | | Alle organen |
| --maanden N | -m | Aantal maanden (**standaard: 36 voor Gemeenteraad, 12 voor anderen**) |
| --output MAP | -d | Uitvoermap |
| --document-filter T | -f | Alleen docs met `T` in naam |
| --notulen | | Shorthand voor --document-filter notulen |
| --agendapunten | -a | Ook per-agendapunt besluiten |
| --zichtbaar | | Browser tonen (SmartCities) |
| --lijst-organen | | Organen tonen en stoppen |
## Output
```
pdfs/
├── Beernem/gemeenteraad/
│   ├── gemeenteraad_20260226/
│   │   └── GR--260226--agenda.pdf
├── Kalmthout/
│   └── Kalmthout_metadata.json
└── liege/
    ├── conseil-communal/
    │   └── 2026-01-15_Rapport.pdf
    └── liege.html
```
## Wat wordt gedownload?
- Agenda's
- Notulen (zittingsverslagen)
- Besluitenlijsten
- Per-agendapunt besluiten (optioneel)
Gebruik `--notulen` om alleen notulen te downloaden.
> **Opmerking:** CBS en Vast Bureau publiceren meestal alleen besluitenlijsten.
