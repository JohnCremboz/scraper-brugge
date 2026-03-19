"""
scraper_echo.py — Scraper voor het ECHO-platform van Cipal Schaubroeck
Dekt 83 Vlaamse gemeenten via {gemeente}-echo.cipalschaubroeck.be/raadpleegomgeving/
en 2 gemeenten via {gemeente}-raadpleegomgeving.csecho.be/
en 4 grote steden via eigen domeinen (Antwerpen, Gent, Brugge, Hasselt).

Gebruik:
    python scraper_echo.py --gemeente aarschot
    python scraper_echo.py --gemeente aarschot --maanden 6
    python scraper_echo.py --alle
    python scraper_echo.py --alle --no-docs

Structuur van het ECHO-platform:
    /raadpleegomgeving/zittingen/lijst?month=MM&year=YYYY   → lijst van vergaderingen
    /raadpleegomgeving/zittingen/{id}                       → vergadering detail + agendapunten
    /raadpleegomgeving/zittingen/{id}/agendapunten/{id}     → gepubliceerd agendapunt detail
    /raadpleegomgeving/document/{id}                        → document download (PDF)
    /raadpleegomgeving/zittingen/{id}/besluitenlijst        → besluitenlijst pagina
    /raadpleegomgeving/zittingen/{id}/agenda                → agenda pagina

csecho.be-sites gebruiken hetzelfde platform maar zonder /raadpleegomgeving prefix
en met URL-patroon: {gemeente}-raadpleegomgeving.csecho.be
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
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

# ---------------------------------------------------------------------------
# Globale toestand (wordt geïnitialiseerd per gemeente-run)
# ---------------------------------------------------------------------------

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def _get(url: str) -> requests.Response | None:
    return robust_get(SESSION, url)


# ---------------------------------------------------------------------------
# CSV parsen — haal ECHO-gemeenten op
# ---------------------------------------------------------------------------

CSV_PATH = Path(__file__).parent / "simba-source.csv"
ECHO_PATTERN = re.compile(r"https?://([a-z0-9-]+)-echo\.cipalschaubroeck\.be", re.I)
CSECHO_PATTERN = re.compile(r"https?://([a-z0-9-]+)-raadpleegomgeving\.csecho\.be", re.I)

# Bekende "custom domain" raadpleegomgeving-sites (zelfde platform, eigen domein)
CUSTOM_RAADPLEEG_SITES = {
    "ebesluit.antwerpen.be",
    "besluitvorming.brugge.be",
    "ebesluitvorming.gent.be",
    "besluitvorming.hasselt.be",
}


def haal_echo_gemeenten() -> list[dict]:
    """
    Lees simba-source.csv en geef lijst van {naam, slug, base_url} terug
    voor alle ECHO-gemeenten, csecho.be-sites én custom-domain raadpleegomgeving-sites.
    """
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

            # ECHO subdomein van cipalschaubroeck.be
            m = ECHO_PATTERN.search(url)
            if m:
                slug = m.group(1)
                base_url = f"https://{slug}-echo.cipalschaubroeck.be/raadpleegomgeving"
                resultaat.append({"naam": gemeente, "slug": slug, "base_url": base_url})
                continue

            # csecho.be variant (zelfde platform, geen /raadpleegomgeving prefix)
            m2 = CSECHO_PATTERN.search(url)
            if m2:
                slug = m2.group(1)
                base_url = f"https://{slug}-raadpleegomgeving.csecho.be"
                resultaat.append({"naam": gemeente, "slug": slug, "base_url": base_url})
                continue

            # Custom-domain raadpleegomgeving (Antwerpen, Gent, Brugge, Hasselt, ...)
            parsed = urlparse(url)
            if parsed.netloc in CUSTOM_RAADPLEEG_SITES:
                base_url = f"{parsed.scheme}://{parsed.netloc}"
                resultaat.append({"naam": gemeente, "slug": parsed.netloc.split(".")[0], "base_url": base_url})

    return resultaat


# ---------------------------------------------------------------------------
# Vergaderingen ophalen
# ---------------------------------------------------------------------------

def haal_vergaderingen(base_url: str, maanden: int = 3) -> list[dict]:
    """
    Haal vergaderingen op voor de afgelopen `maanden` maanden.
    Returns lijst van {id, titel, datum, locatie, url}.
    """
    # Bepaal de origin (scheme + host) voor absolute URL-constructie
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    vergaderingen = []
    gezien_ids: set[str] = set()
    vandaag = date.today()

    for offset in range(maanden):
        jaar = vandaag.year
        maand = vandaag.month - offset
        while maand <= 0:
            maand += 12
            jaar -= 1

        url = f"{base_url}/zittingen/lijst?month={maand:02d}&year={jaar}"
        resp = _get(url)
        if resp is None:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Filter zitting-detail links: UUID of numeriek ID (bijv. 25.1230.6513.7109)
            if not re.search(r"/zittingen/([0-9a-f-]{36}|\d{2}\.\d{4}\.\d{4}\.\d{4})$", href):
                continue
            zitting_id = href.rstrip("/").split("/")[-1]
            if zitting_id in gezien_ids:
                continue
            gezien_ids.add(zitting_id)

            full_url = urljoin(origin + "/", href.lstrip("/"))
            tekst = a.get_text(" ", strip=True)

            # Haal datum en titel uit tekst (formaat: "GemeenteraadThu 08/01/2026 - 20:00...")
            datum_m = re.search(r"(\d{2}/\d{2}/\d{4})", tekst)
            datum_str = datum_m.group(1) if datum_m else ""
            # Titel = alles voor de datum
            titel = tekst.split(datum_str)[0].strip() if datum_str else tekst[:60]

            vergaderingen.append({
                "id": zitting_id,
                "titel": titel,
                "datum": datum_str,
                "url": full_url,
            })

    return vergaderingen


# ---------------------------------------------------------------------------
# Vergadering detail: agendapunten + documenten
# ---------------------------------------------------------------------------

def haal_vergadering_details(vergadering: dict, base_url: str) -> dict:
    """
    Haal agendapunten en publicatie-documenten op voor een vergadering.
    Voegt 'agendapunten' en 'documenten' toe aan het vergadering-dict.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    resp = _get(vergadering["url"])
    if resp is None:
        vergadering["agendapunten"] = []
        vergadering["documenten"] = []
        return vergadering

    soup = BeautifulSoup(resp.text, "lxml")

    # --- Agendapunten ---
    agendapunten = []
    for li in soup.find_all("li", class_="agendapage"):
        ap_id = li.get("data-meeting-item-id", "")
        item_div = li.find("div", class_="item")
        if not item_div:
            continue

        titel_tag = item_div.find("a", class_="title")
        if not titel_tag:
            continue

        titel = titel_tag.get_text(strip=True)
        ap_url = titel_tag.get("href")  # alleen bij gepubliceerde items
        if ap_url:
            ap_url = urljoin(origin + "/", ap_url.lstrip("/"))

        gepubliceerd = "not-published" not in item_div.get("class", [])

        samenvatting_div = li.find("div", class_="summary")
        samenvatting = samenvatting_div.get_text(strip=True) if samenvatting_div else ""

        agendapunten.append({
            "id": ap_id,
            "titel": titel,
            "url": ap_url,
            "gepubliceerd": gepubliceerd,
            "samenvatting": samenvatting,
        })

    # --- Publicatie-documenten (Agenda, Besluitenlijst, ...) ---
    documenten = []
    for pub_li in soup.find_all("li", class_="publication"):
        if "document" not in pub_li.get("class", []):
            continue
        a = pub_li.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if "/document/" not in href:
            continue
        doc_naam = a.get_text(strip=True) or "document"
        doc_url = urljoin(origin + "/", href.lstrip("/"))
        # Bepaal publicatiegroep (Agenda / Besluitenlijst / ...)
        groep_div = pub_li.find_parent("div", class_="publication-group")
        groep = ""
        if groep_div:
            gn = groep_div.find("div", class_="group-name")
            if gn:
                groep = gn.get_text(strip=True)

        documenten.append({
            "naam": doc_naam,
            "url": doc_url,
            "groep": groep,
            "local_file": None,
        })

    vergadering["agendapunten"] = agendapunten
    vergadering["documenten"] = documenten
    return vergadering


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

