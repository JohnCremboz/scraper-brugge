"""
Scraper voor deliberations.be - Metadata van gemeentelijke beslissingen.

deliberations.be is een transparantieplatform van iMio (Plone CMS) dat door
~181 Waalse gemeenten wordt gebruikt voor het publiceren van gemeenteraads-
beslissingen en publicaties.

BELANGRIJKE OPMERKING:
Op dit moment (maart 2026) bevat deliberations.be voornamelijk METADATA
van beslissingen zonder consistente PDF bijlagen. De scraper verzamelt:
- Beslissingstities en beschrijvingen
- Vergaderdata
- Statussen (projet/definitief)
- Links naar externe documenten (waar beschikbaar)

Output formaten:
- JSON: Gestructureerde data voor verdere verwerking
- HTML: Visueel overzicht met modern design

Structuur:
    https://deliberations.be/{gemeente}/decisions          → beslissingen overzicht
    https://deliberations.be/{gemeente}/decisions/@@faceted_query  → items ophalen
    https://deliberations.be/{gemeente}/publications       → publicaties

Gebruik:
    python scraper_deliberations.py --gemeente liege
    python scraper_deliberations.py --alle --no-pdfs
    python scraper_deliberations.py --lijst  # Lijst alle 178 gemeenten
"""

import argparse
import csv
import html
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
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

