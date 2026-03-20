"""
Scraper voor Pubcon-gemeenten (Tobibus) — via publiek LBLOD-endpoint.

Ondersteunde gemeenten:
  Oudsbergen

Navigatiestructuur:
  /LBLOD → Zittingen → /Zitting/Details/{id} → Agendapunten
  → /Agendapunt/Details/{id} → AgendaPuntItemDetails
  → /Agendapunt/AgendaPuntItemDetails/{id} → blob PDF-links

Documenttypes (prefixen):
  N  = Notulen
  BD = Besluit Document
  ABL = Aanvullende Besluitenlijst
  I  = Inkomend stuk
  P  = Processtuk

Gebruik:
    uv run python scraper_pubcon.py --gemeente oudsbergen --maanden 12
    uv run python scraper_pubcon.py --alle --maanden 6
    uv run python scraper_pubcon.py --lijst
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

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

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------

GEMEENTEN: dict[str, dict] = {
    "oudsbergen": {
        "naam": "Oudsbergen",
        "base_url": "https://app-pubcon-oudsbergen.azurewebsites.net",
        "blob_prefix": "stpubconoudsbergen.blob.core.windows.net",
    },
}

# Orgaan-types in de tabel
ORGAAN_MAP = {
    "GR": "Gemeenteraad",
    "CBS": "College van Burgemeester en Schepenen",
    "BURG": "Burgemeester",
    "RMW": "Raad voor Maatschappelijk Welzijn",
    "OCMW-RMW": "OCMW-Raad",
    "VB": "Vast Bureau",
    "OCMW-VB": "OCMW Vast Bureau",
}

_DATUM_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

def init_session(base_url: str) -> None:
    global SESSION, _config
    _config = ScraperConfig(
        base_url=base_url,
        rate_limit_delay=0.3,
        timeout=30,
    )
    SESSION = create_session(_config)


def _get(url: str):
    return rate_limited_get(SESSION, url, _config)


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _parse_zittingen(html: str) -> list[dict]:
    """Parse de LBLOD-tabel en geef lijst van zittingen."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return []

    resultaat = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        orgaan = cells[0].get_text(strip=True)
        datum_tekst = cells[1].get_text(strip=True)
        link = row.find("a", href=True)
        if not link:
            continue

        m = _DATUM_RE.match(datum_tekst)
        if m:
            try:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                d = None
        else:
            d = None

        resultaat.append({
            "orgaan": orgaan,
            "datum": d,
            "datum_tekst": datum_tekst,
            "pad": link["href"],
        })
    return resultaat


def _verzamel_links(soup: BeautifulSoup, patroon: str) -> list[str]:
    """Verzamel unieke paden die een patroon bevatten."""
    gezien: set[str] = set()
    resultaat: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if patroon in href and href not in gezien:
            gezien.add(href)
            resultaat.append(href)
    return resultaat


def _verzamel_blob_docs(soup: BeautifulSoup, blob_prefix: str) -> list[dict]:
    """Verzamel unieke blob-PDF-links uit een pagina."""
    gezien: set[str] = set()
    resultaat: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if blob_prefix in href and href not in gezien:
            gezien.add(href)
            naam = a.get_text(strip=True) or Path(href).name
            resultaat.append({"url": href, "naam": naam})
    return resultaat


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    return [{"naam": k, "uuid": k} for k in ORGAAN_MAP]


