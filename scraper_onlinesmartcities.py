"""
Scraper voor PDF-documenten van OnlineSmartCities / Besluitvorming-portalen

Ondersteunde portalen:
  raadpleeg-halle.onlinesmartcities.be
  besluitvorming.leuven.be
  en andere raadpleeg-*.onlinesmartcities.be / besluitvorming.*.be sites

Gebruik:
    uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-halle.onlinesmartcities.be --lijst-organen
    uv run python scraper_onlinesmartcities.py --base-url https://besluitvorming.leuven.be --orgaan "Gemeenteraad" --maanden 12
    uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-halle.onlinesmartcities.be --alle --maanden 3
    uv run python scraper_onlinesmartcities.py --base-url https://besluitvorming.leuven.be --orgaan "Gemeenteraad" --notulen --maanden 24
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    download_document as base_download_document,
    DownloadResult,
    logger,
)

BASE_URL = "https://raadpleeg-halle.onlinesmartcities.be"
KALENDER_URL = f"{BASE_URL}/zittingen/kalender"

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def init_session():
    """Initialiseer de sessie met base_scraper configuratie."""
    global SESSION, _config
    _config = ScraperConfig(base_url=BASE_URL, output_dir=Path("."))
    try:
        SESSION = create_session(_config)
    except Exception as e:
        logger.warning("Sessie-initialisatie mislukt: %s", e)

# Initialiseer direct bij import voor compatibiliteit
init_session()


def download_document(doc_url: str, bestemming: Path, filename_hint: str = "") -> bool:
    """Download een /document/{id} URL als PDF via base_scraper."""
    if SESSION is None or _config is None:
        logger.error("Sessie niet geïnitialiseerd")
        return False

    result = base_download_document(
        session=SESSION,
        config=_config,
        doc_url=doc_url,
        output_dir=bestemming,
        filename_hint=filename_hint,
        require_pdf=True,
    )

    if not result.success and result.error:
        logger.debug("Download fout %s: %s", doc_url, result.error)

    return result.success


def haal_document_links_van_pagina(url: str) -> list[dict]:
    """Haal alle /document/ links op van een pagina (via requests+BS4)."""
    documenten = []
    try:
        full_url = urljoin(BASE_URL, url) if not url.startswith("http") else url
        resp = SESSION.get(full_url, timeout=30)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/document/" in href:
                tekst = link.get_text(strip=True) or href.split("/")[-1]
                documenten.append({"url": href, "naam": tekst})
    except Exception as e:
        print(f"      [!] Fout ophalen {url}: {e}")

    return documenten


def haal_extra_subpaginas(vergadering_url: str) -> list[str]:
    """
    Zoek bijkomendeagenda- en andere subpagina-links op de vergaderingspagina.
    Leuven heeft bijv. /zittingen/{id}/bijkomendeagenda/{id} links.
    Geeft volledige URLs terug.
    """
    extra = []
    try:
        resp = SESSION.get(vergadering_url, timeout=30)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        verg_pad = vergadering_url.rstrip("/").split(BASE_URL)[-1]  # pad-gedeelte
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Subpagina's van deze vergadering die geen agendapunten zijn
            if (verg_pad in href or href.startswith(verg_pad)) and \
               "/agendapunten/" not in href and \
               "/agenda" not in href and \
               "/besluitenlijst" not in href and \
               href != verg_pad and href.rstrip("/") != verg_pad.rstrip("/"):
                full = urljoin(BASE_URL, href)
                if full not in extra and full != vergadering_url:
                    extra.append(full)
    except Exception as e:
        print(f"      [!] Fout extra subpagina's {vergadering_url}: {e}")
    return extra


def haal_agenda_punten(vergadering_url: str) -> list[str]:
    """Haal alle agendapunt-URLs op van een vergaderingspagina."""
    agendapunten = []
    try:
        resp = SESSION.get(vergadering_url, timeout=30)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/agendapunten/" in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in agendapunten:
                    agendapunten.append(full_url)
    except Exception as e:
        print(f"      [!] Fout agendapunten {vergadering_url}: {e}")

    return agendapunten


def vergadering_heeft_inhoud(vergadering_url: str) -> tuple[bool, str]:
    """
    Controleer of een vergadering gepubliceerde inhoud heeft.
    Geeft (heeft_inhoud, titel) terug.
    """
    try:
        resp = SESSION.get(vergadering_url, timeout=15)
        if resp.status_code != 200:
            return False, ""

        soup = BeautifulSoup(resp.text, "lxml")
        tekst = soup.get_text()

        if "De inhoud van deze zitting is (nog) niet bekendgemaakt" in tekst:
            return False, ""

        # Haal titel op
        h1 = soup.find("h1")
        if h1:
            for a in h1.find_all("a"):
                a.decompose()
            titel = h1.get_text(strip=True)
        else:
            titel = vergadering_url.split("/")[-1]

        return True, titel

    except Exception:
        return False, ""


# Synoniemen die gemeenten gebruiken voor officiële vergaderingsverslagen/notulen.
# Meerdere termen omdat er geen standaard bestaat over de portalen heen.
NOTULEN_SYNONIEMEN: list[str] = [
    "notulen",
    "verslag",
    "zittingsverslag",
    "besluitenlijst",
    "ontwerpbesluitenbundel",
    "dagorde",
]


def document_filter_match(doc_naam: str, document_filter: list[str] | str | None) -> bool:
    """
    Controleer of een documentnaam voldoet aan het filter.
    document_filter kan zijn:
      - None: altijd True (geen filter)
      - str: één term (achterwaartse compatibiliteit)
      - list[str]: één of meer termen, elk wordt als deelstring gecheckt (OR-logica)
    """
    if not document_filter:
        return True
    naam_lower = doc_naam.lower()
    if isinstance(document_filter, str):
        return document_filter.lower() in naam_lower
    return any(term.lower() in naam_lower for term in document_filter)


def verwerk_vergadering(vergadering_url: str, output_pad: Path,
                        ook_agendapunten: bool = False,
                        orgaan_filter: str | None = None,
                        document_filter: list[str] | str | None = None) -> int:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft het aantal nieuw gedownloade PDFs terug.
    Als document_filter opgegeven is, worden alleen documenten waarvan de naam
    één van de opgegeven termen bevat gedownload (OR-logica).
    Geef een lijst door voor meerdere termen (bv. NOTULEN_SYNONIEMEN).
    """
    heeft_inhoud, titel = vergadering_heeft_inhoud(vergadering_url)
    if not heeft_inhoud:
        return 0

    verg_id = vergadering_url.rstrip("/").split("/")[-1]

    print(f"\n    [{titel}] {verg_id}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        # Documentnaam-filter (ondersteunt enkelvoudige string en lijst van termen)
        if not document_filter_match(doc["naam"], document_filter):
            return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [OK] {naam_hint[:70]}")
        return succes

    # 1. Documenten van de vergaderingspagina zelf
    doc_links = haal_document_links_van_pagina(vergadering_url)

    # 2. Standaard subpagina's: agenda en besluitenlijst
    for subpad in [f"{vergadering_url}/agenda", f"{vergadering_url}/besluitenlijst"]:
        doc_links += haal_document_links_van_pagina(subpad)

    # 3. Bijkomendeagenda en andere dynamische subpagina's (bv. Leuven)
    for subpagina_url in haal_extra_subpaginas(vergadering_url):
        doc_links += haal_document_links_van_pagina(subpagina_url)

    for doc in doc_links:
        if verwerk_doc(doc, output_pad):
            downloads += 1

    # 4. Optioneel: agendapunten
    if ook_agendapunten:
        agendapunt_urls = haal_agenda_punten(vergadering_url)
        if agendapunt_urls:
            for ap_url in tqdm(agendapunt_urls, desc="      Agendapunten", leave=False):
                for doc in haal_document_links_van_pagina(ap_url):
                    if verwerk_doc(doc, output_pad):
                        downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


def haal_organen_statisch() -> list[dict]:
    """
    Haal de organen op uit de statische HTML van de kalender.
    Gebruikt <select id='organs' multiple> met <option value='UUID'>.
    Geeft lijst van {naam, uuid} terug.
    """
    try:
        resp = SESSION.get(KALENDER_URL, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")
        select = soup.find("select", id="organs")
        if not select:
            return []
        return [
            {"naam": opt.get_text(strip=True), "uuid": opt.get("value", "")}
            for opt in select.find_all("option")
            if opt.get("value")
        ]
    except Exception as e:
        print(f"  [!] Fout laden organen: {e}")
        return []


def zoek_orgaan_uuid(orgaan_naam: str) -> tuple[str | None, str | None]:
    """
    Zoek UUID van een orgaan op naam (hoofdletterongevoelig, deel-match).
    Geeft (uuid, exacte_naam) terug of (None, None).
    """
    for org in haal_organen_statisch():
        if orgaan_naam.lower() in org["naam"].lower() or org["naam"].lower() in orgaan_naam.lower():
            return org["uuid"], org["naam"]
    return None, None


def haal_vergadering_links_van_pagina(page) -> list[str]:
    """Haal alle vergaderingslinks op van de huidige kalenderweergave."""
    links = []
    try:
        zitting_links = page.locator("a[href*='/zittingen/']")
        count = zitting_links.count()
        for i in range(count):
            link = zitting_links.nth(i)
            href = link.get_attribute("href") or ""
            if "/zittingen/" in href and "kalender" not in href and "lijst" not in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in links:
                    links.append(full_url)
    except Exception as e:
        print(f"  [!] Fout bij ophalen vergaderlinks: {e}")
    return links


def activeer_orgaan_filter(page, orgaan_naam: str) -> bool:
    """Activeer het orgaanfilter via de <select id='organs'> en Select2."""
    uuid, exacte_naam = zoek_orgaan_uuid(orgaan_naam)
    if not uuid:
        print(f"  [!] Orgaan '{orgaan_naam}' niet gevonden.")
        print("      Gebruik --lijst-organen voor beschikbare namen.")
        return False
    try:
        # Gebruik Playwright select_option op het onderliggende <select>
        page.select_option("select#organs", value=[uuid])
        # Trigger change-event zodat Select2/kalender reageert
        page.evaluate("document.getElementById('organs').dispatchEvent(new Event('change'))")
        page.wait_for_load_state("networkidle", timeout=10000)
        time.sleep(0.5)
        print(f"  Filter actief: {exacte_naam} (UUID: {uuid})")
        return True
    except Exception as e:
        print(f"  [!] Filter klikken mislukt: {e}")
        print(f"  => Post-filter op titelnaam wordt gebruikt")
        return False


def navigeer_vorige_maand(page) -> str | None:
    """Ga naar de vorige maand."""
    try:
        vorige = page.locator("li.page-item.previous a").first
        titel_attr = vorige.get_attribute("title") or ""
        vorige.click()
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(0.5)
        return titel_attr or huidige_maand_titel(page)
    except Exception as e:
        print(f"  [!] Maandnavigatie mislukt: {e}")
    return None


def huidige_maand_titel(page) -> str:
    """Haal de huidige maandtitel op."""
    try:
        el = page.locator("li.page-item.current a").first
        return el.inner_text().strip()
    except Exception:
        return ""


def toon_organen():
    """Toon alle beschikbare organen (rechtstreeks uit statische HTML)."""
    organen = haal_organen_statisch()
    if not organen:
        print("Geen organen gevonden.")
        return
    print(f"\nBeschikbare organen op {BASE_URL}:")
    print("-" * 50)
    for org in organen:
        print(f"  - {org['naam']}")


def scrape(orgaan: str | None, output_map: str, maanden: int,
           ook_agendapunten: bool = False, headless: bool = True,
           document_filter: list[str] | str | None = None):
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Scraper: {BASE_URL}")
    print(f"  Orgaan:  {orgaan or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    print(f"  Incl. agendapunten: {'Ja' if ook_agendapunten else 'Nee (gebruik --agendapunten)'}")
    if isinstance(document_filter, list):
        filter_weergave = ", ".join(document_filter)
    else:
        filter_weergave = document_filter or "Geen (alle documenten)"
    print(f"  Documentfilter: {filter_weergave}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    alle_vergadering_urls: set[str] = set()
    totaal_downloads = 0
    vergaderingen_met_docs = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
            )
            page = context.new_page()

            print("[1] Kalender laden...")
            print("    (verbinding maken...)")
            try:
                page.goto(KALENDER_URL, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                print("    [!] Timeout bij networkidle, probeer verder met load...")
                page.goto(KALENDER_URL, wait_until="load", timeout=30000)
            print("    (wacht op elementen...)")
            time.sleep(1)
            print("    OK")

            if orgaan:
                print(f"[2] Filter instellen: {orgaan}")
                print("    (zoeken in beschikbare organen...)")
                if activeer_orgaan_filter(page, orgaan):
                    print("    OK - Filter actief")
                else:
                    print("    [!] Filter kon niet ingesteld worden")
            else:
                print("[2] Geen filter (alle organen)")

            print(f"[3] Doorzoek {maanden} maand(en)...\n")

            for maand_nr in range(maanden):
                maand_titel = huidige_maand_titel(page)
                print(f"  [{maand_titel or f'Maand {maand_nr+1}'}]")
                print(f"    (laden van vergaderingen...)")

                vergaderingen = haal_vergadering_links_van_pagina(page)
                nieuwe = [v for v in vergaderingen if v not in alle_vergadering_urls]
                alle_vergadering_urls.update(vergaderingen)

                print(f"    {len(vergaderingen)} vergaderingen gevonden, {len(nieuwe)} nieuw te verwerken")

                for idx, verg_url in enumerate(nieuwe, 1):
                    print(f"    ({idx}/{len(nieuwe)}) verwerken...", end="", flush=True)
                    n = verwerk_vergadering(
                        verg_url, output_pad, ook_agendapunten,
                        orgaan_filter=None,
                        document_filter=document_filter,
                    )
                    print(f" -> {n} PDF(s)")
                    totaal_downloads += n
                    if n > 0:
                        vergaderingen_met_docs += 1

                if maand_nr < maanden - 1:
                    print(f"    (navigeren naar vorige maand...)")
                    nieuwe_maand = navigeer_vorige_maand(page)
                    if nieuwe_maand is None:
                        print(f"\n  [!] Kan niet verder terug, gestopt na {maand_nr+1} maand(en).")
                        break
                print()

        finally:
            browser.close()

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  Vergaderingen met documenten: {vergaderingen_met_docs}")
    print(f"  PDFs gedownload: {totaal_downloads}")
    print(f"  Output map: {output_pad.resolve()}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Scraper voor OnlineSmartCities / Besluitvorming-portalen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-halle.onlinesmartcities.be --lijst-organen
  uv run python scraper_onlinesmartcities.py --base-url https://besluitvorming.leuven.be --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper_onlinesmartcities.py --base-url https://besluitvorming.leuven.be --orgaan "Gemeenteraad" --notulen --maanden 24
  uv run python scraper_onlinesmartcities.py --base-url https://raadpleeg-halle.onlinesmartcities.be --alle --maanden 3 --agendapunten
        """
    )
    parser.add_argument("--base-url", type=str, default=None,
        help="Basis-URL van het portaal (bv. https://raadpleeg-halle.onlinesmartcities.be)")
    parser.add_argument("--orgaan", "-o", type=str,
        help="Naam van het orgaan (bv. 'Gemeenteraad')")
    parser.add_argument("--alle", action="store_true",
        help="Scrape alle organen zonder filter")
    parser.add_argument("--output", "-d", type=str, default="pdfs_smartcities",
        help="Uitvoermap (standaard: pdfs_smartcities)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
        help="Aantal maanden terug te doorzoeken (standaard: 12)")
    parser.add_argument("--agendapunten", "-a", action="store_true",
        help="Ook individuele agendapunt-besluiten meenemen (trager)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
        help="Filter documenten op naam. Meerdere termen scheiden met komma (bv. 'notulen,verslag'). "
             "Alleen docs die één van de termen bevatten worden gedownload.")
    parser.add_argument("--notulen", action="store_true",
        help="Download alleen verslagdocumenten: notulen, verslag, zittingsverslag, besluitenlijst, "
             "ontwerpbesluitenbundel, dagorde (ongeacht de exacte term die het portaal gebruikt)")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")
    parser.add_argument("--zichtbaar", action="store_true",
        help="Toon de browser (voor debuggen)")

    args = parser.parse_args()

    if args.base_url:
        global BASE_URL, KALENDER_URL
        BASE_URL = args.base_url.rstrip("/")
        KALENDER_URL = f"{BASE_URL}/zittingen/kalender"
        init_session()

    # Zet document_filter om naar lijst van termen (kommagescheiden invoer
    # of de volledige synoniemenlijst bij --notulen)
    resolved_filter: list[str] | None = None
    if args.document_filter:
        resolved_filter = [t.strip() for t in args.document_filter.split(",") if t.strip()]
    elif args.notulen:
        resolved_filter = NOTULEN_SYNONIEMEN

    if args.lijst_organen:
        toon_organen()
        return

    if not args.orgaan and not args.alle:
        print("Geef een orgaan op (--orgaan) of gebruik --alle voor alle organen.")
        print("Gebruik --lijst-organen om beschikbare organen te bekijken.\n")
        parser.print_help()
        sys.exit(1)

    scrape(
        orgaan=None if args.alle else args.orgaan,
        output_map=args.output,
        maanden=args.maanden,
        ook_agendapunten=args.agendapunten,
        headless=not args.zichtbaar,
        document_filter=resolved_filter,
    )


if __name__ == "__main__":
    main()
