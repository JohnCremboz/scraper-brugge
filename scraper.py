"""
Scraper voor PDF-documenten van besluitvorming.brugge.be

Gebruik:
    uv run python scraper.py --lijst-organen
    uv run python scraper.py --orgaan "Gemeenteraad" --output pdfs --maanden 12
    uv run python scraper.py --orgaan "College van Burgemeester en Schepenen" --output cbs --maanden 6
    uv run python scraper.py --alle --maanden 3
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

BASE_URL = "https://besluitvorming.brugge.be"
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
        # Verwijder "Terug" link tekst als die in de h1 zit
        if h1:
            for a in h1.find_all("a"):
                a.decompose()
            titel = h1.get_text(strip=True)
        else:
            titel = vergadering_url.split("/")[-1]

        return True, titel

    except Exception:
        return False, ""


def verwerk_vergadering(vergadering_url: str, output_pad: Path,
                        ook_agendapunten: bool = False,
                        orgaan_filter: str | None = None,
                        document_filter: str | None = None) -> int:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft het aantal nieuw gedownloade PDFs terug.
    Als document_filter opgegeven is (bv. 'notulen'), worden alleen
    documenten waarvan de naam die string bevat gedownload.
    """
    heeft_inhoud, titel = vergadering_heeft_inhoud(vergadering_url)
    if not heeft_inhoud:
        return 0


    verg_id = vergadering_url.rstrip("/").split("/")[-1]
    map_naam = sanitize_filename(f"{titel}_{verg_id}")
    verg_map = output_pad / map_naam
    verg_map.mkdir(parents=True, exist_ok=True)

    print(f"\n    [{titel}] {verg_id}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        # Documentnaam-filter: sla over als de naam het filter niet bevat
        if document_filter and document_filter.lower() not in doc["naam"].lower():
            return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [OK] {naam_hint[:70]}")
        return succes

    # 1. Documenten van de vergaderingspagina
    doc_links = haal_document_links_van_pagina(vergadering_url)

    # 2. Subpagina's: agenda en besluitenlijst
    for subpad in [f"{vergadering_url}/agenda", f"{vergadering_url}/besluitenlijst"]:
        doc_links += haal_document_links_van_pagina(subpad)

    for doc in doc_links:
        if verwerk_doc(doc, verg_map):
            downloads += 1

    # 3. Optioneel: agendapunten
    if ook_agendapunten:
        agendapunt_urls = haal_agenda_punten(vergadering_url)
        if agendapunt_urls:
            ap_map = verg_map / "besluiten_per_punt"
            ap_map.mkdir(exist_ok=True)
            for ap_url in tqdm(agendapunt_urls, desc="      Agendapunten", leave=False):
                for doc in haal_document_links_van_pagina(ap_url):
                    if verwerk_doc(doc, ap_map):
                        downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


def haal_orgaan_uuid(page, orgaan_naam: str) -> str | None:
    """Zoek de UUID van een orgaan op via de checkboxes."""
    try:
        checkboxes = page.query_selector_all("input[type='checkbox'][value]")
        for cb in checkboxes:
            val = cb.get_attribute("value") or ""
            if val == "multiselect-all":
                continue
            label = page.query_selector(f"label[for='{val}']")
            if label:
                label_tekst = label.get_attribute("title") or label.inner_text().strip()
                if orgaan_naam.lower() in label_tekst.lower() or label_tekst.lower() in orgaan_naam.lower():
                    return val
    except Exception as e:
        print(f"  [!] Fout bij zoeken UUID: {e}")
    return None


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
    except Exception:
        pass
    return links


def open_orgaan_dropdown(page) -> bool:
    """Open de organen multiselect dropdown."""
    try:
        # Zoek de dropdown-knop (Bootstrap multiselect button)
        trigger = page.locator(
            "button.multiselect, button[data-toggle='dropdown'], "
            ".multiselect-container ~ button, button.dropdown-toggle"
        ).first
        if trigger.count() == 0:
            # Probeer via het form-groep element
            trigger = page.locator("[class*='multiselect']").first
        if trigger.count() > 0:
            trigger.click()
            time.sleep(0.5)
            return True
    except Exception:
        pass
    return False


def activeer_orgaan_filter(page, orgaan_naam: str) -> bool:
    """Activeer het orgaanfilter op de kalender."""
    try:
        uuid = haal_orgaan_uuid(page, orgaan_naam)
        if not uuid:
            print(f"  [!] Orgaan '{orgaan_naam}' niet gevonden.")
            print("      Gebruik --lijst-organen voor beschikbare namen.")
            return False

        # Probeer de dropdown te openen
        open_orgaan_dropdown(page)

        # Deselecteer alles: klik op de 'Alle' checkbox
        all_input = page.locator("input#all")
        if all_input.count() > 0:
            try:
                all_label = page.locator("label[for='all']")
                all_label.click(timeout=5000)
                time.sleep(0.3)
                # Klik nogmaals als niet-alles geselecteerd na eerste klik
                if not all_input.is_checked():
                    all_label.click(timeout=5000)
                    time.sleep(0.3)
                # Deselect: klik als still checked
                if all_input.is_checked():
                    all_label.click(timeout=5000)
                    time.sleep(0.3)
            except Exception:
                pass

        # Selecteer het gewenste orgaan
        label = page.locator(f"label[for='{uuid}']")
        if label.count() > 0:
            try:
                label.click(timeout=5000)
                time.sleep(0.5)
                print(f"  Filter actief: {orgaan_naam} (UUID: {uuid})")
                return True
            except Exception as e:
                print(f"  [!] Filter klikken mislukt: {e}")
                print(f"  => Post-filter op titelnaam wordt altijd gebruikt")
                return False

    except Exception as e:
        print(f"  [!] Filter fout: {e}")
    return False


def navigeer_vorige_maand(page) -> str | None:
    """
    Ga naar de vorige maand.
    Geeft de nieuwe maandtitel terug, of None als niet gelukt.
    """
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
    """Toon alle beschikbare organen."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(KALENDER_URL, wait_until="networkidle", timeout=30000)

        print("\nBeschikbare organen op besluitvorming.brugge.be:")
        print("-" * 50)

        checkboxes = page.query_selector_all("input[type='checkbox'][value]")
        for cb in checkboxes:
            val = cb.get_attribute("value") or ""
            if val == "multiselect-all":
                continue
            label = page.query_selector(f"label[for='{val}']")
            if label:
                naam = label.get_attribute("title") or label.inner_text().strip()
                print(f"  - {naam}")

        browser.close()


def scrape(orgaan: str | None, output_map: str, maanden: int,
           ook_agendapunten: bool = False, headless: bool = True,
           document_filter: str | None = None):
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Scraper: besluitvorming.brugge.be")
    print(f"  Orgaan:  {orgaan or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    print(f"  Incl. agendapunten: {'Ja' if ook_agendapunten else 'Nee (gebruik --agendapunten)'}")
    print(f"  Documentfilter: {document_filter or 'Geen (alle documenten)'}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    alle_vergadering_urls: set[str] = set()
    totaal_downloads = 0
    vergaderingen_met_docs = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
        )
        page = context.new_page()

        print("[1] Kalender laden...")
        print("    (verbinding maken met besluitvorming.brugge.be...)")
        page.goto(KALENDER_URL, wait_until="networkidle", timeout=30000)
        print("    (pagina geladen, wacht op interactieve elementen...)")
        time.sleep(1)
        print("    OK - Kalender beschikbaar")

        if orgaan:
            print(f"[2] Filter instellen: {orgaan}")
            print("    (zoeken in beschikbare organen...)")
            if activeer_orgaan_filter(page, orgaan):
                print("    OK - Filter actief")
            else:
                print("    [!] Filter kon niet ingesteld worden - alle organen verwerken")
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

            print(f"    {len(vergaderingen)} vergaderingen gevonden, {len(nieuwe)} nieuw")

            for idx, verg_url in enumerate(nieuwe, 1):
                print(f"    ({idx}/{len(nieuwe)}) vergadering verwerken...", end="", flush=True)
                n = verwerk_vergadering(
                    verg_url, output_pad, ook_agendapunten,
                    orgaan_filter=None,
                    document_filter=document_filter,
                )
                print(f" -> {n} PDF(s)")
                totaal_downloads += n
                if n > 0:
                    vergaderingen_met_docs += 1

            # Navigeer naar vorige maand (tenzij het de laatste is)
            if maand_nr < maanden - 1:
                print(f"    (naar vorige maand...)")
                nieuwe_maand = navigeer_vorige_maand(page)
                if nieuwe_maand is None:
                    print(f"\n  [!] Kan niet verder terug, gestopt na {maand_nr+1} maand(en).")
                    break
            print()

        browser.close()

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  Vergaderingen met documenten: {vergaderingen_met_docs}")
    print(f"  PDFs gedownload: {totaal_downloads}")
    print(f"  Output map: {output_pad.resolve()}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Scraper voor PDF-documenten van besluitvorming.brugge.be",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper.py --lijst-organen
  uv run python scraper.py --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper.py --orgaan "Gemeenteraad" --notulen --maanden 24
  uv run python scraper.py --orgaan "Gemeenteraad" --document-filter notulen --maanden 12
  uv run python scraper.py --orgaan "College van Burgemeester en Schepenen" --output cbs
  uv run python scraper.py --alle --maanden 3 --agendapunten
        """
    )
    parser.add_argument("--orgaan", "-o", type=str,
        help="Naam van het orgaan (bv. 'Gemeenteraad')")
    parser.add_argument("--alle", action="store_true",
        help="Scrape alle organen zonder filter")
    parser.add_argument("--output", "-d", type=str, default="pdfs",
        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
        help="Aantal maanden terug te doorzoeken (standaard: 12)")
    parser.add_argument("--agendapunten", "-a", action="store_true",
        help="Ook individuele agendapunt-besluiten meenemen (trager)")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
        help="Filter documenten op naam (bv. 'notulen'). Alleen docs die deze tekst bevatten worden gedownload.")
    parser.add_argument("--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--zichtbaar", action="store_true",
        help="Toon de browser (voor debuggen)")

    args = parser.parse_args()

    # --notulen is een shorthand voor --document-filter notulen
    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

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
        document_filter=args.document_filter,
    )


if __name__ == "__main__":
    main()
