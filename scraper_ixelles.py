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
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document,
    logger,
    print_summary,
    rate_limited_get,
    sanitize_filename,
    DownloadResult,
)

BASE_URL = "https://www.ixelles.be"
LISTING_PAD = "/site/109-Actualite"
_PDF_RE = re.compile(r"/uploads/conseil/(?:odj|pv)/", re.IGNORECASE)
_DATUM_RE = re.compile(r"séance du\s+(\d{1,2})/(\d{2})/(\d{4})", re.IGNORECASE)


def _datum_uit_tekst(tekst: str) -> date | None:
    m = _DATUM_RE.search(tekst)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def scrape(
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

    config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=30)
    session = create_session(config)

    listing_url = BASE_URL + LISTING_PAD
    logger.info("▶  Ixelles  (grensdatum=%s)", grensdatum)
    resp = rate_limited_get(session, listing_url, config)
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
        # Maak URL absoluut (relatieve paden: ../uploads/...)
        full_url = urljoin(listing_url, href)
        if full_url in gezien:
            continue
        gezien.add(full_url)

        naam = a.get_text(strip=True) or Path(href).name
        # Datum uit linktekst
        tekst = a.get_text(strip=True)
        # Zoek ook in parent element
        parent_tekst = a.parent.get_text(strip=True) if a.parent else ""
        datum = _datum_uit_tekst(tekst) or _datum_uit_tekst(parent_tekst)

        if datum is not None and datum < grensdatum:
            continue
        if document_filter:
            if (document_filter.lower() not in naam.lower() and
                    document_filter.lower() not in full_url.lower()):
                continue
        pdfs.append({"url": full_url, "naam": naam})

    logger.info("   %d PDF(s) gevonden", len(pdfs))

    resultaten: list[DownloadResult] = []
    for pdf in pdfs:
        hint = sanitize_filename(Path(pdf["url"]).name)
        result = download_document(
            session, config,
            pdf["url"],
            gem_dir,
            filename_hint=hint or pdf["naam"],
            require_pdf=True,
        )
        resultaten.append(result)

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

    geprobeerd, gedownload = scrape(
        maanden=args.maanden,
        document_filter=args.document_filter,
        output_dir=output_root,
    )
    print(f"\nKlaar. Totaal: {geprobeerd} geprobeerd, {gedownload} gedownload.")


if __name__ == "__main__":
    main()
