"""
Scraper voor de Stad Brussel — ordres du jour, procès-verbaux en documenten.

Platform: bruxelles.be (Drupal CMS met Drupal Views jaarfilter)
Structuur:
  /ordres-du-jour-proces-verbaux-motions?field_date_document_value=YYYY
  → <h3>DD/MM/YYYY</h3> datumsectie
  → .views-field-field-file-document a → directe PDF-links

Gebruik:
    uv run python scraper_brussel.py --maanden 6
    uv run python scraper_brussel.py --alle --output pdfs/brussel
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    logger,
    sanitize_filename,
)

BASE_URL = "https://www.bruxelles.be"
LISTING_PATH = "/ordres-du-jour-proces-verbaux-motions"

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


async def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    if SESSION is not None:
        await SESSION.close()
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=60)
    SESSION = create_async_session(_config)


def _parse_datum_nl(tekst: str) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", tekst)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


async def haal_documenten_voor_jaar(
    jaar: int, grensdatum: date, doc_filter: str | None
) -> list[dict]:
    assert SESSION is not None and _config is not None

    url = f"{BASE_URL}{LISTING_PATH}"
    params = {"field_date_document_value": str(jaar)}
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=60)
        ) as r:
            if r.status != 200:
                logger.warning("Kon pagina niet laden (%s): %s", r.status, url)
                return []
            html = await r.text()
    except Exception as exc:
        logger.warning("GET mislukt %s: %s", url, exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    documenten: list[dict] = []
    huidige_datum: date | None = None
    gezien: set[str] = set()

    view = soup.find("div", class_=re.compile(r"view-town-council"))
    if not view:
        view = soup.find("div", class_=re.compile(r"view-content"))
    if not view:
        view = soup

    for tag in view.find_all(True):
        if tag.name == "h3":
            d = _parse_datum_nl(tag.get_text(strip=True))
            if d:
                huidige_datum = d
            continue

        if "views-field-field-file-document" in tag.get("class", []):
            for a in tag.find_all("a", href=True):
                href = a["href"].strip()
                if not href:
                    continue
                doc_url = urljoin(f"{BASE_URL}/", href.lstrip("/"))
                if doc_url in gezien:
                    continue
                gezien.add(doc_url)

                naam = a.get_text(strip=True) or Path(href).name
                d = huidige_datum

                if d is None or d < grensdatum:
                    continue
                if doc_filter and doc_filter.lower() not in naam.lower() and \
                   doc_filter.lower() not in doc_url.lower():
                    continue

                documenten.append({"url": doc_url, "naam": naam, "datum": d})

    return documenten


async def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    """Haal documenten voor alle relevante jaren parallel op."""
    huidig_jaar = date.today().year
    grens_jaar = grensdatum.year
    jaren = list(range(huidig_jaar, grens_jaar - 1, -1))

    results = await asyncio.gather(*[
        haal_documenten_voor_jaar(j, grensdatum, doc_filter) for j in jaren
    ])

    alle = [doc for docs in results for doc in docs]
    alle.sort(key=lambda d: d["datum"] or date.min, reverse=True)
    return alle


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Stad Brussel — raadsdocumenten (PDF)."
    )
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--orgaan", "-o", type=str, default=None)
    parser.add_argument("--maanden", "-m", type=int, default=12)
    parser.add_argument("--output", "-d", type=str, default="pdfs")
    parser.add_argument("--document-filter", "-f", type=str, default=None)
    args = parser.parse_args()

    if not args.alle and not args.orgaan:
        print("Geef --alle op (of --orgaan voor compatibiliteit).")
        sys.exit(1)

    await init_session(args.base_url)
    maanden = max(1, args.maanden)
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Brussel] documenten ophalen (laatste {maanden} maanden)...")
    docs = await haal_documenten(grensdatum, args.document_filter)
    if not docs:
        print("  (geen documenten gevonden)")
        if SESSION:
            await SESSION.close()
        return

    dl_input = [
        {"url": d["url"], "naam": sanitize_filename(d["naam"] or Path(d["url"]).name)}
        for d in docs
    ]
    results = await async_download_documents_parallel(
        SESSION, _config, dl_input, output_dir, require_pdf=True,
    )
    nieuw = sum(1 for r in results if r.success and not r.skipped)

    if SESSION:
        await SESSION.close()

    print(f"\nKlaar. {nieuw} document(en) gedownload.")


if __name__ == "__main__":
    asyncio.run(main())
