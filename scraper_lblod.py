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
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, parse_qs

import aiohttp
from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    sanitize_filename,
    logger,
)

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

BASE_URL = "https://lblod.gistel.be"
OVERZICHT_PAD = "/LBLODWeb/Home/Overzicht"

NOTULEN_SYNONIEMEN: list[str] = [
    "notulen",
    "verslag",
    "zittingsverslag",
    "besluitenlijst",
    "uittreksel",
    "dagorde",
]

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


def document_filter_match(doc_naam: str, doc_filename: str, doc_type: str,
                          document_filter: list[str] | str | None) -> bool:
    if not document_filter:
        return True
    naam_lower = doc_naam.lower()
    file_lower = doc_filename.lower()
    type_lower = doc_type.lower()
    if isinstance(document_filter, str):
        termen = [document_filter.lower()]
    else:
        termen = [t.lower() for t in document_filter]
    return any(
        t in naam_lower or t in file_lower or t in type_lower
        for t in termen
    )


async def init_session(base_url: str | None = None) -> None:
    global SESSION, _config, BASE_URL
    if SESSION is not None:
        await SESSION.close()
    if base_url:
        BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, output_dir=Path("."))
    SESSION = create_async_session(_config)


async def _get(pad: str) -> _Resp | None:
    """GET helper — pad wordt relatief aan BASE_URL opgelost."""
    url = pad if pad.startswith("http") else f"{BASE_URL}{pad}"
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.debug("GET mislukt %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Stap 1 – bestuurseenheden ophalen
# ---------------------------------------------------------------------------

async def haal_bestuurseenheden() -> list[dict]:
    resp = await _get(OVERZICHT_PAD)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    eenheden: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        tekst = a.get_text(strip=True)
        if href.startswith(f"{OVERZICHT_PAD}/") and len(href) > len(OVERZICHT_PAD) + 10 and tekst:
            if tekst.lower() in ("home", "publicaties") or "Disclaimer" in href:
                continue
            eenheden.append({"naam": tekst, "pad": href})
    return eenheden


# ---------------------------------------------------------------------------
# Stap 2 – organen ophalen per bestuurseenheid
# ---------------------------------------------------------------------------

async def haal_organen(eenheid_pad: str) -> list[dict]:
    resp = await _get(eenheid_pad)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    organen: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        tekst = a.get_text(strip=True)
        if not tekst or "Disclaimer" in href:
            continue
        if href.startswith(f"{eenheid_pad}/") and len(href) > len(eenheid_pad) + 10:
            rest = href[len(eenheid_pad) + 1:]
            if "/" not in rest and len(rest) > 10:
                organen.append({"naam": tekst, "pad": href})
    return organen


async def haal_organen_statisch() -> list[dict]:
    eenheden = await haal_bestuurseenheden()
    orgaan_results = await asyncio.gather(*[haal_organen(e["pad"]) for e in eenheden])
    alle = []
    for eenheid, organen in zip(eenheden, orgaan_results):
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

async def haal_jaren(orgaan_pad: str) -> list[int]:
    resp = await _get(orgaan_pad)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
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
    match2 = re.search(r"(\d{2})-(\d{2})-(\d{4})", tekst)
    if match2:
        try:
            return date(int(match2.group(3)), int(match2.group(2)), int(match2.group(1)))
        except ValueError:
            pass
    return None


async def haal_documenten(orgaan_pad: str, jaar: int | None = None) -> list[dict]:
    pad = f"{orgaan_pad}/{jaar}" if jaar else orgaan_pad
    resp = await _get(pad)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")

    documenten: list[dict] = []
    huidige_datum: date | None = None

    for elem in soup.find_all(["h4", "h3", "h2", "strong", "b", "a"]):
        if elem.name in ("h4", "h3", "h2", "strong", "b"):
            tekst = elem.get_text(strip=True)
            parsed = _parse_datum(tekst)
            if parsed:
                huidige_datum = parsed
            continue

        href = elem.get("href", "")
        if "GetPublication" not in href:
            continue

        naam = elem.get_text(strip=True)
        if not naam or naam == "HTML":
            continue

        filename = ""
        if "filename=" in href:
            filename = unquote(href.split("filename=")[-1])

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
        elif "verslag" in naam_lower or "verslag" in filename_lower:
            doc_type = "verslag"
        elif "dagorde" in naam_lower or "dagorde" in filename_lower:
            doc_type = "dagorde"

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
# Hoofdlogica
# ---------------------------------------------------------------------------

def selecteer_organen(
    alle_organen: list[dict],
    orgaan_naam: str | None,
    alle: bool,
) -> list[dict]:
    if alle or not orgaan_naam:
        return alle_organen
    zoek = orgaan_naam.lower().strip()
    return [o for o in alle_organen if zoek in o["naam"].lower()]


async def scrape(
    orgaan_naam: str | None,
    output_map: str,
    maanden: int,
    document_filter: list[str] | str | None = None,
    alle: bool = False,
    eenheid_filter: str | None = None,
):
    output_pad = Path(output_map)
    output_pad.mkdir(parents=True, exist_ok=True)

    grensdatum = date.today() - timedelta(days=maanden * 31)

    print(f"\n{'='*60}")
    print(f"  Scraper: {BASE_URL} (LBLOD)")
    print(f"  Orgaan:  {orgaan_naam or 'Alle organen'}")
    print(f"  Maanden: {maanden}")
    filter_weergave = (
        ", ".join(document_filter) if isinstance(document_filter, list)
        else document_filter or "Geen (alle documenten)"
    )
    print(f"  Documentfilter: {filter_weergave}")
    print(f"  Output:  {output_pad.resolve()}")
    print(f"{'='*60}\n")

    # 1. Bestuurseenheden
    print("[1] Bestuurseenheden ophalen...")
    eenheden = await haal_bestuurseenheden()
    if not eenheden:
        print("    [!] Geen bestuurseenheden gevonden. Controleer de URL.")
        sys.exit(1)
    if eenheid_filter:
        zoek = eenheid_filter.lower()
        eenheden = [e for e in eenheden if zoek in e["naam"].lower()]
        if not eenheden:
            print(f"    [!] Geen bestuurseenheid gevonden die '{eenheid_filter}' bevat.")
            sys.exit(1)
    print(f"    OK - {len(eenheden)} bestuurseenhe(i)d(en) gevonden")

    # 2. Organen parallel ophalen
    print("\n[2] Organen ophalen (parallel)...")
    orgaan_results = await asyncio.gather(*[haal_organen(e["pad"]) for e in eenheden])
    alle_organen: list[dict] = []
    for eenheid, organen in zip(eenheden, orgaan_results):
        for org in organen:
            org["eenheid"] = eenheid["naam"]
            alle_organen.append(org)
            print(f"      - {org['naam']}")

    if not alle_organen:
        print("    [!] Geen organen gevonden.")
        sys.exit(1)

    te_verwerken = selecteer_organen(alle_organen, orgaan_naam, alle)
    if not te_verwerken:
        print(f"    [!] Orgaan '{orgaan_naam}' niet gevonden. Gebruik --lijst-organen.")
        sys.exit(1)
    print(f"    OK - {len(te_verwerken)} orgaan/organen geselecteerd")

    # 3. Jaren parallel ophalen
    print("\n[3] Beschikbare jaren ophalen (parallel)...")
    jaren_results = await asyncio.gather(*[haal_jaren(o["pad"]) for o in te_verwerken])

    # 4. Documenten parallel ophalen per (orgaan, jaar)
    print("\n[4] Documenten ophalen (parallel)...")
    taken: list[tuple[dict, int]] = []
    for orgaan, jaren in zip(te_verwerken, jaren_results):
        beschikbare = jaren or [date.today().year]
        for jaar in beschikbare:
            if jaar >= grensdatum.year:
                taken.append((orgaan, jaar))

    doc_results = await asyncio.gather(*[haal_documenten(o["pad"], j) for o, j in taken])

    # 5. Filter en verzamel alle docs
    alle_docs: list[dict] = []
    for (orgaan, jaar), docs in zip(taken, doc_results):
        docs = [d for d in docs if d["datum"] is None or d["datum"] >= grensdatum]
        if document_filter:
            docs = [
                d for d in docs
                if document_filter_match(d["naam"], d["filename"], d["type"], document_filter)
            ]
        for doc in docs:
            doc["_orgaan"] = orgaan["naam"]
        alle_docs.extend(docs)

    print(f"    {len(alle_docs)} document(en) gevonden")

    if not alle_docs:
        print(f"\n{'='*60}")
        print("  Klaar! Geen documenten gevonden.")
        print(f"{'='*60}")
        return

    # 6. Download parallel
    print(f"\n[5] Downloaden ({len(alle_docs)} documenten, parallel)...")
    dl_input = [
        {
            "url": d["url"] if d["url"].startswith("http") else f"{BASE_URL}{d['url']}",
            "naam": d["filename"] or sanitize_filename(d["naam"]) + ".pdf",
        }
        for d in alle_docs
    ]
    results = await async_download_documents_parallel(
        SESSION, _config, dl_input, output_pad, require_pdf=True,
    )

    gedownload = sum(1 for r in results if r.success and not r.skipped)
    overgeslagen = sum(1 for r in results if not r.success)

    print(f"\n{'='*60}")
    print(f"  Klaar!")
    print(f"  PDFs gedownload:  {gedownload}")
    print(f"  Overgeslagen:     {overgeslagen}")
    print(f"  Output map:       {output_pad.resolve()}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
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
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--orgaan", "-o", type=str, default=None)
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--output", "-d", type=str, default="pdfs")
    parser.add_argument("--maanden", "-m", type=int, default=12)
    parser.add_argument("--document-filter", "-f", type=str, default=None)
    parser.add_argument("--notulen", action="store_true")
    parser.add_argument("--eenheid", "-e", type=str, default=None)
    parser.add_argument("--lijst-organen", action="store_true")
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")

    args = parser.parse_args()

    resolved_filter: list[str] | None = None
    if args.document_filter:
        resolved_filter = [t.strip() for t in args.document_filter.split(",") if t.strip()]
    elif args.notulen:
        resolved_filter = NOTULEN_SYNONIEMEN

    await init_session(args.base_url)

    if args.lijst_organen:
        print(f"\nBeschikbare organen op {BASE_URL}:")
        organen = await haal_organen_statisch()
        if not organen:
            print("    Geen organen gevonden.")
        else:
            huidige_eenheid = ""
            for org in organen:
                if org["eenheid"] != huidige_eenheid:
                    huidige_eenheid = org["eenheid"]
                    print(f"\n  [{huidige_eenheid}]")
                print(f"    - {org['naam']}")
        if SESSION:
            await SESSION.close()
        return

    if not args.alle and not args.orgaan:
        print("Geef --orgaan of --alle op.")
        sys.exit(1)

    await scrape(
        orgaan_naam=args.orgaan,
        output_map=args.output,
        maanden=args.maanden,
        document_filter=resolved_filter,
        alle=args.alle,
        eenheid_filter=args.eenheid,
    )

    if SESSION:
        await SESSION.close()


if __name__ == "__main__":
    asyncio.run(main())
