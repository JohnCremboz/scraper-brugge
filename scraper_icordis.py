"""
Scraper voor Icordis CMS (LCP nv) gemeenten.

Ondersteunde gemeenten:
  Eeklo, Baarle-Hertog, Kortenberg, Lanaken,
  Bilzen-Hoeselt, Houthulst, Oostkamp

URL-patroon in simba-source.csv: */file/download

Gebruik:
    uv run python scraper_icordis.py --gemeente eeklo --maanden 12
    uv run python scraper_icordis.py --alle --maanden 6
    uv run python scraper_icordis.py --lijst
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

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------

DUTCH_MONTHS: dict[str, int] = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

# Geshortcut aliassen voor URL-formaat (mrt, apr, …)
_MONTH_ABBREV: dict[str, str] = {
    "jan": "januari", "feb": "februari", "mrt": "maart", "apr": "april",
    "jun": "juni", "jul": "juli", "aug": "augustus",
    "sep": "september", "okt": "oktober", "nov": "november", "dec": "december",
}


def _normaliseer_maand(token: str) -> int | None:
    t = token.lower()
    if t in DUTCH_MONTHS:
        return DUTCH_MONTHS[t]
    if t in _MONTH_ABBREV:
        return DUTCH_MONTHS[_MONTH_ABBREV[t]]
    return None


def datum_uit_url(url: str) -> date | None:
    """Probeer een datum te destilleren uit een Icordis-URL.

    Herkent:
    - DD-maandnaam-YYYY  (bijv. /notulen-gr-15-januari-2026)
    - YYYYMMDD            (bijv. /gemeenteraad_notulen_20260202)
    - van-YYYYMMDD        (bijv. /besluitenlijst_..._van_20260309)
    """
    pad = urlparse(url).path.lower()

    # Patroon: YYYYMMDD
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Patroon: DD-maandnaam-YYYY of DD-maandafkorting-YYYY
    m = re.search(
        r"-(\d{1,2})-([a-z]+)-(20\d{2})",
        pad,
    )
    if m:
        dag, maand_str, jaar = int(m.group(1)), m.group(2), int(m.group(3))
        maand = _normaliseer_maand(maand_str)
        if maand:
            try:
                return date(jaar, maand, dag)
            except ValueError:
                pass

    return None


# ---------------------------------------------------------------------------
# Per-gemeente configuratie
# ---------------------------------------------------------------------------
# Sleutel = netloc van de base_url (zoals doorgegeven via --base-url)
# listing_pad: pad naar de pagina met vergaderingslinks
# vergadering_re: patroon om vergaderingspaden te herkennen (relatieve paden)
# jaar_re: optioneel — patroon voor tussenliggende jaarpagina's
#          (als aanwezig, volgen we eerst de jaarpagina's, dan de vergaderingen)

GEMEENTEN: dict[str, dict] = {
    "www.eeklo.be": {
        "naam": "Eeklo",
        "listing_pad": "/gemeenteraad",
        "jaar_re": re.compile(r"^/gemeenteraad-\d{4}$"),
        # Vergadering-URLs: /gemeenteraad-DD-maandnaam-YYYY (begint met 1-2 cijfers gevolgd door een letter)
        "vergadering_re": re.compile(r"^/gemeenteraad-\d{1,2}-[a-z]"),
    },
    "www.baarle-hertog.be": {
        "naam": "Baarle-Hertog",
        "listing_pad": "/bekendmakingen/categorie/12/gemeenteraad",
        "vergadering_re": re.compile(r"^/bekendmakingen/detail/\d+/"),
    },
    "www.kortenberg.be": {
        "naam": "Kortenberg",
        "listing_pad": "/gemeenteraad-bekendmakingen",
        "vergadering_re": re.compile(
            r"^/(gemeenteraad|besluitenlijst)[_\-]"
        ),
    },
    "www.lanaken.be": {
        "naam": "Lanaken",
        "listing_pad": "/bekendmakingen/categorie/24/gemeente-en-ocmw-raad",
        "vergadering_re": re.compile(r"^/gemeente-en-ocmw-raad-"),
    },
    "www.bilzenhoeselt.be": {
        "naam": "Bilzen-Hoeselt",
        "listing_pad": "/gemeenteraad",
        "vergadering_re": re.compile(
            r"^/(notulen-gr|besluitenlijst-gr|goedkeuring-notulen)"
        ),
    },
    "www.houthulst.be": {
        "naam": "Houthulst",
        "listing_pad": "/notulen-gemeenteraad",
        "vergadering_re": re.compile(
            r"^/(notulen-gr|bekendmakingen/detail)"
        ),
    },
    "www.oostkamp.be": {
        "naam": "Oostkamp",
        "listing_pad": "/bekendmakingen/categorie/1/bestuursorganen",
        "vergadering_re": re.compile(
            r"^/bekendmakingen/detail/\d+/gemeenteraad"
        ),
    },
}

SESSION = None
_config: ScraperConfig | None = None
BASE_URL = ""


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


def _get(url: str) -> "requests.Response | None":
    return rate_limited_get(SESSION, url, _config)


def _absolute(pad: str) -> str:
    if pad.startswith("http"):
        return pad
    return urljoin(BASE_URL + "/", pad.lstrip("/"))


# ---------------------------------------------------------------------------
# Vergaderingen ophalen
# ---------------------------------------------------------------------------

def _links_van_pagina(html: str, patroon: re.Pattern) -> list[str]:
    """Geef alle unieke relatieve paden die matchen met `patroon`."""
    soup = BeautifulSoup(html, "lxml")
    gezien: set[str] = set()
    resultaat: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0].rstrip("/")
        # Normaliseer naar relatief pad
        parsed = urlparse(href)
        pad = parsed.path if parsed.path else href
        if patroon.search(pad) and pad not in gezien:
            gezien.add(pad)
            resultaat.append(pad)
    return resultaat


def haal_vergaderingen(
    config: dict,
    grensdatum: date,
) -> list[tuple[str, date | None]]:
    """
    Haal een lijst van (url, datum) op voor vergaderingen na grensdatum.

    Bij een twee-niveaustructuur (jaar_re aanwezig) worden eerst
    de jaarpagina's opgehaald, dan de vergaderingen daarbinnen.
    """
    listing_url = _absolute(config["listing_pad"])
    resp = _get(listing_url)
    if not resp or resp.status_code != 200:
        logger.warning("Listing niet bereikbaar: %s", listing_url)
        return []

    html = resp.text
    vergadering_re = config["vergadering_re"]
    jaar_re = config.get("jaar_re")

    if jaar_re:
        # Twee-niveau: listing → jaarpagina's → vergaderingen
        jaar_paden = _links_van_pagina(html, jaar_re)
        # Filter op relevante jaren (huidig jaar en jaar van grensdatum)
        min_jaar = grensdatum.year
        jaar_paden = [
            p for p in jaar_paden
            if (m := re.search(r"(\d{4})$", p)) and int(m.group(1)) >= min_jaar
        ]
        vergadering_paden: list[str] = []
        for jp in jaar_paden:
            r = _get(_absolute(jp))
            if r and r.status_code == 200:
                vergadering_paden.extend(_links_van_pagina(r.text, vergadering_re))
    else:
        vergadering_paden = _links_van_pagina(html, vergadering_re)

    resultaat: list[tuple[str, date | None]] = []
    for pad in vergadering_paden:
        url = _absolute(pad)
        datum = datum_uit_url(url)
        if datum is None or datum >= grensdatum:
            resultaat.append((url, datum))
    return resultaat


# ---------------------------------------------------------------------------
# Downloads ophalen van een vergaderingspagina
# ---------------------------------------------------------------------------

def haal_downloads(vergadering_url: str) -> list[str]:
    """Haal alle /file/download/-URLs op van een vergaderingspagina."""
    resp = _get(vergadering_url)
    if not resp or resp.status_code != 200:
        logger.warning("Vergadering niet bereikbaar: %s", vergadering_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    gezien: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/file/download/" not in href:
            continue
        full_url = _absolute(href)
        if full_url not in gezien:
            gezien.add(full_url)
            urls.append(full_url)
    return urls


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    """Compatibel met startwizard; geeft Gemeenteraad terug."""
    return [{"naam": "Gemeenteraad", "uuid": "gemeenteraad"}]


def _zoek_gemeente(netloc: str) -> dict | None:
    netloc = netloc.lower().replace("www.", "www.")
    for sleutel, conf in GEMEENTEN.items():
        if sleutel == netloc or sleutel.lstrip("www.") == netloc.lstrip("www."):
            return conf
    return None


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
    document_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape één Icordis-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    from base_scraper import DownloadResult

    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    vergaderingen = haal_vergaderingen(config, grensdatum)
    logger.info("   %d vergadering(en) gevonden", len(vergaderingen))

    alle_resultaten: list[DownloadResult] = []

    for verg_url, verg_datum in vergaderingen:
        datum_str = verg_datum.isoformat() if verg_datum else "onbekend"
        logger.debug("  📅 %s  %s", datum_str, verg_url)

        download_urls = haal_downloads(verg_url)
        if not download_urls:
            logger.debug("     (geen downloads)")
            continue

        for doc_url in download_urls:
            # Optioneel: filter op bestandsnaam-hint
            if document_filter:
                if document_filter.lower() not in doc_url.lower():
                    continue

            hint = sanitize_filename(f"{datum_str}_{Path(doc_url.split('?')[0]).name}")
            result = download_document(
                SESSION, _config,
                doc_url,
                gem_dir,
                filename_hint=hint,
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
        description="Scraper voor Icordis CMS gemeenten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL van de gemeente (bijv. https://www.eeklo.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam of sleutel (bijv. eeklo)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter", help="Filter op URL-fragment (bijv. notulen)")
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

    # Bepaal welke gemeenten te verwerken
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
        # Initialiseer sessie per gemeente als we --alle gebruiken
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
