"""
Scraper voor PDF-documenten van ranst.meetingburger.net

Gebruik:
    uv run python scraper_ranst.py --lijst-organen
    uv run python scraper_ranst.py --orgaan "Gemeenteraad" --output pdfs_ranst --maanden 12
    uv run python scraper_ranst.py --orgaan "Gemeenteraad" --notulen --maanden 24
    uv run python scraper_ranst.py --orgaan "College van burgemeester en schepenen" --output cbs_ranst --maanden 6
    uv run python scraper_ranst.py --alle --maanden 3
"""

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

BASE_URL = "https://ranst.meetingburger.net"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
})

# Maanden in het Nederlands → nummer
MAAND_NL = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def sanitize_filename(name: str) -> str:
    """Verwijder ongeldige tekens uit bestandsnamen."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip("_. ")
    return name[:180] if len(name) > 180 else name or "document"


def parse_datum_uit_titel(titel: str) -> datetime | None:
    """
    Probeer een datum te ontleden uit een vergaderingstitel zoals
    'Gemeenteraad 23 februari 2026 21:00'.
    """
    patroon = re.compile(
        r'(\d{1,2})\s+(' + '|'.join(MAAND_NL.keys()) + r')\s+(\d{4})',
        re.IGNORECASE
    )
    m = patroon.search(titel)
    if m:
        dag = int(m.group(1))
        maand = MAAND_NL[m.group(2).lower()]
        jaar = int(m.group(3))
        try:
            return datetime(jaar, maand, dag)
        except ValueError:
            pass
    return None


def download_document(file_url: str, bestemming: Path, filename_hint: str = "") -> bool:
    """
    Download een HandleFile.ashx URL als bestand.
    Zet &download=1 om de juiste Content-Disposition header te forceren.
    """
    # Voeg &download=1 toe als dat ontbreekt
    if "download=1" not in file_url:
        sep = "&" if "?" in file_url else "?"
        dl_url = file_url + sep + "download=1"
    else:
        dl_url = file_url

    full_url = urljoin(BASE_URL, dl_url) if not dl_url.startswith("http") else dl_url

    try:
        resp = SESSION.get(full_url, stream=True, timeout=60, allow_redirects=True)
        if resp.status_code != 200:
            return False

        # Bepaal bestandsnaam: gebruik filename_hint (linktekst) als primaire bron,
        # valt terug op Content-Disposition en daarna op UUID
        naam = ""
        if filename_hint:
            naam = filename_hint
        else:
            cd = resp.headers.get("content-disposition", "")
            if "filename=" in cd:
                # UTF-8 encoded filename (RFC 5987)
                m = re.search(r"filename\*=utf-8''([^\s;]+)", cd, re.IGNORECASE)
                if m:
                    from urllib.parse import unquote
                    naam = unquote(m.group(1))
                else:
                    m = re.search(r'filename=["\']?([^"\';\n]+)', cd)
                    if m:
                        naam = m.group(1).strip().strip('"\'')

        if not naam:
            naam = file_url.split("id=")[-1].split("&")[0]

        # Extensie toevoegen als nodig
        if "." not in naam[-6:]:
            content_type = resp.headers.get("content-type", "")
            if "pdf" in content_type:
                naam += ".pdf"
            else:
                naam += ".bin"

        naam = sanitize_filename(naam)
        bestemming_pad = bestemming / naam

        # Overgeslagen als al bestaat (vorige run)
        if bestemming_pad.exists():
            return True

        # Lees in chunks — controleer op PDF-header (optioneel)
        eerste_chunk = None
        chunks = []
        for chunk in resp.iter_content(8192):
            if chunk:
                if eerste_chunk is None:
                    eerste_chunk = chunk
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


def haal_file_links_van_pagina(url: str) -> list[dict]:
    """
    Haal alle HandleFile.ashx links op van een pagina.
    Sla 'Download'-knop-duplicaten over door te dedupliceren op file-id.
    Geeft lijst van {url, naam} terug.
    """
    documenten = []
    seen_ids: set[str] = set()

    try:
        full_url = urljoin(BASE_URL, url) if not url.startswith("http") else url
        resp = SESSION.get(full_url, timeout=30)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Alleen HandleFile.ashx links, geen 'download=1' duplicaten hier
            if "HandleFile.ashx" not in href:
                continue
            # Sla de pure "Download"-knoppen en YouTube/externe links over
            tekst = link.get_text(strip=True)
            if tekst.lower() == "download":
                continue
            if "youtube.com" in href or "youtu.be" in href:
                continue
            # Extraheer file id om te dedupliceren
            m = re.search(r'[?&]id=([^&]+)', href)
            if not m:
                continue
            file_id = m.group(1)
            if file_id in seen_ids:
                continue
            seen_ids.add(file_id)

            # Gebruik bestandsnaam als tekst, anders id
            naam = tekst if tekst else file_id
            documenten.append({"url": href, "naam": naam})

    except Exception as e:
        print(f"      [!] Fout ophalen {url}: {e}")

    return documenten


def vergadering_heeft_inhoud(vergadering_url: str) -> tuple[bool, str]:
    """
    Controleer of een vergadering beschikbare inhoud heeft.
    Geeft (heeft_inhoud, titel) terug.
    """
    try:
        full_url = urljoin(BASE_URL, vergadering_url) if not vergadering_url.startswith("http") else vergadering_url
        resp = SESSION.get(full_url, timeout=15)
        if resp.status_code != 200:
            return False, ""

        soup = BeautifulSoup(resp.text, "lxml")
        tekst = soup.get_text()

        # Pagina nog niet gepubliceerd
        if "nog niet bekendgemaakt" in tekst or "niet beschikbaar" in tekst.lower():
            return False, ""

        # Titel uit <title> of breadcrumb
        title_tag = soup.find("title")
        titel = title_tag.get_text(strip=True) if title_tag else ""
        # Verwijder site-naam suffix
        titel = re.sub(r'\s*[|–-].*meetingburger.*$', '', titel, flags=re.IGNORECASE).strip()

        # Fallback: gebruik h1
        if not titel:
            h1 = soup.find("h1")
            if h1:
                for a in h1.find_all("a"):
                    a.decompose()
                titel = h1.get_text(strip=True)

        if not titel:
            titel = vergadering_url.rstrip("/").split("/")[-1]

        return True, titel

    except Exception:
        return False, ""


def verwerk_vergadering(
    vergadering_url: str,
    output_pad: Path,
    titel: str = "",
    document_filter: str | None = None,
) -> int:
    """
    Verwerk een vergadering: download alle bijhorende bestanden.
    Geeft het aantal nieuw gedownloade bestanden terug.
    """
    full_url = urljoin(BASE_URL, vergadering_url) if not vergadering_url.startswith("http") else vergadering_url

    # Als geen bekende titel, controleer of gepubliceerd (snelle check)
    if not titel:
        if not vergadering_is_gepubliceerd(full_url):
            return 0
        # Probeer titel uit de pagina-span te halen
        try:
            resp = SESSION.get(full_url, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")
            for span in soup.find_all("span"):
                t = span.get_text(strip=True)
                if len(t) > 10 and any(m in t.lower() for m in ["gemeenteraad", "college", "bureau", "raad", "commissie", "burgemeester"]):
                    titel = re.sub(r'\s*\([^)]+\)\s*$', '', t).strip()
                    break
        except Exception:
            pass
        if not titel:
            titel = full_url.rstrip("/").split("/")[-1]

    verg_id = full_url.rstrip("/").split("/")[-1]
    map_naam = sanitize_filename(f"{titel}_{verg_id}")
    verg_map = output_pad / map_naam
    verg_map.mkdir(parents=True, exist_ok=True)

    print(f"\n    [{titel}]")

    downloads = 0
    verwerkt_ids: set[str] = set()
    gebruikte_namen: set[str] = set()

    def verwerk_doc(doc: dict, bestemming: Path) -> bool:
        m = re.search(r'[?&]id=([^&]+)', doc["url"])
        file_id = m.group(1) if m else doc["url"]
        if file_id in verwerkt_ids:
            return False
        if document_filter and document_filter.lower() not in doc["naam"].lower():
            return False
        verwerkt_ids.add(file_id)
        naam_hint = sanitize_filename(doc["naam"])
        # Voeg UUID-fragment toe als naam al gebruikt is (ander bestand, zelfde naam)
        if naam_hint in gebruikte_namen:
            id_fragment = file_id.replace("-", "")[:8]
            if "." in naam_hint:
                basis, ext = naam_hint.rsplit(".", 1)
                naam_hint = f"{basis}_{id_fragment}.{ext}"
            else:
                naam_hint = f"{naam_hint}_{id_fragment}"
        gebruikte_namen.add(naam_hint)
        succes = download_document(doc["url"], bestemming, naam_hint)
        if succes:
            print(f"      [✓] {naam_hint[:70]}")
        return succes

    # Vergaderingspagina zelf + subpagina's
    for subpad in [full_url, f"{full_url}/agenda", f"{full_url}/besluitenlijst", f"{full_url}/notulen"]:
        for doc in haal_file_links_van_pagina(subpad):
            if verwerk_doc(doc, verg_map):
                downloads += 1

    if downloads == 0:
        print(f"      (geen documenten gevonden)")

    return downloads


def haal_organen() -> list[dict]:
    """
    Haal alle beschikbare organen op van de hoofdpagina.
    Extraheert unieke slugs uit vergaderingslinks (/{slug}/{UUID}).
    Geeft lijst van {naam, slug, url} terug.
    """
    organen = []
    gezien_slugs: set[str] = set()
    uuid_re = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    datum_re = re.compile(
        r'\s+\d{1,2}\s+\w+\s+\d{4}.*$', re.IGNORECASE
    )
    skip_slugs = {"search", "bekendmakingen", "pages", ""}

    try:
        # Haal zowel recente als alle vergaderingen op
        for url in [BASE_URL, f"{BASE_URL}?AlleVergaderingen=True"]:
            resp = SESSION.get(url, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if not href.startswith("http"):
                    href = urljoin(BASE_URL, href)

                parsed = urlparse(href)
                if parsed.netloc != "ranst.meetingburger.net":
                    continue

                delen = [s for s in parsed.path.strip("/").split("/") if s]
                # Zoek links met patroon /{slug}/{UUID}
                if len(delen) != 2:
                    continue
                slug, mogelijke_uuid = delen[0], delen[1]
                if uuid_re.match(mogelijke_uuid) and slug not in skip_slugs:
                    if slug in gezien_slugs:
                        continue
                    gezien_slugs.add(slug)

                    # Orgaannaam: verwijder datum-gedeelte uit linktekst
                    tekst = link.get_text(strip=True)
                    naam = datum_re.sub("", tekst).strip()
                    if not naam:
                        naam = slug

                    organen.append({
                        "naam": naam,
                        "slug": slug,
                        "url": f"{BASE_URL}/{slug}",
                    })

    except Exception as e:
        print(f"  [!] Fout laden organen: {e}")

    # Dedupliceer op slug (naam van eerste gevonden instantie)
    uniek: dict[str, dict] = {}
    for org in organen:
        if org["slug"] not in uniek:
            uniek[org["slug"]] = org
    return list(uniek.values())


def haal_vergadering_links(orgaan_slug: str) -> list[dict]:
    """
    Haal alle vergaderingslinks op voor een orgaan via /{slug}?AlleVergaderingen=True.
    Geeft lijst van {url, titel} terug, gesorteerd van nieuwst naar oudst.
    """
    items: list[dict] = []
    gezien: set[str] = set()

    uuid_re = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    # Datum-suffix in linktekst verwijderen: tekst bevat bv. "Gemeenteraad 23 februari..."
    # Bewaar de volledige tekst als titel

    for url in [
        f"{BASE_URL}/{orgaan_slug}",
        f"{BASE_URL}/{orgaan_slug}?AlleVergaderingen=True",
    ]:
        try:
            resp = SESSION.get(url, timeout=30)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = urljoin(BASE_URL, href)
                parsed = urlparse(full)
                if parsed.netloc != "ranst.meetingburger.net":
                    continue
                delen = [s for s in parsed.path.strip("/").split("/") if s]
                if (len(delen) == 2
                        and delen[0] == orgaan_slug
                        and uuid_re.match(delen[1])):
                    clean_url = f"{BASE_URL}/{delen[0]}/{delen[1]}"
                    if clean_url not in gezien:
                        gezien.add(clean_url)
                        titel = a.get_text(" ", strip=True)
                        items.append({"url": clean_url, "titel": titel})

        except Exception as e:
            print(f"  [!] Fout ophalen vergaderingen {url}: {e}")

    return items


def toon_organen():
    """Toon alle beschikbare organen."""
    organen = haal_organen()
    if not organen:
        print("Geen organen gevonden.")
        return
    print(f"\nBeschikbare organen op ranst.meetingburger.net:")
    print("-" * 50)
    for org in organen:
        print(f"  - {org['naam']}  (/{org['slug']})")


def scrape(
    orgaan: str | None,
    output_map: str,
    maanden: int,
    document_filter: str | None = None,
):
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    drempelDatum = datetime.now() - timedelta(days=maanden * 30)

    print(f"\n{'='*60}")
    print(f"  Scraper: ranst.meetingburger.net")
    print(f"  Orgaan:  {orgaan or 'Alle organen'}")
    print(f"  Maanden: {maanden} (vanaf {drempelDatum.strftime('%d/%m/%Y')})")
    print(f"  Documentfilter: {document_filter or 'Geen (alle documenten)'}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    alle_organen = haal_organen()

    if not alle_organen:
        print("[!] Geen organen gevonden op de hoofdpagina. Controleer de verbinding.")
        sys.exit(1)

    # Filter op orgaan indien opgegeven
    if orgaan:
        # Gebruik woordgrens-matching zodat "Gemeenteraad" niet ook
        # "Gemeenteraadscommissie" matcht (maar "Raad voor maatschappelijk" wel)
        kwb_patroon = re.compile(
            r'(?i)(^|\s)' + re.escape(orgaan) + r'(\s|$)'
        )
        te_verwerken = [
            o for o in alle_organen
            if kwb_patroon.search(o["naam"]) or o["naam"].lower() == orgaan.lower()
        ]
        if not te_verwerken:
            print(f"[!] Orgaan '{orgaan}' niet gevonden.")
            print("    Gebruik --lijst-organen voor beschikbare namen.")
            sys.exit(1)
    else:
        te_verwerken = alle_organen

    totaal_downloads = 0
    vergaderingen_met_docs = 0

    for org in te_verwerken:
        print(f"\n[Orgaan] {org['naam']}  (/{org['slug']})")
        print(f"  Vergaderingen ophalen...")

        vergadering_items = haal_vergadering_links(org["slug"])
        print(f"  {len(vergadering_items)} vergaderingen gevonden\n")

        for item in tqdm(vergadering_items, desc=f"  {org['naam'][:30]}", unit="verg"):
            verg_url = item["url"]
            titel = item["titel"]

            # Datumfilter: vergelijking op basis van bekende titel
            datum = parse_datum_uit_titel(titel)
            if datum and datum < drempelDatum:
                tqdm.write(f"  (drempelDatum bereikt bij '{titel}', stop)")
                break

            n = verwerk_vergadering(
                verg_url,
                output_pad,
                titel=titel,
                document_filter=document_filter,
            )
            totaal_downloads += n
            if n > 0:
                vergaderingen_met_docs += 1

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  Vergaderingen met documenten: {vergaderingen_met_docs}")
    print(f"  Bestanden gedownload: {totaal_downloads}")
    print(f"  Output map: {output_pad.resolve()}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Scraper voor PDF-documenten van ranst.meetingburger.net",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper_ranst.py --lijst-organen
  uv run python scraper_ranst.py --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper_ranst.py --orgaan "Gemeenteraad" --notulen --maanden 24
  uv run python scraper_ranst.py --orgaan "College van burgemeester en schepenen" --output cbs_ranst --maanden 6
  uv run python scraper_ranst.py --alle --maanden 3
        """
    )
    parser.add_argument("--orgaan", "-o", type=str,
        help="Naam van het orgaan (bv. 'Gemeenteraad')")
    parser.add_argument("--alle", action="store_true",
        help="Scrape alle organen zonder filter")
    parser.add_argument("--output", "-d", type=str, default="pdfs_ranst",
        help="Uitvoermap (standaard: pdfs_ranst)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
        help="Aantal maanden terug te doorzoeken (standaard: 12)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
        help="Filter documenten op naam (bv. 'notulen')")
    parser.add_argument("--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--lijst-organen", action="store_true",
        help="Toon beschikbare organen en stop")

    args = parser.parse_args()

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
        document_filter=args.document_filter,
    )


if __name__ == "__main__":
    main()
