# Scraper: besluitvorming.brugge.be

Downloadt automatisch PDF-documenten (notulen, besluitenlijsten, agenda's en besluiten) van
https://besluitvorming.brugge.be van een specifiek bestuursorgaan.

## Installatie

1. Zorg dat [uv](https://docs.astral.sh/uv/) geïnstalleerd is
2. Installeer de browser:
   ```powershell
   uv run python -m playwright install chromium
   ```

## Gebruik

### Beschikbare organen bekijken

```powershell
uv run python scraper.py --lijst-organen
```

### PDFs downloaden voor een specifiek orgaan

```powershell
# Alleen notulen downloaden
uv run python scraper.py --orgaan "Gemeenteraad" --notulen --maanden 24

# Specifieke documentnaam filteren
uv run python scraper.py --orgaan "Gemeenteraad" --document-filter notulen --maanden 12

# Gemeenteraad – laatste 12 maanden
uv run python scraper.py --orgaan "Gemeenteraad" --maanden 12

# College van Burgemeester en Schepenen – laatste 6 maanden, custom map
uv run python scraper.py --orgaan "College van Burgemeester en Schepenen" --output cbs_pdfs --maanden 6

# Raad voor Maatschappelijk Welzijn
uv run python scraper.py --orgaan "Raad voor Maatschappelijk Welzijn" --maanden 12

# Alle organen – 3 maanden
uv run python scraper.py --alle --maanden 3
```

### Ook individuele besluiten per agendapunt meenemen (trager)

```powershell
uv run python scraper.py --orgaan "Gemeenteraad" --maanden 3 --agendapunten
```

### Opties

| Optie              | Korte vorm | Beschrijving                                          |
|--------------------|------------|-------------------------------------------------------|
| `--orgaan NAAM`       | `-o NAAM`  | Filter op orgaannaam (bv. "Gemeenteraad")             |
| `--alle`              |            | Alle organen zonder filter                            |
| `--output MAP`        | `-d MAP`   | Uitvoermap (standaard: `pdfs`)                        |
| `--maanden N`         | `-m N`     | Aantal maanden terug (standaard: `12`)                |
| `--agendapunten`      | `-a`       | Ook per-agendapunt besluit-PDFs downloaden            |
| `--notulen`           |            | Alleen documenten met "notulen" in de naam            |
| `--document-filter T` | `-f T`     | Alleen docs waarvan de naam tekst `T` bevat           |
| `--lijst-organen`     |            | Toon beschikbare organen en stop                      |
| `--zichtbaar`         |            | Toon de browser (voor debuggen)                       |

## Mappenstructuur output

```
pdfs/
├── Gemeenteraad_25.0926.5454.7355/
│   ├── Besluitenlijst gepubliceerd op 25.02.2026.pdf
│   └── besluiten_per_punt/        ← alleen met --agendapunten
│       ├── Besluit.pdf
│       └── ...
└── Gemeenteraad_25.0926.1234.5678/
    └── Besluitenlijst gepubliceerd op 27.01.2026.pdf
```

## Wat wordt gedownload?

Per vergadering worden de volgende PDFs gedownload:
- **Notulen** – goedgekeurde verslaglegging (verschijnt pas na goedkeuring op volgende vergadering)
- **Besluitenlijst** – overzicht van alle genomen besluiten
- **Agenda** – de gepubliceerde agenda (indien beschikbaar als PDF)
- **Besluiten per agendapunt** – alleen met de `--agendapunten` vlag

Gebruik `--notulen` of `--document-filter notulen` om alleen de notulen te downloaden.

> Nota: het CBS (College van Burgemeester en Schepenen) en Vast Bureau publiceren
> enkel de besluitenlijst, geen volledige besluiten.

## Bekende organen

- Gemeenteraad
- College van Burgemeester en Schepenen
- Raad voor Maatschappelijk Welzijn
- Vast Bureau
- Burgemeester
- Commissie 1, Commissie 2, Commissie 3
- RvB Mintus, RvB De Blauwe Lelie, RvB Ons Huis, RvB Ruddersstove
- RvB SAS, RvB SVK, RvB Spoor, RvB WOK, RvB De Schakelaar
- AV Mintus, AV De Blauwe Lelie, ...