def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
    orgaan_filter: str | None = None,
    document_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape één Pubcon-gemeente via LBLOD-endpoint.

    Navigatie: LBLOD → Zittingen → Agendapunten → Items → Blob-PDFs

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    base_url = config["base_url"]
    blob_prefix = config["blob_prefix"]
    naam = config["naam"]
    grensdatum = date.today() - timedelta(days=maanden * 31)

    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    init_session(base_url)
    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    # Stap 1: Haal zittingen-overzicht
    resp = _get(base_url + "/LBLOD")
    if not resp or resp.status_code != 200:
        logger.warning("LBLOD niet bereikbaar: %s", base_url)
        return 0, 0

    zittingen = _parse_zittingen(resp.text)
    logger.info("   %d zittingen gevonden", len(zittingen))

    # Filter op orgaan
    if orgaan_filter:
        of = orgaan_filter.upper()
        zittingen = [z for z in zittingen if z["orgaan"] == of]
        logger.info("   %d na orgaanfilter (%s)", len(zittingen), of)

    # Filter op datum
    zittingen = [z for z in zittingen if z["datum"] is None or z["datum"] >= grensdatum]
    logger.info("   %d na datumfilter", len(zittingen))

    if not zittingen:
        return 0, 0

    # Stap 2: Per zitting → agendapunten → items → documenten
    alle_docs: list[dict] = []
    gezien_urls: set[str] = set()

    for idx, zitting in enumerate(zittingen, 1):
        logger.debug("Zitting %d/%d: %s %s",
                      idx, len(zittingen), zitting["orgaan"], zitting["datum_tekst"])

        # Haal zitting-detailpagina
        zitting_url = base_url + zitting["pad"]
        resp_z = _get(zitting_url)
        if not resp_z or resp_z.status_code != 200:
            continue
        soup_z = BeautifulSoup(resp_z.text, "lxml")

        # Blob-docs direct op zitting-pagina (zeldzaam)
        for doc in _verzamel_blob_docs(soup_z, blob_prefix):
            if doc["url"] not in gezien_urls:
                gezien_urls.add(doc["url"])
                doc["zitting"] = zitting
                alle_docs.append(doc)

        # Agendapunten
        ap_paden = _verzamel_links(soup_z, "/Agendapunt/Details/")

        for ap_pad in ap_paden:
            resp_ap = _get(base_url + ap_pad)
            if not resp_ap or resp_ap.status_code != 200:
                continue
            soup_ap = BeautifulSoup(resp_ap.text, "lxml")

            # Blob-docs op agendapunt-niveau
            for doc in _verzamel_blob_docs(soup_ap, blob_prefix):
                if doc["url"] not in gezien_urls:
                    gezien_urls.add(doc["url"])
                    doc["zitting"] = zitting
                    alle_docs.append(doc)

            # Agendapunt-items (sub-items)
            item_paden = _verzamel_links(soup_ap, "AgendaPuntItemDetails/")

            for item_pad in item_paden:
                resp_item = _get(base_url + item_pad)
                if not resp_item or resp_item.status_code != 200:
                    continue
                soup_item = BeautifulSoup(resp_item.text, "lxml")

                for doc in _verzamel_blob_docs(soup_item, blob_prefix):
                    if doc["url"] not in gezien_urls:
                        gezien_urls.add(doc["url"])
                        doc["zitting"] = zitting
                        alle_docs.append(doc)

    logger.info("   %d unieke document(en) gevonden", len(alle_docs))

    # Stap 3: Filter op documenttype
    if document_filter:
        df = document_filter.lower()
        alle_docs = [d for d in alle_docs
                     if df in d["naam"].lower() or df in d["url"].lower()]
        logger.info("   %d na documentfilter", len(alle_docs))

    # Stap 4: Download
    alle_resultaten: list[DownloadResult] = []
    for doc in alle_docs:
        fname = Path(doc["url"]).name
        # Prefix met zitting-datum voor sorteerbaarheid
        zitting = doc.get("zitting", {})
        d = zitting.get("datum")
        org = zitting.get("orgaan", "")
        if d:
            prefix = f"{d.isoformat()}_{org}_"
        else:
            prefix = f"{zitting.get('datum_tekst', 'onbekend')}_{org}_"
        hint = sanitize_filename(prefix + fname)

        result = download_document(
            SESSION, _config,
            doc["url"],
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
        description="Scraper voor Pubcon-gemeenten (Tobibus LBLOD-endpoint)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL (bijv. https://app-pubcon-oudsbergen.azurewebsites.net)")
    parser.add_argument("--gemeente", help="Gemeentenaam (bijv. oudsbergen)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Filter op orgaan (bijv. GR, CBS, RMW)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter",
                        help="Filter op documentnaam (bijv. N voor notulen)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand: alleen notulen (N-prefix)")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon beschikbare organen")
    parser.add_argument("--debug", action="store_true",
                        help="Uitgebreide logging")
    args = parser.parse_args()

    if args.debug:
        from base_scraper import set_log_level
        set_log_level("DEBUG")

    if args.notulen and not args.document_filter:
        args.document_filter = "/N2"

    if args.lijst:
        print("Ondersteunde gemeenten:")
        for sleutel, conf in GEMEENTEN.items():
            print(f"  {conf['naam']:25s}  {conf['base_url']}")
        return

    if args.lijst_organen:
        print("Organen:")
        for code, naam in ORGAAN_MAP.items():
            print(f"  {code:10s}  {naam}")
        return

    te_verwerken: list[dict] = []

    if args.base_url:
        # Zoek config op base_url
        for sleutel, conf in GEMEENTEN.items():
            if conf["base_url"] in args.base_url:
                te_verwerken = [conf]
                break
        if not te_verwerken:
            print(f"[!] Geen configuratie gevonden voor {args.base_url}")
            sys.exit(1)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "")
        for sleutel, conf in GEMEENTEN.items():
            if zoek in sleutel or zoek in conf["naam"].lower():
                te_verwerken = [conf]
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
        gevonden, gedownload = scrape_gemeente(
            conf,
            output_root,
            maanden=args.maanden,
            orgaan_filter=args.orgaan,
            document_filter=args.document_filter,
        )
        totaal_geprobeerd += gevonden
        totaal_gedownload += gedownload

    print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    main()
