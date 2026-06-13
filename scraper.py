"""
Scraper voor PDF-documenten van besluitvorming.brugge.be

Gebruik:
    uv run python scraper.py --lijst-organen
    uv run python scraper.py --orgaan "Gemeenteraad" --output pdfs --maanden 12
    uv run python scraper.py --orgaan "College van Burgemeester en Schepenen" --output cbs --maanden 6
    uv run python scraper.py --alle --maanden 3
"""

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from base_scraper import (
    ScraperConfig,
    async_download_document,
    async_rate_limit,
    create_async_session,
    sanitize_filename,
    DownloadResult,
    logger,
)

BASE_URL = "https://besluitvorming.brugge.be"
KALENDER_URL = f"{BASE_URL}/zittingen/kalender"

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


async def init_session() -> None:
    """Initialiseer de sessie met base_scraper configuratie."""
    global SESSION, _config
    if SESSION is not None:
        await SESSION.close()
    _config = ScraperConfig(base_url=BASE_URL, output_dir=Path("."))
    SESSION = create_async_session(_config)


async def _get(url: str, timeout: int = 30) -> _Resp | None:
    if SESSION is None or _config is None:
        return None
    full_url = urljoin(BASE_URL, url) if not url.startswith("http") else url
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            full_url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.warning("GET mislukt %s: %s", full_url, exc)
        return None


