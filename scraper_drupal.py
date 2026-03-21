"""
Scraper voor Drupal-gemeenten met directe PDF-links.

Ondersteunde gemeenten:
  Dilbeek, Knokke-Heist, Rijkevorsel, Willebroek, Wervik, Putte,
  Auderghem, Uccle, Laakdal, Destelbergen, Essen

URL-patroon in simba-source.csv: */sites/default/files* of */sites/*/files*

Gebruik:
    uv run python scraper_drupal.py --gemeente dilbeek --maanden 12
    uv run python scraper_drupal.py --alle --maanden 6
    uv run python scraper_drupal.py --lijst
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
)

SESSION = None
_config: ScraperConfig | None = None
BASE_URL = ""

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------
# listing_pad: pad naar de pagina met PDF-links of jaarpagina-links
# jaar_re: optioneel — patroon voor jaarpagina-links op listing-pagina
# vergadering_re: optioneel — patroon voor vergadering-detailpagina-links
#   Als vergadering_re aanwezig is, navigeren we naar detailpagina's
#   en halen we daar de PDFs op.
# pdf_re: patroon om PDF-links te herkennen (default: /sites/*/files/*.pdf)

_DRUPAL_PDF_RE = re.compile(
    r"(?:/sites/[^/]+/files/|/system/files/).*\.pdf", re.IGNORECASE
)

GEMEENTEN: dict[str, dict] = {
    "www.dilbeek.be": {
        "naam": "Dilbeek",
        "listing_pad": "/nl/agenda-en-verslagen",
    },
    "www.knokke-heist.be": {
        "naam": "Knokke-Heist",
        "listing_pad": "/gemeente-en-bestuur/bestuur/gemeenteraad",
        "jaar_re": re.compile(r"agenda-en-notulen-gemeenteraad-\d{4}"),
    },
    "www.rijkevorsel.be": {
        "naam": "Rijkevorsel",
        "listing_pad": "/gemeenteraad",
    },
    "www.willebroek.be": {
        "naam": "Willebroek",
        "listing_pad": (
            "/nl/over-willebroek/bestuur/gemeenteraad/"
            "agendas-en-verslagen-gemeenteraad"
        ),
    },
    "www.wervik.be": {
        "naam": "Wervik",
        "listing_pad": "/gemeenteraad-en-ocmw-raad",
    },
    "www.putte.be": {
        "naam": "Putte",
        "listing_pad": (
            "/bestuur-en-organisatie/bestuur/gemeenteraad/"
            "agenda-notulen-en-besluiten-0"
        ),
        "vergadering_re": re.compile(
            r"/bestuur-en-organisatie/bestuur/gemeentebestuur/gemeenteraad/"
            r"agenda-notulen-en-b"
        ),
    },
    "www.auderghem.be": {
        "naam": "Auderghem",
        "listing_pad": "/college-et-conseil",
    },
    "www.uccle.be": {
        "naam": "Uccle",
        "listing_pad": "/fr/ma-commune/le-conseil-communal",
    },
    "www.laakdal.be": {
        "naam": "Laakdal",
        "listing_pad": "/notulen-gemeenteraad",
    },
    "www.destelbergen.be": {
        "naam": "Destelbergen",
        "listing_pad": (
            "/bestuur/reglementen-besluiten"
            "?f%5B0%5D=reglementen-besluiten-orgaan%3A649"
        ),
        "vergadering_re": re.compile(
            r"^/(?:"
            r"agenda-gemeenteraad|notulen-gemeenteraad|besluitenlijst-gemeenteraad"
            r"|goedkeuring-|belasting|subsidiereglement|reglement-|bijzondere-"
            r"|algemene-|huishoudelijk-|node/\d+"
            r"|bestuur/reglementen-besluiten/(?!klacht)"
            r")"
        ),
        "pagina_max": 9,
    },
    "www.essen.be": {
        "naam": "Essen",
        "listing_pad": "/besluiten-bekendmakingen-en-zittingsdocumenten",
        "vergadering_re": re.compile(r"/\d{8}-gemeente"),
        "pagina_max": 9,
    },
}


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

def init_session(base_url: str) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(
        base_url=BASE_URL,
        rate_limit_delay=0.5,
        timeout=30,
    )
    SESSION = create_session(_config)


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
    """Probeer datum te destilleren uit een Drupal-bestandspad."""
    # /sites/default/files/YYYY-MM/bestand.pdf
    m = re.search(r"/(20\d{2})-(\d{2})/", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            pass

    # Bestandsnaam: YYYY-MM-DD (bijv. CC-2026-03-19-OJ_...)
    m = re.search(r"(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Bestandsnaam: YYYY_MM_DD (bijv. 2026_01_29_pv.pdf — Auderghem)
    m = re.search(r"(20\d{2})_(0[1-9]|1[0-2])_([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Bestands- of paginanaam: YYYYMMDD
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# PDF-links verzamelen
# ---------------------------------------------------------------------------

def _pdfs_van_html(html: str, base: str) -> list[dict]:
    """Verzamel alle PDF-links uit HTML, geef {'url', 'naam'} terug."""
    soup = BeautifulSoup(html, "lxml")
    gezien: set[str] = set()
    resultaat: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        parsed = urlparse(href)
        pad = parsed.path
        if not _DRUPAL_PDF_RE.search(pad):
            continue
        full_url = _absolute(href) if not href.startswith("http") else href
        if full_url in gezien:
            continue
        gezien.add(full_url)
        naam = a.get_text(strip=True) or Path(pad).name
        resultaat.append({"url": full_url, "naam": naam})
    return resultaat


def _vergadering_links_van_html(html: str, patroon: re.Pattern) -> list[str]:
    """Verzamel vergadering-detailpagina-links uit HTML."""
    base_netloc = urlparse(BASE_URL).netloc
    soup = BeautifulSoup(html, "lxml")
    gezien: set[str] = set()
    resultaat: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        parsed = urlparse(href)
        # Externe links (andere netloc) altijd overslaan
        if parsed.scheme and parsed.netloc and parsed.netloc != base_netloc:
            continue
        pad = parsed.path
        if patroon.search(pad) and pad not in gezien:
            gezien.add(pad)
            resultaat.append(_absolute(href) if not href.startswith("http") else href)
    return resultaat


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    return [{"naam": "Gemeenteraad", "uuid": "gemeenteraad"}]


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
    """Scrape één Drupal-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    from base_scraper import DownloadResult

    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    listing_url = _absolute(config["listing_pad"])
    resp = _get(listing_url)
    if not resp or resp.status_code != 200:
        logger.warning("Listing niet bereikbaar: %s (HTTP %s)",
                       listing_url, getattr(resp, "status_code", "?"))
        return 0, 0

    html = resp.text
    jaar_re = config.get("jaar_re")
    vergadering_re = config.get("vergadering_re")

    # Verzamel alle PDF-bronpagina's (HTML-tekst, URL)
    paginas: list[tuple[str, str]] = []

    if jaar_re:
        # Listing → jaarpagina's → PDFs
        soup = BeautifulSoup(html, "lxml")
        jaar_urls: list[str] = []
        gezien: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            parsed = urlparse(href)
            pad = parsed.path
            if jaar_re.search(pad) and pad not in gezien:
                m = re.search(r"(\d{4})$", pad)
                if m and int(m.group(1)) >= grensdatum.year:
                    gezien.add(pad)
                    jaar_urls.append(_absolute(href) if not href.startswith("http") else href)
        for jaar_url in jaar_urls:
            r = _get(jaar_url)
            if r and r.status_code == 200:
                paginas.append((r.text, jaar_url))
    elif vergadering_re:
        # Listing → vergadering-detailpagina's → PDFs
        # Optionele paginering: pagina_max geeft het max. paginanummer (0-gebaseerd)
        pagina_max = config.get("pagina_max", 0)
        listing_htmls = [(html, listing_url)]
        sep = "&" if "?" in listing_url else "?"
        for pagina_nr in range(1, pagina_max + 1):
            r = _get(f"{listing_url}{sep}page={pagina_nr}")
            if r and r.status_code == 200:
                listing_htmls.append((r.text, f"{listing_url}{sep}page={pagina_nr}"))
        gezien_verg: set[str] = set()
        for listing_html, _ in listing_htmls:
            for verg_url in _vergadering_links_van_html(listing_html, vergadering_re):
                if verg_url not in gezien_verg:
                    gezien_verg.add(verg_url)
                    r = _get(verg_url)
                    if r and r.status_code == 200:
                        paginas.append((r.text, verg_url))
    else:
        # Directe PDFs op listing-pagina
        paginas = [(html, listing_url)]

    # Verzamel alle PDFs
    alle_pdfs: list[dict] = []
    for pagina_html, pagina_url in paginas:
        pdfs = _pdfs_van_html(pagina_html, pagina_url)
        for pdf in pdfs:
            # Datumfilter
            pad = urlparse(pdf["url"]).path
            datum = datum_uit_pad(pad)
            if datum is not None and datum < grensdatum:
                continue
            # Documentfilter
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
        description="Scraper voor Drupal-gemeenten met directe PDF-links",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL van de gemeente (bijv. https://www.dilbeek.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam (bijv. dilbeek)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter", help="Filter op bestandsnaam (bijv. notulen)")
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

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [conf]
        init_session(args.base_url)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "")
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
