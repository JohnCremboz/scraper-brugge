"""
scraper_ibabs.py — Scraper voor het iBabs Publieksportaal (bestuurlijkeinformatie.nl)
Dekt Kalmthout en Stabroek.

Gebruik:
    python scraper_ibabs.py --gemeente kalmthout
    python scraper_ibabs.py --alle

Platform structuur:
    /Calendar                          → lijst van categorieën per orgaan
    /Calendar/OpenCategory/{id}        → redirect naar meest recente vergadering
    /Agenda/Index/{uuid}               → vergadering detail + sidebar met jaar-overzicht
    /Agenda/Document/{uuid}?documentId={doc-uuid} → bijlage download
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document,
    robust_get,
    sanitize_filename,
)

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def _get(url: str) -> requests.Response | None:
    return robust_get(SESSION, url)


# ---------------------------------------------------------------------------
# CSV parsen
# ---------------------------------------------------------------------------

CSV_PATH = Path(__file__).parent / "simba-source.csv"
IBABS_PATTERN = re.compile(r"https?://([a-z0-9-]+)\.bestuurlijkeinformatie\.nl", re.I)

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def haal_ibabs_gemeenten() -> list[dict]:
    resultaat = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for regel in f:
            regel = regel.strip()
            if not regel or regel.startswith("Gemeente"):
                continue
            delen = regel.split(";", 1)
            if len(delen) < 2:
                continue
            gemeente, url = delen[0].strip(), delen[1].strip()
            m = IBABS_PATTERN.search(url)
            if m:
                slug = m.group(1)
                base_url = f"https://{slug}.bestuurlijkeinformatie.nl"
                resultaat.append({"naam": gemeente, "slug": slug, "base_url": base_url})
    return resultaat


# ---------------------------------------------------------------------------
# Categorieën en vergaderingen ophalen
# ---------------------------------------------------------------------------

# Relevante categorienamen (case-insensitive substrings)
RELEVANTE_CATEGORIEEN = [
    "gemeenteraad", "raad voor maatschappelijk welzijn", "besluitenlijst",
    "verslag", "agenda", "vast bureau", "college",
]


def _is_relevant(naam: str) -> bool:
    naam_l = naam.lower()
    return any(cat in naam_l for cat in RELEVANTE_CATEGORIEEN)


def haal_vergaderingen(base_url: str, maanden: int = 3) -> list[dict]:
    """
    Haal vergaderingen op via de Calendar-pagina.
    Stap 1: haal alle categorieën op.
    Stap 2: per categorie, haal de meest recente vergadering op.
    Stap 3: van die vergadering, lees de sidebar om alle vergaderingen van dat jaar te vinden.
    """
    vergaderingen = []
    gezien_uuids: set[str] = set()
    vandaag = date.today()
    cutoff = date(vandaag.year, max(1, vandaag.month - maanden + 1), 1)

    resp = _get(f"{base_url}/Calendar")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cat_links = [
        a["href"] for a in soup.find_all("a", href=True)
        if "/Calendar/OpenCategory/" in a["href"] and _is_relevant(a.get_text(strip=True))
    ]

    for cat_href in cat_links:
        cat_url = urljoin(base_url, cat_href)
        cat_resp = _get(cat_url)
        if cat_resp is None:
            continue

        cat_soup = BeautifulSoup(cat_resp.text, "lxml")
        # De categoriepagina toont de meest recente vergadering (of redirect ernaar)
        # Verzamel alle Agenda/Index links op deze pagina (inclusief sidebar)
        agenda_links = [
            a["href"] for a in cat_soup.find_all("a", href=True)
            if re.search(r"/Agenda/Index/[0-9a-f-]{36}", a["href"])
        ]

        for href in agenda_links:
            uuid = href.rstrip("/").split("/")[-1]
            if uuid in gezien_uuids:
                continue
            gezien_uuids.add(uuid)

            # Haal de vergaderingspagina op voor datum + agendapunten
            verg_url = urljoin(base_url, href)
            verg_resp = _get(verg_url)
            if verg_resp is None:
                continue

            verg_soup = BeautifulSoup(verg_resp.text, "lxml")
            # Datum parsing vanuit de title: "... maandag 26 januari 2026 20:00 ..."
            title = verg_soup.title.string if verg_soup.title else ""
            datum = _parseer_datum(title)
            if datum is None:
                continue
            if datum < cutoff:
                continue

            # Haal ook alle verdere UUIDs op uit de sidebar van deze vergadering
            extra_links = [
                a["href"] for a in verg_soup.find_all("a", href=True)
                if re.search(r"/Agenda/Index/[0-9a-f-]{36}", a["href"])
            ]
            for ex_href in extra_links:
                ex_uuid = ex_href.rstrip("/").split("/")[-1]
                gezien_uuids.add(ex_uuid)  # markeer als gezien, maar niet opnieuw fetchen

            # Titel en orgaan uit de h1/header
            orgaan = _haal_orgaan(verg_soup)
            vergaderingen.append({
                "uuid": uuid,
                "titel": orgaan,
                "datum": datum.strftime("%d/%m/%Y"),
                "url": verg_url,
                "soup": verg_soup,
            })

    return vergaderingen


def _parseer_datum(tekst: str) -> date | None:
    m = re.search(
        r"(\d{1,2})\s+(" + "|".join(DUTCH_MONTHS.keys()) + r")\s+(\d{4})",
        tekst, re.I,
    )
    if not m:
        return None
    dag, maand_str, jaar = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    maand = DUTCH_MONTHS.get(maand_str, 0)
    if not maand:
        return None
    try:
        return date(jaar, maand, dag)
    except ValueError:
        return None


def _haal_orgaan(soup: BeautifulSoup) -> str:
    # De h2 of main heading bevat orgaan + datum
    for tag in ["h1", "h2", "h3"]:
        el = soup.find(tag)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text[:80]
    return "Vergadering"


# ---------------------------------------------------------------------------
# Vergadering details: agendapunten + bijlagen
# ---------------------------------------------------------------------------

def haal_vergadering_details(vergadering: dict, base_url: str) -> dict:
    soup = vergadering.pop("soup", None)
    if soup is None:
        resp = _get(vergadering["url"])
        if resp is None:
            vergadering["agendapunten"] = []
            vergadering["documenten"] = []
            return vergadering
        soup = BeautifulSoup(resp.text, "lxml")

    # --- Agendapunten ---
    agendapunten = []
    # Zoek de agendapunten sectie (meestal na de "Agendapunten" header)
    ap_sectie = soup.find(string=re.compile(r"Agendapunten", re.I))
    if ap_sectie:
        container = ap_sectie.find_parent()
        while container and container.name not in ["div", "section", "ul", "ol", "table"]:
            container = container.find_parent()
        if container:
            for item in container.find_all(["li", "tr", "div"], recursive=False):
                tekst = item.get_text(" ", strip=True)
                if tekst and len(tekst) > 3:
                    agendapunten.append({"titel": tekst[:300]})

    # Fallback: zoek alle tekstblokken na "Agendapunten"
    if not agendapunten:
        tekst_blokken = soup.get_text("\n").split("\n")
        in_agenda = False
        for lijn in tekst_blokken:
            lijn = lijn.strip()
            if not lijn:
                continue
            if re.match(r"Agendapunten", lijn, re.I):
                in_agenda = True
                continue
            if in_agenda and re.match(r"Bijlagen|iBabs|Inloggen", lijn, re.I):
                break
            if in_agenda and len(lijn) > 5:
                agendapunten.append({"titel": lijn[:300]})

    # --- Bijlagen (documenten) ---
    # Link-href: /Agenda/Document/{meetingId}?documentId={docId}&agendaItemId={itemId}
    # Download-URL: /Document/LoadAgendaItemDocument/{docId}?agendaItemId={itemId}
    documenten = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/Agenda/Document/" not in href:
            continue
        doc_naam = a.get_text(strip=True)
        if not doc_naam:
            continue
        # Extraheer query parameters
        from urllib.parse import parse_qs, urlparse as _up
        parsed_href = _up(href)
        qs = parse_qs(parsed_href.query)
        doc_id = qs.get("documentId", [None])[0]
        item_id = qs.get("agendaItemId", [None])[0]
        if doc_id and item_id:
            download_url = f"{base_url}/Document/LoadAgendaItemDocument/{doc_id}?agendaItemId={item_id}"
        else:
            download_url = urljoin(base_url, href)
        documenten.append({
            "naam": doc_naam,
            "url": download_url,
            "local_file": None,
        })

    vergadering["agendapunten"] = agendapunten
    vergadering["documenten"] = documenten
    return vergadering


# ---------------------------------------------------------------------------
# HTML genereren
# ---------------------------------------------------------------------------

def genereer_html(gemeente_naam: str, vergaderingen: list[dict], output_dir: Path) -> Path:
    html_path = output_dir.parent / f"{sanitize_filename(gemeente_naam)}.html"

    rijen = []
    for v in vergaderingen:
        ap_html = ""
        if v.get("agendapunten"):
            items = "".join(f"<li>{ap['titel']}</li>" for ap in v["agendapunten"])
            ap_html = f"<ul class='agendapunten'>{items}</ul>"

        doc_html = ""
        if v.get("documenten"):
            badges = []
            for doc in v["documenten"]:
                if doc.get("local_file"):
                    rel = Path(doc["local_file"]).relative_to(output_dir.parent)
                    badges.append(f"<a class='doc-link' href='{rel}'>{doc['naam']}</a>")
                else:
                    badges.append(
                        f"<a class='doc-link' href='{doc['url']}' target='_blank'>{doc['naam']}</a>"
                    )
            doc_html = f"<div class='documenten'>{''.join(badges)}</div>"

        rijen.append(f"""
        <tr>
            <td>{v['datum']}</td>
            <td>{v['titel']}</td>
            <td>{ap_html}</td>
            <td>{doc_html}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>Vergaderingen {gemeente_naam} – iBabs</title>
