"""
Scraper voor PDF-documenten van Ingelmunster (bekendmakingen).

Gebruik:
    uv run python scraper_ingelmunster.py --lijst-organen
    uv run python scraper_ingelmunster.py --orgaan "Gemeenteraad" --maanden 12
    uv run python scraper_ingelmunster.py --alle --maanden 6
"""

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    download_document as base_download_document,
    logger,
)

BASE_URL = "https://www.ingelmunster.be"
ACTIEVE_BASE_URL = BASE_URL

ORGANEN = [
    {
        "naam": "Gemeenteraad",
        "slug": "gemeenteraad",
        "url": "/gemeente-en-bestuur/bestuur/gemeenteraad/bekendmakingen",
    },
    {
        "naam": "College van burgemeester en schepenen",
        "slug": "college",
        "url": "/gemeente-en-bestuur/bestuur/college-van-burgemeester-en-schepenen/bekendmakingen",
    },
    {
        "naam": "Raad voor maatschappelijk welzijn",
        "slug": "rmw",
        "url": "/gemeente-en-bestuur/bestuur/raad-voor-maatschappelijk-welzijn/bekendmakingen",
    },
    {
        "naam": "Vast Bureau",
        "slug": "vast_bureau",
        "url": "/gemeente-en-bestuur/bestuur/vast-bureau/bekendmakingen",
    },
]

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def init_session(base_url: str) -> None:
    """Initialiseer HTTP sessie met retries en browser-achtige headers."""
    global SESSION, _config, ACTIEVE_BASE_URL
    ACTIEVE_BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=ACTIEVE_BASE_URL, output_dir=Path("."))
    SESSION = create_session(_config)


def haal_organen_statisch() -> list[dict]:
    """Compatibel met de startwizard: geef bekende organen terug."""
    return [{"naam": o["naam"], "uuid": o["slug"]} for o in ORGANEN]


def extract_datum(href: str) -> date | None:
    """Probeer YYYYMMDD uit de bestandsnaam/URL te halen."""
    match = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", href)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def absolute_url(href: str) -> str:
    """TYPO3 pagina's gebruiken vaak root-relatieve links zoals db_files_2/..."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(f"{ACTIEVE_BASE_URL}/", href.lstrip("/"))


def bestandsnaam_van_url(url: str) -> str:
    pad = urlparse(url).path
    naam = unquote(Path(pad).name)
    return sanitize_filename(naam or "document.pdf")


def haal_documenten(orgaan: dict, grensdatum: date, document_filter: str | None) -> list[dict]:
    """Lees een bekendmakingen-pagina en verzamel PDF links binnen de periode."""
    assert SESSION is not None

    pagina_url = absolute_url(orgaan["url"])
    response = SESSION.get(pagina_url, timeout=30)
    if response.status_code != 200:
        print(f"  [!] Kon pagina niet laden ({response.status_code}): {pagina_url}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    documenten: list[dict] = []
    gezien: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        href_lower = href.lower()
        if ".pdf" not in href_lower and "db_files_2" not in href_lower:
            continue

        doc_url = absolute_url(href)
        if doc_url in gezien:
            continue
        gezien.add(doc_url)

        naam = link.get_text(strip=True) or bestandsnaam_van_url(doc_url)
        datum = extract_datum(doc_url)

        # Op deze site staan ook losse bijlagen zonder datum in de URL.
        # Die slaan we over zodat --maanden consequent werkt.
        if datum is None:
            continue
        if datum < grensdatum:
            continue
        if document_filter and document_filter.lower() not in naam.lower() and \
           document_filter.lower() not in doc_url.lower():
            continue

        documenten.append({
            "url": doc_url,
            "naam": naam,
            "datum": datum,
        })

    documenten.sort(key=lambda d: d["datum"] or date.min, reverse=True)
    return documenten


def download_document(doc_url: str, output_dir: Path, filename_hint: str) -> bool:
    """Download een PDF-document via de gedeelde downloader."""
    assert SESSION is not None and _config is not None
    result = base_download_document(
        session=SESSION,
        config=_config,
        doc_url=doc_url,
        output_dir=output_dir,
        filename_hint=filename_hint,
        require_pdf=True,
    )
    if not result.success and result.error:
        logger.debug("Download fout %s: %s", doc_url, result.error)
    return result.success


def selecteer_organen(orgaan_naam: str | None, alle: bool) -> list[dict]:
    if alle or not orgaan_naam:
        return ORGANEN

    zoek = orgaan_naam.lower().strip()
    matches = [o for o in ORGANEN if zoek in o["naam"].lower()]
    if matches:
        return matches

    print(f"[!] Orgaan '{orgaan_naam}' niet gevonden. Gebruik --lijst-organen.")
    return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Ingelmunster bekendmakingen (PDF)."
    )
    parser.add_argument("--base-url", default=BASE_URL,
                        help="Basis-URL (standaard: https://www.ingelmunster.be)")
    parser.add_argument("--orgaan", "-o", type=str, default=None,
                        help="Filter op orgaannaam (deel-match)")
    parser.add_argument("--alle", action="store_true",
                        help="Verwerk alle ondersteunde organen")
    parser.add_argument("--output", "-d", type=str, default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
                        help="Aantal maanden terug (standaard: 12)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
                        help="Filter documenten op naam (bv. notulen)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon ondersteunde organen en stop")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing voor deze scraper (compatibiliteit)")

    args = parser.parse_args()

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    if args.lijst_organen:
        print("Beschikbare organen:")
        for o in ORGANEN:
            print(f"  - {o['naam']}")
        return

    if not args.alle and not args.orgaan:
        print("Geef --orgaan of --alle op.")
        sys.exit(1)

    init_session(args.base_url)
    geselecteerd = selecteer_organen(args.orgaan, args.alle)
    if not geselecteerd:
        sys.exit(1)

    maanden = max(1, int(args.maanden))
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    totaal = 0
    for orgaan in geselecteerd:
        print(f"\n[{orgaan['naam']}]")
        print(f"  (documenten ophalen...)")
        docs = haal_documenten(orgaan, grensdatum, args.document_filter)
        if not docs:
            print("  (geen documenten gevonden)")
            continue

        doelmap = output_root / sanitize_filename(orgaan["slug"])
        doelmap.mkdir(parents=True, exist_ok=True)

        nieuw = 0
        for idx, doc in enumerate(docs, 1):
            print(f"  ({idx}/{len(docs)}) downloaden...", end="", flush=True)
            hint = bestandsnaam_van_url(doc["url"])
            if download_document(doc["url"], doelmap, hint):
                nieuw += 1
                print(f" [OK]")
            else:
                print(f" [SKIP]")

        totaal += nieuw
        print(f"  -> {nieuw} document(en) gedownload")

    print(f"\nKlaar. Totaal gedownload: {totaal}")


if __name__ == "__main__":
    main()



