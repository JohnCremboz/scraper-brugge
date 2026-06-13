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

BASE_URL = "https://raadpleeg-halle.onlinesmartcities.be"
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
        print(f"      [!] Fout ophalen {url}: {e}")

    return documenten


async def haal_extra_subpaginas(vergadering_url: str) -> list[str]:
    """
    Zoek bijkomende agenda- en andere subpagina-links op de vergaderingspagina.
    Leuven heeft bijv. /zittingen/{id}/bijkomendeagenda/{id} links.
    Geeft volledige URLs terug.
    """
    extra = []
    resp = await _get(vergadering_url, timeout=30)
    if not resp or resp.status_code != 200:
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        verg_pad = vergadering_url.rstrip("/").split(BASE_URL)[-1]
        for link in soup.find_all("a", href=True):
            href = link["href"]
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
        print(f"      [!] Fout agendapunten {vergadering_url}: {e}")

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
    except Exception:
        return False, ""


# Synoniemen die gemeenten gebruiken voor officiële vergaderingsverslagen/notulen.
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


async def zoek_html_publicaties(vergadering_url: str, ook_agendapunten: bool = False) -> list[dict]:
    """
    Zoek LBLOD HTML-publicatiepagina's op een vergaderingspagina.
    Herkent links met property="lblodBesluit:linkToPublication".
    """
    pubs = []
    resp = await _get(vergadering_url, timeout=15)
    if not resp or resp.status_code != 200:
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", property="lblodBesluit:linkToPublication"):
            href = link.get("href", "")
            if not href:
                continue
            is_agendapunt = "/agendapunten/" in href or "/bijkomendeagenda/" in href
            if is_agendapunt and not ook_agendapunten:
                continue
            full_url = urljoin(BASE_URL, href) if not href.startswith("http") else href
            naam = link.get_text(strip=True) or href.rstrip("/").split("/")[-1]
            item_id = href.rstrip("/").split("/")[-1]
            if "/agendapunten/" in href:
                pub_type = f"agendapunt_{item_id}"
            elif "/bijkomendeagenda/" in href:
                pub_type = f"bijkomendeagenda_{item_id}"
            else:
                pub_type = item_id
            pubs.append({"url": full_url, "naam": naam, "type": pub_type})
    except Exception as e:
        logger.debug("HTML publicaties zoeken fout %s: %s", vergadering_url, e)
    return pubs


_HTML_SUBPADEN = ["notulen", "besluitenlijst"]


async def zoek_directe_html_subpaginas(vergadering_url: str) -> list[dict]:
    """
    Fallback voor portalen (zoals Maaseik) die notulen/besluitenlijst als pure
    HTML-pagina's publiceren zonder lblodBesluit:linkToPublication-property.
    """
    pubs = []
    for suffix in _HTML_SUBPADEN:
        url = f"{vergadering_url.rstrip('/')}/{suffix}"
        resp = await _get(url, timeout=15)
        if not resp or resp.status_code != 200:
            continue
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            tekst = soup.get_text()
            if "De inhoud van deze zitting is (nog) niet bekendgemaakt" in tekst:
                continue
            if len(tekst.strip()) < 200:
                continue
            naam = suffix.capitalize()
            pubs.append({"url": url, "naam": naam, "type": suffix})
        except Exception as e:
            logger.debug("Directe HTML subpagina check fout %s: %s", url, e)
    return pubs


async def sla_html_op(pub: dict, output_pad: Path) -> bool:
    """Sla een LBLOD HTML-publicatiepagina op als .html-bestand."""
    url = pub["url"]
    try:
        if "/zittingen/" in url:
            verg_id = url.split("/zittingen/")[1].split("/")[0]
        else:
            verg_id = url.rstrip("/").split("/")[-1]
        pub_type = sanitize_filename(pub.get("type", "publicatie"))
        filename = f"{pub_type}_{verg_id}.html"
        output_file = output_pad / filename
        if output_file.exists():
            return True
        output_pad.mkdir(parents=True, exist_ok=True)
        resp = await _get(url, timeout=30)
        if not resp or resp.status_code != 200:
            return False
        output_file.write_text(resp.text, encoding="utf-8")
        print(f"      [HTML] {filename}")
        return True
    except Exception as e:
        print(f"      [!] HTML opslaan fout {url}: {e}")
        return False


