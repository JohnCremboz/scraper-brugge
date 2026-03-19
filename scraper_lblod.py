"""
Scraper voor PDF-documenten van LBLOD-sites (lblod.*.be / LBLODWeb).

LBLOD (Lokale Besluiten als Linked Open Data) is een .NET webapplicatie
die door ~48 Vlaamse gemeenten wordt gebruikt.

Structuur:
    /LBLODWeb/Home/Overzicht                           → bestuurseenheden
    /LBLODWeb/Home/Overzicht/{eenheid}                  → organen
    /LBLODWeb/Home/Overzicht/{eenheid}/{orgaan}         → publicaties (huidig jaar)
    /LBLODWeb/Home/Overzicht/{eenheid}/{orgaan}/{jaar}  → publicaties per jaar
    /LBLODWeb/Home/Overzicht/{orgaan}/GetPublication?filename=X.pdf  → download

Gebruik:
    uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen
    uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --maanden 12
    uv run python scraper_lblod.py --base-url https://lblod.gistel.be --alle --maanden 36
"""

import argparse
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    robust_get,
    logger,
)

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

BASE_URL = "https://lblod.gistel.be"
OVERZICHT_PAD = "/LBLODWeb/Home/Overzicht"

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def init_session(base_url: str | None = None) -> None:
    """Initialiseer HTTP-sessie."""
    global SESSION, _config, BASE_URL
    if base_url:
        BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, output_dir=Path("."))
    SESSION = create_session(_config)


def _get(pad: str) -> requests.Response | None:
    """GET helper — pad wordt relatief aan BASE_URL opgelost."""
    url = pad if pad.startswith("http") else f"{BASE_URL}{pad}"
    return robust_get(SESSION, url, retries=1, timeout=30)


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "lxml")


# ---------------------------------------------------------------------------
# Stap 1 – bestuurseenheden ophalen (Gemeente X, OCMW X)
# ---------------------------------------------------------------------------

def haal_bestuurseenheden() -> list[dict]:
    """Haal bestuurseenheden op van de overzichtspagina."""
    resp = _get(OVERZICHT_PAD)
    if resp is None:
        return []
    soup = _soup(resp)
    eenheden: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        tekst = a.get_text(strip=True)
        if href.startswith(f"{OVERZICHT_PAD}/") and len(href) > len(OVERZICHT_PAD) + 10 and tekst:
            # Filter navigatie-links
            if tekst.lower() in ("home", "publicaties") or "Disclaimer" in href:
                continue
            eenheden.append({"naam": tekst, "pad": href})
    return eenheden


# ---------------------------------------------------------------------------
# Stap 2 – organen ophalen per bestuurseenheid
# ---------------------------------------------------------------------------

def haal_organen(eenheid_pad: str) -> list[dict]:
    """Haal organen (Gemeenteraad, CBS, …) op voor een bestuurseenheid."""
    resp = _get(eenheid_pad)
    if resp is None:
        return []
    soup = _soup(resp)
    organen: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        tekst = a.get_text(strip=True)
        if not tekst or "Disclaimer" in href:
            continue
        # Orgaan-links zijn dieper dan eenheid_pad
        if href.startswith(f"{eenheid_pad}/") and len(href) > len(eenheid_pad) + 10:
            # Geen jaar-links (die zijn korter, bv. /2025)
            rest = href[len(eenheid_pad) + 1:]
            if "/" not in rest and len(rest) > 10:
                organen.append({"naam": tekst, "pad": href})
    return organen


def haal_organen_statisch() -> list[dict]:
    """Haal alle organen op van alle bestuurseenheden — voor CLI --lijst-organen."""
    alle = []
    eenheden = haal_bestuurseenheden()
    for eenheid in eenheden:
        organen = haal_organen(eenheid["pad"])
        for org in organen:
            alle.append({
                "naam": org["naam"],
                "uuid": org["pad"],
                "eenheid": eenheid["naam"],
            })
    return alle


