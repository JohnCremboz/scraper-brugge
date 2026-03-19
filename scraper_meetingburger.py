"""
scraper_meetingburger.py — Scraper voor het meetingburger.net platform
Dekt 41 Vlaamse gemeenten via {gemeente}.meetingburger.net

Gebruik:
    python scraper_meetingburger.py --gemeente alken
    python scraper_meetingburger.py --gemeente alken --maanden 6
    python scraper_meetingburger.py --alle

Structuur van het platform:
    /?AlleVergaderingen=True              → lijst van alle vergaderingen
    /{type}/{uuid}                        → vergadering detail met agendapunten
    /{type}/{uuid}/agenda                 → agenda (HTML)
    /{type}/{uuid}/besluitenlijst         → besluitenlijst (HTML)
    /{type}/{uuid}/notulen                → notulen (HTML)
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

from base_scraper import ScraperConfig, create_session, sanitize_filename

# ---------------------------------------------------------------------------
# Maandnamen NL → nummer
# ---------------------------------------------------------------------------

MAANDEN_NL = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

# ---------------------------------------------------------------------------
# Globale sessie
# ---------------------------------------------------------------------------

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def _get(url: str, retries: int = 3) -> requests.Response | None:
    for poging in range(retries):
        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if poging == retries - 1:
                print(f"    [!] Fout bij {url}: {exc}")
                return None
            time.sleep(1.5 * (poging + 1))
    return None


# ---------------------------------------------------------------------------
# CSV parsen
# ---------------------------------------------------------------------------

CSV_PATH = Path(__file__).parent / "simba-source.csv"
MB_PATTERN = re.compile(r"https?://([a-z0-9-]+)\.meetingburger\.net", re.I)


def haal_mb_gemeenten() -> list[dict]:
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
            m = MB_PATTERN.search(url)
            if m:
                slug = m.group(1)
                base_url = f"https://{slug}.meetingburger.net"
                resultaat.append({"naam": gemeente, "slug": slug, "base_url": base_url})
    return resultaat


# ---------------------------------------------------------------------------
# Datum parsen uit vergaderingstitel
# ---------------------------------------------------------------------------

DATE_RE = re.compile(
    r"(\d{1,2})\s+("
    + "|".join(MAANDEN_NL.keys())
    + r")\s+(\d{4})",
    re.I,
)


def parse_datum(tekst: str) -> date | None:
    m = DATE_RE.search(tekst.lower())
    if not m:
        return None
    try:
        dag = int(m.group(1))
        maand = MAANDEN_NL[m.group(2)]
        jaar = int(m.group(3))
        return date(jaar, maand, dag)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Vergaderingen ophalen van homepage
# ---------------------------------------------------------------------------

def haal_vergaderingen(base_url: str, maanden: int = 3) -> list[dict]:
    """
    Haal vergaderingen op van de homepage.
    Filtert op de afgelopen `maanden` maanden.
    """
    grens = date.today() - timedelta(days=maanden * 31)

    resp = _get(f"{base_url}/?AlleVergaderingen=True")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    vergaderingen = []

    for item in soup.find_all("div", class_="meetingsListItem"):
        # Vergadering link en titel
        meeting_div = item.find("div", class_="meeting")
        if not meeting_div:
            continue
        a = meeting_div.find("a", href=True)
        if not a:
            continue

        url = a["href"]
        titel = a.get_text(strip=True)

        # Datum filteren
        datum = parse_datum(titel)
        if datum and datum < grens:
            continue

        # Type uit URL pad (/gr/, /rmw/, /vabu/, /sc/, /bb/, ...)
        url_parts = urlparse(url).path.strip("/").split("/")
        verg_type = url_parts[0].upper() if url_parts else "?"

        # Links naar publicaties
        agenda_link = None
        bl_link = None
        notulen_link = None

        agenda_div = item.find("div", class_="meetingAgenda")
        if agenda_div:
            al = agenda_div.find("a", href=True)
            if al:
                agenda_link = {
                    "url": al["href"],
                    "label": al.get_text(strip=True),
                    "datum": al.get("title", ""),
                }

        bl_div = item.find("div", class_="meetingBesluitenlijst")
        if bl_div:
            bl = bl_div.find("a", href=True)
            if bl:
                bl_link = {
                    "url": bl["href"],
                    "label": bl.get_text(strip=True),
                    "datum": bl.get("title", ""),
                }

        not_div = item.find("div", class_="meetingNotulen")
        if not_div:
            nl = not_div.find("a", href=True)
            if nl:
                notulen_link = {
                    "url": nl["href"],
                    "label": nl.get_text(strip=True),
                    "datum": nl.get("title", ""),
                }

        vergaderingen.append({
            "titel": titel,
            "type": verg_type,
            "datum": datum.isoformat() if datum else "",
            "url": url,
            "agenda": agenda_link,
            "besluitenlijst": bl_link,
            "notulen": notulen_link,
        })

    return vergaderingen


# ---------------------------------------------------------------------------
# Agendapunten ophalen van vergadering detail pagina
# ---------------------------------------------------------------------------

def haal_agendapunten(vergadering: dict) -> list[dict]:
    """
    Haal agendapunten op van de vergadering detail pagina.
    """
    resp = _get(vergadering["url"])
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    content = soup.find("div", class_="content")
    if not content:
        return []

    agendapunten = []

    # Agendapunten staan als <div class="topic"> met een anchor id
    topics = content.find_all("div", class_="topic")
    if topics:
        for topic in topics:
            ap_id = topic.get("id", "")
            # Titel: eerste heading of eerste vette tekst
            heading = topic.find(["h1", "h2", "h3", "h4", "h5", "b", "strong"])
            titel = heading.get_text(strip=True) if heading else topic.get_text(strip=True)[:100]
            # Samenvatting: tekst zonder de titel
            if heading:
                heading.extract()
            samenvatting = topic.get_text(" ", strip=True)[:300]
            agendapunten.append({
                "id": ap_id,
                "titel": titel,
                "samenvatting": samenvatting,
            })
    else:
        # Fallback: topicslist (de inhoudsopgave)
        topicslist = content.find("ul", class_="topicslist")
        if topicslist:
            for li in topicslist.find_all("li"):
                a = li.find("a")
                if a:
                    agendapunten.append({
                        "id": a.get("href", "").lstrip("#"),
                        "titel": a.get_text(strip=True),
                        "samenvatting": "",
                    })

    return agendapunten


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

TYPE_LABELS = {
    "GR": "Gemeenteraad",
    "RMW": "Raad voor Maatschappelijk Welzijn",
    "VABU": "Vast Bureau",
    "CBS": "College van Burgemeester en Schepenen",
    "SC": "Schepencollege",
    "BB": "Bijzonder Comité voor de Sociale Dienst",
    "OC": "Onderhandelingscomité",
}


def type_label(verg_type: str) -> str:
    return TYPE_LABELS.get(verg_type.upper(), verg_type)


def genereer_html(metadata: dict, output_path: Path) -> None:
    gemeente = metadata["gemeente"]
    datum = metadata["datum"]
    vergaderingen = metadata.get("vergaderingen", [])
    totaal_aps = sum(len(v.get("agendapunten", [])) for v in vergaderingen)

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{gemeente} — meetingburger</title>
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
        .vergadering {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 18px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
        .verg-header {{ padding: 14px 20px; display: flex; align-items: center; gap: 12px; background: #2c3e50; color: #fff; }}
        .verg-type {{ background: rgba(255,255,255,0.15); padding: 3px 10px; border-radius: 12px; font-size: 0.78em; font-weight: 600; white-space: nowrap; }}
        .verg-titel {{ font-size: 1.05em; font-weight: 600; flex: 1; }}
        .verg-titel a {{ color: inherit; text-decoration: none; }}
        .verg-datum {{ font-size: 0.85em; opacity: 0.8; }}
        .verg-ap-count {{ background: rgba(255,255,255,0.15); padding: 2px 10px; border-radius: 20px; font-size: 0.8em; white-space: nowrap; }}
        .pub-links {{ padding: 10px 20px; background: #f8f9fa; border-bottom: 1px solid #eee; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
        .pub-label {{ font-size: 0.75em; color: #7f8c8d; text-transform: uppercase; letter-spacing: 0.05em; margin-right: 4px; }}
        .pub-link {{ display: inline-flex; align-items: center; gap: 5px; padding: 4px 12px; background: #e8f4fd; color: #2980b9; border: 1px solid #bee3f8; border-radius: 20px; text-decoration: none; font-size: 0.82em; transition: all 0.2s; }}
        .pub-link:hover {{ background: #2980b9; color: #fff; }}
        .pub-link.bl {{ background: #e8f8f5; color: #1a9974; border-color: #a3e4d7; }}
        .pub-link.bl:hover {{ background: #1a9974; color: #fff; }}
        .pub-link.not {{ background: #fef9e7; color: #b7770d; border-color: #f9e79f; }}
        .pub-link.not:hover {{ background: #b7770d; color: #fff; }}
        .agendapunten {{ padding: 0; }}
        .ap {{ padding: 11px 20px; border-bottom: 1px solid #f5f5f5; display: flex; gap: 10px; }}
        .ap:last-child {{ border-bottom: none; }}
        .ap-nr {{ color: #95a5a6; font-size: 0.85em; min-width: 22px; padding-top: 2px; }}
        .ap-titel {{ font-size: 0.92em; color: #2c3e50; font-weight: 500; }}
        .ap-samen {{ font-size: 0.83em; color: #7f8c8d; margin-top: 3px; line-height: 1.4; }}
        .leeg {{ padding: 20px; text-align: center; color: #95a5a6; font-size: 0.9em; }}
        .footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #e0e0e0; text-align: center; color: #95a5a6; font-size: 0.85em; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🏛️ {gemeente}<small>meetingburger.net</small></h1>
    <div class="meta-info">
        <div class="meta-item"><span class="meta-label">Gemeente</span><span class="meta-value">{gemeente}</span></div>
        <div class="meta-item"><span class="meta-label">Datum scraping</span><span class="meta-value">{datum}</span></div>
        <div class="meta-item"><span class="meta-label">Vergaderingen</span><span class="meta-value">{len(vergaderingen)}</span></div>
        <div class="meta-item"><span class="meta-label">Agendapunten</span><span class="meta-value">{totaal_aps}</span></div>
    </div>
"""

    if not vergaderingen:
        html += '<div class="leeg">Geen vergaderingen gevonden.</div>\n'
    else:
        for v in vergaderingen:
            aps = v.get("agendapunten", [])
            label = type_label(v["type"])
            html += f"""
    <div class="vergadering">
        <div class="verg-header">
            <span class="verg-type">{label}</span>
            <div style="flex:1">
                <div class="verg-titel"><a href="{v['url']}" target="_blank">{v['titel']}</a></div>
                {"<div class='verg-datum'>📅 " + v['datum'] + "</div>" if v['datum'] else ""}
            </div>
            {"<span class='verg-ap-count'>" + str(len(aps)) + " agendapunten</span>" if aps else ""}
        </div>
"""
            # Publicatie links
            pub_links = []
            if v.get("agenda"):
                pub_links.append(f'<a class="pub-link" href="{v["agenda"]["url"]}" target="_blank">📋 Agenda</a>')
            if v.get("besluitenlijst"):
                pub_links.append(f'<a class="pub-link bl" href="{v["besluitenlijst"]["url"]}" target="_blank">✅ Besluitenlijst</a>')
            if v.get("notulen"):
                pub_links.append(f'<a class="pub-link not" href="{v["notulen"]["url"]}" target="_blank">📝 Notulen</a>')

            if pub_links:
                html += f'        <div class="pub-links"><span class="pub-label">Publicaties:</span>{"".join(pub_links)}</div>\n'

            if aps:
                html += '        <div class="agendapunten">\n'
                for i, ap in enumerate(aps, 1):
                    html += f'            <div class="ap"><span class="ap-nr">{i}.</span><div><div class="ap-titel">{ap["titel"]}</div>'
                    if ap.get("samenvatting"):
                        html += f'<div class="ap-samen">{ap["samenvatting"]}</div>'
                    html += "</div></div>\n"
                html += "        </div>\n"

            html += "    </div>\n"

    html += f"""
    <div class="footer">
        <p>Gegenereerd op {datum} • Bron: meetingburger.net</p>
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
    fetch_details: bool = True,
) -> tuple[int, int]:
    global SESSION, _config

    naam = gemeente_info["naam"]
    base_url = gemeente_info["base_url"]

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  Gemeente : {naam}")
    print(f"  Platform : {base_url}")
    print(f"  Output   : {output_dir}")
    print(f"{'='*70}\n")

    _config = ScraperConfig(base_url=base_url, output_dir=output_dir)
    SESSION = create_session(_config)

    # 1. Vergaderingen ophalen
    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = haal_vergaderingen(base_url, maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("    [!] Geen vergaderingen gevonden, stop.")
        return 0, 0

    # 2. Agendapunten ophalen per vergadering
    totaal_aps = 0
    if fetch_details:
        print(f"\n[2] Agendapunten ophalen...")
        for v in tqdm(vergaderingen, desc="Vergaderingen verwerken"):
            aps = haal_agendapunten(v)
            v["agendapunten"] = aps
            totaal_aps += len(aps)
        print(f"    ✓ {totaal_aps} agendapunten")

    # 3. Metadata + HTML opslaan
    print(f"\n[3] Metadata opslaan...")
    metadata = {
        "gemeente": naam,
        "datum": date.today().isoformat(),
        "platform": "meetingburger.net",
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

    return len(vergaderingen), totaal_aps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper voor meetingburger.net gemeenten")
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument("--gemeente", help="Naam of slug van de gemeente")
    groep.add_argument("--alle", action="store_true", help="Scrape alle gemeenten")
    parser.add_argument("--maanden", type=int, default=3, help="Aantal maanden terug (standaard: 3)")
    parser.add_argument("--no-details", action="store_true", help="Geen agendapunten ophalen")
    parser.add_argument("--output", default="pdfs", help="Output map (standaard: pdfs)")
    args = parser.parse_args()

    output_root = Path(args.output)
    fetch_details = not args.no_details
    gemeenten = haal_mb_gemeenten()

    if args.gemeente:
        naam_lower = args.gemeente.lower()
        matches = [g for g in gemeenten if g["naam"].lower() == naam_lower or g["slug"].lower() == naam_lower]
        if not matches:
            matches = [g for g in gemeenten if naam_lower in g["naam"].lower() or naam_lower in g["slug"].lower()]
        if not matches:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden.")
            print(f"    Beschikbaar: {', '.join(g['naam'] for g in gemeenten[:10])}...")
            sys.exit(1)
        te_scrapen = matches[:1]
    else:
        te_scrapen = gemeenten

    print(f"meetingburger-scraper — {len(te_scrapen)} gemeente(n) te verwerken")
    print(f"Periode: afgelopen {args.maanden} maanden\n")

    totaal_vergaderingen = 0
    totaal_aps = 0
    fouten = []

    for i, g in enumerate(te_scrapen, 1):
        if len(te_scrapen) > 1:
            print(f"\n[{i}/{len(te_scrapen)}] {g['naam']}")
        output_dir = output_root / sanitize_filename(g["naam"])
        try:
            v, a = scrape_gemeente(g, output_dir, args.maanden, fetch_details)
            totaal_vergaderingen += v
            totaal_aps += a
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
    print(f"  Agendapunten       : {totaal_aps}")
    if fouten:
        print(f"  Fouten ({len(fouten)})        : {', '.join(fouten)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