async def verwerk_vergadering(vergadering_url: str, output_pad: Path,
                              ook_agendapunten: bool = False,
                              orgaan_filter: str | None = None,
                              document_filter: list[str] | str | None = None,
                              ) -> tuple[int, list[dict]]:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft (aantal_downloads, html_publicaties) terug.
    """
    heeft_inhoud, titel = await vergadering_heeft_inhoud(vergadering_url)
    if not heeft_inhoud:
        return 0, []

    verg_id = vergadering_url.rstrip("/").split("/")[-1]
    print(f"\n    [{titel}] {verg_id}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    async def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        if not document_filter_match(doc["naam"], document_filter):
            return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = await download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [OK] {naam_hint[:70]}")
        return succes

    # 1. Documenten van de vergaderingspagina zelf
    doc_links = await haal_document_links_van_pagina(vergadering_url)

    # 2. Standaard subpagina's: agenda en besluitenlijst
    for subpad in [f"{vergadering_url}/agenda", f"{vergadering_url}/besluitenlijst"]:
        doc_links += await haal_document_links_van_pagina(subpad)

    # 3. Bijkomendeagenda en andere dynamische subpagina's (bv. Leuven)
    for subpagina_url in await haal_extra_subpaginas(vergadering_url):
        doc_links += await haal_document_links_van_pagina(subpagina_url)

    for doc in doc_links:
        if await verwerk_doc(doc, output_pad):
            downloads += 1

    # 4. Optioneel: agendapunten
    if ook_agendapunten:
        agendapunt_urls = await haal_agenda_punten(vergadering_url)
        for ap_url in agendapunt_urls:
            for doc in await haal_document_links_van_pagina(ap_url):
                if await verwerk_doc(doc, output_pad):
                    downloads += 1

    if downloads == 0:
        html_pubs = await zoek_html_publicaties(vergadering_url, ook_agendapunten)
        if not html_pubs:
            html_pubs = await zoek_directe_html_subpaginas(vergadering_url)
        if html_pubs:
            namen = ", ".join(p["naam"] for p in html_pubs)
            print(f"      (HTML-publicatie: {namen} — wordt opgeslagen als HTML)")
        else:
            print(f"      (geen documenten gevonden)")
        return 0, html_pubs

    return downloads, []


async def haal_organen_via_playwright() -> list[dict]:
    """
    Haal de organen op via een headless browser (Playwright).
    Fallback wanneer de statische HTML geen <select id='organs'> bevat.
    """
    calendar_meetings: list[dict] = []

    def _on_response(response):
        if "fetchcalendar" in response.url:
            try:
                data = response.json()
                calendar_meetings.extend(data.get("meetings", []))
            except Exception:
                pass

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
                )
                page = await ctx.new_page()
                page.on("response", _on_response)
                await page.goto(KALENDER_URL, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(1)

                # Strategie 1: organen extraheren uit fetchcalendar API
                if calendar_meetings:
                    seen: dict[str, str] = {}
                    for m in calendar_meetings:
                        organ = m.get("organ") or {}
                        naam = organ.get("name", "").strip()
                        uuid = organ.get("id", "").strip()
                        if naam and uuid and uuid not in seen:
                            seen[uuid] = naam
                    if seen:
                        return [{"naam": naam, "uuid": uid} for uid, naam in seen.items()]

                # Strategie 2: <select#organs> in de gerenderde DOM
                options = page.locator("select#organs option")
                count = await options.count()
                organen = []
                for i in range(count):
                    opt = options.nth(i)
                    naam = await opt.inner_text()
                    naam = naam.strip()
                    uuid = await opt.get_attribute("value") or ""
                    if uuid:
                        organen.append({"naam": naam, "uuid": uuid})
                return organen
            finally:
                await browser.close()
    except Exception as e:
        print(f"  [!] Playwright organen ophalen mislukt: {e}")
        return []


async def haal_organen_statisch() -> list[dict]:
    """
    Haal de organen op uit de statische HTML van de kalender.
    Valt terug op Playwright als het select-element niet in de statische HTML zit.
    """
    resp = await _get(KALENDER_URL, timeout=15)
    if resp and resp.status_code == 200:
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", id="organs")
            if select:
                return [
                    {"naam": opt.get_text(strip=True), "uuid": opt.get("value", "")}
                    for opt in select.find_all("option")
                    if opt.get("value")
                ]
        except Exception as e:
            print(f"  [!] Fout laden organen (statisch): {e}")
    return await haal_organen_via_playwright()


async def zoek_orgaan_uuid(orgaan_naam: str) -> tuple[str | None, str | None]:
    """Zoek UUID van een orgaan op naam (hoofdletterongevoelig, deel-match)."""
    for org in await haal_organen_statisch():
        if orgaan_naam.lower() in org["naam"].lower() or org["naam"].lower() in orgaan_naam.lower():
            return org["uuid"], org["naam"]
    return None, None


async def toon_organen() -> None:
    """Toon alle beschikbare organen."""
    organen = await haal_organen_statisch()
    if not organen:
        print("Geen organen gevonden.")
        return
    print(f"\nBeschikbare organen op {BASE_URL}:")
    print("-" * 50)
    for org in organen:
        print(f"  - {org['naam']}")


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
    except Exception as e:
        print(f"  [!] Fout bij ophalen vergaderlinks: {e}")
    return links


async def activeer_orgaan_filter(page, orgaan_naam: str) -> bool:
    """Activeer het orgaanfilter via de <select id='organs'> en Select2."""
    uuid, exacte_naam = await zoek_orgaan_uuid(orgaan_naam)
    if not uuid:
        print(f"  [!] Orgaan '{orgaan_naam}' niet gevonden.")
        print("      Gebruik --lijst-organen voor beschikbare namen.")
        return False
    try:
        await page.select_option("select#organs", value=[uuid])
        await page.evaluate("document.getElementById('organs').dispatchEvent(new Event('change'))")
        await page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(0.5)
        print(f"  Filter actief: {exacte_naam} (UUID: {uuid})")
        return True
    except Exception as e:
        print(f"  [!] Filter klikken mislukt: {e}")
        print(f"  => Post-filter op titelnaam wordt gebruikt")
        return False


async def navigeer_vorige_maand(page) -> str | None:
    """Ga naar de vorige maand. Ondersteunt twee kalender-stijlen."""
    # Strategie 1: Bootstrap-paginatie
    try:
        li = page.locator("li.page-item.previous").first
        klasse = await li.get_attribute("class", timeout=3000) or ""
        if "disabled" in klasse:
            return None
        vorige = li.locator("a").first
        titel_attr = await vorige.get_attribute("title", timeout=3000) or ""
        await vorige.click()
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(0.5)
        return titel_attr or await huidige_maand_titel(page)
    except Exception:
        pass

    # Strategie 2: tekst-gebaseerde "vorige maand"-link
    try:
        vorige = page.get_by_text(re.compile(r"vorige\s+maand", re.IGNORECASE)).first
        await vorige.click(timeout=5000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(0.5)
        return await huidige_maand_titel(page) or "?"
    except Exception as e:
        print(f"  [!] Maandnavigatie mislukt: {e}")
    return None


async def huidige_maand_titel(page) -> str:
    """Haal de huidige maandtitel op. Probeert meerdere kalender-stijlen."""
    try:
        el = page.locator("li.page-item.current a").first
        tekst = await el.inner_text(timeout=2000)
        if tekst.strip():
            return tekst.strip()
    except Exception:
        pass

    try:
        maand = await page.locator("select option:checked").nth(0).inner_text(timeout=2000)
        jaar = await page.locator("select option:checked").nth(1).inner_text(timeout=2000)
        if maand.strip() and jaar.strip():
            return f"{maand.strip()} {jaar.strip()}"
    except Exception:
        pass

    return ""


async def scrape(orgaan: str | None, output_map: str, maanden: int,
                 ook_agendapunten: bool = False, headless: bool = True,
                 document_filter: list[str] | str | None = None,
                 max_workers: int = 4) -> None:
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
    html_publicaties_todo: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
            )
            page = await context.new_page()

            print("[1] Kalender laden...")
            print("    (verbinding maken...)")
            try:
                await page.goto(KALENDER_URL, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                print("    [!] Timeout bij networkidle, probeer verder met load...")
                await page.goto(KALENDER_URL, wait_until="load", timeout=30000)
            print("    (wacht op elementen...)")
            await asyncio.sleep(1)
            print("    OK")

            if orgaan:
                print(f"[2] Filter instellen: {orgaan}")
                print("    (zoeken in beschikbare organen...)")
                if await activeer_orgaan_filter(page, orgaan):
                    print("    OK - Filter actief")
                else:
                    print("    [!] Filter kon niet ingesteld worden")
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

                print(f"    {len(vergaderingen)} vergaderingen gevonden, {len(nieuwe)} nieuw te verwerken")

                if nieuwe:
                    # Verwerk vergaderingen concurrent maar respecteer rate limits
                    semaphore = asyncio.Semaphore(max_workers)

                    async def verwerk_met_sem(verg_url: str) -> tuple[str, int, list[dict]]:
                        async with semaphore:
                            n, html_pubs = await verwerk_vergadering(
                                verg_url, output_pad,
                                ook_agendapunten, None, document_filter,
                            )
                            return verg_url, n, html_pubs

                    resultaten = await asyncio.gather(
                        *[verwerk_met_sem(v) for v in nieuwe],
                        return_exceptions=True,
                    )

                    for idx, res in enumerate(resultaten, 1):
                        if isinstance(res, Exception):
                            print(f"    ({idx}/{len(nieuwe)}) FOUT: {res}")
                            continue
                        verg_url, n, html_pubs = res
                        print(f"    ({idx}/{len(nieuwe)}) {verg_url.split('/')[-1]} -> {n} PDF(s)")
                        totaal_downloads += n
                        if n > 0:
                            vergaderingen_met_docs += 1
                        if html_pubs:
                            if document_filter:
                                html_pubs = [p for p in html_pubs
                                             if document_filter_match(p["naam"], document_filter)]
                            html_publicaties_todo.extend(html_pubs)

                if maand_nr < maanden - 1:
                    print(f"    (navigeren naar vorige maand...)")
                    nieuwe_maand = await navigeer_vorige_maand(page)
                    if nieuwe_maand is None:
                        print(f"\n  [!] Kan niet verder terug, gestopt na {maand_nr+1} maand(en).")
                        break
                print()

            if html_publicaties_todo:
                print(f"[4] HTML opslaan: {len(html_publicaties_todo)} publicatie(s)...\n")
                for pub in html_publicaties_todo:
                    if await sla_html_op(pub, output_pad):
                        totaal_downloads += 1
                        vergaderingen_met_docs += 1

        finally:
            await browser.close()

    lege_mappen = [p for p in output_pad.rglob("*") if p.is_dir() and not any(p.iterdir())]
    for lege in lege_mappen:
        lege.rmdir()
    if lege_mappen:
        print(f"  Lege mappen verwijderd: {len(lege_mappen)}")

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
        help="Filter documenten op naam. Meerdere termen scheiden met komma.")
    parser.add_argument("--notulen", action="store_true",
        help="Download alleen verslagdocumenten: notulen, verslag, zittingsverslag, etc.")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")
    parser.add_argument("--zichtbaar", action="store_true",
        help="Toon de browser (voor debuggen)")
    parser.add_argument("--max-workers", type=int, default=4,
        help="Parallelle vergadering-verwerking (standaard: 4)")

    args = parser.parse_args()

    if args.base_url:
        global BASE_URL, KALENDER_URL
        BASE_URL = args.base_url.rstrip("/")
        KALENDER_URL = f"{BASE_URL}/zittingen/kalender"

    resolved_filter: list[str] | None = None
    if args.document_filter:
        resolved_filter = [t.strip() for t in args.document_filter.split(",") if t.strip()]
    elif args.notulen:
        resolved_filter = NOTULEN_SYNONIEMEN

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
            document_filter=resolved_filter,
            max_workers=args.max_workers,
        )
        if SESSION is not None:
            await SESSION.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
