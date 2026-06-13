"""
Scraper voor Molenbeek-Saint-Jean — conseil communal documenten.

Platform: molenbeek.irisnet.be (statische HTML, per jaar)
Structuur:
  /fr/vie-politique/conseil/seance-du-conseil-communal-en-{jaar}
  → tabel met rijen per vergadering
  → eerste kolom: datum (DD/MM/YYYY)
  → overige kolommen: directe PDF-links (ordre du jour, notes, PV, registre, compte-rendu)

Gebruik:
    uv run python scraper_molenbeek.py --maanden 6
    uv run python scraper_molenbeek.py --alle --output pdfs/molenbeek
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    logger,
    print_summary,
    sanitize_filename,
)

BASE_URL = "https://www.molenbeek.irisnet.be"
LISTING_PATH_TPL = "/fr/vie-politique/conseil/seance-du-conseil-communal-en-{jaar}"

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


async def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    if SESSION is not None:
        await SESSION.close()
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=60)
    SESSION = create_async_session(_config)


async def _get(url: str) -> _Resp | None:
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, timeout=aiohttp.ClientTimeout(total=_config.timeout)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.warning("GET mislukt %s: %s", url, exc)
        return None


def _parse_datum(tekst: str) -> date | None:
    """Parse 'DD/MM/YYYY' of 'DD/MM/YYYY à ...' naar date-object."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", tekst)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


async def haal_documenten_voor_jaar(jaar: int, grensdatum: date, doc_filter: str | None) -> list[dict]:
    """Haal documenten op voor een gegeven jaar."""
    url = f"{BASE_URL}{LISTING_PATH_TPL.format(jaar=jaar)}"
    r = await _get(url)
    if not r:
        return []
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        logger.warning("Kon pagina niet laden (%s): %s", r.status_code, url)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    documenten: list[dict] = []
    gezien: set[str] = set()

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        d = _parse_datum(cells[0].get_text(strip=True))
        if d is None:
            continue
        if d < grensdatum:
            continue

        for a in row.find_all("a", href=True):
            href = a["href"].strip()
            if not href or ".pdf" not in href.lower():
                continue

            doc_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            if doc_url in gezien:
                continue
            gezien.add(doc_url)

            naam_tekst = a.get_text(strip=True)
            naam = naam_tekst if naam_tekst.lower() not in ("pdf", "") else Path(href).stem
            if doc_filter and doc_filter.lower() not in naam.lower() and \
               doc_filter.lower() not in doc_url.lower():
                continue

            documenten.append({"url": doc_url, "naam": naam, "datum": d})

    return documenten


async def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    huidig_jaar = date.today().year
    grens_jaar = grensdatum.year
    alle: list[dict] = []

    for jaar in range(huidig_jaar, grens_jaar - 1, -1):
        docs = await haal_documenten_voor_jaar(jaar, grensdatum, doc_filter)
        alle.extend(docs)

    alle.sort(key=lambda d: d["datum"], reverse=True)
    return alle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Molenbeek-Saint-Jean — conseil communal documenten (PDF)."
    )
    parser.add_argument("--base-url", default=BASE_URL,
                        help="Basis-URL (standaard: https://www.molenbeek.irisnet.be)")
    parser.add_argument("--alle", action="store_true",
                        help="Verwerk alle documenten (standaardgedrag voor deze scraper)")
    parser.add_argument("--orgaan", "-o", type=str, default=None,
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
                        help="Aantal maanden terug (standaard: 12)")
    parser.add_argument("--output", "-d", type=str, default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
                        help="Filter documenten op naam")
    args = parser.parse_args()

    if not args.alle and not args.orgaan:
        print("Geef --alle op (of --orgaan voor compatibiliteit).")
        sys.exit(1)

    maanden = max(1, args.maanden)
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        await init_session(args.base_url)
        print(f"[Molenbeek-Saint-Jean] documenten ophalen (laatste {maanden} maanden)...")
        docs = await haal_documenten(grensdatum, args.document_filter)
        if not docs:
            print("  (geen documenten gevonden)")
            if SESSION is not None:
                await SESSION.close()
            return

        pdf_docs = [{"url": d["url"], "naam": sanitize_filename(d["naam"] or Path(d["url"]).name)}
                    for d in docs]
        resultaten = await async_download_documents_parallel(
            SESSION, _config, pdf_docs, output_dir, require_pdf=True,
        )
        print_summary(resultaten, naam="Molenbeek-Saint-Jean")
        nieuw = sum(1 for r in resultaten if r.success and not r.skipped)
        print(f"\nKlaar. {nieuw} document(en) gedownload.")
        if SESSION is not None:
            await SESSION.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
