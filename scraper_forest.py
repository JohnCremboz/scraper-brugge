"""
Scraper voor Gemeente Forest (Vorst) — conseil communal documenten.

Platform: forest.brussels (Drupal CMS)
Structuur:
  /fr/publications/conseil-communal-1
  → <div class="publication demarche conseil"> kaarten
  → <time class="datetime" datetime="YYYY-MM-DD..."> voor datum
  → <a href="/sites/default/files/publications/..."> voor PDFs

Gebruik:
    uv run python scraper_forest.py --maanden 6
    uv run python scraper_forest.py --alle --output pdfs/forest
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document as base_download_document,
    logger,
    sanitize_filename,
)

BASE_URL = "https://www.forest.brussels"
LISTING_PATH = "/fr/publications/conseil-communal-1"

SESSION = None
_config: ScraperConfig | None = None


def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.5, timeout=60)
    SESSION = create_session(_config)


def haal_documenten(grensdatum: date, doc_filter: str | None) -> list[dict]:
    """Haal alle PDF-documenten op van de Forest conseil communal pagina."""
    assert SESSION is not None

    url = f"{BASE_URL}{LISTING_PATH}"
    r = SESSION.get(url, timeout=60)
    if r.status_code != 200:
        logger.warning("Kon pagina niet laden (%s): %s", r.status_code, url)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    documenten: list[dict] = []
    gezien: set[str] = set()

    # Elke vergadering is een kaart met class "publication demarche conseil"
    for kaart in soup.find_all("div", class_=lambda c: c and "publication" in c and "conseil" in c):
        # Datum uit <time> element
        time_tag = kaart.find("time", class_="datetime")
        if not time_tag:
            continue
        dt_str = time_tag.get("datetime", "")
        try:
            d = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            continue

        if d < grensdatum:
            continue

        for a in kaart.find_all("a", href=True):
            href = a["href"].strip()
            if not href or ".pdf" not in href.lower():
                continue

            doc_url = urljoin(f"{BASE_URL}/", href.lstrip("/"))
            if doc_url in gezien:
                continue
            gezien.add(doc_url)

            naam = a.get_text(strip=True) or Path(href).stem
            if doc_filter and doc_filter.lower() not in naam.lower() and \
               doc_filter.lower() not in doc_url.lower():
                continue

            documenten.append({"url": doc_url, "naam": naam, "datum": d})

    documenten.sort(key=lambda d: d["datum"], reverse=True)
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
        description="Scraper voor Forest/Vorst — conseil communal documenten (PDF)."
    )
    parser.add_argument("--base-url", default=BASE_URL,
                        help="Basis-URL (standaard: https://www.forest.brussels)")
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

    print(f"[Forest] documenten ophalen (laatste {maanden} maanden)...")
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
