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

BASE_URL = "https://www.1030.be"
SITEMAP_URL = "/sitemap.xml"
NOTULEN_PREFIX = "/nl/notulen-van-gemeenteraad/"

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


def _parse_datum_uit_slug(slug: str) -> date | None:
    """Parseer datum uit URL-slug DDMMYYYY (bv. '28052025' → 2025-05-28)."""
    slug = slug.split("-")[0]
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", slug)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


async def haal_notulen_urls(grensdatum: date) -> list[tuple[str, date]]:
    """Haal alle notulen-URL's op via sitemap die nieuwer zijn dan grensdatum."""
    r = await _get(f"{BASE_URL}{SITEMAP_URL}")
    if not r or r.status_code != 200:
        logger.warning("Sitemap niet beschikbaar (%s)", getattr(r, "status_code", "?"))
        return []

    gezien: set[str] = set()
    resultaten: list[tuple[str, date]] = []

    for m in re.finditer(r"<loc>(https?://[^<]+/nl/notulen-van-gemeenteraad/([^<]+))</loc>", r.text):
        url, slug = m.group(1), m.group(2)
        d = _parse_datum_uit_slug(slug)
        if d is None or d < grensdatum:
            continue
        if url in gezien:
            continue
        gezien.add(url)
        resultaten.append((url, d))

    resultaten.sort(key=lambda x: x[1], reverse=True)
    return resultaten


async def haal_pdfs_van_pagina(url: str) -> list[str]:
    """Haal alle PDF-links op van een individuele notulenpagina."""
    r = await _get(url)
    if not r or r.status_code != 200:
        logger.warning("Kon pagina niet laden (%s): %s", getattr(r, "status_code", "?"), url)
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


async def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    notulen = await haal_notulen_urls(grensdatum)
    documenten: list[dict] = []

    for url, d in notulen:
        pdf_urls = await haal_pdfs_van_pagina(url)
        for pdf_url in pdf_urls:
            naam = Path(pdf_url.split("?")[0]).stem.replace("-", " ").replace("_", " ")
            if doc_filter and doc_filter.lower() not in naam.lower() and \
               doc_filter.lower() not in pdf_url.lower():
                continue
            documenten.append({"url": pdf_url, "naam": naam, "datum": d})

    return documenten


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

    maanden = max(1, args.maanden)
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        await init_session(args.base_url)
        print(f"[Schaerbeek] documenten ophalen via sitemap (laatste {maanden} maanden)...")
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
        print_summary(resultaten, naam="Schaerbeek")
        nieuw = sum(1 for r in resultaten if r.success and not r.skipped)
        print(f"\nKlaar. {nieuw} document(en) gedownload.")
        if SESSION is not None:
            await SESSION.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
