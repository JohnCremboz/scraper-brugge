# Besluitendatabank Scraper
Automatische downloader voor PDF-documenten (notulen, besluitenlijsten, agenda's, besluiten) van Belgische gemeenten.
**Ondersteunde gemeenten:** 200+ gemeenten, inclusief Brugge, Leuven, Kortrijk, Roeselare, Beernem, Ingelmunster en vele anderen.
## Installatie
1. Zorg dat [uv](https://docs.astral.sh/uv/) geïnstalleerd is
2. Installeer de browser:
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
# Ingelmunster - nieuwe dedicated scraper
uv run python scraper_ingelmunster.py --orgaan "Gemeenteraad" --maanden 12
```
## Websitetypes
| Type | Voorbeelden | Scraper | Browser |
|------|------------|---------|---------|
| SmartCities | Brugge, Leuven, Kortrijk, Roeselare, Halle | scraper_halle.py | Ja (Playwright) |
| CipalSchaubroeck | Beernem, Menen, Ieper | scraper_menen.py | Nee (REST API) |
| MeetingBurger | Ranst, Hulshout | scraper_ranst.py | Nee (REST API) |
| Ingelmunster | Ingelmunster | scraper_ingelmunster.py | Nee (HTML) |
| **LBLOD** | **Gistel, Bredene, Assenede, +45 anderen** | **scraper_lblod.py** | **Nee (HTML)** |
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
### CipalSchaubroeck-gemeenten
```powershell
uv run python scraper_menen.py --base-url https://beernem-echo.cipalschaubroeck.be --orgaan "Gemeenteraad" --maanden 12
```
### MeetingBurger-gemeenten
```powershell
uv run python scraper_ranst.py --base-url https://ranst.meetingburger.net --orgaan "Gemeenteraad" --maanden 12
```
### Ingelmunster
```powershell
uv run python scraper_ingelmunster.py --lijst-organen
uv run python scraper_ingelmunster.py --orgaan "Gemeenteraad" --maanden 12
```
### LBLOD-gemeenten (48 gemeenten)

```powershell
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --notulen --maanden 36
uv run python scraper_lblod.py --base-url https://lblod.bredene.be --alle --maanden 12
```

### Batch scrapen (meerdere gemeenten)
```powershell
# Alle SmartCities, Gemeenteraad, notulen, 36 maanden
uv run python scraper_groep.py --type smartcities --orgaan "Gemeenteraad" --maanden 36 --notulen
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
## Standaarden
**Gemeenteraad (interactieve wizard):** 36 maanden + notulen
**Anderen:** 12 maanden zonder filter
**Commando regel:** 12 maanden zonder filter (tenzij aangegeven)
## Voortgang in terminal
Alle scrapers tonen voortgang zodat u weet dat het programma bezig is:
```
[1] Kalender laden...
    (verbinding maken...)
    OK
[2] Filter instellen: Gemeenteraad
    OK
[3] Doorzoek 1 maand(en)...
    (laden van vergaderingen...)
    8 vergaderingen, 2 nieuw
    (1/2) verwerken... -> 4 PDF(s)
    (2/2) verwerken... -> 2 PDF(s)
```
Dit voorkomt dat u denkt dat het script vastzit.
## Output
```
pdfs/
├── Beernem/gemeenteraad/
│   ├── gemeenteraad_20260226/
│   │   └── GR--260226--agenda.pdf
└── Ingelmunster/gemeenteraad/
    └── 20260302_Agenda GR.pdf
```
## Wat wordt gedownload?
- Agenda's
- Notulen (zittingsverslagen)
- Besluitenlijsten
- Per-agendapunt besluiten (optioneel)
Gebruik `--notulen` om alleen notulen te downloaden.
> **Opmerking:** CBS en Vast Bureau publiceren meestal alleen besluitenlijsten.

## LBLOD-gemeenten

**48 gemeenten** gebruiken LBLOD (Linked Open Data), waaronder: Gistel, Assenede, Berlare, Bredene, Buggenhout, Denderleeuw, Diksmuide, en vele anderen.

De LBLOD-scraper (`scraper_lblod.py`) werkt via het LBLODWeb publicatieportaal dat elke gemeente aanbiedt op `lblod.{gemeente}.be/LBLODWeb/`.

```powershell
# Beschikbare organen tonen
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen

# Notulen gemeenteraad downloaden (36 maanden)
uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --notulen --maanden 36

# Alle organen, alle documenten (6 maanden)
uv run python scraper_lblod.py --base-url https://lblod.bredene.be --alle --maanden 6

# Via batch scraper
uv run python scraper_groep.py --type lblod --orgaan "Gemeenteraad" --notulen --maanden 36
```

## Ondersteunde organen (voorbeelden)
- Gemeenteraad
- College van Burgemeester en Schepenen
- Raad voor Maatschappelijk Welzijn
- Vast Bureau
- Commissies
- Besturen (RvB, AV)
