"""
Scraper voor Schaerbeek (Schaarbeek) — notulen van de gemeenteraad.

Platform: 1030.be (Vue.js SPA; listing via sitemap.xml, pagina's zijn statische HTML)
Structuur:
  /sitemap.xml → <loc>...nl/notulen-van-gemeenteraad/DDMMYYYY</loc> per vergadering
  /nl/notulen-van-gemeenteraad/{DDMMYYYY} → HTML pagina met PDF-link(s)

Gebruik:
    uv run python scraper_schaerbeek.py --maanden 6
    uv run python scraper_schaerbeek.py --alle --output pdfs/schaerbeek
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document as base_download_document,
    logger,
    sanitize_filename,
)

BASE_URL = "https://www.1030.be"
SITEMAP_URL = "/sitemap.xml"
NOTULEN_PREFIX = "/nl/notulen-van-gemeenteraad/"

SESSION = None
_config: ScraperConfig | None = None


def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=60)
    SESSION = create_session(_config)


def _parse_datum_uit_slug(slug: str) -> date | None:
    """Parseer datum uit URL-slug DDMMYYYY (bv. '28052025' → 2025-05-28)."""
    # Negeer suffix zoals -0, -1, ...
    slug = slug.split("-")[0]
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", slug)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def haal_notulen_urls(grensdatum: date) -> list[tuple[str, date]]:
    """Haal alle notulen-URL's op via sitemap die nieuwer zijn dan grensdatum."""
    assert SESSION is not None

    r = SESSION.get(f"{BASE_URL}{SITEMAP_URL}", timeout=60)
    if r.status_code != 200:
        logger.warning("Sitemap niet beschikbaar (%s)", r.status_code)
        return []

    gezien: set[str] = set()
    resultaten: list[tuple[str, date]] = []

    for m in re.finditer(r"<loc>(https?://[^<]+/nl/notulen-van-gemeenteraad/([^<]+))</loc>", r.text):
        url, slug = m.group(1), m.group(2)
        # Dedupliceer per datum (soms is er een -0 suffix voor dezelfde dag)
        d = _parse_datum_uit_slug(slug)
        if d is None or d < grensdatum:
            continue
        if url in gezien:
            continue
        gezien.add(url)
        resultaten.append((url, d))

    resultaten.sort(key=lambda x: x[1], reverse=True)
    return resultaten


def haal_pdfs_van_pagina(url: str) -> list[str]:
    """Haal alle PDF-links op van een individuele notulenpagina."""
    assert SESSION is not None

    r = SESSION.get(url, timeout=60)
    if r.status_code != 200:
        logger.warning("Kon pagina niet laden (%s): %s", r.status_code, url)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    pdf_urls: list[str] = []
    gezien: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" not in href.lower():
            continue
        doc_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        if doc_url not in gezien:
            gezien.add(doc_url)
            pdf_urls.append(doc_url)

    return pdf_urls


def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    notulen = haal_notulen_urls(grensdatum)
    documenten: list[dict] = []

    for url, d in notulen:
        pdf_urls = haal_pdfs_van_pagina(url)
        for pdf_url in pdf_urls:
            naam = Path(pdf_url.split("?")[0]).stem.replace("-", " ").replace("_", " ")
            if doc_filter and doc_filter.lower() not in naam.lower() and \
               doc_filter.lower() not in pdf_url.lower():
                continue
            documenten.append({"url": pdf_url, "naam": naam, "datum": d})

    return documenten


def download_document(doc_url: str, output_dir: Path, filename_hint: str) -> bool:
    assert SESSION is not None and _config is not None
    result = base_download_document(
        session=SESSION,
        config=_config,
        doc_url=doc_url,
        output_dir=output_dir,
        filename_hint=filename_hint,
        require_pdf=True,
    )
    return result.success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Schaerbeek — notulen gemeenteraad (PDF)."
    )
    parser.add_argument("--base-url", default=BASE_URL,
                        help="Basis-URL (standaard: https://www.1030.be)")
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

    init_session(args.base_url)
    maanden = max(1, args.maanden)
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Schaerbeek] documenten ophalen via sitemap (laatste {maanden} maanden)...")
    docs = haal_documenten(grensdatum, args.document_filter)
    if not docs:
        print("  (geen documenten gevonden)")
        return

    nieuw = 0
    for idx, doc in enumerate(docs, 1):
        hint = sanitize_filename(doc["naam"] or Path(doc["url"]).name)
        print(f"  ({idx}/{len(docs)}) {hint[:60]}...", end="", flush=True)
        if download_document(doc["url"], output_dir, hint):
            nieuw += 1
            print(" [OK]")
        else:
            print(" [SKIP]")

    print(f"\nKlaar. {nieuw} document(en) gedownload.")


if __name__ == "__main__":
    main()