async def download_document(doc_url: str, bestemming: Path, filename_hint: str = "") -> bool:
    """Download een /document/{id} URL als PDF via async_download_document."""
    if SESSION is None or _config is None:
        logger.error("Sessie niet geïnitialiseerd")
        return False

    result = await async_download_document(
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


async def haal_document_links_van_pagina(url: str) -> list[dict]:
    """Haal alle /document/ links op van een pagina (via aiohttp+BS4)."""
    documenten = []
    full_url = urljoin(BASE_URL, url) if not url.startswith("http") else url
    resp = await _get(full_url, timeout=30)
    if not resp or resp.status_code != 200:
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/document/" in href:
                tekst = link.get_text(strip=True) or href.split("/")[-1]
                documenten.append({"url": href, "naam": tekst})
    except Exception as e:
        logger.warning("Parse-fout ophalen %s: %s", url, e)

    return documenten


async def haal_agenda_punten(vergadering_url: str) -> list[str]:
    """Haal alle agendapunt-URLs op van een vergaderingspagina."""
    agendapunten = []
    resp = await _get(vergadering_url, timeout=30)
    if not resp or resp.status_code != 200:
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/agendapunten/" in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in agendapunten:
                    agendapunten.append(full_url)
    except Exception as e:
        logger.warning("Parse-fout agendapunten %s: %s", vergadering_url, e)

    return agendapunten


async def vergadering_heeft_inhoud(vergadering_url: str) -> tuple[bool, str]:
    """
    Controleer of een vergadering gepubliceerde inhoud heeft.
    Geeft (heeft_inhoud, titel) terug.
    """
    resp = await _get(vergadering_url, timeout=15)
    if not resp or resp.status_code != 200:
        return False, ""

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        tekst = soup.get_text()

        if "De inhoud van deze zitting is (nog) niet bekendgemaakt" in tekst:
            return False, ""

        h1 = soup.find("h1")
        if h1:
            for a in h1.find_all("a"):
                a.decompose()
            titel = h1.get_text(strip=True)
        else:
            titel = vergadering_url.split("/")[-1]

        return True, titel

    except Exception as e:
        logger.warning("Fout bij vergadering %s: %s", vergadering_url, e)
        return False, ""


async def verwerk_vergadering(vergadering_url: str, output_pad: Path,
                              ook_agendapunten: bool = False,
                              orgaan_filter: str | None = None,
                              document_filter: str | None = None) -> int:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft het aantal nieuw gedownloade PDFs terug.
    """
    heeft_inhoud, titel = await vergadering_heeft_inhoud(vergadering_url)
    if not heeft_inhoud:
        return 0

    verg_id = vergadering_url.rstrip("/").split("/")[-1]
    print(f"\n    [{titel}] {verg_id}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    async def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        if document_filter and document_filter.lower() not in doc["naam"].lower():
            return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = await download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [OK] {naam_hint[:70]}")
        return succes

    # 1. Documenten van de vergaderingspagina
    doc_links = await haal_document_links_van_pagina(vergadering_url)

    # 2. Subpagina's: agenda en besluitenlijst
    for subpad in [f"{vergadering_url}/agenda", f"{vergadering_url}/besluitenlijst"]:
        doc_links += await haal_document_links_van_pagina(subpad)

    for doc in doc_links:
        if await verwerk_doc(doc, output_pad):
            downloads += 1

    # 3. Optioneel: agendapunten
    if ook_agendapunten:
        agendapunt_urls = await haal_agenda_punten(vergadering_url)
        for ap_url in agendapunt_urls:
            for doc in await haal_document_links_van_pagina(ap_url):
                if await verwerk_doc(doc, output_pad):
                    downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


async def haal_orgaan_uuid(page, orgaan_naam: str) -> str | None:
    """Zoek de UUID van een orgaan op via de checkboxes."""
    try:
        checkboxes = await page.query_selector_all("input[type='checkbox'][value]")
        for cb in checkboxes:
            val = await cb.get_attribute("value") or ""
            if val == "multiselect-all":
                continue
            label = await page.query_selector(f"label[for='{val}']")
            if label:
                label_tekst = await label.get_attribute("title") or await label.inner_text()
                label_tekst = label_tekst.strip()
                if orgaan_naam.lower() in label_tekst.lower() or label_tekst.lower() in orgaan_naam.lower():
                    return val
    except PlaywrightTimeout as e:
        logger.warning("Timeout bij zoeken UUID voor '%s': %s", orgaan_naam, e)
    except Exception as e:
        logger.warning("Fout bij zoeken UUID voor '%s': %s", orgaan_naam, e)
    return None


async def haal_vergadering_links_van_pagina(page) -> list[str]:
    """Haal alle vergaderingslinks op van de huidige kalenderweergave."""
    links = []
    try:
        zitting_links = page.locator("a[href*='/zittingen/']")
        count = await zitting_links.count()
        for i in range(count):
            link = zitting_links.nth(i)
            href = await link.get_attribute("href") or ""
            if "/zittingen/" in href and "kalender" not in href and "lijst" not in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in links:
                    links.append(full_url)
    except PlaywrightTimeout as e:
        logger.warning("Timeout bij ophalen vergaderlinks: %s", e)
    except Exception as e:
        logger.warning("Fout bij ophalen vergaderlinks: %s", e)
    return links


async def open_orgaan_dropdown(page) -> bool:
    """Open de organen multiselect dropdown."""
    try:
        trigger = page.locator(
            "button.multiselect, button[data-toggle='dropdown'], "
            ".multiselect-container ~ button, button.dropdown-toggle"
        ).first
        if await trigger.count() == 0:
            trigger = page.locator("[class*='multiselect']").first
        if await trigger.count() > 0:
            await trigger.click()
            await asyncio.sleep(0.5)
            return True
    except PlaywrightTimeout as e:
        logger.warning("Timeout bij openen dropdown: %s", e)
    except Exception as e:
        logger.warning("Fout bij openen dropdown: %s", e)
    return False


async def activeer_orgaan_filter(page, orgaan_naam: str) -> bool:
    """Activeer het orgaanfilter op de kalender."""
    try:
        uuid = await haal_orgaan_uuid(page, orgaan_naam)
        if not uuid:
            print(f"  [!] Orgaan '{orgaan_naam}' niet gevonden.")
            print("      Gebruik --lijst-organen voor beschikbare namen.")
            return False

        await open_orgaan_dropdown(page)

        # Deselecteer alles: klik op de 'Alle' checkbox
        all_input = page.locator("input#all")
        if await all_input.count() > 0:
            try:
                all_label = page.locator("label[for='all']")
                await all_label.click(timeout=5000)
                await asyncio.sleep(0.3)
                if not await all_input.is_checked():
                    await all_label.click(timeout=5000)
                    await asyncio.sleep(0.3)
                if await all_input.is_checked():
                    await all_label.click(timeout=5000)
                    await asyncio.sleep(0.3)
            except PlaywrightTimeout as e:
                logger.warning("Timeout bij resetten van alle-filters: %s", e)
            except Exception as e:
                logger.warning("Fout bij resetten van alle-filters: %s", e)

        # Selecteer het gewenste orgaan
        label = page.locator(f"label[for='{uuid}']")
        if await label.count() > 0:
            try:
                await label.click(timeout=5000)
                await asyncio.sleep(0.5)
                print(f"  Filter actief: {orgaan_naam} (UUID: {uuid})")
                return True
            except PlaywrightTimeout as e:
                print(f"  [!] Filter timeout: {e}")
                print(f"  => Post-filter op titelnaam wordt altijd gebruikt")
                return False
            except Exception as e:
                print(f"  [!] Filter klikken mislukt: {e}")
                print(f"  => Post-filter op titelnaam wordt altijd gebruikt")
                return False

    except PlaywrightTimeout as e:
        print(f"  [!] Filter timeout: {e}")
    except Exception as e:
        print(f"  [!] Filter fout: {e}")
    return False


async def navigeer_vorige_maand(page) -> str | None:
    """Ga naar de vorige maand. Geeft de nieuwe maandtitel terug, of None als niet gelukt."""
    try:
        vorige = page.locator("li.page-item.previous a").first
        titel_attr = await vorige.get_attribute("title") or ""
        await vorige.click()
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(0.5)
        return titel_attr or await huidige_maand_titel(page)
    except PlaywrightTimeout as e:
        logger.warning("Timeout bij maandnavigatie: %s", e)
    except Exception as e:
        logger.warning("Maandnavigatie mislukt: %s", e)
    return None


async def huidige_maand_titel(page) -> str:
    """Haal de huidige maandtitel op."""
    try:
        el = page.locator("li.page-item.current a").first
        return (await el.inner_text()).strip()
    except PlaywrightTimeout as e:
        logger.warning("Timeout bij ophalen maandtitel: %s", e)
        return ""
    except Exception as e:
        logger.warning("Fout bij ophalen maandtitel: %s", e)
        return ""


async def _goto_met_fallback(page, url: str, timeout: int = 30000) -> None:
    """Laad een pagina; valt terug op 'load' als 'networkidle' een timeout geeft."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout)
    except PlaywrightTimeout:
        print("    [!] Timeout bij networkidle, probeer verder met load...")
        await page.goto(url, wait_until="load", timeout=timeout)


async def toon_organen() -> None:
    """Toon alle beschikbare organen."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await _goto_met_fallback(page, KALENDER_URL)

            print("\nBeschikbare organen op besluitvorming.brugge.be:")
            print("-" * 50)

            checkboxes = await page.query_selector_all("input[type='checkbox'][value]")
            for cb in checkboxes:
                val = await cb.get_attribute("value") or ""
                if val == "multiselect-all":
                    continue
                label = await page.query_selector(f"label[for='{val}']")
                if label:
                    naam = await label.get_attribute("title") or await label.inner_text()
                    print(f"  - {naam.strip()}")
        finally:
            await browser.close()


async def scrape(orgaan: str | None, output_map: str, maanden: int,
                 ook_agendapunten: bool = False, headless: bool = True,
                 document_filter: str | None = None) -> None:
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
            )
            page = await context.new_page()

            print("[1] Kalender laden...")
            print("    (verbinding maken met besluitvorming.brugge.be...)")
            await _goto_met_fallback(page, KALENDER_URL)
            print("    (pagina geladen, wacht op interactieve elementen...)")
            await asyncio.sleep(1)
            print("    OK - Kalender beschikbaar")

            if orgaan:
                print(f"[2] Filter instellen: {orgaan}")
                print("    (zoeken in beschikbare organen...)")
                if await activeer_orgaan_filter(page, orgaan):
                    print("    OK - Filter actief")
                else:
                    print("    [!] Filter kon niet ingesteld worden - alle organen verwerken")
            else:
                print("[2] Geen filter (alle organen)")

            print(f"[3] Doorzoek {maanden} maand(en)...\n")

            for maand_nr in range(maanden):
                maand_titel = await huidige_maand_titel(page)
                print(f"  [{maand_titel or f'Maand {maand_nr+1}'}]")
                print(f"    (laden van vergaderingen...)")

                vergaderingen = await haal_vergadering_links_van_pagina(page)
                nieuwe = [v for v in vergaderingen if v not in alle_vergadering_urls]
                alle_vergadering_urls.update(vergaderingen)

                print(f"    {len(vergaderingen)} vergaderingen gevonden, {len(nieuwe)} nieuw")

                for idx, verg_url in enumerate(nieuwe, 1):
                    print(f"    ({idx}/{len(nieuwe)}) vergadering verwerken...", end="", flush=True)
                    n = await verwerk_vergadering(
                        verg_url, output_pad, ook_agendapunten,
                        orgaan_filter=None,
                        document_filter=document_filter,
                    )
                    print(f" -> {n} PDF(s)")
                    totaal_downloads += n
                    if n > 0:
                        vergaderingen_met_docs += 1

                if maand_nr < maanden - 1:
                    print(f"    (naar vorige maand...)")
                    nieuwe_maand = await navigeer_vorige_maand(page)
                    if nieuwe_maand is None:
                        print(f"\n  [!] Kan niet verder terug, gestopt na {maand_nr+1} maand(en).")
                        break
                print()

        finally:
            await browser.close()

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
        help="Filter documenten op naam (bv. 'notulen').")
    parser.add_argument("--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--zichtbaar", action="store_true",
        help="Toon de browser (voor debuggen)")

    args = parser.parse_args()

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    async def _run() -> None:
        await init_session()

        if args.lijst_organen:
            await toon_organen()
            if SESSION is not None:
                await SESSION.close()
            return

        if not args.orgaan and not args.alle:
            print("Geef een orgaan op (--orgaan) of gebruik --alle voor alle organen.")
            print("Gebruik --lijst-organen om beschikbare organen te bekijken.\n")
            parser.print_help()
            if SESSION is not None:
                await SESSION.close()
            sys.exit(1)

        await scrape(
            orgaan=None if args.alle else args.orgaan,
            output_map=args.output,
            maanden=args.maanden,
            ook_agendapunten=args.agendapunten,
            headless=not args.zichtbaar,
            document_filter=args.document_filter,
        )
        if SESSION is not None:
            await SESSION.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
