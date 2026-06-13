"""
Scraper voor PDF-documenten van menen-echo.cipalschaubroeck.be/raadpleegomgeving/

Gebruik:
    uv run python scraper_menen.py --lijst-organen
    uv run python scraper_menen.py --orgaan "Gemeenteraad" --output pdfs --maanden 12
    uv run python scraper_menen.py --orgaan "Gemeenteraad" --notulen --maanden 24
    uv run python scraper_menen.py --orgaan "College van Burgemeester en Schepenen" --output cbs --maanden 6
    uv run python scraper_menen.py --alle --maanden 3
"""

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

from base_scraper import (
    ScraperConfig,
    async_download_document,
    async_rate_limit,
    create_async_session,
    DownloadResult,
    logger,
    sanitize_filename,
    safe_output_path,
)

BASE_URL = "https://menen-echo.cipalschaubroeck.be"
CONTEXT = "/raadpleegomgeving"
LIJST_URL = f"{BASE_URL}{CONTEXT}/zittingen/lijst"
KALENDER_API = f"{BASE_URL}{CONTEXT}/calendar/fetchcalendar"
ZOEKEN_URL = f"{BASE_URL}{CONTEXT}/zoeken"

_config: ScraperConfig | None = None
SESSION: aiohttp.ClientSession | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


async def init_session() -> None:
    global SESSION, _config
    if SESSION is not None:
        await SESSION.close()
    if _config is None:
        _config = ScraperConfig(base_url=BASE_URL)
    SESSION = create_async_session(_config)
    # Initialiseer sessie door lijst-pagina op te halen
    resp = await _get(LIJST_URL, timeout=15)
    if not resp:
        logger.warning("Sessie-initialisatie mislukt: %s", LIJST_URL)


async def _get(url: str, timeout: int = 30) -> _Resp | None:
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.warning("GET mislukt %s: %s", url, exc)
        return None


async def _get_json(url: str, params: dict | None = None, timeout: int = 15) -> dict | None:
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("GET JSON mislukt %s: %s", url, exc)
        return None


async def download_document_menen(doc_url: str, bestemming: Path, filename_hint: str = "") -> bool:
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
    """Haal alle /document/ links op van een pagina."""
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


async def haal_agendapunt_urls(vergadering_url: str) -> list[str]:
    """Haal alle agendapunt-URLs op van een vergaderingspagina."""
    agendapunten = []
    full_url = urljoin(BASE_URL, vergadering_url) if not vergadering_url.startswith("http") else vergadering_url
    resp = await _get(full_url, timeout=30)
    if not resp or resp.status_code != 200:
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/agendapunten/" in href:
                full = urljoin(BASE_URL, href)
                if full not in agendapunten:
                    agendapunten.append(full)
    except Exception as e:
        print(f"      [!] Fout agendapunten {vergadering_url}: {e}")

    return agendapunten


