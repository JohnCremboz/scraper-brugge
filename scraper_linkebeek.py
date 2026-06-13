"""
Scraper voor LCP-gemeenteportalen (agenda-notulen module).

Ondersteunde gemeenten:
  Linkebeek
  Sint-Genesius-Rode

URL-patroon in simba-source.csv: */download.ashx*

Gebruik:
    uv run python scraper_linkebeek.py --gemeente linkebeek --maanden 12
    uv run python scraper_linkebeek.py --alle --maanden 6
    uv run python scraper_linkebeek.py --lijst

Structuur (variant A – agenda-notulen):
  Jaarpagina:   /nl/agenda-notulen/{orgaan_id}/{slug}?y={jaar}
  Detailpagina: /nl/agenda-notulen-detail/{id}/{slug}
  Download:     /download.ashx?id={id}

Structuur (variant B – statische lijstpagina):
  Lijstpagina:  {listing_page}  (bijv. /nl/gemeenteraad)
  Download:     /download.ashx?id={id}  (met datum in linktekst GR_YYYY_MM_DD)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

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

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------

DUTCH_MONTHS: dict[str, int] = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "augstus": 8,  # typefout op Linkebeek-site
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

# Orgaan-ID en slug voor de gemeenteraad per gemeente
GEMEENTEN: dict[str, dict] = {
    "www.linkebeek.be": {
        "naam": "Linkebeek",
        "orgaan_id": 3048,
        "orgaan_slug": "gemeenteraad",
    },
    "www.sint-genesius-rode.be": {
        "naam": "Sint-Genesius-Rode",
        "listing_page": "/nl/gemeenteraad",  # statische pagina met alle download-links
    },
}

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None
BASE_URL = ""


@dataclass
class _Resp:
    status_code: int
    text: str


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

async def init_session(base_url: str) -> None:
    global SESSION, _config, BASE_URL
    if SESSION is not None:
        await SESSION.close()
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(
        base_url=BASE_URL,
        rate_limit_delay=0.5,
        timeout=30,
    )
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


def _absolute(pad: str) -> str:
    if pad.startswith("http"):
        return pad
    return urljoin(BASE_URL + "/", pad.lstrip("/"))


# ---------------------------------------------------------------------------
# Datumextractie
# ---------------------------------------------------------------------------

def datum_uit_url(url: str) -> date | None:
    """
    Destilleer datum uit detail-URL zoals:
      /nl/agenda-notulen-detail/1485/notulen-gemeenteraad-30-maart-2026
    """
    pad = urlparse(url).path.rstrip("/")
    m = re.search(
        r"-(\d{1,2})-([a-z]+)-(\d{4})$",
        pad,
        re.IGNORECASE,
    )
    if m:
        dag, maand_str, jaar = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        maand = DUTCH_MONTHS.get(maand_str)
        if maand:
            try:
                return date(jaar, maand, dag)
            except ValueError:
                pass
    return None


def datum_uit_tekst(tekst: str) -> date | None:
    """
    Destilleer datum uit linktekst zoals:
      GR_2026_03_24 - notulen  →  date(2026, 3, 24)
    """
    m = re.search(r"GR_(\d{4})_(\d{2})_(\d{2})", tekst, re.IGNORECASE)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Vergaderingen ophalen (variant B – statische lijstpagina)
# ---------------------------------------------------------------------------

async def haal_direct_downloads(
    listing_url: str,
    grensdatum: date,
) -> list[tuple[str, str, date | None]]:
    """
    Haal alle download-links op van een statische lijstpagina (variant B).

    Returns:
        lijst van (url, naam, datum) tuples
    """
    resp = await _get(listing_url)
    if not resp or resp.status_code != 200:
        logger.warning("Lijstpagina niet bereikbaar: %s", listing_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    gezien: set[str] = set()
    resultaat: list[tuple[str, str, date | None]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/download.ashx" not in href:
            continue
        full_url = _absolute(href)
        if full_url in gezien:
            continue
        gezien.add(full_url)
        naam = a.get_text(strip=True)
        datum = datum_uit_tekst(naam)
        if datum is None or datum >= grensdatum:
            resultaat.append((full_url, naam, datum))

    return resultaat


async def haal_vergaderingen(config: dict, grensdatum: date) -> list[tuple[str, date | None]]:
    """Haal vergadering-detail-URLs op voor de jaren >= grensdatum.jaar."""
    orgaan_id = config["orgaan_id"]
    slug = config["orgaan_slug"]
    detail_re = re.compile(r"/nl/agenda-notulen-detail/\d+/")

    resultaat: list[tuple[str, date | None]] = []
    gezien: set[str] = set()

    for jaar in range(grensdatum.year, date.today().year + 1):
        listing_url = _absolute(
            f"/nl/agenda-notulen/{orgaan_id}/{slug}?y={jaar}"
        )
        resp = await _get(listing_url)
        if not resp or resp.status_code != 200:
            logger.warning("Jaarpagina niet bereikbaar: %s", listing_url)
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            pad = urlparse(href).path
            if not detail_re.search(pad):
                continue
            full_url = _absolute(href)
            if full_url in gezien:
                continue
            gezien.add(full_url)
            datum = datum_uit_url(full_url)
            if datum is None or datum >= grensdatum:
                resultaat.append((full_url, datum))

    return resultaat


# ---------------------------------------------------------------------------
# Downloads ophalen van een detailpagina
# ---------------------------------------------------------------------------

async def haal_downloads(vergadering_url: str) -> list[tuple[str, str]]:
    """
    Haal alle /download.ashx?id=…-URLs van een detailpagina.

    Returns:
        lijst van (url, naam) tuples
    """
    resp = await _get(vergadering_url)
    if not resp or resp.status_code != 200:
        logger.warning("Vergadering niet bereikbaar: %s", vergadering_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    gezien: set[str] = set()
    resultaat: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/download.ashx" not in href:
            continue
        full_url = _absolute(href)
        if full_url in gezien:
            continue
        gezien.add(full_url)
        naam = a.get_text(strip=True) or Path(href).name
        resultaat.append((full_url, naam))
    return resultaat


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    return [{"naam": "Gemeenteraad", "uuid": "gemeenteraad"}]


def _zoek_gemeente(netloc: str) -> dict | None:
    for sleutel, conf in GEMEENTEN.items():
        if sleutel == netloc or sleutel.lstrip("www.") == netloc.lstrip("www."):
            return conf
    return None


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

async def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
    document_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape één LCP-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    alle_docs: list[dict] = []

    if "listing_page" in config:
        # Variant B: statische lijstpagina met directe download-links
        listing_url = _absolute(config["listing_page"])
        downloads = await haal_direct_downloads(listing_url, grensdatum)
        logger.info("   %d document(en) gevonden", len(downloads))

        for doc_url, doc_naam, doc_datum in downloads:
            if document_filter and document_filter.lower() not in doc_naam.lower():
                continue
            datum_str = doc_datum.isoformat() if doc_datum else "onbekend"
            hint = sanitize_filename(f"{datum_str}_{doc_naam}")
            alle_docs.append({"url": doc_url, "naam": hint})

    else:
        # Variant A: agenda-notulen met detailpagina's
        vergaderingen = await haal_vergaderingen(config, grensdatum)
        logger.info("   %d vergadering(en) gevonden", len(vergaderingen))

        for verg_url, verg_datum in vergaderingen:
            datum_str = verg_datum.isoformat() if verg_datum else "onbekend"
            logger.debug("  📅 %s  %s", datum_str, verg_url)

            downloads = await haal_downloads(verg_url)
            if not downloads:
                logger.debug("     (geen downloads)")
                continue

            for doc_url, doc_naam in downloads:
                if document_filter and document_filter.lower() not in doc_naam.lower():
                    continue
                hint = sanitize_filename(f"{datum_str}_{doc_naam}")
                alle_docs.append({"url": doc_url, "naam": hint})

    resultaten = await async_download_documents_parallel(
        SESSION, _config, alle_docs, gem_dir, require_pdf=True,
    )

    gedownload = sum(1 for r in resultaten if r.success and not r.skipped)
    print_summary(resultaten, naam=naam)
    return len(resultaten), gedownload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor LCP agenda-notulen gemeenteportalen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL van de gemeente (bijv. https://www.linkebeek.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam of sleutel (bijv. linkebeek)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter",
                        help="Filter op documentnaam (bijv. notulen)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon organen (compatibiliteit)")
    parser.add_argument("--debug", action="store_true",
                        help="Uitgebreide logging")
    args = parser.parse_args()

    if args.debug:
        from base_scraper import set_log_level
        set_log_level("DEBUG")

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    if args.lijst or args.lijst_organen:
        print("Ondersteunde gemeenten:")
        for netloc, conf in GEMEENTEN.items():
            print(f"  {conf['naam']:25s}  https://{netloc}/")
        return

    te_verwerken: list[dict] = []
    init_base_urls: list[str] = []

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [conf]
        init_base_urls = [args.base_url]
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "")
            if zoek in naam_sleutel or zoek in netloc:
                te_verwerken = [conf]
                init_base_urls = [f"https://{netloc}"]
                break
        if not te_verwerken:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden. Gebruik --lijst.")
            sys.exit(1)
    elif args.alle:
        te_verwerken = list(GEMEENTEN.values())
        init_base_urls = [f"https://{k}" for k in GEMEENTEN]
    else:
        parser.print_help()
        return

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        totaal_geprobeerd = totaal_gedownload = 0
        for conf, base_url in zip(te_verwerken, init_base_urls):
            await init_session(base_url)
            geprobeerd, gedownload = await scrape_gemeente(
                conf, output_dir,
                maanden=args.maanden,
                document_filter=args.document_filter,
            )
            totaal_geprobeerd += geprobeerd
            totaal_gedownload += gedownload
        if SESSION is not None:
            await SESSION.close()
        if len(te_verwerken) > 1:
            print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, "
                  f"{totaal_gedownload} gedownload.")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