# ---------------------------------------------------------------------------
# Stap 3 – beschikbare jaren ophalen per orgaan
# ---------------------------------------------------------------------------

def haal_jaren(orgaan_pad: str) -> list[int]:
    """Haal beschikbare jaarnummers op voor een orgaan."""
    resp = _get(orgaan_pad)
    if resp is None:
        return []
    soup = _soup(resp)
    jaren: list[int] = []
    for a in soup.find_all("a", href=True):
        tekst = a.get_text(strip=True)
        if re.fullmatch(r"20\d{2}", tekst):
            jaren.append(int(tekst))
    jaren.sort(reverse=True)
    return jaren


# ---------------------------------------------------------------------------
# Stap 4 – documenten ophalen per orgaan + jaar
# ---------------------------------------------------------------------------

def _parse_datum(tekst: str) -> date | None:
    """Probeer een datum te parsen uit tekst zoals '05 februari 2026'."""
    maanden = {
        "januari": 1, "februari": 2, "maart": 3, "april": 4,
        "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
        "september": 9, "oktober": 10, "november": 11, "december": 12,
    }
    match = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", tekst.strip())
    if match:
        dag, maand_naam, jaar = match.groups()
        maand_nr = maanden.get(maand_naam.lower())
        if maand_nr:
            try:
                return date(int(jaar), maand_nr, int(dag))
            except ValueError:
                pass
    # Probeer dd-mm-yyyy uit bestandsnaam
    match2 = re.search(r"(\d{2})-(\d{2})-(\d{4})", tekst)
    if match2:
        try:
            return date(int(match2.group(3)), int(match2.group(2)), int(match2.group(1)))
        except ValueError:
            pass
    return None


def haal_documenten(orgaan_pad: str, jaar: int | None = None) -> list[dict]:
    """
    Haal alle downloadbare documenten op voor een orgaan (optioneel per jaar).
    Geeft lijst van {naam, url, datum, type} dicts.
    """
    pad = f"{orgaan_pad}/{jaar}" if jaar else orgaan_pad
    resp = _get(pad)
    if resp is None:
        return []
    soup = _soup(resp)

    documenten: list[dict] = []
    huidige_datum: date | None = None

    # De pagina is gestructureerd als secties per vergaderdatum
    # met daarin links naar GetPublication?filename=…
    for elem in soup.find_all(["h4", "h3", "h2", "strong", "b", "a"]):
        if elem.name in ("h4", "h3", "h2", "strong", "b"):
            tekst = elem.get_text(strip=True)
            parsed = _parse_datum(tekst)
            if parsed:
                huidige_datum = parsed
            continue

        # <a> element
        href = elem.get("href", "")
        if "GetPublication" not in href:
            continue

        naam = elem.get_text(strip=True)
        if not naam or naam == "HTML":
            continue  # sla HTML-links over, alleen PDFs

        # Bepaal documenttype uit bestandsnaam of linktekst
        filename = ""
        if "filename=" in href:
            filename = href.split("filename=")[-1]
            filename = unquote(filename)

        # Sla .html over
        if filename.lower().endswith(".html"):
            continue

        doc_type = "overig"
        naam_lower = naam.lower()
        filename_lower = filename.lower()
        if "notulen" in naam_lower or "notulen" in filename_lower:
            doc_type = "notulen"
        elif "agenda" in naam_lower or "agenda" in filename_lower:
            doc_type = "agenda"
        elif "besluitenlijst" in naam_lower or "besluitenlijst" in filename_lower:
            doc_type = "besluitenlijst"
        elif "uittreksel" in naam_lower or "uittreksel" in filename_lower:
            doc_type = "uittreksel"

        # Probeer datum uit bestandsnaam als we geen heading-datum hebben
        doc_datum = huidige_datum
        if doc_datum is None:
            doc_datum = _parse_datum(filename)

        documenten.append({
            "naam": naam,
            "url": href,
            "filename": filename,
            "datum": doc_datum,
            "type": doc_type,
        })

    return documenten


# ---------------------------------------------------------------------------
# Stap 5 – download
# ---------------------------------------------------------------------------

