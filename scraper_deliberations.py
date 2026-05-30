"""
Scraper voor deliberations.be - beslissingen en publicaties.

deliberations.be is een transparantieplatform van iMio (Plone CMS) voor ~167
Waalse gemeenten. Publicatiemodel: beslissing per beslissing (uittreksels),
niet als volledig PV-document. Uitzondering: Mons en Seneffe hebben volledige PV-PDFs.

Twee types content:
- /decisions: beslissingstekst per agendapunt (HTML, zelden PDF-bijlage)
- /publications: officieel gepubliceerde documenten (arrêtés, règlements,
  uittreksels) — PDFs via JS-rendered @@download links (Playwright vereist)

Output: PDF-bestanden + JSON-metadata (geen HTML).

Gebruik:
    python scraper_deliberations.py --gemeente liege
    python scraper_deliberations.py --alle
    python scraper_deliberations.py --lijst
"""

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    robust_get,
    logger,
    download_document,
    DownloadResult,
)

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

BASE_URL = "https://deliberations.be"
SESSION: requests.Session | None = None
_config: ScraperConfig | None = None

_FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}


def init_session(base_url: str | None = None) -> None:
    """Initialiseer HTTP-sessie."""
    global SESSION, _config, BASE_URL
    if base_url:
        BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.3)
    SESSION = create_session(_config)


def _get(url: str) -> requests.Response | None:
    """GET helper — pad wordt relatief aan BASE_URL opgelost."""
    full_url = url if url.startswith("http") else f"{BASE_URL}{url}"
    return robust_get(SESSION, full_url, retries=3, timeout=30)


def _parse_french_date(datum_str: str) -> str | None:
    """
    Parse French date strings like "02 mars 2026" or "2 Mars 2026 (séance)".
    Returns ISO date string or None.
    """
    if not datum_str:
        return None
    s = datum_str.split("(")[0].strip().lower()
    parts = s.split()
    if len(parts) == 3:
        try:
            day = int(parts[0])
            month = _FRENCH_MONTHS.get(parts[1])
            year = int(parts[2])
            if month and 1 <= day <= 31 and 1900 <= year <= 2100:
                return date(year, month, day).isoformat()
        except (ValueError, TypeError):
            pass
    return None


# ---------------------------------------------------------------------------
# Gemeenten lijst (van CSV)
# ---------------------------------------------------------------------------

def haal_gemeenten_lijst() -> list[str]:
    """Haal lijst van deliberations.be gemeenten uit simba-source.csv."""
    csv_path = Path(__file__).parent / "simba-source.csv"
    if not csv_path.exists():
        logger.warning("simba-source.csv niet gevonden")
        return []

    gemeenten = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        if not reader.fieldnames:
            logger.warning("CSV-header ontbreekt in simba-source.csv")
            return []
        if "Bron" not in reader.fieldnames:
            logger.warning("Kolom 'Bron' ontbreekt in simba-source.csv")
            return []
        for row in reader:
            bron = row.get('Bron', '')
            if 'deliberations.be' in bron:
                parsed = urlparse(bron)
                gemeente = parsed.path.strip('/').split('/')[0]
                if gemeente:
                    gemeenten.append(gemeente)

    return sorted(set(gemeenten))


# ---------------------------------------------------------------------------
# Items ophalen via faceted query (beslissingen + publicaties)
# ---------------------------------------------------------------------------

_PAGE_SIZE = 20  # server ignores b_size, always returns 20


def _parse_card(item, gemeente: str) -> dict | None:
    """Parse one item-card div into a result dict."""
    link = item.find("a", href=True)
    if not link:
        return None

    item_url = urljoin(f"{BASE_URL}/{gemeente}", link["href"])

    titel_elem = item.find(["h2", "h3", "h4"])
    titel = titel_elem.get_text(strip=True) if titel_elem else link.get_text(strip=True)

    metadata = {}
    for row in item.find_all("div", class_="item-metadata-row"):
        label_elem = row.find("div", class_="item-metadata-label")
        if not label_elem:
            continue
        label_text = label_elem.get_text(strip=True)
        # Value is in a <span> or <a> sibling — get text from row minus the label
        label_elem.extract()
        value_text = row.get_text(strip=True)
        if label_text and value_text:
            metadata[label_text] = value_text

    card_classes = " ".join(item.get("class", []))
    status = "projet" if "in_project" in card_classes else "definitief"

    datum_str = metadata.get("Séance") or metadata.get("Date")
    datum = _parse_french_date(datum_str) if datum_str else None

    return {
        "titel": titel,
        "url": item_url,
        "datum": datum,
        "status": status,
        "metadata": metadata,
    }