def genereer_html(metadata: dict, output_path: Path) -> None:
    gemeente = metadata["gemeente"]
    datum = metadata["datum"]
    vergaderingen = metadata.get("vergaderingen", [])
    totaal_aps = sum(len(v.get("agendapunten", [])) for v in vergaderingen)
    totaal_docs = sum(len(v.get("documenten", [])) for v in vergaderingen)

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{gemeente} — ECHO Raadpleegomgeving</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f7fa; color: #333; line-height: 1.6; }}
        .container {{ max-width: 1000px; margin: 0 auto; padding: 20px; }}
        h1 {{ font-size: 1.8em; color: #1a252f; margin-bottom: 5px; }}
        h1 small {{ font-size: 0.55em; color: #7f8c8d; font-weight: normal; margin-left: 10px; }}
        .meta-info {{ display: flex; flex-wrap: wrap; gap: 15px; background: #fff; border-radius: 8px; padding: 15px 20px; margin: 15px 0 25px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
        .meta-item {{ display: flex; flex-direction: column; }}
        .meta-label {{ font-size: 0.75em; color: #7f8c8d; text-transform: uppercase; letter-spacing: 0.05em; }}
        .meta-value {{ font-size: 1.1em; font-weight: 600; color: #2c3e50; }}
        .vergadering {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 20px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
        .vergadering-header {{ background: #2c3e50; color: #fff; padding: 14px 20px; display: flex; align-items: center; gap: 12px; }}
        .vergadering-header h2 {{ font-size: 1.05em; font-weight: 600; }}
        .vergadering-meta {{ font-size: 0.85em; opacity: 0.8; margin-top: 2px; }}
        .badge {{ background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 20px; font-size: 0.8em; margin-left: auto; white-space: nowrap; }}
        .vergadering-body {{ padding: 0; }}
        .agendapunten-lijst {{ list-style: none; border-top: 1px solid #f0f0f0; }}
        .agendapunt {{ padding: 12px 20px; border-bottom: 1px solid #f5f5f5; display: flex; align-items: flex-start; gap: 10px; }}
        .agendapunt:last-child {{ border-bottom: none; }}
        .agendapunt.niet-gepubliceerd {{ opacity: 0.6; }}
        .ap-icon {{ font-size: 1.1em; margin-top: 1px; flex-shrink: 0; }}
        .ap-content {{ flex: 1; }}
        .ap-titel {{ font-size: 0.95em; color: #2c3e50; }}
        .ap-titel a {{ color: #2c3e50; text-decoration: none; }}
        .ap-titel a:hover {{ color: #3498db; }}
        .ap-samenvatting {{ font-size: 0.85em; color: #7f8c8d; margin-top: 4px; line-height: 1.4; }}
        .publicaties {{ padding: 14px 20px; background: #f8f9fa; border-top: 2px solid #e8e8e8; }}
        .publicaties-label {{ font-size: 0.75em; color: #7f8c8d; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; font-weight: 600; }}
        .doc-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .doc-link {{ display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px; background: #e8f4fd; color: #2980b9; border: 1px solid #bee3f8; border-radius: 20px; text-decoration: none; font-size: 0.82em; transition: all 0.2s; }}
        .doc-link:hover {{ background: #2980b9; color: #fff; border-color: #2980b9; }}
        .doc-link .groep {{ font-size: 0.85em; opacity: 0.7; }}
        .leeg {{ padding: 20px; text-align: center; color: #95a5a6; font-size: 0.9em; }}
        .footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #e0e0e0; text-align: center; color: #95a5a6; font-size: 0.85em; }}
        .footer a {{ color: #95a5a6; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🏛️ {gemeente}<small>ECHO Raadpleegomgeving</small></h1>
    <div class="meta-info">
        <div class="meta-item"><span class="meta-label">Gemeente</span><span class="meta-value">{gemeente}</span></div>
        <div class="meta-item"><span class="meta-label">Datum scraping</span><span class="meta-value">{datum}</span></div>
        <div class="meta-item"><span class="meta-label">Vergaderingen</span><span class="meta-value">{len(vergaderingen)}</span></div>
        <div class="meta-item"><span class="meta-label">Agendapunten</span><span class="meta-value">{totaal_aps}</span></div>
        <div class="meta-item"><span class="meta-label">Documenten</span><span class="meta-value">{totaal_docs}</span></div>
    </div>
"""

    if not vergaderingen:
        html += '<div class="leeg">Geen vergaderingen gevonden.</div>\n'
    else:
        for v in vergaderingen:
            aps = v.get("agendapunten", [])
            docs = v.get("documenten", [])
            html += f"""
    <div class="vergadering">
        <div class="vergadering-header">
            <div>
                <h2><a href="{v['url']}" target="_blank" style="color:inherit;text-decoration:none">{v['titel']}</a></h2>
                <div class="vergadering-meta">📅 {v['datum']}</div>
            </div>
            <span class="badge">{len(aps)} agendapunten</span>
        </div>
        <div class="vergadering-body">
"""
            if aps:
                html += '        <ul class="agendapunten-lijst">\n'
                for ap in aps:
                    klasse = "" if ap["gepubliceerd"] else " niet-gepubliceerd"
                    icon = "📄" if ap["gepubliceerd"] else "🔒"
                    if ap.get("url"):
                        titel_html = f'<a href="{ap["url"]}" target="_blank">{ap["titel"]}</a>'
                    else:
                        titel_html = ap["titel"]
                    html += f"""            <li class="agendapunt{klasse}">
                <span class="ap-icon">{icon}</span>
                <div class="ap-content">
                    <div class="ap-titel">{titel_html}</div>
"""
                    if ap.get("samenvatting"):
                        html += f'                    <div class="ap-samenvatting">{ap["samenvatting"]}</div>\n'
                    html += "                </div>\n            </li>\n"
                html += "        </ul>\n"

            if docs:
                html += '        <div class="publicaties">\n'
                html += '            <div class="publicaties-label">Publicaties</div>\n'
                html += '            <div class="doc-links">\n'
                for doc in docs:
                    if doc.get("local_file"):
                        href = doc["local_file"]
                        target = ""
                    else:
                        href = doc["url"]
                        target = ' target="_blank"'
                    groep_span = f' <span class="groep">({doc["groep"]})</span>' if doc.get("groep") else ""
                    html += f'                <a class="doc-link" href="{href}"{target}>📄 {doc["naam"]}{groep_span}</a>\n'
                html += "            </div>\n        </div>\n"

            html += "        </div>\n    </div>\n"

    html += f"""
    <div class="footer">
        <p>Gegenereerd op {datum} • Bron: ECHO Raadpleegomgeving (Cipal Schaubroeck)</p>
    </div>
</div>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Hoofdlogica
# ---------------------------------------------------------------------------

def scrape_gemeente(
    gemeente_info: dict,
    output_dir: Path,
    maanden: int = 3,
    download_docs: bool = True,
) -> tuple[int, int]:
    """
    Scrape één ECHO-gemeente. Returns (aantal_vergaderingen, aantal_documenten).
    """
    global SESSION, _config

    naam = gemeente_info["naam"]
    base_url = gemeente_info["base_url"]

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  Gemeente : {naam}")
    print(f"  Platform : {base_url}")
    print(f"  Output   : {output_dir}")
    print(f"{'='*70}\n")

    # Initialiseer sessie voor deze gemeente
    _config = ScraperConfig(
        base_url=base_url,
        output_dir=output_dir,
    )
    SESSION = create_session(_config)

    # 1. Vergaderingen ophalen
    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = haal_vergaderingen(base_url, maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("    [!] Geen vergaderingen gevonden, stop.")
        return 0, 0

    # 2. Details ophalen (agendapunten + doc-links)
    print(f"\n[2] Vergadering-details ophalen...")
    for v in tqdm(vergaderingen, desc="Vergaderingen verwerken"):
        haal_vergadering_details(v, base_url)

    totaal_aps = sum(len(v.get("agendapunten", [])) for v in vergaderingen)
    totaal_doc_links = sum(len(v.get("documenten", [])) for v in vergaderingen)
    print(f"    ✓ {totaal_aps} agendapunten, {totaal_doc_links} document-links")

    # 3. Documenten downloaden
    doc_count = 0
    if download_docs and totaal_doc_links > 0:
        print(f"\n[3] Documenten downloaden...")
        for v in vergaderingen:
            for doc in v.get("documenten", []):
                result = download_document(
                    SESSION,
                    _config,
                    doc["url"],
                    output_dir,
                    doc["naam"],
                    require_pdf=False,
                )
                if result.success:
                    doc["local_file"] = result.path.name
                    if not result.skipped:
                        doc_count += 1
                        print(f"    → {result.path.name}")
        print(f"    ✓ {doc_count} documenten gedownload")

    # 4. Metadata opslaan
    print(f"\n[4] Metadata opslaan...")
    metadata = {
        "gemeente": naam,
        "datum": date.today().isoformat(),
        "platform": "echo.cipalschaubroeck.be",
        "base_url": base_url,
        "aantal_vergaderingen": len(vergaderingen),
        "vergaderingen": vergaderingen,
    }

    json_file = output_dir / f"{sanitize_filename(naam)}_metadata.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"    ✓ JSON: {json_file.name}")

    html_file = output_dir / f"{sanitize_filename(naam)}.html"
    genereer_html(metadata, html_file)
    print(f"    ✓ HTML: {html_file.name}")

    return len(vergaderingen), doc_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor ECHO (Cipal Schaubroeck) gemeenten"
    )
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument("--gemeente", help="Naam van de gemeente (zoals in CSV)")
    groep.add_argument("--alle", action="store_true", help="Scrape alle ECHO-gemeenten")
    parser.add_argument("--maanden", type=int, default=3, help="Aantal maanden terug (standaard: 3)")
    parser.add_argument("--no-docs", action="store_true", help="Geen documenten downloaden")
    parser.add_argument("--output", default="pdfs", help="Output map (standaard: pdfs)")
    args = parser.parse_args()

    download_docs = not args.no_docs
    output_root = Path(args.output)
    gemeenten = haal_echo_gemeenten()

    if args.gemeente:
        # Zoek op naam (case-insensitief)
        naam_lower = args.gemeente.lower()
        matches = [g for g in gemeenten if g["naam"].lower() == naam_lower or g["slug"].lower() == naam_lower]
        if not matches:
            # Probeer gedeeltelijke match
            matches = [g for g in gemeenten if naam_lower in g["naam"].lower() or naam_lower in g["slug"].lower()]
        if not matches:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden in ECHO-lijst.")
            print(f"    Beschikbaar: {', '.join(g['naam'] for g in gemeenten[:10])}...")
            sys.exit(1)
        te_scrapen = matches[:1]
    else:
        te_scrapen = gemeenten

    print(f"ECHO-scraper — {len(te_scrapen)} gemeente(n) te verwerken")
    print(f"Periode: afgelopen {args.maanden} maanden\n")

    totaal_vergaderingen = 0
    totaal_docs = 0
    fouten = []

    for i, g in enumerate(te_scrapen, 1):
        if len(te_scrapen) > 1:
            print(f"\n[{i}/{len(te_scrapen)}] {g['naam']}")
        output_dir = output_root / sanitize_filename(g["naam"])
        try:
            v, d = scrape_gemeente(g, output_dir, args.maanden, download_docs)
            totaal_vergaderingen += v
            totaal_docs += d
        except KeyboardInterrupt:
            print("\n[!] Onderbroken door gebruiker.")
            break
        except Exception as exc:
            print(f"    [!] Fout bij {g['naam']}: {exc}")
            fouten.append(g["naam"])

    print(f"\n{'='*70}")
    print(f"  ✓ Klaar!")
    print(f"  Gemeenten verwerkt : {len(te_scrapen) - len(fouten)}")
    print(f"  Vergaderingen      : {totaal_vergaderingen}")
    print(f"  Documenten         : {totaal_docs}")
    if fouten:
        print(f"  Fouten ({len(fouten)})        : {', '.join(fouten)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
