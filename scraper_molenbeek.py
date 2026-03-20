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

BASE_URL = "https://www.molenbeek.irisnet.be"
LISTING_PATH_TPL = "/fr/vie-politique/conseil/seance-du-conseil-communal-en-{jaar}"

SESSION = None
_config: ScraperConfig | None = None


def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=60)
    SESSION = create_session(_config)


def _parse_datum(tekst: str) -> date | None:
    """Parse 'DD/MM/YYYY' of 'DD/MM/YYYY à ...' naar date-object."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", tekst)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def haal_documenten_voor_jaar(jaar: int, grensdatum: date, doc_filter: str | None) -> list[dict]:
    """Haal documenten op voor een gegeven jaar."""
    assert SESSION is not None

    url = f"{BASE_URL}{LISTING_PATH_TPL.format(jaar=jaar)}"
    r = SESSION.get(url, timeout=60)
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

        # Eerste cel bevat datum
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


def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    huidig_jaar = date.today().year
    grens_jaar = grensdatum.year
    alle: list[dict] = []

    for jaar in range(huidig_jaar, grens_jaar - 1, -1):
        docs = haal_documenten_voor_jaar(jaar, grensdatum, doc_filter)
        alle.extend(docs)

    alle.sort(key=lambda d: d["datum"], reverse=True)
    return alle


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

    init_session(args.base_url)
    maanden = max(1, args.maanden)
    grensdatum = date.today() - timedelta(days=maanden * 31)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Molenbeek-Saint-Jean] documenten ophalen (laatste {maanden} maanden)...")
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