def zoek_documenten(item_url: str) -> list[dict]:
    """
    Zoek documenten (PDF, Word) op een item detail pagina.

    Returns lijst van {url, naam, type} dicts.
    """
    resp = _get(item_url)
    if resp is None:
        return []
    if not resp.text.strip():
        logger.warning("Lege HTML-respons op item-pagina: %s", item_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    documenten = []
    seen_base_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        href_lower = href.lower()

        doc_type = None

        if ".pdf" in href_lower:
            doc_type = "pdf"
        elif ".docx" in href_lower:
            doc_type = "word"
        elif ".doc" in href_lower:
            doc_type = "word"
        elif "download" in href_lower:
            doc_type = "unknown"

        if not doc_type:
            continue

        full_url = urljoin(item_url, href)

        # Plone serves each file at both a direct URL and a @@download/file/ URL.
        # The @@download variant appends the filename again, causing double extensions
        # (e.g. foo.pdf/@@download/file/foo.pdf.pdf). Deduplicate by base object URL.
        base_url = full_url.split("/@@download/")[0] if "/@@download/" in full_url else full_url
        if base_url in seen_base_urls:
            continue
        seen_base_urls.add(base_url)

        # Prefer link text; fall back to filename from URL path (use base, not @@download path)
        if not text:
            url_path = urlparse(base_url).path
            text = url_path.rstrip("/").split("/")[-1] or "document"

        documenten.append({
            "url": full_url,
            "naam": text,
            "type": doc_type,
        })

    return documenten


# ---------------------------------------------------------------------------
# HTML Output Generator
# ---------------------------------------------------------------------------

def genereer_html(metadata: dict, output_path: Path) -> None:
    """Genereer een HTML overzicht van de metadata."""
    gemeente = metadata["gemeente"]
    datum = metadata["datum"]
    beslissingen = metadata.get("beslissingen", [])
    publicaties = metadata.get("publicaties", [])

    def e(text: str) -> str:
        return html.escape(str(text))

    html_content = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{e(gemeente.title())} - Deliberations.be</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}

        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
            margin-bottom: 30px;
            font-size: 2.2em;
        }}

        .meta-info {{
            background: #ecf0f1;
            padding: 20px;
            border-radius: 6px;
            margin-bottom: 30px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}

        .meta-item {{
            display: flex;
            flex-direction: column;
        }}

        .meta-label {{
            font-weight: 600;
            color: #7f8c8d;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .meta-value {{
            font-size: 1.3em;
            color: #2c3e50;
            margin-top: 5px;
        }}

        h2 {{
            color: #2c3e50;
            margin-top: 40px;
            margin-bottom: 20px;
            font-size: 1.8em;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .badge {{
            background: #3498db;
            color: white;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.7em;
            font-weight: 600;
        }}

        .item {{
            background: #fff;
            border: 1px solid #e0e0e0;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 4px;
            transition: all 0.2s ease;
        }}

        .item:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            transform: translateY(-2px);
        }}

        .item-title {{
            font-size: 1.15em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
            line-height: 1.4;
        }}

        .item-title a {{
            color: #2c3e50;
            text-decoration: none;
            transition: color 0.2s;
        }}

        .item-title a:hover {{
            color: #3498db;
        }}

        .item-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-top: 10px;
        }}

        .item-meta-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.9em;
            color: #7f8c8d;
        }}

        .item-meta-item strong {{
            color: #555;
        }}

        .status {{
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 600;
        }}

        .status.projet {{
            background: #fff3cd;
            color: #856404;
        }}

        .status.definitif {{
            background: #d4edda;
            color: #155724;
        }}

        .status.unknown {{
            background: #e2e3e5;
            color: #383d41;
        }}

        .metadata {{
            background: #f8f9fa;
            padding: 12px;
            border-radius: 4px;
            margin-top: 10px;
            font-size: 0.9em;
        }}

        .metadata-row {{
            display: grid;
            grid-template-columns: 150px 1fr;
            gap: 10px;
            padding: 6px 0;
            border-bottom: 1px solid #e9ecef;
        }}

        .metadata-row:last-child {{
            border-bottom: none;
        }}

        .metadata-label {{
            font-weight: 600;
            color: #6c757d;
        }}

        .metadata-value {{
            color: #495057;
        }}

        .documenten {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }}

        .doc-link {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 12px;
            background: #e8f4fd;
            color: #2980b9;
            border: 1px solid #bee3f8;
            border-radius: 20px;
            text-decoration: none;
            font-size: 0.85em;
            transition: all 0.2s;
        }}

        .doc-link:hover {{
            background: #2980b9;
            color: #fff;
            border-color: #2980b9;
        }}

        .footer {{
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            text-align: center;
            color: #7f8c8d;
            font-size: 0.9em;
        }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #95a5a6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{e(gemeente.title())}</h1>

        <div class="meta-info">
            <div class="meta-item">
                <span class="meta-label">Gemeente</span>
                <span class="meta-value">{e(gemeente.title())}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Datum scraping</span>
                <span class="meta-value">{e(datum)}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Beslissingen</span>
                <span class="meta-value">{len(beslissingen)}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Publicaties</span>
                <span class="meta-value">{len(publicaties)}</span>
            </div>
        </div>
"""

    def _render_items_section(items: list[dict], label: str, icon: str) -> str:
        if not items:
            return f"""
        <h2>{icon} {e(label)}</h2>
        <div class="empty-state">
            <p>Geen {e(label.lower())} gevonden</p>
        </div>
"""
        out = f"""
        <h2>{icon} {e(label)} <span class="badge">{len(items)}</span></h2>
"""
        for item in items:
            status_val = item.get("status", "")
            status_class = "projet" if status_val == "projet" else "definitif" if status_val == "definitief" else "unknown"
            status_text = e(status_val.title()) if status_val else "Onbekend"

            out += f"""
        <div class="item">
            <div class="item-title">
                <a href="{e(item['url'])}" target="_blank">{e(item['titel'])}</a>
            </div>
            <div class="item-meta">
                <div class="item-meta-item">
                    <span class="status {status_class}">{status_text}</span>
                </div>
            </div>
"""
            if item.get("metadata"):
                out += """
            <div class="metadata">
"""
                for key, value in item["metadata"].items():
                    if value:
                        out += f"""
                <div class="metadata-row">
                    <div class="metadata-label">{e(key)}:</div>
                    <div class="metadata-value">{e(value)}</div>
                </div>
"""
                out += """
            </div>
"""
            if item.get("documenten"):
                out += """
            <div class="documenten">
"""
                for doc in item["documenten"]:
                    doc_icon = "📄" if doc.get("type") == "pdf" else "📝"
                    href = doc.get("local_file") or doc["url"]
                    target = "" if doc.get("local_file") else ' target="_blank"'
                    out += f"""
                <a class="doc-link" href="{e(href)}"{target}>{doc_icon} {e(doc['naam'])}</a>
"""
                out += """
            </div>
"""
            out += """
        </div>
"""
        return out

    html_content += _render_items_section(beslissingen, "Beslissingen", "📋")
    html_content += _render_items_section(publicaties, "Publicaties", "📰")

    html_content += f"""
        <div class="footer">
            <p>Gegenereerd op {e(datum)} &bull; Bron: <a href="https://deliberations.be/{e(gemeente)}" target="_blank">deliberations.be/{e(gemeente)}</a></p>
        </div>
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


# ---------------------------------------------------------------------------
# Hoofdlogica - Scrape gemeente
# ---------------------------------------------------------------------------

def scrape_gemeente(
    gemeente: str,
    output_dir: Path,
    maanden: int | None = None,
    download_pdfs: bool = True,
) -> tuple[int, int]:
    """
    Scrape een gemeente van deliberations.be.

    Args:
        maanden: Haal items op uit de afgelopen N maanden (None = alles).
    Returns: (aantal_items, aantal_documenten)
    """
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

    logger.info("[3] Metadata opslaan...")

    metadata = {
        "gemeente": gemeente,
        "datum": date.today().isoformat(),
        "aantal_beslissingen": len(beslissingen),
        "aantal_publicaties": len(publicaties),
        "beslissingen": beslissingen,
        "publicaties": publicaties,
    }

    metadata_file = output_dir / f"{gemeente}_metadata.json"
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    logger.info("    JSON: %s", metadata_file.name)

    doc_count = 0
    if download_pdfs:
        logger.info("[4] Documenten zoeken (PDF, Word)...")

        for item in tqdm(alle_items, desc="Items controleren"):
            documenten = zoek_documenten(item["url"])

            if documenten:
                doc_types = ", ".join(set(d["type"] for d in documenten))
                logger.info("    %d document(en) gevonden (%s): %s", len(documenten), doc_types, item["titel"][:50])

                item["documenten"] = []

                for doc in documenten:
                    if SESSION and _config:
                        require_pdf = (doc["type"] == "pdf")

                        result = download_document(
                            SESSION,
                            _config,
                            doc["url"],
                            output_dir,
                            doc["naam"],
                            require_pdf=require_pdf,
                        )

                        if result.success and not result.skipped:
                            doc_count += 1
                            logger.info("      [%s] %s", doc["type"].upper(), result.path.name)
                            item["documenten"].append({
                                "naam": doc["naam"],
                                "url": doc["url"],
                                "local_file": result.path.name,
                                "type": doc["type"],
                            })
                        else:
                            item["documenten"].append({
                                "naam": doc["naam"],
                                "url": doc["url"],
                                "local_file": None,
                                "type": doc["type"],
                            })

        if doc_count == 0:
            logger.warning("Geen documenten gevonden/gedownload voor %s", gemeente)

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    html_file = output_dir / f"{gemeente}.html"
    genereer_html(metadata, html_file)
    logger.info("    HTML: %s", html_file.name)

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
    maanden = args.maanden

    if args.gemeente:
        totaal_items, totaal_docs = scrape_gemeente(
            args.gemeente,
            output_dir / args.gemeente,
            maanden,
            download_pdfs,
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
