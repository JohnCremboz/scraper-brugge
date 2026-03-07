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
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

BASE_URL = "https://menen-echo.cipalschaubroeck.be"
CONTEXT = "/raadpleegomgeving"
LIJST_URL = f"{BASE_URL}{CONTEXT}/zittingen/lijst"
KALENDER_API = f"{BASE_URL}{CONTEXT}/calendar/fetchcalendar"
ZOEKEN_URL = f"{BASE_URL}{CONTEXT}/zoeken"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
})


def init_session():
    """Initialiseer de sessie door de lijstpagina te bezoeken (JSESSIONID ophalen)."""
    try:
        SESSION.get(LIJST_URL, timeout=15)
    except Exception as e:
        print(f"[!] Sessie-initialisatie mislukt: {e}")


def sanitize_filename(name: str) -> str:
    """Verwijder ongeldige tekens uit bestandsnamen."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip("_. ")
    return name[:180] if len(name) > 180 else name or "document"


def download_document(doc_url: str, bestemming: Path, filename_hint: str = "") -> bool:
    """Download een /document/{id} URL als PDF."""
    full_url = urljoin(BASE_URL, doc_url) if not doc_url.startswith("http") else doc_url
    try:
        resp = SESSION.get(full_url, stream=True, timeout=60, allow_redirects=True)
        if resp.status_code != 200:
            return False

        # Bepaal bestandsnaam vanuit Content-Disposition
        cd = resp.headers.get("content-disposition", "")
        naam = ""
        if "filename=" in cd:
            match = re.search(r'filename=["\']?([^"\';\n]+)', cd)
            if match:
                naam = match.group(1).strip().strip('"\'')

        if not naam:
            naam = filename_hint or doc_url.split("/")[-1]

        if not naam.lower().endswith(".pdf"):
            naam += ".pdf"

        naam = sanitize_filename(naam)
        bestemming_pad = bestemming / naam

        # Overgeslagen als al bestaat
        if bestemming_pad.exists():
            return True

        # Lees chunks, controleer op PDF header
        eerste_chunk = None
        chunks = []
        for chunk in resp.iter_content(8192):
            if chunk:
                if eerste_chunk is None:
                    eerste_chunk = chunk
                    if not chunk.startswith(b"%PDF"):
                        return False  # Geen PDF
                chunks.append(chunk)

        if not chunks:
            return False

        with open(bestemming_pad, "wb") as f:
            for chunk in chunks:
                f.write(chunk)

        return True

    except Exception as e:
        print(f"      [!] Download fout {full_url}: {type(e).__name__}: {e}")
        return False


def haal_document_links_van_pagina(url: str) -> list[dict]:
    """Haal alle /document/ links op van een pagina."""
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


def haal_agendapunt_urls(vergadering_url: str) -> list[str]:
    """Haal alle agendapunt-URLs op van een vergaderingspagina."""
    agendapunten = []
    try:
        full_url = urljoin(BASE_URL, vergadering_url) if not vergadering_url.startswith("http") else vergadering_url
        resp = SESSION.get(full_url, timeout=30)
        if resp.status_code != 200:
            return []

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


def verwerk_vergadering(meeting: dict, output_pad: Path,
                        ook_agendapunten: bool = False,
                        document_filter: str | None = None) -> int:
    """
    Verwerk een vergadering: download alle bijhorende PDFs.
    Geeft het aantal nieuw gedownloade PDFs terug.
    Als document_filter opgegeven is (bv. 'notulen'), worden alleen
    documenten waarvan de naam die string bevat gedownload.
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
    map_naam = sanitize_filename(f"{organ}_{datum_label}_{mid[:8]}")
    verg_map = output_pad / map_naam
    verg_map.mkdir(parents=True, exist_ok=True)

    print(f"\n    [{organ}] {dt_str[:10] if dt_str else mid[:8]}")

    downloads = 0
    verwerkt_ids: set[str] = set()

    def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        doc_id = doc["url"].split("/")[-1]
        if doc_id in verwerkt_ids:
            return False
        if document_filter and document_filter.lower() not in doc["naam"].lower():
            return False
        verwerkt_ids.add(doc_id)
        naam_hint = sanitize_filename(doc["naam"])
        succes = download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [✓] {naam_hint[:70]}")
        return succes

    # Documenten van de vergaderingspagina (agenda, besluitenlijst, notulen, ...)
    doc_links = haal_document_links_van_pagina(verg_url)
    for doc in doc_links:
        if verwerk_doc(doc, verg_map):
            downloads += 1

    # Optioneel: individuele agendapunten
    if ook_agendapunten:
        ap_urls = haal_agendapunt_urls(verg_url)
        if ap_urls:
            ap_map = verg_map / "besluiten_per_punt"
            ap_map.mkdir(exist_ok=True)
            for ap_url in tqdm(ap_urls, desc="      Agendapunten", leave=False):
                for doc in haal_document_links_van_pagina(ap_url):
                    if verwerk_doc(doc, ap_map):
                        downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


