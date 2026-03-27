"""
Scraper voor Irisnet (publi.irisnet.be) — Brusselse gemeenten.

Plaform: Editoria e-publications van gemeenten in het Brussels Gewest.
API-structuur:
  /web/organizations           -> lijst van organisaties met vipKeys
  /web/categoryContent?vipKey= -> mappen (top-level) van een organisatie
  /web/categoryComplete?vipKey=-> items (datum-mappen of publicaties) in een map
  /web/download?pubKey=        -> bestand downloaden

Gebruik:
    uv run python scraper_irisnet.py --gemeente Anderlecht --maanden 12
    uv run python scraper_irisnet.py --alle --maanden 6
    uv run python scraper_irisnet.py --lijst
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

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

BASE_URL = "https://publi.irisnet.be"
CSV_PATH = Path(__file__).parent / "simba-source.csv"

SESSION = None
_config: ScraperConfig | None = None
ORG_KEY: str | None = None  # org_key voor de huidige gemeente (gezet via GEMEENTE_URL)


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

def init_session(base_url: str = BASE_URL) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(
        base_url=BASE_URL,
        rate_limit_delay=0.3,
        timeout=60,
    )
    SESSION = create_session(_config)


def haal_organen_statisch() -> list[dict]:
    """Haal mappen (= organen) op voor de huidige gemeente via ORG_KEY.

    ORG_KEY wordt gezet door start.py via de volledige gemeente-URL
    (die de vipKey as query-parameter bevat).
    """
    if not ORG_KEY:
        return []
    if SESSION is None:
        init_session()
    mappen = haal_mappen(ORG_KEY)
    return [{"naam": m["naam"], "uuid": m["key"]} for m in mappen]


# ---------------------------------------------------------------------------
# HTTP-hulpfunctie
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, ajax: bool = True):
    """Rate-limited GET, optioneel met AJAX Accept-header."""
    headers = {"Accept": "text/html;type=ajax"} if ajax else {}
    return rate_limited_get(SESSION, url, _config, params=params, headers=headers)


# ---------------------------------------------------------------------------
# Organisatielijst ophalen
# ---------------------------------------------------------------------------

def haal_org_keys() -> dict[str, str]:
    """Haal alle organisaties op van de publi.irisnet.be/web/organizations pagina.

    Returns:
        Dict met organisatienaam → vipKey (zonder 'O'-prefix).
    """
    r = _get(f"{BASE_URL}/web/organizations", ajax=False)
    if not r or r.status_code != 200:
        logger.warning("Kon organisatielijst niet ophalen van %s/web/organizations", BASE_URL)
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    result: dict[str, str] = {}
    for opt in soup.find_all("option"):
        val = opt.get("value", "")
        txt = opt.get_text(strip=True)
        if not val or "vipKey=" not in val:
            continue
        m = re.search(r"vipKey=([A-Za-z0-9_\-]+)", val)
        if m:
            result[txt] = m.group(1)
    logger.debug("Gevonden organisaties op platform: %s", list(result.keys()))
    return result


# ---------------------------------------------------------------------------
# CSV lezen
# ---------------------------------------------------------------------------

def haal_gemeenten_van_csv() -> list[dict]:
    """Lees irisnet-rijen uit simba-source.csv.

    Returns:
        Lijst van dicts met 'gemeente', 'url', 'org_key' (of None als niet in URL).
    """
    gemeenten = []
    try:
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                gemeente = row.get("Gemeente", "").strip()
                bron = row.get("Bron", "").strip()
                if "publi.irisnet.be" not in bron:
                    continue
                m = re.search(r"vipKey=([A-Za-z0-9_\-]+)", bron)
                org_key = m.group(1) if m else None
                gemeenten.append({
                    "gemeente": gemeente,
                    "url": bron,
                    "org_key": org_key,
                })
    except FileNotFoundError:
        logger.warning("simba-source.csv niet gevonden op %s", CSV_PATH)
    return gemeenten


# ---------------------------------------------------------------------------
# Mappen en items ophalen
# ---------------------------------------------------------------------------

def haal_mappen(org_key: str) -> list[dict]:
    """Haal top-level mappen op voor een organisatie.

    Returns:
        Lijst van dicts met 'key' en 'naam'.
    """
    r = _get(f"{BASE_URL}/web/categoryContent", params={"vipKey": org_key})
    if not r or r.status_code != 200:
        logger.warning("Kon mappen niet ophalen voor org_key=%s (HTTP %s)",
                       org_key, getattr(r, "status_code", "?"))
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    mappen: list[dict] = []
    gezien: set[str] = set()
    for el in soup.find_all(True):
        bk = el.get("data-bk")
        if not bk or bk == org_key or bk in gezien:
            continue
        gezien.add(bk)
        naam = el.get_text(strip=True)
        if naam:
            mappen.append({"key": bk, "naam": naam})
    return mappen


def haal_datum_items(folder_key: str, grensdatum: date) -> Iterator[dict]:
    """Haal datum-items op in een map die op of na grensdatum vallen.

    Verwerkt zowel platte structuren (tekst = ISO-datum) als geneste structuren
    met een tussenliggend jaar-niveau (bijv. Jette: map → jaarmap → sessie).

    Yields:
        Dicts met 'key', 'datum' (date-object) en optioneel 'label'.
    """
    r = _get(f"{BASE_URL}/web/categoryComplete", params={"vipKey": folder_key})
    if not r or r.status_code != 200:
        return

    soup = BeautifulSoup(r.text, "html.parser")
    gezien: set[str] = set()

    for el in soup.find_all(True):
        bk = el.get("data-bk")
        if not bk or bk == folder_key or bk in gezien:
            continue
        gezien.add(bk)
        tekst = el.get_text(strip=True)

        # ── Jaarmap (bijv. "2024", "2025") → recursief inladen ─────────────
        if re.fullmatch(r"\d{4}", tekst):
            jaar = int(tekst)
            if jaar >= grensdatum.year:
                yield from haal_datum_items(bk, grensdatum)
            continue

        # ── Directe ISO-datum (bijv. "2025-01-29") ─────────────────────────
        try:
            item_datum = date.fromisoformat(tekst)
            if item_datum >= grensdatum:
                yield {"key": bk, "datum": item_datum, "label": tekst}
            continue
        except ValueError:
            pass

        # ── Datum achteraan in sessiebeschrijving (bijv. "Council of 2025-01-29") ──
        m = re.search(r"(\d{4}-\d{2}-\d{2})$", tekst)
        if m:
            try:
                item_datum = date.fromisoformat(m.group(1))
                if item_datum >= grensdatum:
                    yield {"key": bk, "datum": item_datum, "label": tekst}
                continue
            except ValueError:
                pass

        # ── Datum in DD-MM-YYYY-formaat (bijv. "Council of 27-01-2016") ────
        m = re.search(r"(\d{2})-(\d{2})-(\d{4})$", tekst)
        if m:
            try:
                item_datum = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                if item_datum >= grensdatum:
                    yield {"key": bk, "datum": item_datum, "label": tekst}
            except ValueError:
                pass


def haal_publicaties(item_key: str) -> list[dict]:
    """Haal publicaties op voor een datum-item.

    Returns:
        Lijst van dicts met 'pub_key', 'titel', 'datum', 'url'.
    """
    r = _get(f"{BASE_URL}/web/categoryComplete", params={"vipKey": item_key})
    if not r or r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    publicaties: list[dict] = []
    gezien_keys: set[str] = set()

    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue

        titel = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        datum_txt = cells[3].get_text(strip=True) if len(cells) > 3 else ""

        for a in tr.find_all("a", href=True):
            href = a["href"]
            if "download?pubKey=" not in href:
                continue
            m = re.search(r"pubKey=([A-Za-z0-9_\-]+)", href)
            if not m:
                continue
            pk = m.group(1)
            if pk in gezien_keys:
                continue
            gezien_keys.add(pk)
            publicaties.append({
                "pub_key": pk,
                "titel": titel,
                "datum": datum_txt,
                "url": f"{BASE_URL}/web/download?pubKey={pk}",
            })

    return publicaties


# ---------------------------------------------------------------------------
# HTML-output genereren
# ---------------------------------------------------------------------------

def _genereer_html(gemeente: str, docs: list[dict], output_dir: Path) -> None:
    """Genereer een HTML-indexpagina voor alle gevonden documenten."""
    from html_output import genereer_html_kaarten
    html_path = output_dir / f"{sanitize_filename(gemeente)}.html"
    genereer_html_kaarten(
        naam=gemeente,
        bron_url=BASE_URL,
        docs=docs,
        output_pad=html_path,
    )
    logger.info("HTML-index gegenereerd: %s", html_path)


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def scrape_gemeente(
    gemeente: str,
    org_key: str,
    output_dir: Path,
    maanden: int = 12,
    map_filter: str | None = None,
    doc_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape alle publicaties voor één gemeente.

    Args:
        gemeente:    Naam van de gemeente.
        org_key:     VipKey van de organisatie op publi.irisnet.be.
        output_dir:  Basismap voor downloads.
        maanden:     Terugkijkperiode in maanden.
        map_filter:  Optioneel: filter op mapnaam (substring, niet hoofdlettergevoelig).
        doc_filter:  Optioneel: filter op documenttitel/bestandsnaam (substring).

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    from base_scraper import DownloadResult

    grensdatum = (datetime.today() - timedelta(days=30 * maanden)).date()
    gem_dir = output_dir / sanitize_filename(gemeente)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (vipKey=%s, grensdatum=%s)", gemeente, org_key, grensdatum)

    mappen = haal_mappen(org_key)
    if not mappen:
        logger.warning("Geen mappen gevonden voor %s", gemeente)
        return 0, 0

    if map_filter:
        mappen = [m for m in mappen if map_filter.lower() in m["naam"].lower()]
        logger.info("  Filter '%s' -> %d map(pen) over", map_filter, len(mappen))

    alle_docs: list[dict] = []
    results: list[DownloadResult] = []

    for map_item in mappen:
        map_naam = map_item["naam"]
        logger.info("  📁 %s", map_naam)
        for item in haal_datum_items(map_item["key"], grensdatum):
            datum_str = item["datum"].isoformat()
            publicaties = haal_publicaties(item["key"])
            if not publicaties:
                continue

            for pub in publicaties:
                # Sla over als documenttitel niet overeenkomt met doc_filter
                if doc_filter and doc_filter.lower() not in (pub.get("titel") or "").lower():
                    continue
                hint = sanitize_filename(
                    f"{datum_str}_{pub['titel']}" if pub["titel"] else datum_str
                )
                result = download_document(
                    SESSION, _config,
                    pub["url"],
                    gem_dir,
                    filename_hint=hint,
                    require_pdf=False,
                )
                results.append(result)
                alle_docs.append({
                    **pub,
                    "map": map_naam,
                    "datum_item": datum_str,
                    "local_path": str(result.path) if result.path else None,
                })

    gedownload = sum(1 for r in results if r.success and not r.skipped)

    if alle_docs:
        _genereer_html(gemeente, alle_docs, gem_dir)

    print_summary(results, naam=gemeente)
    return len(results), gedownload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Irisnet (publi.irisnet.be) — Brusselse gemeenten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--gemeente", help="Naam (of deel) van de gemeente")
    parser.add_argument("--alle", action="store_true", help="Alle irisnet-gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true", help="Toon beschikbare gemeenten en sluit af")
    parser.add_argument("--base-url", default=BASE_URL, help="Basis-URL (standaard: publi.irisnet.be)")
    parser.add_argument("--maanden", type=int, default=12, help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs", help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--orgaan", help="Filter op mapnaam, bijv. 'Conseil communal'")
    parser.add_argument("--document-filter", help="Filter op documenttitel (substring)")
    parser.add_argument("--notulen", action="store_true", help="Snelkoppeling voor --document-filter notulen")
    parser.add_argument("--debug", action="store_true", help="Uitgebreide logging")
    args = parser.parse_args()

    if args.debug:
        from base_scraper import set_log_level
        set_log_level("DEBUG")

    init_session(args.base_url)

    logger.info("Ophalen organisatielijst van %s...", BASE_URL)
    platform_keys = haal_org_keys()
    csv_gemeenten = haal_gemeenten_van_csv()

    # Vul ontbrekende org_keys aan via naamsovereenkomst met platform
    for g in csv_gemeenten:
        if not g["org_key"]:
            match = next(
                (platform_keys[k] for k in platform_keys if k.lower() == g["gemeente"].lower()),
                None,
            )
            g["org_key"] = match

    if args.lijst:
        print("\nGemeenten in simba-source.csv (irisnet-type):")
        for g in csv_gemeenten:
            status = "✅" if g["org_key"] else "❌ niet op platform"
            print(f"  {status}  {g['gemeente']}")
        print("\nAlle communes op publi.irisnet.be:")
        for naam in sorted(platform_keys):
            print(f"  {naam}  ->  {platform_keys[naam]}")
        sys.exit(0)

    if args.gemeente:
        zoek = args.gemeente.lower()
        te_verwerken = [g for g in csv_gemeenten if zoek in g["gemeente"].lower()]
        if not te_verwerken:
            print(f"Geen gemeente gevonden met '{args.gemeente}' in simba-source.csv.")
            sys.exit(1)
    elif args.alle:
        te_verwerken = csv_gemeenten
    else:
        parser.print_help()
        sys.exit(1)

    output_dir = Path(args.output)
    map_filter = args.orgaan or None
    doc_filter = args.document_filter or ("notulen" if args.notulen else None)

    totaal_gevonden = 0
    totaal_gedownload = 0

    for g in te_verwerken:
        org_key = g.get("org_key")
        if not org_key:
            logger.warning(
                "⚠️  %s: geen vipKey gevonden op %s — overgeslagen",
                g["gemeente"], BASE_URL,
            )
            continue
        gevonden, gedownload = scrape_gemeente(
            g["gemeente"], org_key, output_dir, args.maanden,
            map_filter=map_filter, doc_filter=doc_filter,
        )
        totaal_gevonden += gevonden
        totaal_gedownload += gedownload

    print(f"\nKlaar. Totaal: {totaal_gevonden} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    main()
