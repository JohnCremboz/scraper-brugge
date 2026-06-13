"""
Scraper voor Docodis-gemeenteportalen.

Ondersteunde gemeenten:
  Koekelberg

URL-patroon in simba-source.csv: */AC-file/docodis/*

Gebruik:
    uv run python scraper_docodis.py --gemeente koekelberg --maanden 12
    uv run python scraper_docodis.py --alle --maanden 6
    uv run python scraper_docodis.py --lijst

Structuur:
  Rootfolder:       /w/modules/docodis/front_end.php?id={root_id}&lgn=1
  Jaarfolder:       /w/modules/docodis/front_end.php?id={jaar_id}&p={root_id}&lgn=1
  Vergaderingsfolder: /w/modules/docodis/front_end.php?id={verg_id}&p={jaar_id}&lgn=1
  Download:         /w/AC-file/docodis/{le_nom}.{ext}

De datum staat als tekst in de vergaderingslink (bijv. "2026-01-19").
Documenten worden gevonden via open_document("id","lgn","le_nom","ext") JS-calls.
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

GEMEENTEN: dict[str, dict] = {
    "www.koekelberg.be": {
        "naam": "Koekelberg",
        "docodis_root": 152,   # ID van de folder "Conseils communaux : ordres du jour..."
        "docodis_base": "/w/modules/docodis/front_end.php",
        "download_base": "/w/AC-file/docodis/",
    },
}

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None
BASE_URL = ""

_OPEN_DOC_RE = re.compile(
    r'open_document\("(\d+)","(\d+)","([^"]+)","([^"]+)"\)'
)


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
# Vergaderingen & documenten ophalen
# ---------------------------------------------------------------------------

async def haal_vergaderingen(config: dict, grensdatum: date) -> list[tuple[str, date | None]]:
    """
    Navigeer root → jaar-folders → vergadering-folders.

    Returns:
        lijst van (vergadering_url, datum) tuples
    """
    root_id = config["docodis_root"]
    base_php = config["docodis_base"]
    root_url = _absolute(f"{base_php}?id={root_id}&lgn=1")

    resp = await _get(root_url)
    if not resp or resp.status_code != 200:
        logger.warning("Rootfolder niet bereikbaar: %s", root_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Jaar-folders: links met p={root_id}
    jaar_re = re.compile(rf"id=(\d+)&p={root_id}")
    jaar_links: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        m = jaar_re.search(a["href"])
        if m:
            try:
                jaar = int(a.get_text(strip=True))
            except ValueError:
                continue
            if jaar >= grensdatum.year:
                jaar_links.append((m.group(1), jaar))

    resultaat: list[tuple[str, date | None]] = []

    for jaar_folder_id, _ in jaar_links:
        jaar_url = _absolute(f"{base_php}?id={jaar_folder_id}&p={root_id}&lgn=1")
        resp2 = await _get(jaar_url)
        if not resp2 or resp2.status_code != 200:
            continue

        soup2 = BeautifulSoup(resp2.text, "lxml")
        verg_re = re.compile(rf"id=(\d+)&p={jaar_folder_id}")
        for a in soup2.find_all("a", href=True):
            m = verg_re.search(a["href"])
            if not m:
                continue
            verg_tekst = a.get_text(strip=True)
            datum = _datum_uit_tekst(verg_tekst)
            if datum is None or datum >= grensdatum:
                verg_url = _absolute(
                    f"{base_php}?id={m.group(1)}&p={jaar_folder_id}&lgn=1"
                )
                resultaat.append((verg_url, datum))

    return resultaat


def _datum_uit_tekst(tekst: str) -> date | None:
    """Parse ISO-datum YYYY-MM-DD uit vergaderingstekst."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", tekst)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


async def haal_downloads(vergadering_url: str, config: dict) -> list[tuple[str, str]]:
    """
    Haal alle downloadbare documenten van een vergaderingsfolder.

    Returns:
        lijst van (url, naam) tuples
    """
    resp = await _get(vergadering_url)
    if not resp or resp.status_code != 200:
        logger.warning("Vergadering niet bereikbaar: %s", vergadering_url)
        return []

    download_base = config["download_base"]
    resultaat: list[tuple[str, str]] = []
    gezien: set[str] = set()

    for m in _OPEN_DOC_RE.finditer(resp.text):
        le_nom = m.group(3)
        ext = m.group(4)
        url = _absolute(f"{download_base}{le_nom}.{ext}")
        if url in gezien:
            continue
        gezien.add(url)
        naam = f"{le_nom}.{ext}"
        resultaat.append((url, naam))

    return resultaat


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

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
    """Scrape één Docodis-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    vergaderingen = await haal_vergaderingen(config, grensdatum)
    logger.info("   %d vergadering(en) gevonden", len(vergaderingen))

    alle_docs: list[dict] = []

    for verg_url, verg_datum in vergaderingen:
        datum_str = verg_datum.isoformat() if verg_datum else "onbekend"
        logger.debug("  📅 %s  %s", datum_str, verg_url)

        downloads = await haal_downloads(verg_url, config)
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
        description="Scraper voor Docodis-gemeenteportalen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL (bijv. https://www.koekelberg.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam (bijv. koekelberg)")
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
                        help="Filter op documentnaam")
    parser.add_argument("--notulen", action="store_true",
                        help="Niet van toepassing (Docodis heeft geen aparte notulen-filter)")
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
