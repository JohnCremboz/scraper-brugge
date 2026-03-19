"""
scraper_provantwerpen.py — Scraper voor Provincie Antwerpen (provincieraad)

Bron: https://www.provincieantwerpen.be/nl/politiek-bestuur/provincieraad/agenda-en-verslagen
De pagina bevat directe links naar verslagen (.html) en stenografische notulen (.pdf),
gegroepeerd per jaar en vergaderdatum.

Gebruik:
    python scraper_provantwerpen.py --maanden 6
    python scraper_provantwerpen.py --maanden 36
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from pathlib import Path
from urllib.parse import urljoin

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

BASE_URL = "https://www.provincieantwerpen.be"
PAGINA_URL = f"{BASE_URL}/nl/politiek-bestuur/provincieraad/agenda-en-verslagen"
NAAM = "Provincie Antwerpen"

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def _get(url: str) -> requests.Response | None:
    return robust_get(SESSION, url)


# ---------------------------------------------------------------------------
# Vergaderingen + documenten ophalen
# ---------------------------------------------------------------------------

def haal_vergaderingen(maanden: int = 6) -> list[dict]:
    """Parse de agenda-en-verslagen pagina en extraheer vergaderingen met docs."""
    resp = _get(PAGINA_URL)
    if not resp:
        print("  [!] Kan pagina niet laden")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Alle links naar open-data provincieraad bestanden
    alle_links = soup.find_all("a", href=True)
    doc_links = [
        (a.get_text(strip=True).replace("arrow_forward", "").strip(), a["href"])
        for a in alle_links
        if "/open-data/provincieraad/" in a["href"]
    ]

    # Groepeer per datum
    cutoff = date.today() - relativedelta(months=maanden)
    vergaderingen: dict[str, dict] = {}

    for tekst, href in doc_links:
        m = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", href)
        if not m:
            continue
        datum_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        vergader_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if vergader_date < cutoff:
            continue

        if datum_str not in vergaderingen:
            vergaderingen[datum_str] = {
                "datum": datum_str,
                "orgaan": "Provincieraad",
                "documenten": [],
            }

        ext = href.rsplit(".", 1)[-1].lower() if "." in href.split("/")[-1] else ""
        vergaderingen[datum_str]["documenten"].append({
            "naam": tekst or f"document_{datum_str}",
            "url": href if href.startswith("http") else urljoin(BASE_URL, href),
            "type": ext,
            "local_file": None,
        })

    result = sorted(vergaderingen.values(), key=lambda v: v["datum"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# HTML genereren
# ---------------------------------------------------------------------------

def genereer_html(vergaderingen: list[dict], output_dir: Path) -> Path:
    html_path = output_dir.parent / f"{sanitize_filename(NAAM)}.html"

    rijen = []
    for v in vergaderingen:
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
            <td>{v['orgaan']}</td>
            <td>{doc_html}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>{NAAM} – Provincieraad</title>
<style>
  body {{ font-family: sans-serif; margin: 2rem; }}
  h1 {{ color: #003366; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: .5rem .75rem; vertical-align: top; }}
  th {{ background: #003366; color: white; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  .documenten {{ display: flex; flex-wrap: wrap; gap: .3rem; }}
  .doc-link {{ background: #e8f0fe; border: 1px solid #4285f4; border-radius: 3px;
               padding: 2px 6px; font-size: .8rem; text-decoration: none; color: #1a0dab; }}
  .doc-link:hover {{ background: #d2e3fc; }}
</style>
</head>
<body>
<h1>🏛️ {NAAM}</h1>
<p>Bron: provincieantwerpen.be — {len(vergaderingen)} vergadering(en)</p>
<table>
  <thead><tr><th>Datum</th><th>Orgaan</th><th>Documenten</th></tr></thead>
  <tbody>{''.join(rijen)}</tbody>
</table>
</body>
</html>"""

    html_path.write_text(html, encoding="utf-8")
    return html_path


# ---------------------------------------------------------------------------
# Hoofd scrape-functie
# ---------------------------------------------------------------------------

def scrape(maanden: int = 6, output_base: str = "pdfs") -> None:
    global SESSION, _config
    output_dir = Path(output_base) / sanitize_filename(NAAM)
    output_dir.mkdir(parents=True, exist_ok=True)

    _config = ScraperConfig(base_url=BASE_URL, output_dir=output_dir)
    SESSION = create_session(_config)

    print(f"\n{'=' * 70}")
    print(f"  Naam     : {NAAM}")
    print(f"  Platform : {PAGINA_URL}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = haal_vergaderingen(maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("  Geen vergaderingen gevonden.")
        return

    # Download alleen PDF's (stenografische notulen), HTML-verslagen linken we
    n_pdfs = sum(1 for v in vergaderingen for d in v["documenten"] if d["type"] == "pdf")
    gedownload = 0

    if n_pdfs > 0:
        print(f"[2] PDF-notulen downloaden ({n_pdfs} totaal)...")
        for v in tqdm(vergaderingen, desc="Downloaden"):
            for doc in v["documenten"]:
                if doc["type"] != "pdf":
                    continue
                result = download_document(
                    SESSION, _config, doc["url"], output_dir,
                    filename_hint=f"{v['datum']}_{sanitize_filename(doc['naam'])}.pdf",
                )
                if result and result.success:
                    doc["local_file"] = str(result.path)
                    gedownload += 1
    else:
        print("[2] Geen PDF-notulen beschikbaar.")

    n_total = sum(len(v["documenten"]) for v in vergaderingen)

    print("[3] Opslaan...")
    meta_pad = output_dir / f"{sanitize_filename(NAAM)}_metadata.json"
    meta_pad.write_text(json.dumps(vergaderingen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✓ JSON: {meta_pad.name}")

    html_pad = genereer_html(vergaderingen, output_dir)
    print(f"    ✓ HTML: {html_pad.name}")

    print(f"\n{'=' * 70}")
    print(f"  ✓ Klaar!")
    print(f"  Vergaderingen    : {len(vergaderingen)}")
    print(f"  Documenten       : {n_total} ({gedownload} PDF's gedownload)")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper voor Provincie Antwerpen (provincieraad)")
    parser.add_argument("--maanden", type=int, default=6, help="Aantal maanden terug (standaard 6)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap")
    # Standaard TUI-argumenten
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()
    scrape(maanden=args.maanden, output_base=args.output)


if __name__ == "__main__":
    main()