<style>
  body {{ font-family: sans-serif; margin: 2rem; }}
  h1 {{ color: #003366; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: .5rem .75rem; vertical-align: top; }}
  th {{ background: #003366; color: white; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  .agendapunten {{ margin: 0; padding-left: 1.2rem; font-size: .85rem; }}
  .documenten {{ display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .4rem; }}
  .doc-link {{ background: #e8f0fe; border: 1px solid #4285f4; border-radius: 3px;
               padding: 2px 6px; font-size: .8rem; text-decoration: none; color: #1a0dab; }}
  .doc-link:hover {{ background: #d2e3fc; }}
</style>
</head>
<body>
<h1>Vergaderingen {gemeente_naam}</h1>
<p>Bron: iBabs Publieksportaal — {len(vergaderingen)} vergadering(en)</p>
<table>
  <thead><tr><th>Datum</th><th>Orgaan</th><th>Agendapunten</th><th>Documenten</th></tr></thead>
  <tbody>{''.join(rijen)}</tbody>
</table>
</body>
</html>"""

    html_path.write_text(html, encoding="utf-8")
    return html_path


# ---------------------------------------------------------------------------
# Hoofd scrape-functie
# ---------------------------------------------------------------------------

def scrape_gemeente(gemeente: dict, maanden: int = 3, docs: bool = True, output_base: str = "pdfs") -> None:
    global SESSION, _config
    naam = gemeente["naam"]
    base_url = gemeente["base_url"]
    output_dir = Path(output_base) / sanitize_filename(naam)
    output_dir.mkdir(parents=True, exist_ok=True)

    _config = ScraperConfig(base_url=base_url, output_dir=output_dir)
    SESSION = create_session(_config)

    print(f"\n{'=' * 70}")
    print(f"  Gemeente : {naam}")
    print(f"  Platform : {base_url}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = haal_vergaderingen(base_url, maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("  Geen vergaderingen gevonden.")
        return

    print("[2] Vergadering-details ophalen...")
    for v in tqdm(vergaderingen, desc="Details verwerken"):
        haal_vergadering_details(v, base_url)

    n_docs = sum(len(v.get("documenten", [])) for v in vergaderingen)
    gedownload = 0

    if docs and n_docs > 0:
        print(f"[3] Documenten downloaden ({n_docs} totaal)...")
        for v in tqdm(vergaderingen, desc="Documenten downloaden"):
            for doc in v.get("documenten", []):
                local = download_document(
                        SESSION,
                        _config,
                        doc["url"],
                        output_dir,
                        doc["naam"],
                    )
                if local and local.success:
                    doc["local_file"] = str(local.path)
                    gedownload += 1
    else:
        print(f"[3] Documenten overgeslagen ({n_docs} beschikbaar).")

    print("[4] Metadata opslaan...")
    meta_pad = output_dir / f"{sanitize_filename(naam)}_metadata.json"
    exporteerbaar = [{k: v for k, v in verg.items() if k != "soup"} for verg in vergaderingen]
    meta_pad.write_text(json.dumps(exporteerbaar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✓ JSON: {meta_pad.name}")

    html_pad = genereer_html(naam, vergaderingen, output_dir)
    print(f"    ✓ HTML: {html_pad.name}")

    print(f"\n{'=' * 70}")
    print(f"  ✓ Klaar!")
    print(f"  Vergaderingen      : {len(vergaderingen)}")
    print(f"  Documenten         : {gedownload}/{n_docs}")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="iBabs scraper (bestuurlijkeinformatie.nl)")
    parser.add_argument("--gemeente", help="Naam van gemeente (fuzzy match)")
    parser.add_argument("--alle", action="store_true", help="Verwerk alle iBabs-gemeenten")
    parser.add_argument("--maanden", type=int, default=3, help="Aantal maanden terug (standaard 3)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--no-docs", action="store_true", help="Geen documenten downloaden")
    # Standaard TUI-argumenten (worden genegeerd)
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()

    gemeenten = haal_ibabs_gemeenten()

    if args.alle:
        doellijst = gemeenten
    elif args.gemeente:
        zoek = args.gemeente.lower()
        doellijst = [g for g in gemeenten if zoek in g["naam"].lower() or zoek in g["slug"].lower()]
        if not doellijst:
            print(f"Gemeente '{args.gemeente}' niet gevonden. Beschikbaar:")
            for g in gemeenten:
                print(f"  {g['naam']} ({g['slug']})")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)

    print(f"iBabs-scraper — {len(doellijst)} gemeente(n) te verwerken")
    print(f"Periode: afgelopen {args.maanden} maanden")

    for g in doellijst:
        scrape_gemeente(g, maanden=args.maanden, docs=not args.no_docs, output_base=args.output)


if __name__ == "__main__":
    main()