async def verwerk_vergadering(meeting: dict, output_pad: Path,
                              ook_agendapunten: bool = False,
                              document_filter: str | None = None) -> int:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft het aantal nieuw gedownloade PDFs terug.
    """
    mid = meeting["id"]
    organ = meeting.get("organ", {}).get("name", "Onbekend")
    dt_str = meeting.get("dateTime", "")
    try:
        dt = datetime.fromisoformat(dt_str)
        datum_label = dt.strftime("%Y%m%d")
    except Exception:
        datum_label = mid[:8]

    verg_url = f"{CONTEXT}/zittingen/{mid}"

    print(f"\n    [{organ}] {dt_str[:10] if dt_str else mid[:8]}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    NOTULEN_ALIASSEN = {"notulen", "zittingsverslag", "zittingsnotulen", "verslag"}

    async def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        if document_filter:
            filter_lower = document_filter.lower()
            naam_lower = doc["naam"].lower()
            aliassen = NOTULEN_ALIASSEN if filter_lower == "notulen" else {filter_lower}
            if not any(alias in naam_lower for alias in aliassen):
                return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = await download_document_menen(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [OK] {naam_hint[:70]}")
        return succes

    doc_links = await haal_document_links_van_pagina(verg_url)
    for doc in doc_links:
        if await verwerk_doc(doc, output_pad):
            downloads += 1

    if ook_agendapunten:
        ap_urls = await haal_agendapunt_urls(verg_url)
        if ap_urls:
            for ap_url in ap_urls:
                for doc in await haal_document_links_van_pagina(ap_url):
                    if await verwerk_doc(doc, output_pad):
                        downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


async def haal_organen() -> list[dict]:
    """Haal de beschikbare organen op uit de zoekpagina."""
    resp = await _get(ZOEKEN_URL, timeout=15)
    if not resp:
        return []
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        select = soup.find("select", {"name": "organId"})
        if not select:
            return []
        organen = []
        for opt in select.find_all("option"):
            uuid = opt.get("value", "").strip()
            naam = opt.get_text(strip=True)
            if uuid:
                organen.append({"naam": naam, "uuid": uuid})
        return organen
    except Exception as e:
        print(f"  [!] Fout laden organen: {e}")
        return []


async def zoek_orgaan(orgaan_naam: str) -> tuple[str | None, str | None]:
    """Zoek UUID van een orgaan op naam (hoofdletterongevoelig, deel-match)."""
    for org in await haal_organen():
        if orgaan_naam.lower() in org["naam"].lower() or org["naam"].lower() in orgaan_naam.lower():
            return org["uuid"], org["naam"]
    return None, None


async def haal_datum_grenzen() -> tuple[str, str]:
    """Haal de eerste en laatste vergaderdatum op uit de lijstpagina."""
    resp = await _get(LIJST_URL, timeout=15)
    if not resp:
        return "202101", datetime.now().strftime("%Y%m")
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for script in soup.find_all("script"):
            text = script.get_text()
            first_m = re.search(r'\$firstMeetingDate\s*=\s*"(\d{6})"', text)
            last_m = re.search(r'\$lastMeetingDate\s*=\s*"(\d{6})"', text)
            if first_m and last_m:
                return first_m.group(1), last_m.group(1)
    except Exception as e:
        print(f"  [!] Fout datum grenzen: {e}")
    return "202101", datetime.now().strftime("%Y%m")


async def haal_vergaderingen_voor_maand(maand: int, jaar: int) -> list[dict]:
    """Haal alle vergaderingen op voor een gegeven maand/jaar via de calendar API."""
    params = {
        "month": f"{maand:02d}",
        "year": str(jaar),
        "calendarview": "false",
        "skipmeetings": "false",
    }
    data = await _get_json(KALENDER_API, params=params, timeout=15)
    if not data:
        return []
    return data.get("meetings", [])


def maand_range(start_yyyymm: str, einde_yyyymm: str) -> list[tuple[int, int]]:
    """Genereer een lijst van (maand, jaar) tuples van start tot einde (inclusief)."""
    resultaat = []
    start_jaar = int(start_yyyymm[:4])
    start_maand = int(start_yyyymm[4:6])
    einde_jaar = int(einde_yyyymm[:4])
    einde_maand = int(einde_yyyymm[4:6])

    jaar, maand = start_jaar, start_maand
    while (jaar, maand) <= (einde_jaar, einde_maand):
        resultaat.append((maand, jaar))
        maand += 1
        if maand > 12:
            maand = 1
            jaar += 1
    return resultaat


async def toon_organen() -> None:
    """Toon alle beschikbare organen."""
    organen = await haal_organen()
    if not organen:
        print("Geen organen gevonden.")
        return
    print(f"\nBeschikbare organen op {BASE_URL}{CONTEXT}:")
    print("-" * 55)
    for org in organen:
        print(f"  - {org['naam']}")


def bereken_start_datum(maanden: int) -> str:
    """Bereken de startdatum (YYYYMM) voor het opgegeven aantal maanden geleden."""
    start = datetime.now() - relativedelta(months=maanden)
    return f"{start.year}{start.month:02d}"


async def scrape(orgaan: str | None, output_map: str, maanden: int,
                 ook_agendapunten: bool = False,
                 document_filter: str | None = None) -> None:
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Scraper: {BASE_URL}{CONTEXT}")
    print(f"  Orgaan:  {orgaan or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    print(f"  Incl. agendapunten: {'Ja' if ook_agendapunten else 'Nee (gebruik --agendapunten)'}")
    print(f"  Documentfilter: {document_filter or 'Geen (alle documenten)'}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    print("[1] Sessie initialiseren...")
    await init_session()

    orgaan_filter_naam = None
    _geen_filter_sentinels = {"alle organen (geen filter)", "alle organen", "__alle__"}
    if orgaan and orgaan.lower() not in _geen_filter_sentinels:
        print(f"[2] Orgaan valideren: {orgaan}")
        uuid, exacte_naam = await zoek_orgaan(orgaan)
        if uuid:
            orgaan_filter_naam = exacte_naam
            print(f"  Orgaan gevonden: {exacte_naam}")
        else:
            print(f"  [!] Orgaan '{orgaan}' niet gevonden. Gebruik --lijst-organen.")
            print(f"  => Post-filter op naam wordt toch geprobeerd.")
            orgaan_filter_naam = orgaan
    else:
        print("[2] Geen orgaanfilter (alle organen)")

    print("[3] Datumgrenzen ophalen...")
    first_date, last_date = await haal_datum_grenzen()
    start_datum = bereken_start_datum(maanden)
    if start_datum < first_date:
        start_datum = first_date
    if start_datum > last_date:
        print(f"  [!] Geen vergaderingen beschikbaar in de gevraagde periode.")
        return

    print(f"  Doorzoek van {start_datum} tot {last_date}\n")
    print(f"[4] Vergaderingen doorzoeken...\n")

    maand_lijst = maand_range(start_datum, last_date)
    alle_meeting_ids: set[str] = set()
    totaal_downloads = 0
    vergaderingen_met_docs = 0

    for maand, jaar in maand_lijst:
        meetings = await haal_vergaderingen_voor_maand(maand, jaar)
        if not meetings:
            continue

        gepubliceerd = [m for m in meetings if not m.get("notPublished", False)]
        if not gepubliceerd:
            continue

        if orgaan_filter_naam:
            gepubliceerd = [
                m for m in gepubliceerd
                if orgaan_filter_naam.lower() in m.get("organ", {}).get("name", "").lower()
                   or m.get("organ", {}).get("name", "").lower() in orgaan_filter_naam.lower()
            ]

        nieuw = [m for m in gepubliceerd if m["id"] not in alle_meeting_ids]
        alle_meeting_ids.update(m["id"] for m in gepubliceerd)

        if not nieuw:
            continue

        maand_label = f"{jaar}/{maand:02d}"
        print(f"  [{maand_label}] {len(nieuw)} vergadering(en) te verwerken")

        for idx, meeting in enumerate(nieuw, 1):
            print(f"    ({idx}/{len(nieuw)}) verwerken...", end="", flush=True)
            n = await verwerk_vergadering(
                meeting, output_pad, ook_agendapunten, document_filter
            )
            print(f" -> {n} PDF(s)")
            totaal_downloads += n
            if n > 0:
                vergaderingen_met_docs += 1

        await asyncio.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  Vergaderingen met documenten: {vergaderingen_met_docs}")
    print(f"  PDFs gedownload: {totaal_downloads}")
    print(f"  Output map: {output_pad.resolve()}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Scraper voor PDF-documenten van menen-echo.cipalschaubroeck.be",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper_menen.py --lijst-organen
  uv run python scraper_menen.py --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper_menen.py --orgaan "Gemeenteraad" --notulen --maanden 24
  uv run python scraper_menen.py --orgaan "College van Burgemeester en Schepenen" --output cbs --maanden 6
  uv run python scraper_menen.py --alle --maanden 3 --agendapunten
        """
    )
    parser.add_argument("--orgaan", "-o", type=str,
        help="Naam van het orgaan (bv. 'Gemeenteraad')")
    parser.add_argument("--alle", action="store_true",
        help="Scrape alle organen zonder filter")
    parser.add_argument("--output", "-d", type=str, default="pdfs_menen",
        help="Uitvoermap (standaard: pdfs_menen)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
        help="Aantal maanden terug te doorzoeken (standaard: 12)")
    parser.add_argument("--agendapunten", "-a", action="store_true",
        help="Ook individuele agendapunt-besluiten meenemen (trager)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
        help="Filter documenten op naam (bv. 'notulen').")
    parser.add_argument("--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")
    parser.add_argument("--base-url", type=str, default=None,
        help="Alternatieve basis-URL (voor gebruik via scraper_groep.py)")
    parser.add_argument("--context", type=str, default=None,
        help="Context-pad (standaard: /raadpleegomgeving, leeg voor csecho.be)")

    args = parser.parse_args()

    global BASE_URL, CONTEXT, LIJST_URL, KALENDER_API, ZOEKEN_URL, _config
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")
    if args.context is not None:
        CONTEXT = args.context.rstrip("/")
    elif args.base_url and "csecho.be" in BASE_URL.lower():
        CONTEXT = ""
    LIJST_URL = f"{BASE_URL}{CONTEXT}/zittingen/lijst"
    KALENDER_API = f"{BASE_URL}{CONTEXT}/calendar/fetchcalendar"
    ZOEKEN_URL = f"{BASE_URL}{CONTEXT}/zoeken"
    _config = ScraperConfig(base_url=BASE_URL)

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    async def _run() -> None:
        if args.lijst_organen:
            await init_session()
            await toon_organen()
            if SESSION is not None:
                await SESSION.close()
            return

        if not args.orgaan and not args.alle:
            print("Geef een orgaan op (--orgaan) of gebruik --alle voor alle organen.")
            print("Gebruik --lijst-organen om beschikbare organen te bekijken.\n")
            parser.print_help()
            sys.exit(1)

        await scrape(
            orgaan=None if args.alle else args.orgaan,
            output_map=args.output,
            maanden=args.maanden,
            ook_agendapunten=args.agendapunten,
            document_filter=args.document_filter,
        )
        if SESSION is not None:
            await SESSION.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