def haal_organen() -> list[dict]:
    """
    Haal de beschikbare organen op uit de zoekpagina.
    Geeft lijst van {naam, uuid} terug.
    """
    try:
        resp = SESSION.get(ZOEKEN_URL, timeout=15)
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


def zoek_orgaan(orgaan_naam: str) -> tuple[str | None, str | None]:
    """
    Zoek UUID van een orgaan op naam (hoofdletterongevoelig, deel-match).
    Geeft (uuid, exacte_naam) terug of (None, None).
    """
    for org in haal_organen():
        if orgaan_naam.lower() in org["naam"].lower() or org["naam"].lower() in orgaan_naam.lower():
            return org["uuid"], org["naam"]
    return None, None


def haal_datum_grenzen() -> tuple[str, str]:
    """
    Haal de eerste en laatste vergaderdatum op uit de lijstpagina.
    Geeft (firstMeetingDate, lastMeetingDate) terug als 'YYYYMM' strings.
    """
    try:
        resp = SESSION.get(LIJST_URL, timeout=15)
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


def haal_vergaderingen_voor_maand(maand: int, jaar: int) -> list[dict]:
    """
    Haal alle vergaderingen op voor een gegeven maand/jaar via de calendar API.
    Geeft lijst van meeting-dicts terug.
    """
    try:
        params = {
            "month": f"{maand:02d}",
            "year": str(jaar),
            "calendarview": "false",
            "skipmeetings": "false",
        }
        resp = SESSION.get(KALENDER_API, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("meetings", [])
    except Exception as e:
        print(f"  [!] Fout calendar API {jaar}/{maand:02d}: {e}")
        return []


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


def toon_organen():
    """Toon alle beschikbare organen."""
    organen = haal_organen()
    if not organen:
        print("Geen organen gevonden.")
        return
    print("\nBeschikbare organen op menen-echo.cipalschaubroeck.be:")
    print("-" * 55)
    for org in organen:
        print(f"  - {org['naam']}")


def bereken_start_datum(maanden: int) -> str:
    """Bereken de startdatum (YYYYMM) voor het opgegeven aantal maanden geleden."""
    nu = datetime.now()
    maand = nu.month - maanden
    jaar = nu.year
    while maand <= 0:
        maand += 12
        jaar -= 1
    return f"{jaar}{maand:02d}"


def scrape(orgaan: str | None, output_map: str, maanden: int,
           ook_agendapunten: bool = False,
           document_filter: str | None = None):
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Scraper: menen-echo.cipalschaubroeck.be")
    print(f"  Orgaan:  {orgaan or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    print(f"  Incl. agendapunten: {'Ja' if ook_agendapunten else 'Nee (gebruik --agendapunten)'}")
    print(f"  Documentfilter: {document_filter or 'Geen (alle documenten)'}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    print("[1] Sessie initialiseren...")
    init_session()

    orgaan_filter_naam = None
    if orgaan:
        print(f"[2] Orgaan valideren: {orgaan}")
        uuid, exacte_naam = zoek_orgaan(orgaan)
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
    first_date, last_date = haal_datum_grenzen()
    start_datum = bereken_start_datum(maanden)
    # Beperk tot de beschikbare data
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
        meetings = haal_vergaderingen_voor_maand(maand, jaar)
        if not meetings:
            continue

        # Filter op gepubliceerd
        gepubliceerd = [m for m in meetings if not m.get("notPublished", False)]
        if not gepubliceerd:
            continue

        # Filter op orgaan
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

        for meeting in nieuw:
            n = verwerk_vergadering(
                meeting, output_pad, ook_agendapunten, document_filter
            )
            totaal_downloads += n
            if n > 0:
                vergaderingen_met_docs += 1

        time.sleep(0.5)

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
        help="Filter documenten op naam (bv. 'notulen'). Alleen docs die deze tekst bevatten worden gedownload.")
    parser.add_argument("--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")

    args = parser.parse_args()

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    if args.lijst_organen:
        init_session()
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
        document_filter=args.document_filter,
    )


if __name__ == "__main__":
    main()