def _haal_items(
    gemeente: str,
    endpoint: str,
    min_datum: date | None = None,
) -> list[dict]:
    """
    Haal alle items op van een faceted_query endpoint, gepagineerd.

    Stopt zodra een volledige pagina items terug heeft die ouder zijn dan
    min_datum (items zijn gesorteerd nieuwste-eerst), of wanneer de server
    minder dan _PAGE_SIZE items teruggeeft (laatste pagina).
    """
    base_url = f"{BASE_URL}/{gemeente}/{endpoint}/@@faceted_query"
    result = []
    b_start = 0

    while True:
        params = {"b_size": str(_PAGE_SIZE), "b_start": str(b_start)}
        resp = _get(base_url + "?" + urlencode(params))
        if resp is None or not resp.text.strip():
            break

        # Plone login wall: some municipalities gate their decisions endpoint.
        # The server returns HTTP 200 with an auth form instead of data.
        if "cookies ne sont pas activés" in resp.text or "fieldname-__ac_name" in resp.text:
            logger.warning("Login wall op %s/%s — niet publiek toegankelijk", gemeente, endpoint)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.find_all("div", class_="item-card")
        if not cards:
            break

        cutoff_reached = False
        for card in cards:
            parsed = _parse_card(card, gemeente)
            if parsed is None:
                continue
            if min_datum and parsed["datum"] and parsed["datum"] < min_datum.isoformat():
                cutoff_reached = True
                break
            result.append(parsed)

        if cutoff_reached or len(cards) < _PAGE_SIZE:
            break

        b_start += _PAGE_SIZE

    if not result:
        logger.warning("Geen item-card elementen gevonden voor %s/%s", gemeente, endpoint)

    return result


def haal_beslissingen(gemeente: str, min_datum: date | None = None) -> list[dict]:
    return _haal_items(gemeente, "decisions", min_datum)


def haal_publicaties(gemeente: str, min_datum: date | None = None) -> list[dict]:
    return _haal_items(gemeente, "publications", min_datum)


# ---------------------------------------------------------------------------
# Zoek documenten op item pagina
# ---------------------------------------------------------------------------

_playwright_browser = None


def _verwerk_item_pagina(
    url: str,
    fallback_pdf_path: "Path | None" = None,
) -> "tuple[list[dict], bool]":
    """
    Open item pagina, zoek PDF/Word-documenten (@@download links, JS-rendered).
    Als geen gevonden en fallback_pdf_path opgegeven: sla pagina op als PDF.
    Returns: (documenten, pdf_gegenereerd)
    """
    global _playwright_browser
    page = None
    try:
        page = _playwright_browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=20000)
        html = page.content()
    except PlaywrightTimeout:
        logger.warning("Playwright timeout: %s", url)
        if page:
            try:
                page.close()
            except Exception:
                pass
        return [], False
    except Exception as exc:
        logger.warning("Playwright fout op %s: %s", url, exc)
        if page:
            try:
                page.close()
            except Exception:
                pass
        return [], False

    soup = BeautifulSoup(html, "lxml")
    documenten = []
    seen_base_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        href_lower = href.lower()

        doc_type = None
        if ".pdf" in href_lower:
            doc_type = "pdf"
        elif ".docx" in href_lower or ".doc" in href_lower:
            doc_type = "word"
        elif "@@download" in href_lower:
            doc_type = "pdf"

        if not doc_type:
            continue

        full_url = urljoin(url, href)
        base_url = full_url.split("/@@download/")[0] if "/@@download/" in full_url else full_url
        if base_url in seen_base_urls:
            continue
        seen_base_urls.add(base_url)

        if not text:
            url_path = urlparse(base_url).path
            text = url_path.rstrip("/").split("/")[-1] or "document"

        documenten.append({"url": full_url, "naam": text, "type": doc_type})

    pdf_gegenereerd = False
    if not documenten and fallback_pdf_path is not None and not fallback_pdf_path.exists():
        try:
            fallback_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(fallback_pdf_path), format="A4")
            pdf_gegenereerd = True
        except Exception as exc:
            logger.warning("HTML→PDF mislukt voor %s: %s", url, exc)

    try:
        page.close()
    except Exception:
        pass

    return documenten, pdf_gegenereerd


