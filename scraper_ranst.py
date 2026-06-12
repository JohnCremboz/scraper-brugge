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

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    download_document as base_download_document,
    DownloadResult,
    logger,
    rate_limited_get,
)

BASE_URL = "https://ranst.meetingburger.net"

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None

# Maanden in het Nederlands → nummer
MAAND_NL = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def init_session():
    """Initialiseer de sessie met base_scraper configuratie."""
    global SESSION, _config
    _config = ScraperConfig(base_url=BASE_URL, output_dir=Path("."))
    try:
        SESSION = create_session(_config)
    except Exception as e:
        logger.warning("Sessie-initialisatie mislukt: %s", e)


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
        resp = rate_limited_get(SESSION, full_url, _config, timeout=30)
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
        resp = rate_limited_get(SESSION, full_url, _config, timeout=15)
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


def vergadering_is_gepubliceerd(vergadering_url: str) -> bool:
    """Compatibiliteitshelper voor codepaden zonder vooraf gekende titel."""
    heeft_inhoud, _ = vergadering_heeft_inhoud(vergadering_url)
    return heeft_inhoud


def _html_naar_pdf(html_tekst: str, pdf_pad: Path) -> bool:
    """Render HTML als PDF via PyMuPDF Story. Strips scripts/nav voor cleaner output."""
    import fitz

    soup_clean = BeautifulSoup(html_tekst, "lxml")
    for tag in soup_clean.find_all(["script", "style", "nav", "header", "footer", "iframe"]):
        tag.decompose()
    body = soup_clean.find("main") or soup_clean.find("article") or soup_clean.find("body")
    inhoud = str(body) if body else html_tekst
    # Expliciete UTF-8 declaratie zodat PyMuPDF speciale tekens correct verwerkt
    schone_html = f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{inhoud}</body></html>'

    story = fitz.Story(html=schone_html)
    writer = fitz.DocumentWriter(str(pdf_pad))
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (50, 50, -50, -50)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return True


def sla_notulen_als_pdf_op(notulen_url: str, output_pad: Path, vergadering_titel: str) -> bool:
    """
    Haal de notulen-HTML op, filter op mandaatrelevantie en sla op als PDF.
    Geeft True terug als een bestand opgeslagen of al aanwezig was.
    """
    full_url = urljoin(BASE_URL, notulen_url) if not notulen_url.startswith("http") else notulen_url

    try:
        resp = rate_limited_get(SESSION, full_url, _config, timeout=30)
        if resp.status_code != 200:
            return False

        soup = BeautifulSoup(resp.text, "lxml")
        tekst = soup.get_text(" ", strip=True)

        # Pagina niet gepubliceerd of leeg
        if "nog niet bekendgemaakt" in tekst or len(tekst.strip()) < 200:
            return False

        # Bestandsnaam op basis van vergaderingstitel
        veilige_titel = re.sub(r'[\\/*?:"<>|]', '', vergadering_titel)[:60].strip()
        bestandsnaam = sanitize_filename(f"notulen_{veilige_titel}.pdf")
        output_pad.mkdir(parents=True, exist_ok=True)
        bestand = output_pad / bestandsnaam

        if bestand.exists():
            return True

        # Content filter op tekst
        if _config and _config.content_filter:
            from mandaat_filter import is_relevant
            if not is_relevant(tekst):
                return False

        if _html_naar_pdf(resp.text, bestand):
            print(f"      [OK] {bestandsnaam} (notulen -> PDF)")
            return True
        return False

    except Exception as e:
        print(f"      [!] Fout notulen PDF: {e}")
        return False


