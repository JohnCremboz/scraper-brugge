"""
scraper_vlaamsbrabant.py — Scraper voor bestuur.vlaamsbrabant.be
Dekt Provincie Vlaams-Brabant.

Gebruik:
    python scraper_vlaamsbrabant.py
    python scraper_vlaamsbrabant.py --maanden 6
    python scraper_vlaamsbrabant.py --no-docs

Platform structuur:
    /           → overzichtstabel met alle publicaties (Alpine.js filtering)
    /publicaties/{slug}.html  → detail pagina met agendapunten + stemmingen
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    robust_get,
    sanitize_filename,
)

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None

BASE_URL = "https://bestuur.vlaamsbrabant.be"
NAAM = "Provincie Vlaams-Brabant"

# Categorie- en type-namen (uit de filter-selects op de homepage)
CATEGORIEEN = {"1": "Agenda", "2": "Notulen", "3": "Besluit"}
TYPES = {
    "1": "Deputatie",
    "2": "Provincieraad",
    "3": "RC: Ruimte",
    "4": "RC: Kenniseconomie, Mens, Vrije tijd",
    "5": "RC: Ondersteuning en Stafdiensten",
    "6": "RC: Verenigde raadscommissie",
    "7": "RC: Financiën",
    "8": "RC: Ontwikkeling en beleving",
    "9": "RC: Omgeving",
}


def _get(url: str) -> requests.Response | None:
    return robust_get(SESSION, url)


# ---------------------------------------------------------------------------
# Publicaties ophalen van de homepage
# ---------------------------------------------------------------------------

def haal_publicaties(maanden: int = 3) -> list[dict]:
    """
    Parse de overzichtstabel op de homepage.
    Filtert op datum: alleen publicaties van de afgelopen `maanden` maanden.
    """
    resp = _get(BASE_URL)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    vandaag = date.today()
    cutoff_ts = int(datetime(
        vandaag.year,
        max(1, vandaag.month - maanden),
        1,
        tzinfo=timezone.utc,
    ).timestamp())

    publicaties = []
    for row in soup.find_all("tr"):
        xshow = row.get("x-show", "")
        if not xshow:
            continue

        # Datum (Unix timestamp)
        ts_m = re.search(r"filterDate == '(\d+)'", xshow)
        if not ts_m:
            continue
        ts = int(ts_m.group(1))
        if ts < cutoff_ts:
            continue

        # Categorie en type
        cat_m = re.search(r"filterCategory == '(\d+)'", xshow)
        type_m = re.search(r"filterType == '(\d+)'", xshow)
        cat_id = cat_m.group(1) if cat_m else ""
        type_id = type_m.group(1) if type_m else ""

        # Datum en titel uit cellen
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        datum_tekst = cells[0].get_text(strip=True)
        titel = cells[1].get_text(strip=True)
        href = cells[0].find("a", href=True)
        if not href:
            href = cells[1].find("a", href=True)
        if not href:
            continue

        pub_url = urljoin(BASE_URL, href["href"])
        pub_datum = datetime.fromtimestamp(ts, tz=timezone.utc).date()

        publicaties.append({
            "datum": pub_datum.strftime("%d/%m/%Y"),
            "datum_tekst": datum_tekst,
            "categorie": CATEGORIEEN.get(cat_id, cat_id),
            "orgaan": TYPES.get(type_id, type_id),
            "titel": titel,
            "url": pub_url,
        })

    return publicaties


# ---------------------------------------------------------------------------
# Publicatie detail: agendapunten + stemmingen
# ---------------------------------------------------------------------------

def haal_detail(publicatie: dict) -> dict:
    resp = _get(publicatie["url"])
    if resp is None:
        publicatie["agendapunten"] = []
        return publicatie

    soup = BeautifulSoup(resp.text, "lxml")
    agendapunten = []

    # --- Structuur 1: Besluit-pagina's via <dl typeof="besluit:Agendapunt"> ---
    for dl in soup.find_all("dl", attrs={"typeof": "besluit:Agendapunt"}):
        dd = dl.find("dd")
        if dd:
            tekst = (dd.get("content") or dd.get_text(strip=True)).strip()
            # Tekst formaat: "1. Titel van het agendapunt"
            m = re.match(r"^(\d+[\w.]*\.?)\s+(.*)", tekst)
            if m:
                agendapunten.append({"nr": m.group(1), "titel": m.group(2).strip()[:300]})
            elif tekst:
                agendapunten.append({"nr": "", "titel": tekst[:300]})

    # --- Structuur 2: Agenda/Notulen-pagina's via <span> na "Overzicht Agendapunten" ---
    if not agendapunten:
        overzicht = soup.find(string=re.compile(r"Overzicht Agendapunten", re.I))
        if overzicht:
            parent = overzicht.find_parent()
            if parent:
                for span in parent.find_next_siblings("span"):
                    # <span><span>{nr}</span>. <span>{titel}</span></span>
                    inner_spans = span.find_all("span", recursive=False)
                    if len(inner_spans) >= 2:
                        nr = inner_spans[0].get_text(strip=True)
                        titel = inner_spans[1].get_text(strip=True)
                        if nr and titel:
                            agendapunten.append({"nr": nr, "titel": titel[:300]})
                    elif span.get_text(strip=True):
                        tekst = span.get_text(" ", strip=True)
                        m = re.match(r"^(\d+)\.\s+(.*)", tekst)
                        if m:
                            agendapunten.append({"nr": m.group(1), "titel": m.group(2).strip()[:300]})

    publicatie["agendapunten"] = agendapunten
    return publicatie


# ---------------------------------------------------------------------------
# HTML genereren
# ---------------------------------------------------------------------------

def genereer_html(publicaties: list[dict], output_dir: Path) -> Path:
    from html_output import agendapunten_html, genereer_html_tabel
    html_path = output_dir.parent / f"{sanitize_filename(NAAM)}.html"
    rijen = [
        [
            p["datum_tekst"],
            p["orgaan"],
            p["categorie"],
            f"<a href='{p['url']}' target='_blank'>{p['titel']}</a>",
            agendapunten_html(p.get("agendapunten", []), genummerd=True),
        ]
        for p in publicaties
    ]
    return genereer_html_tabel(
        naam=NAAM,
        bron="bestuur.vlaamsbrabant.be",
        kolommen=["Datum", "Orgaan", "Categorie", "Titel", "Agendapunten"],
        rijen=rijen,
        output_pad=html_path,
    )


# ---------------------------------------------------------------------------
# Hoofd scrape-functie
# ---------------------------------------------------------------------------

def scrape(maanden: int = 3, output_base: str = "pdfs") -> None:
    global SESSION, _config
    output_dir = Path(output_base) / sanitize_filename(NAAM)
    output_dir.mkdir(parents=True, exist_ok=True)

    _config = ScraperConfig(base_url=BASE_URL, output_dir=output_dir)
    SESSION = create_session(_config)

    print(f"\n{'=' * 70}")
    print(f"  Naam     : {NAAM}")
    print(f"  Platform : {BASE_URL}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Publicaties ophalen (afgelopen {maanden} maanden)...")
    publicaties = haal_publicaties(maanden)
    print(f"    ✓ {len(publicaties)} publicaties gevonden")

    if not publicaties:
        print("  Geen publicaties gevonden.")
        return

    print("[2] Details ophalen...")
    for p in tqdm(publicaties, desc="Detail pagina's"):
        haal_detail(p)

    print("[3] Opslaan...")
    meta_pad = output_dir / f"{sanitize_filename(NAAM)}_metadata.json"
    meta_pad.write_text(json.dumps(publicaties, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✓ JSON: {meta_pad.name}")

    html_pad = genereer_html(publicaties, output_dir)
    print(f"    ✓ HTML: {html_pad.name}")

    totaal_aps = sum(len(p.get("agendapunten", [])) for p in publicaties)
    print(f"\n{'=' * 70}")
    print(f"  ✓ Klaar!")
    print(f"  Publicaties   : {len(publicaties)}")
    print(f"  Agendapunten  : {totaal_aps}")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper voor Provincie Vlaams-Brabant")
    parser.add_argument("--maanden", type=int, default=3, help="Aantal maanden terug (standaard 3)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap (standaard: pdfs)")
    # Standaard TUI-argumenten (worden genegeerd)
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()
    scrape(maanden=args.maanden, output_base=args.output)


if __name__ == "__main__":
    main()