def zoek_documenten(item_url: str) -> list[dict]:
    """Zoek documenten (PDF) op een item detail pagina via Playwright."""
    docs, _ = _verwerk_item_pagina(item_url)
    return docs


def _bouw_fallback_pdf_pad(output_dir: "Path", item: dict) -> "Path":
    """Bouw output pad voor HTML→PDF fallback: {output_dir}/{datum}/{nr:03d}_{titel}.pdf"""
    datum = item.get("datum") or "onbekend"
    punt_nr_str = (item.get("metadata") or {}).get("Numéro du point", "0")
    try:
        punt_nr = int(punt_nr_str)
    except (ValueError, TypeError):
        punt_nr = 0
    titel = sanitize_filename(item["titel"][:80])
    return output_dir / datum / f"{punt_nr:03d}_{titel}.pdf"

# ---------------------------------------------------------------------------
# Hoofdlogica - Scrape gemeente
# ---------------------------------------------------------------------------

def scrape_gemeente(
    gemeente: str,
    output_dir: Path,
    maanden: int | None = None,
    download_pdfs: bool = True,
    html_naar_pdf: bool = True,
) -> tuple[int, int]:
    """
    Scrape een gemeente van deliberations.be.

    Args:
        maanden: Haal items op uit de afgelopen N maanden (None = alles).
        html_naar_pdf: Als True, genereer PDF van HTML voor beslissingen zonder bijlage.
    Returns: (aantal_items, aantal_documenten)
    """
    global _playwright_browser

    output_dir.mkdir(parents=True, exist_ok=True)

    from dateutil.relativedelta import relativedelta
    min_datum: date | None = None
    if maanden:
        min_datum = date.today() - relativedelta(months=maanden)

    logger.info("Gemeente: %s — %s/%s", gemeente, BASE_URL, gemeente)

    logger.info("[1] Beslissingen ophalen...")
    beslissingen = haal_beslissingen(gemeente, min_datum)
    logger.info("    %d beslissingen gevonden", len(beslissingen))

    logger.info("[2] Publicaties ophalen...")
    publicaties = haal_publicaties(gemeente, min_datum)
    logger.info("    %d publicaties gevonden", len(publicaties))

    alle_items = beslissingen + publicaties

    if not alle_items:
        logger.warning("Geen items gevonden voor %s", gemeente)
        return 0, 0

    metadata = {
        "gemeente": gemeente,
        "datum": date.today().isoformat(),
        "aantal_beslissingen": len(beslissingen),
        "aantal_publicaties": len(publicaties),
        "beslissingen": beslissingen,
        "publicaties": publicaties,
    }

    metadata_file = output_dir / f"{gemeente}_metadata.json"

    doc_count = 0
    if download_pdfs:
        logger.info("[3] Documenten zoeken via Playwright...")

        pw_ctx = sync_playwright().start()
        _playwright_browser = pw_ctx.chromium.launch(headless=True)

        try:
            for item in tqdm(alle_items, desc="Items controleren"):
                fallback_path = (
                    _bouw_fallback_pdf_pad(output_dir, item) if html_naar_pdf else None
                )
                documenten, pdf_gegenereerd = _verwerk_item_pagina(item["url"], fallback_path)

                if pdf_gegenereerd:
                    doc_count += 1
                    logger.info("      [HTML→PDF] %s", fallback_path.name)
                    item["documenten"] = [{
                        "naam": fallback_path.name,
                        "url": item["url"],
                        "local_file": str(fallback_path),
                        "type": "html-pdf",
                    }]
                elif documenten:
                    doc_types = ", ".join(set(d["type"] for d in documenten))
                    logger.info("    %d document(en) gevonden (%s): %s",
                                len(documenten), doc_types, item["titel"][:50])
                    item["documenten"] = []

                    for doc in documenten:
                        if SESSION and _config:
                            result = download_document(
                                SESSION, _config,
                                doc["url"], output_dir, doc["naam"],
                                require_pdf=(doc["type"] == "pdf"),
                            )
                            if result.success and not result.skipped:
                                doc_count += 1
                                logger.info("      [%s] %s", doc["type"].upper(), result.path.name)
                            item["documenten"].append({
                                "naam": doc["naam"],
                                "url": doc["url"],
                                "local_file": result.path.name if result.success else None,
                                "type": doc["type"],
                            })
        finally:
            _playwright_browser.close()
            pw_ctx.stop()
            _playwright_browser = None

        if doc_count == 0:
            logger.warning("Geen documenten gevonden/gedownload voor %s", gemeente)

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    logger.info("    JSON: %s", metadata_file.name)

    return len(alle_items), doc_count