def verwerk_vergadering(
    vergadering_url: str,
    output_pad: Path,
    titel: str = "",
    document_filter: str | None = None,
    subpagina_urls: list[str] | None = None,
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
            resp = rate_limited_get(SESSION, full_url, _config, timeout=15)
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

        if "download=1" not in doc["url"]:
            sep = "&" if "?" in doc["url"] else "?"
            dl_url = doc["url"] + sep + "download=1"
        else:
            dl_url = doc["url"]

        result = base_download_document(SESSION, _config, dl_url, bestemming, naam_hint, require_pdf=False)
        if result.success and not result.skipped:
            print(f"      [OK] {result.path.name[:70] if result.path else naam_hint[:70]}")
        elif not result.success:
            print(f"      [!] Fout: {result.error}")
        return result.success

    # Vergaderingspagina zelf + alleen gepubliceerde subpagina's uit de lijstpagina.
    subpaden = [full_url]
    if subpagina_urls:
        subpaden.extend(subpagina_urls)
    else:
        subpaden.extend([f"{full_url}/agenda", f"{full_url}/besluitenlijst", f"{full_url}/notulen"])

    notulen_url = f"{full_url}/notulen"
    for subpad in dict.fromkeys(subpaden):
        for doc in haal_file_links_van_pagina(subpad):
            if verwerk_doc(doc, output_pad):
                downloads += 1

    # Notulen opslaan als PDF (bevat de eigenlijke besluiten, ook zonder PDF-bijlagen)
    if sla_notulen_als_pdf_op(notulen_url, output_pad, titel):
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
            resp = rate_limited_get(SESSION, url, _config, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if not href.startswith("http"):
                    href = urljoin(BASE_URL, href)

                parsed = urlparse(href)
                if parsed.netloc != urlparse(BASE_URL).netloc:
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
    per_url: dict[str, dict] = {}

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
            resp = rate_limited_get(SESSION, url, _config, timeout=30)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = urljoin(BASE_URL, href)
                parsed = urlparse(full)
                if parsed.netloc != urlparse(BASE_URL).netloc:
                    continue
                delen = [s for s in parsed.path.strip("/").split("/") if s]
                if len(delen) < 2 or delen[0] != orgaan_slug or not uuid_re.match(delen[1]):
                    continue

                clean_url = f"{BASE_URL}/{delen[0]}/{delen[1]}"
                if clean_url not in per_url:
                    titel = a.get_text(" ", strip=True)
                    per_url[clean_url] = {
                        "url": clean_url,
                        "titel": titel,
                        "subpagina_urls": [],
                    }

                if len(delen) == 2 and clean_url not in gezien:
                    gezien.add(clean_url)
                    items.append(per_url[clean_url])
                elif len(delen) == 3 and delen[2] in {"agenda", "besluitenlijst", "notulen"}:
                    sub_url = f"{clean_url}/{delen[2]}"
                    if sub_url not in per_url[clean_url]["subpagina_urls"]:
                        per_url[clean_url]["subpagina_urls"].append(sub_url)

        except Exception as e:
            print(f"  [!] Fout ophalen vergaderingen {url}: {e}")

    return items


def toon_organen():
    """Toon alle beschikbare organen."""
    organen = haal_organen()
    if not organen:
        print("Geen organen gevonden.")
        return
    print(f"\nBeschikbare organen op {BASE_URL}:")
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
    print(f"  Scraper: {BASE_URL}")
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
        print(f"  (vergaderingen ophalen...)")

        vergadering_items = haal_vergadering_links(org["slug"])
        print(f"  {len(vergadering_items)} vergaderingen gevonden\n")

        for idx, item in enumerate(tqdm(vergadering_items, desc=f"  {org['naam'][:30]}", unit="verg"), 1):
            verg_url = item["url"]
            titel = item["titel"]

            # Datumfilter: vergelijking op basis van bekende titel
            datum = parse_datum_uit_titel(titel)
            if datum and datum < drempelDatum:
                tqdm.write(f"  (drempelDatum bereikt bij '{titel}', stop)")
                break

            datum_str = datum.strftime("%Y-%m-%d") if datum else "onbekend"
            vergadering_pad = output_pad / datum_str

            n = verwerk_vergadering(
                verg_url,
                vergadering_pad,
                titel=titel,
                document_filter=document_filter,
                subpagina_urls=item.get("subpagina_urls"),
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
    parser.add_argument("--base-url", type=str, default=None,
        help="Alternatieve basis-URL (voor gebruik via scraper_groep.py)")

    args = parser.parse_args()

    if args.base_url:
        global BASE_URL, _config
        BASE_URL = args.base_url.rstrip("/")

    init_session()

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
