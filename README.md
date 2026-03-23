# Besluitendatabank Scraper
Automatische downloader voor PDF-documenten (notulen, besluitenlijsten, agenda's, besluiten) van Belgische gemeenten.
**Ondersteunde gemeenten:** 574 gemeenten met scraper, 575 in de bronlijst.
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
# Ingelmunster - dedicated scraper
uv run python scraper_ingelmunster.py --orgaan "Gemeenteraad" --maanden 12
```
## Websitetypes
| Type | Aantal | Voorbeelden | Scraper | Browser |
|------|--------|------------|---------|---------|
| SmartCities | 70 | Brugge, Leuven, Kortrijk, Halle | scraper_halle.py | Ja (Playwright) |
| CipalSchaubroeck / CSEcho | 79 | Beernem, Menen, Ieper, Pajottegem | scraper_menen.py | Nee (REST API) |
| MeetingBurger | 46 | Ranst, Hulshout | scraper_ranst.py | Nee (REST API) |
| LBLOD | 62 | Gistel, Bredene, Assenede | scraper_lblod.py | Nee (HTML) |
| iDélibé (conseilcommunal.be) | 31 | Anderlecht, Namur, Spa | scraper_idelibe.py | Nee (HTML) |
| Deliberations.be | 167 | Liège, Charleroi, Awans | scraper_deliberations.py | Nee (HTML) |
| iMio/Plone gemeenten | 31 | Arlon, Viroinval, Herstal | scraper_imio.py | Nee (HTML) |
| WordPress / Plone | 36 | Hastière, Courcelles, Waterloo | scraper_wordpress.py | Nee (HTML) |
| Icordis CMS | 12 | Vlaamse Icordis-gemeenten | scraper_icordis.py | Nee (HTML) |
| Drupal | 15 | Diverse sites | scraper_drupal.py | Nee (HTML) |
| iBabs | 2 | Kalmthout, Stabroek | scraper_ibabs.py | Nee (HTML) |
| Provincie Vlaams-Brabant | 1 | Provincie Vlaams-Brabant | scraper_vlaamsbrabant.py | Nee (HTML) |
| Ingelmunster | 1 | Ingelmunster | scraper_drupal.py | Nee (HTML) |
| Irisnet (Brussel) | 10 | Schaerbeek, Forest, Molenbeek | scraper_irisnet.py | Nee (HTML) |
| Overig | 1 | Herstappe | — | — |
## Commando regel voorbeelden
### Brugge / Leuven (SmartCities)
```powershell
uv run python scraper.py --orgaan "Gemeenteraad" --maanden 36
uv run python scraper.py --alle --maanden 12
uv run python scraper.py --orgaan "Gemeenteraad" --agendapunten --maanden 6
```
### Andere SmartCities-gemeenten
```powershell
uv run python scraper_halle.py --base-url https://raadpleeg-kortrijk.onlinesmartcities.be --orgaan "Gemeenteraad" --maanden 12
uv run python scraper_halle.py --base-url https://raadpleeg-roeselare.onlinesmartcities.be --alle --maanden 6
```
### CipalSchaubroeck / CSEcho-gemeenten
```powershell
uv run python scraper_menen.py --base-url https://beernem-echo.cipalschaubroeck.be --orgaan "Gemeenteraad" --maanden 12
```
### MeetingBurger-gemeenten
```powershell
uv run python scraper_ranst.py --base-url https://ranst.meetingburger.net --orgaan "Gemeenteraad" --maanden 12
```
### LBLOD-gemeenten (48 gemeenten)
```powershell
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --notulen --maanden 36
```
### Deliberations.be (180 Waalse gemeenten)
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
uv run python scraper_ingelmunster.py --lijst-organen
uv run python scraper_ingelmunster.py --orgaan "Gemeenteraad" --maanden 12
```
### Batch scrapen (meerdere gemeenten)
```powershell
# Alle SmartCities, Gemeenteraad, notulen, 36 maanden
uv run python scraper_groep.py --type smartcities --orgaan "Gemeenteraad" --maanden 36 --notulen
# Alle deliberations.be gemeenten
uv run python scraper_groep.py --type deliberations --alle --maanden 3
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
