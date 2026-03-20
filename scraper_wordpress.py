"""
Scraper voor WordPress-gemeenten (Duitstalige gemeenten) met directe PDF-links.

Ondersteunde gemeenten:
  Bütgenbach, Kelmis, Lontzen, Raeren, Burg-Reuland, Eupen, Sankt Vith

URL-patroon in simba-source.csv: */wp-content/uploads* of www.st.vith.be

Gebruik:
    uv run python scraper_wordpress.py --gemeente butgenbach --maanden 12
    uv run python scraper_wordpress.py --alle --maanden 6
    uv run python scraper_wordpress.py --lijst
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

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

SESSION = None
_config: ScraperConfig | None = None
BASE_URL = ""

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------
# listing_pad:       pad naar de pagina met PDF-links
# listing_paden:     lijst van paden (meerdere listingpagina's)
# jaar_navigatie:    True → gebruik jaarpagina's (Sankt Vith/Plone)
# jaar_pad_re:       regex om jaarpagina-links te herkennen
# pdf_re:            patroon om PDF-links te herkennen (default: WP uploads)
# extra_pdf_domeinen: extra domeinen waarvan PDF-links geaccepteerd worden

_WP_PDF_RE = re.compile(r"/wp-content/uploads/.*\.pdf", re.IGNORECASE)
_ST_VITH_PDF_RE = re.compile(r"/de/buergerservice-politik/.*\.pdf", re.IGNORECASE)
_ST_VITH_JAAR_RE = re.compile(r"protokolle/(\d{4})$")

GEMEENTEN: dict[str, dict] = {
    "butgenbach.be": {
        "naam": "Bütgenbach",
        "listing_pad": "/buergerservice/verwaltung/sekretariat/",
        # PDF: /wp-content/uploads/YYYY/MM/GR20251216-Internet.pdf
        # Datum in bestandsnaam: YYYYMMDD na prefix "GR"
    },
    "www.kelmis.be": {
        "naam": "Kelmis",
        "listing_pad": "/politik/gemeinderat",
        # PDF: /wp-content/uploads/YYYY/MM/Protokoll-Ratssitzung-DD.M.YYYY.pdf
        # Datum in bestandsnaam: DD.M.YYYY of DD.MM.YYYY
    },
    "lontzen.be": {
        "naam": "Lontzen",
        "listing_pad": "/gemeinderat",
        # PDF: /wp-content/uploads/YYYY/MM/bestandsnaam.pdf
        # Datum uit URL-pad /YYYY/MM/
    },
    "www.raeren.be": {
        "naam": "Raeren",
        "listing_pad": "/gemeinderat",
        "extra_pdf_domeinen": ["static.raeren.be"],
        # PDF: https://static.raeren.be/wp-content/uploads/YYYYMMDD*.pdf
    },
    "www.burg-reuland.be": {
        "naam": "Burg-Reuland",
        "listing_paden": [
            "/unsere-gemeinde/politik/sitzungen/tagesordnungen-des-gemeinderats",
            "/unsere-gemeinde/politik/sitzungen/sitzungsprotokolle-des-gemeinderats",
        ],
        # PDF: /wp-content/uploads/to-gemeinderat-vom-DD-MM-YYYY*.pdf
    },
    "www.eupen.be": {
        "naam": "Eupen",
        "listing_pad": "/politik-verwaltung/politik/stadtrat/",
        # PDF: /wp-content/uploads/YYYY/MM/*.pdf
    },
    "www.st.vith.be": {
        "naam": "Sankt Vith",
        "listing_pad": "/de/buergerservice-politik/politik/stadtrat/protokolle/",
        "jaar_navigatie": True,
        "pdf_re": _ST_VITH_PDF_RE,
        # PDF: /de/.../protokolle/YYYY/YYYY/YYYYMMDD_sitzung-des-stadtrates_protokoll.pdf
    },
}


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

def init_session(base_url: str) -> None:
    global SESSION, _config, BASE_URL
    parsed = urlparse(base_url)
    BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
    _config = ScraperConfig(
        base_url=BASE_URL,
        rate_limit_delay=0.5,
        timeout=30,
    )
    SESSION = create_session(_config)
    # WordPress-sites verwachten een browser User-Agent
    SESSION.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })


def _get(url: str):
    return rate_limited_get(SESSION, url, _config)


def _absolute(pad: str) -> str:
    if pad.startswith("http"):
        return pad
    return urljoin(BASE_URL + "/", pad.lstrip("/"))


# ---------------------------------------------------------------------------
# Datum extractie
# ---------------------------------------------------------------------------

def datum_uit_pad(pad: str) -> date | None:
    """Probeer datum te destilleren uit een WordPress-bestandspad of -naam."""
    # Bestandsnaam: YYYYMMDD (bijv. GR20251216-Internet.pdf, 20250127_sitzung...pdf)
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Bestandsnaam: DD.M.YYYY of DD.MM.YYYY (bijv. Protokoll-Ratssitzung-18.12.2023.pdf)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # URL-pad: /wp-content/uploads/YYYY/MM/ (upload-datum, minder nauwkeurig)
    m = re.search(r"/wp-content/uploads/(20\d{2})/(\d{2})/", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# PDF-links verzamelen
# ---------------------------------------------------------------------------

def _pdfs_van_html(
    html: str,
    base_url: str,
    pdf_re: re.Pattern = _WP_PDF_RE,
    extra_domeinen: list[str] | None = None,
) -> list[dict]:
    """Verzamel alle PDF-links uit HTML, geef {'url', 'naam'} terug."""
    base_netloc = urlparse(base_url).netloc
    extra = set(extra_domeinen or [])
    soup = BeautifulSoup(html, "lxml")
    gezien: set[str] = set()
    resultaat: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = href if href.startswith("http") else urljoin(base_url + "/", href.lstrip("/"))
        parsed = urlparse(full_url)
        netloc = parsed.netloc
        pad = parsed.path

        # Domein moet overeenkomen (hoofddomein of extra CDN-domein).
        # Strip "www." voor vergelijking zodat www.X en X als hetzelfde gelden.
        if (netloc != base_netloc and
                netloc.lstrip("www.") != base_netloc.lstrip("www.") and
                netloc not in extra):
            continue

        # PDF-patroon controleren
        if netloc in extra:
            # Extra domeinen: elk .pdf-bestand accepteren
            if not pad.lower().endswith(".pdf"):
                continue
        else:
            if not pdf_re.search(pad):
                continue

        if full_url in gezien:
            continue
        gezien.add(full_url)
        naam = a.get_text(strip=True) or Path(pad).name
        resultaat.append({"url": full_url, "naam": naam})

    return resultaat


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    return [{"naam": "Gemeinderat", "uuid": "gemeinderat"}]


def _zoek_gemeente(netloc: str) -> dict | None:
    for sleutel, conf in GEMEENTEN.items():
        if sleutel == netloc or sleutel.lstrip("www.") == netloc.lstrip("www."):
            return conf
    return None


def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
    document_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape één WordPress-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    pdf_re = config.get("pdf_re", _WP_PDF_RE)
    extra_domeinen = config.get("extra_pdf_domeinen")

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    # Bepaal listing-URL's
    if "listing_paden" in config:
        listing_urls = [_absolute(p) for p in config["listing_paden"]]
    else:
        listing_urls = [_absolute(config["listing_pad"])]

    # Verzamel alle PDF-bronpagina's
    paginas: list[tuple[str, str]] = []

    for listing_url in listing_urls:
        resp = _get(listing_url)
        if not resp or resp.status_code != 200:
            logger.warning("Listing niet bereikbaar: %s (HTTP %s)",
                           listing_url, getattr(resp, "status_code", "?"))
            continue

        html = resp.text

        if config.get("jaar_navigatie"):
            # Jaarpagina-navigatie (Sankt Vith / Plone)
            soup = BeautifulSoup(html, "lxml")
            gezien_jaren: set[int] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = href if href.startswith("http") else _absolute(href)
                m = _ST_VITH_JAAR_RE.search(urlparse(full).path)
                if m:
                    jaar = int(m.group(1))
                    if jaar >= grensdatum.year and jaar not in gezien_jaren:
                        gezien_jaren.add(jaar)
                        r = _get(full)
                        if r and r.status_code == 200:
                            paginas.append((r.text, full))
        else:
            paginas.append((html, listing_url))

    if not paginas:
        return 0, 0

    # Verzamel alle PDFs
    alle_pdfs: list[dict] = []
    for pagina_html, pagina_url in paginas:
        pdfs = _pdfs_van_html(pagina_html, pagina_url, pdf_re, extra_domeinen)
        for pdf in pdfs:
            pad = urlparse(pdf["url"]).path
            datum = datum_uit_pad(pad)
            if datum is not None and datum < grensdatum:
                continue
            if document_filter:
                naam_lower = pdf["naam"].lower()
                url_lower = pdf["url"].lower()
                if (document_filter.lower() not in naam_lower and
                        document_filter.lower() not in url_lower):
                    continue
            alle_pdfs.append(pdf)

    logger.info("   %d PDF(s) gevonden", len(alle_pdfs))

    alle_resultaten: list[DownloadResult] = []
    for pdf in alle_pdfs:
        hint = sanitize_filename(Path(urlparse(pdf["url"]).path).name)
        result = download_document(
            SESSION, _config,
            pdf["url"],
            gem_dir,
            filename_hint=hint or pdf["naam"],
            require_pdf=True,
        )
        alle_resultaten.append(result)

    gedownload = sum(1 for r in alle_resultaten if r.success and not r.skipped)
    print_summary(alle_resultaten, naam=naam)
    return len(alle_resultaten), gedownload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor WordPress-gemeenten (Duitstalige regio)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL van de gemeente (bijv. https://www.kelmis.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam (bijv. kelmis)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter", help="Filter op bestandsnaam (bijv. protokoll)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter protokoll")
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
        args.document_filter = "protokoll"

    if args.lijst or args.lijst_organen:
        print("Ondersteunde gemeenten:")
        for netloc, conf in GEMEENTEN.items():
            print(f"  {conf['naam']:25s}  https://{netloc}/")
        return

    te_verwerken: list[dict] = []

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [conf]
        init_session(args.base_url)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "").replace("ü", "u")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "").replace("ü", "u")
            if zoek in naam_sleutel or zoek in netloc:
                te_verwerken = [conf]
                init_session(f"https://{netloc}")
                break
        if not te_verwerken:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden. Gebruik --lijst.")
            sys.exit(1)
    elif args.alle:
        te_verwerken = list(GEMEENTEN.values())
    else:
        parser.print_help()
        sys.exit(1)

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    totaal_geprobeerd = 0
    totaal_gedownload = 0

    for conf in te_verwerken:
        if args.alle and not args.base_url:
            netloc = next(k for k, v in GEMEENTEN.items() if v is conf)
            init_session(f"https://{netloc}")

        gevonden, gedownload = scrape_gemeente(
            conf,
            output_root,
            maanden=args.maanden,
            document_filter=args.document_filter,
        )
        totaal_geprobeerd += gevonden
        totaal_gedownload += gedownload

    print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    main()