def download_document(doc_url: str, output_dir: Path, filename_hint: str) -> bool:
    """Download een PDF van een GetPublication-URL."""
    assert SESSION is not None
    url = doc_url if doc_url.startswith("http") else f"{BASE_URL}{doc_url}"
    try:
        r = SESSION.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return False

        # Bepaal bestandsnaam
        naam = filename_hint or "document.pdf"
        naam = sanitize_filename(naam)
        if not naam.lower().endswith(".pdf"):
            naam += ".pdf"

        bestemming = output_dir / naam
        if bestemming.exists():
            return False  # al gedownload

        bestemming.parent.mkdir(parents=True, exist_ok=True)
        with open(bestemming, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

        # Controleer geldigheid
        if bestemming.stat().st_size < 100:
            bestemming.unlink()
            return False

        return True

    except Exception as e:
        logger.debug("Download fout %s: %s", url, e)
        return False


# ---------------------------------------------------------------------------
# Hoofdlogica
# ---------------------------------------------------------------------------

def selecteer_organen(
    alle_organen: list[dict],
    orgaan_naam: str | None,
    alle: bool,
) -> list[dict]:
    """Filter organen op naam (deel-match, hoofdletterongevoelig)."""
    if alle or not orgaan_naam:
        return alle_organen
    zoek = orgaan_naam.lower().strip()
    matches = [o for o in alle_organen if zoek in o["naam"].lower()]
    return matches


def bepaal_jaren(orgaan_pad: str, maanden: int) -> list[int]:
    """Bepaal welke jaren we moeten doorzoeken op basis van --maanden."""
    beschikbaar = haal_jaren(orgaan_pad)
    if not beschikbaar:
        # Fallback: huidig jaar
        return [date.today().year]

    grensdatum = date.today() - timedelta(days=maanden * 31)
    grens_jaar = grensdatum.year
    return [j for j in beschikbaar if j >= grens_jaar]


def scrape(
    orgaan_naam: str | None,
    output_map: str,
    maanden: int,
    document_filter: str | None = None,
    alle: bool = False,
):
    """Hoofdfunctie voor het scrapen."""
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    grensdatum = date.today() - timedelta(days=maanden * 31)

    print(f"\n{'='*60}")
    print(f"  Scraper: {BASE_URL} (LBLOD)")
    print(f"  Orgaan:  {orgaan_naam or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    print(f"  Documentfilter: {document_filter or 'Geen (alle documenten)'}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    # 1. Bestuurseenheden ophalen
    print("[1] Bestuurseenheden ophalen...")
    print("    (verbinding maken...)")
    eenheden = haal_bestuurseenheden()
    if not eenheden:
        print("    [!] Geen bestuurseenheden gevonden. Controleer de URL.")
        sys.exit(1)
    print(f"    OK - {len(eenheden)} bestuurseenhe(i)d(en) gevonden")
    for e in eenheden:
        print(f"      - {e['naam']}")

    # 2. Organen ophalen
    print("\n[2] Organen ophalen...")
    alle_organen: list[dict] = []
    for eenheid in eenheden:
        organen = haal_organen(eenheid["pad"])
        for org in organen:
            org["eenheid"] = eenheid["naam"]
            alle_organen.append(org)
        for org in organen:
            print(f"      - {org['naam']}")

    if not alle_organen:
        print("    [!] Geen organen gevonden.")
        sys.exit(1)

    te_verwerken = selecteer_organen(alle_organen, orgaan_naam, alle)
    if not te_verwerken:
        print(f"    [!] Orgaan '{orgaan_naam}' niet gevonden. Gebruik --lijst-organen.")
        sys.exit(1)
    print(f"    OK - {len(te_verwerken)} orgaan/organen geselecteerd")

    # 3. Per orgaan scrapen
    totaal_downloads = 0
    totaal_overgeslagen = 0

    for orgaan in te_verwerken:
        print(f"\n[Orgaan] {orgaan['naam']}")

        # Bepaal jaren
        print("    (beschikbare jaren ophalen...)")
        jaren = bepaal_jaren(orgaan["pad"], maanden)
        if not jaren:
            print("    (geen jaren beschikbaar)")
            continue
        print(f"    Jaren: {', '.join(str(j) for j in jaren)}")

        orgaan_slug = sanitize_filename(orgaan["naam"])
        orgaan_map = output_pad / orgaan_slug
        orgaan_map.mkdir(parents=True, exist_ok=True)

        orgaan_downloads = 0

        for jaar in jaren:
            print(f"\n    [{jaar}]")
            print(f"      (documenten ophalen...)")

            docs = haal_documenten(orgaan["pad"], jaar)

            # Filter op datum
            docs = [d for d in docs if d["datum"] is None or d["datum"] >= grensdatum]

            # Filter op documenttype
            if document_filter:
                filter_lower = document_filter.lower()
                docs = [
                    d for d in docs
                    if filter_lower in d["naam"].lower()
                    or filter_lower in d["filename"].lower()
                    or filter_lower in d["type"]
                ]

            if not docs:
                print(f"      (geen documenten gevonden)")
                continue

            print(f"      {len(docs)} document(en) gevonden")

            for idx, doc in enumerate(docs, 1):
                hint = doc["filename"] or sanitize_filename(doc["naam"]) + ".pdf"
                print(f"      ({idx}/{len(docs)}) downloaden...", end="", flush=True)

                if download_document(doc["url"], orgaan_map, hint):
                    orgaan_downloads += 1
                    print(f" [OK] {hint[:60]}")
                else:
                    totaal_overgeslagen += 1
                    print(f" [SKIP]")

        totaal_downloads += orgaan_downloads
        print(f"    -> {orgaan_downloads} document(en) gedownload")

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  PDFs gedownload:  {totaal_downloads}")
    print(f"  Overgeslagen:     {totaal_overgeslagen}")
    print(f"  Output map:       {output_pad.resolve()}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor PDF-documenten van LBLOD-sites (lblod.*.be).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper_lblod.py --base-url https://lblod.gistel.be --lijst-organen
  uv run python scraper_lblod.py --base-url https://lblod.gistel.be --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper_lblod.py --base-url https://lblod.gistel.be --alle --notulen --maanden 36
  uv run python scraper_lblod.py --base-url https://lblod.assenede.be --alle --maanden 6
        """,
    )
    parser.add_argument("--base-url", type=str, default=None,
                        help="Basis-URL van de LBLOD-site (bv. https://lblod.gistel.be)")
    parser.add_argument("--orgaan", "-o", type=str, default=None,
                        help="Filter op orgaannaam (deel-match)")
    parser.add_argument("--alle", action="store_true",
                        help="Verwerk alle organen")
    parser.add_argument("--output", "-d", type=str, default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--maanden", "-m", type=int, default=12,
                        help="Aantal maanden terug (standaard: 12)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
                        help="Filter documenten op naam (bv. notulen)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter notulen")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon beschikbare organen en stop")
    # Compatibiliteit met scraper_groep.py
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--zichtbaar", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")

    args = parser.parse_args()

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    init_session(args.base_url)

    if args.lijst_organen:
        print(f"\nBeschikbare organen op {BASE_URL}:")
        print("    (ophalen...)")
        organen = haal_organen_statisch()
        if not organen:
            print("    Geen organen gevonden.")
            return
        huidige_eenheid = ""
        for org in organen:
            if org["eenheid"] != huidige_eenheid:
                huidige_eenheid = org["eenheid"]
                print(f"\n  [{huidige_eenheid}]")
            print(f"    - {org['naam']}")
        return

    if not args.alle and not args.orgaan:
        print("Geef --orgaan of --alle op.")
        sys.exit(1)

    scrape(
        orgaan_naam=args.orgaan,
        output_map=args.output,
        maanden=args.maanden,
        document_filter=args.document_filter,
        alle=args.alle,
    )


if __name__ == "__main__":
    main()

