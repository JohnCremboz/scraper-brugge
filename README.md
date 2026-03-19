# Besluitendatabank Scraper
Automatische downloader voor PDF-documenten (notulen, besluitenlijsten, agenda's, besluiten) van Belgische gemeenten.
**Ondersteunde gemeenten:** 429 gemeenten met scraper, 555 in de bronlijst.
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
| SmartCities | 71 | Brugge, Leuven, Kortrijk, Halle | scraper_halle.py | Ja (Playwright) |
| CipalSchaubroeck / CSEcho | 85 | Beernem, Menen, Ieper, Pajottegem | scraper_menen.py | Nee (REST API) |
| MeetingBurger | 41 | Ranst, Hulshout | scraper_ranst.py | Nee (REST API) |
| LBLOD | 48 | Gistel, Bredene, Assenede | scraper_lblod.py | Nee (HTML) |
| Deliberations.be | 180 | Liège, Namur, Charleroi, Awans | scraper_deliberations.py | Nee (HTML) |
| iBabs | 2 | Kalmthout, Stabroek | scraper_ibabs.py | Nee (HTML) |
| Provincie Vlaams-Brabant | 1 | Provincie Vlaams-Brabant | scraper_vlaamsbrabant.py | Nee (HTML) |
| Ingelmunster | 1 | Ingelmunster | scraper_ingelmunster.py | Nee (HTML) |
| Irisnet (Brussel) | 8 | Schaerbeek, Forest, Molenbeek | — | — |
| Overig | 118 | Diverse sites | — | — |
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
