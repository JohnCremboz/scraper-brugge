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
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    logger,
    sanitize_filename,
)

BASE_URL = "https://www.provincieantwerpen.be"
PAGINA_URL = f"{BASE_URL}/nl/politiek-bestuur/provincieraad/agenda-en-verslagen"
NAAM = "Provincie Antwerpen"

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


async def init_session() -> None:
    global SESSION, _config
    if SESSION is not None:
        await SESSION.close()
    _config = ScraperConfig(base_url=BASE_URL)
    SESSION = create_async_session(_config)


async def _get(url: str) -> _Resp | None:
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.warning("GET mislukt %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Vergaderingen + documenten ophalen
# ---------------------------------------------------------------------------

async def haal_vergaderingen(maanden: int = 6) -> list[dict]:
    """Parse de agenda-en-verslagen pagina en extraheer vergaderingen met docs."""
    resp = await _get(PAGINA_URL)
    if not resp:
        print("  [!] Kan pagina niet laden")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    alle_links = soup.find_all("a", href=True)
    doc_links = [
        (a.get_text(strip=True).replace("arrow_forward", "").strip(), a["href"])
        for a in alle_links
        if "/open-data/provincieraad/" in a["href"]
    ]

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
    from html_output import doc_badges_html, genereer_html_tabel, html_output_path
    html_path = html_output_path(output_dir, NAAM)
    rijen = [
        [v["datum"], v["orgaan"], doc_badges_html(v.get("documenten", []), html_path)]
        for v in vergaderingen
    ]
    return genereer_html_tabel(
        naam=NAAM,
        bron="provincieantwerpen.be",
        kolommen=["Datum", "Orgaan", "Documenten"],
        rijen=rijen,
        output_pad=html_path,
    )


# ---------------------------------------------------------------------------
# Hoofd scrape-functie
# ---------------------------------------------------------------------------

async def scrape(maanden: int = 6, output_base: str = "pdfs") -> None:
    output_dir = Path(output_base) / sanitize_filename(NAAM)
    output_dir.mkdir(parents=True, exist_ok=True)

    await init_session()

    print(f"\n{'=' * 70}")
    print(f"  Naam     : {NAAM}")
    print(f"  Platform : {PAGINA_URL}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = await haal_vergaderingen(maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("  Geen vergaderingen gevonden.")
        return

    pdf_docs = [
        {
            "url": doc["url"],
            "naam": f"{v['datum']}_{sanitize_filename(doc['naam'])}.pdf",
        }
        for v in vergaderingen
        for doc in v["documenten"]
        if doc["type"] == "pdf"
    ]

    gedownload = 0
    if pdf_docs:
        print(f"[2] PDF-notulen downloaden ({len(pdf_docs)} totaal)...")
        resultaten = await async_download_documents_parallel(
            SESSION, _config, pdf_docs, output_dir, require_pdf=True,
        )
        # Koppel local_file terug aan vergaderingen
        idx = 0
        for v in vergaderingen:
            for doc in v["documenten"]:
                if doc["type"] == "pdf":
                    r = resultaten[idx]
                    if r.success:
                        doc["local_file"] = str(r.path)
                        gedownload += 1
                    idx += 1
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

    if SESSION is not None:
        await SESSION.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper voor Provincie Antwerpen (provincieraad)")
    parser.add_argument("--maanden", type=int, default=6, help="Aantal maanden terug (standaard 6)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap")
    parser.add_argument("--base-url", type=str, default="")
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()
    asyncio.run(scrape(maanden=args.maanden, output_base=args.output))


if __name__ == "__main__":
    main()
