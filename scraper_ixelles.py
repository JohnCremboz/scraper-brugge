"""
Scraper voor Ixelles / Elsene — conseil communal publicaties.

De gemeente Ixelles publiceert agenda's (ODJ) en processen-verbaal (PV)
op een statische listingpagina: https://www.ixelles.be/site/109-Actualite
PDF-links zijn relatief aan /uploads/conseil/(odj|pv)/.
De vergaderdatum staat in de linktekst: "séance du DD/MM/YYYY".

Gebruik:
    uv run python scraper_ixelles.py --maanden 12
    uv run python scraper_ixelles.py --notulen
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from base_scraper import (
    DownloadResult,
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    logger,
    print_summary,
    sanitize_filename,
)

BASE_URL = "https://www.ixelles.be"
LISTING_PAD = "/site/109-Actualite"
_PDF_RE = re.compile(r"/uploads/conseil/(?:odj|pv)/", re.IGNORECASE)
_DATUM_RE = re.compile(r"séance du\s+(\d{1,2})/(\d{2})/(\d{4})", re.IGNORECASE)

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


def _datum_uit_tekst(tekst: str) -> date | None:
    m = _DATUM_RE.search(tekst)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def init_session() -> None:
    global SESSION, _config
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=30)
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


async def scrape(
    maanden: int = 12,
    document_filter: str | None = None,
    output_dir: Path = Path("pdfs"),
) -> tuple[int, int]:
    """Scrape Ixelles conseil communal PDFs.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    grensdatum = date.today() - timedelta(days=maanden * 31)
    gem_dir = output_dir / "Ixelles"
    gem_dir.mkdir(parents=True, exist_ok=True)

    listing_url = BASE_URL + LISTING_PAD
    logger.info("▶  Ixelles  (grensdatum=%s)", grensdatum)
    resp = await _get(listing_url)
    if not resp or resp.status_code != 200:
        logger.warning("Listing niet bereikbaar: %s", listing_url)
        return 0, 0

    soup = BeautifulSoup(resp.text, "lxml")
    gezien: set[str] = set()
    pdfs: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _PDF_RE.search(href):
            continue
        full_url = urljoin(listing_url, href)
        if full_url in gezien:
            continue
        gezien.add(full_url)

        naam = a.get_text(strip=True) or Path(href).name
        tekst = a.get_text(strip=True)
        parent_tekst = a.parent.get_text(strip=True) if a.parent else ""
        datum = _datum_uit_tekst(tekst) or _datum_uit_tekst(parent_tekst)

        if datum is not None and datum < grensdatum:
            continue
        if document_filter:
            if (document_filter.lower() not in naam.lower() and
                    document_filter.lower() not in full_url.lower()):
                continue
        pdfs.append({"url": full_url, "naam": sanitize_filename(Path(full_url).name)})

    logger.info("   %d PDF(s) gevonden", len(pdfs))

    resultaten: list[DownloadResult] = await async_download_documents_parallel(
        SESSION, _config, pdfs, gem_dir, require_pdf=True,
    )

    gedownload = sum(1 for r in resultaten if r.success and not r.skipped)
    print_summary(resultaten, naam="Ixelles")
    return len(resultaten), gedownload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Ixelles conseil communal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter",
                        help="Filter op bestandsnaam (bijv. pv, odj)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter pv")
    parser.add_argument("--base-url", default="",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon organen (compatibiliteit)")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--alle", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--debug", action="store_true",
                        help="Uitgebreide logging")
    args = parser.parse_args()

    if args.debug:
        from base_scraper import set_log_level
        set_log_level("DEBUG")

    if args.lijst_organen:
        print("Organen: Conseil communal (Ixelles)")
        return

    if args.notulen and not args.document_filter:
        args.document_filter = "pv"

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        init_session()
        geprobeerd, gedownload = await scrape(
            maanden=args.maanden,
            document_filter=args.document_filter,
            output_dir=output_root,
        )
        print(f"\nKlaar. Totaal: {geprobeerd} geprobeerd, {gedownload} gedownload.")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