def haal_organen_statisch() -> list[dict]:
    """Deliberations.be heeft geen orgaanindeling — geeft altijd lege lijst terug."""
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor deliberations.be gemeenten (metadata + documenten)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  python scraper_deliberations.py --lijst
  python scraper_deliberations.py --gemeente liege
  python scraper_deliberations.py --gemeente braine-lalleud --max-items 50
  python scraper_deliberations.py --base-url https://deliberations.be/liege --output pdfs/liege
  python scraper_deliberations.py --alle --output-dir data/deliberations
        """,
    )

    parser.add_argument("--base-url", type=str,
                        help="Volledige gemeente-URL (bijv. https://deliberations.be/liege)")
    parser.add_argument("--orgaan", type=str,
                        help="Orgaan — deliberations.be heeft geen organen, wordt genegeerd")
    parser.add_argument("--maanden", type=int, default=None,
                        help="Periode in maanden (wordt omgezet naar --max-items)")
    parser.add_argument("--output", type=str, default=None,
                        help="Uitvoermap (alias voor --output-dir)")
    parser.add_argument("--document-filter", type=str,
                        help="Documentfilter — wordt genegeerd voor deliberations.be")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Individuele besluiten — wordt genegeerd voor deliberations.be")
    parser.add_argument("--zichtbaar", action="store_true",
                        help="Browser zichtbaar — deliberations.be gebruikt geen browser")

    parser.add_argument("--gemeente", "-g", type=str,
                        help="Gemeente-slug (bijv. liege); gebruik --lijst voor opties")
    parser.add_argument("--alle", action="store_true",
                        help="Scrape alle deliberations.be gemeenten (zonder --base-url)")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon lijst van beschikbare gemeenten")
    parser.add_argument("--output-dir", "-o", type=str, default="pdfs",
                        help="Output directory (standaard: pdfs)")
    parser.add_argument("--no-pdfs", action="store_true",
                        help="Sla alleen metadata op, geen documenten downloaden")
    parser.add_argument("--no-html-pdf", action="store_true",
                        help="Geen PDF genereren van HTML als er geen bijlage gevonden wordt")

    args = parser.parse_args()

    if args.base_url and not args.gemeente:
        slug = urlparse(args.base_url).path.strip("/").split("/")[0]
        if slug:
            args.gemeente = slug

    if args.output and args.output_dir == "pdfs":
        args.output_dir = args.output

    init_session()

    if args.lijst:
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            logger.error("Geen gemeenten gevonden in CSV")
            return
        for i, gemeente in enumerate(gemeenten, 1):
            print(f"  {i:3}. {gemeente}")
        print(f"\n   Totaal: {len(gemeenten)} gemeenten")
        return

    if not args.gemeente and not args.alle:
        logger.error("Geef --gemeente, --base-url of --alle op (gebruik --lijst voor opties)")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    download_pdfs = not args.no_pdfs
    html_naar_pdf = not args.no_html_pdf
    maanden = args.maanden

    if args.gemeente:
        totaal_items, totaal_docs = scrape_gemeente(
            args.gemeente,
            output_dir / args.gemeente,
            maanden,
            download_pdfs,
            html_naar_pdf,
        )
        logger.info("Klaar — items: %d, documenten: %d", totaal_items, totaal_docs)
        return

    if args.alle:
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            logger.error("Geen gemeenten gevonden in simba-source.csv")
            sys.exit(1)

        logger.info("Scraping %d gemeenten...", len(gemeenten))

        totaal_items = 0
        totaal_docs = 0

        for i, gemeente in enumerate(gemeenten, 1):
            logger.info("[%d/%d] %s", i, len(gemeenten), gemeente)
            items, docs = scrape_gemeente(
                gemeente,
                output_dir / gemeente,
                maanden,
                download_pdfs,
                html_naar_pdf,
            )
            totaal_items += items
            totaal_docs += docs

            if i < len(gemeenten):
                time.sleep(1)

        logger.info(
            "Alle gemeenten klaar — %d gemeenten, %d items, %d documenten — output: %s",
            len(gemeenten), totaal_items, totaal_docs, output_dir.resolve(),
        )


if __name__ == "__main__":
    main()
