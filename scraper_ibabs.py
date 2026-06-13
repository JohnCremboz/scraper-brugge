"""
scraper_ibabs.py — Scraper voor het iBabs Publieksportaal (bestuurlijkeinformatie.nl)
Dekt Kalmthout en Stabroek.

Gebruik:
    python scraper_ibabs.py --gemeente kalmthout
    python scraper_ibabs.py --alle

Platform structuur:
    /Calendar                          → lijst van categorieën per orgaan
    /Calendar/OpenCategory/{id}        → redirect naar meest recente vergadering
    /Agenda/Index/{uuid}               → vergadering detail + sidebar met jaar-overzicht
    /Agenda/Document/{uuid}?documentId={doc-uuid} → bijlage download
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    sanitize_filename,
)

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None


@dataclass
class _Resp:
    status_code: int
    text: str


async def _get(url: str) -> _Resp | None:
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            url, timeout=aiohttp.ClientTimeout(total=_config.timeout)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        print(f"[!] GET mislukt {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# CSV parsen
# ---------------------------------------------------------------------------

CSV_PATH = Path(__file__).parent / "simba-source.csv"
IBABS_PATTERN = re.compile(r"https?://([a-z0-9-]+)\.bestuurlijkeinformatie\.nl", re.I)

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def haal_ibabs_gemeenten() -> list[dict]:
    resultaat = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for lijn_nr, regel in enumerate(f, start=1):
            regel = regel.strip()
            if not regel:
                continue
            if lijn_nr == 1:
                continue
            delen = regel.split(";")
            if len(delen) < 2:
                print(f"[!] CSV lijn {lijn_nr}: te weinig kolommen, overgeslagen")
                continue
            gemeente, url = delen[0].strip(), delen[1].strip()
            if not gemeente:
                print(f"[!] CSV lijn {lijn_nr}: lege gemeentenaam, overgeslagen")
                continue
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                print(f"[!] CSV lijn {lijn_nr} ({gemeente}): ongeldige URL, overgeslagen")
                continue
            m = IBABS_PATTERN.search(url)
            if m:
                slug = m.group(1)
                base_url = f"https://{slug}.bestuurlijkeinformatie.nl"
                resultaat.append({"naam": gemeente, "slug": slug, "base_url": base_url})
    return resultaat


# ---------------------------------------------------------------------------
# Categorieën en vergaderingen ophalen
# ---------------------------------------------------------------------------

RELEVANTE_CATEGORIEEN = [
    "gemeenteraad", "raad voor maatschappelijk welzijn", "besluitenlijst",
    "verslag", "agenda", "vast bureau", "college",
]


def _is_relevant(naam: str) -> bool:
    naam_l = naam.lower()
    return any(cat in naam_l for cat in RELEVANTE_CATEGORIEEN)


def _parseer_datum(tekst: str) -> date | None:
    m_kort = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", tekst)
    if m_kort:
        dag, maand, jaar = int(m_kort.group(1)), int(m_kort.group(2)), int(m_kort.group(3))
        try:
            return date(jaar, maand, dag)
        except ValueError:
            return None

    m = re.search(
        r"(\d{1,2})\s+(" + "|".join(DUTCH_MONTHS.keys()) + r")\s+(\d{4})",
        tekst, re.I,
    )
    if not m:
        return None
    dag, maand_str, jaar = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    maand = DUTCH_MONTHS.get(maand_str, 0)
    if not maand:
        return None
    try:
        return date(jaar, maand, dag)
    except ValueError:
        return None


def _haal_orgaan(soup: BeautifulSoup) -> str:
    for tag in ["h1", "h2", "h3"]:
        el = soup.find(tag)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text[:80]
    return "Vergadering"


async def _verwerk_vergadering_href(
    href: str,
    base_url: str,
    cutoff: date,
    gezien_uuids: set[str],
) -> dict | None:
    """Haal één vergaderingspagina op en parse datum/orgaan."""
    uuid = href.rstrip("/").split("/")[-1]
    if uuid in gezien_uuids:
        return None
    gezien_uuids.add(uuid)

    verg_url = urljoin(base_url, href)
    verg_resp = await _get(verg_url)
    if verg_resp is None:
        return None

    verg_soup = BeautifulSoup(verg_resp.text, "lxml")
    title_tag = verg_soup.title
    if title_tag is None or title_tag.string is None:
        return None
    datum = _parseer_datum(title_tag.string)
    if datum is None or datum < cutoff:
        return None

    return {
        "uuid": uuid,
        "titel": _haal_orgaan(verg_soup),
        "datum": datum.strftime("%d/%m/%Y"),
        "url": verg_url,
        "soup": verg_soup,
    }


async def haal_vergaderingen(base_url: str, maanden: int = 3) -> list[dict]:
    """
    Haal vergaderingen op via de Calendar-pagina.
    Categorie-fetches en vergadering-fetches lopen parallel.
    """
    vandaag = date.today()
    cutoff = date(vandaag.year, max(1, vandaag.month - maanden + 1), 1)

    resp = await _get(f"{base_url}/Calendar")
    if resp is None or not resp.text.strip():
        print(f"[!] Lege HTML ontvangen voor Calendar: {base_url}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cat_links = [
        a["href"] for a in soup.find_all("a", href=True)
        if "/Calendar/OpenCategory/" in a["href"] and _is_relevant(a.get_text(strip=True))
    ]

    # Haal alle categoriepagina's parallel op
    async def _haal_cat(cat_href: str) -> list[str]:
        cat_url = urljoin(base_url, cat_href)
        cat_resp = await _get(cat_url)
        if cat_resp is None:
            return []
        cat_soup = BeautifulSoup(cat_resp.text, "lxml")
        return [
            a["href"] for a in cat_soup.find_all("a", href=True)
            if re.search(r"/Agenda/Index/[0-9a-f-]{36}", a["href"])
        ]

    cat_results = await asyncio.gather(*[_haal_cat(h) for h in cat_links])
    alle_hrefs = [href for hrefs in cat_results for href in hrefs]

    # Haal alle vergaderingspagina's parallel op
    gezien_uuids: set[str] = set()
    verg_tasks = [
        _verwerk_vergadering_href(href, base_url, cutoff, gezien_uuids)
        for href in alle_hrefs
    ]
    verg_results = await asyncio.gather(*verg_tasks)
    return [v for v in verg_results if v is not None]


# ---------------------------------------------------------------------------
# Vergadering details: agendapunten + bijlagen
# ---------------------------------------------------------------------------

async def haal_vergadering_details(vergadering: dict, base_url: str) -> dict:
    soup = vergadering.pop("soup", None)
    if soup is None:
        resp = await _get(vergadering["url"])
        if resp is None or not resp.text.strip():
            vergadering["agendapunten"] = []
            vergadering["documenten"] = []
            return vergadering
        soup = BeautifulSoup(resp.text, "lxml")

    # --- Agendapunten ---
    agendapunten = []
    ap_sectie = soup.find(string=re.compile(r"Agendapunten", re.I))
    if ap_sectie:
        container = ap_sectie.find_parent()
        while container and container.name not in ["div", "section", "ul", "ol", "table"]:
            container = container.find_parent()
        if container:
            for item in container.find_all(["li", "tr", "div"], recursive=False):
                tekst = item.get_text(" ", strip=True)
                if tekst and len(tekst) > 3:
                    agendapunten.append({"titel": tekst[:300]})

    if not agendapunten:
        tekst_blokken = soup.get_text("\n").split("\n")
        in_agenda = False
        for lijn in tekst_blokken:
            lijn = lijn.strip()
            if not lijn:
                continue
            if re.match(r"Agendapunten", lijn, re.I):
                in_agenda = True
                continue
            if in_agenda and re.match(r"Bijlagen|iBabs|Inloggen", lijn, re.I):
                break
            if in_agenda and len(lijn) > 5:
                agendapunten.append({"titel": lijn[:300]})

    # --- Bijlagen (documenten) ---
    documenten = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/Agenda/Document/" not in href:
            continue
        doc_naam = a.get_text(" ", strip=True)
        doc_naam = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:KB|MB)\s*$", "", doc_naam, flags=re.I)
        if not doc_naam:
            continue
        parsed_href = urlparse(href)
        qs = parse_qs(parsed_href.query)
        doc_id = qs.get("documentId", [None])[0]
        item_id = qs.get("agendaItemId", [None])[0]
        if doc_id and item_id:
            download_url = f"{base_url}/Document/LoadAgendaItemDocument/{doc_id}?agendaItemId={item_id}"
        else:
            download_url = urljoin(base_url, href)
        documenten.append({
            "naam": doc_naam,
            "url": download_url,
            "local_file": None,
        })

    vergadering["agendapunten"] = agendapunten
    vergadering["documenten"] = documenten
    return vergadering


async def haal_report_documenten(base_url: str, maanden: int = 3) -> list[dict]:
    """Haal PDF-bijlagen op uit iBabs Overzichten/Reports."""
    vandaag = date.today()
    cutoff = date(vandaag.year, max(1, vandaag.month - maanden + 1), 1)

    resp = await _get(f"{base_url}/Reports")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    report_links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/Reports/Details/[0-9a-f-]{36}", href):
            continue
        titel = a.get_text(" ", strip=True)
        if not re.search(r"\b(notulen|verslag|besluitenlijst)\b", titel, re.I):
            continue
        report_id = href.rstrip("/").split("/")[-1]
        report_links[report_id] = titel

    documenten: list[dict] = []
    gezien_docs: set[str] = set()
    gebruikte_namen: set[str] = set()

    for report_id, report_titel in report_links.items():
        data = {
            "draw": "1",
            "start": "0",
            "length": "100",
            "order[0][column]": "0",
            "order[0][dir]": "desc",
            "columns[0][data]": "registrationdate",
            "columns[0][name]": "registrationdate",
            "columns[1][data]": "title",
            "columns[1][name]": "title",
        }
        try:
            await async_rate_limit(_config)
            async with SESSION.post(
                f"{base_url}/Reports/GetReportData/{report_id}",
                data=data,
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                r.raise_for_status()
                rows = (await r.json()).get("data", [])
        except Exception:
            continue

        # Haal alle report-items parallel op
        async def _haal_item(row: dict) -> list[dict]:
            reg_date = _parseer_datum(row.get("registrationdate", ""))
            if reg_date is not None and reg_date < cutoff:
                return []
            item_id = row.get("DT_RowId")
            if not item_id:
                return []
            item_resp = await _get(f"{base_url}/Reports/Item/{item_id}")
            if item_resp is None:
                return []
            item_soup = BeautifulSoup(item_resp.text, "lxml")
            gevonden = []
            for a in item_soup.find_all("a", href=True):
                href = a["href"]
                if "/Reports/Document/" not in href:
                    continue
                qs = parse_qs(urlparse(href).query)
                doc_id = qs.get("documentId", [None])[0]
                if not doc_id or doc_id in gezien_docs:
                    continue
                gezien_docs.add(doc_id)
                doc_naam = a.get_text(" ", strip=True)
                doc_naam = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:KB|MB)\s*$", "", doc_naam, flags=re.I)
                if not doc_naam:
                    doc_naam = row.get("title") or report_titel
                if doc_naam in gebruikte_namen:
                    doc_naam = f"{doc_naam} - {report_titel}"
                gebruikte_namen.add(doc_naam)
                gevonden.append({
                    "naam": doc_naam,
                    "url": f"{base_url}/Document/View/{doc_id}",
                    "local_file": None,
                    "bron": report_titel,
                    "datum": row.get("registrationdate"),
                })
            return gevonden

        item_results = await asyncio.gather(*[_haal_item(row) for row in rows])
        for gevonden in item_results:
            documenten.extend(gevonden)

    return documenten


# ---------------------------------------------------------------------------
# HTML genereren
# ---------------------------------------------------------------------------

def genereer_html(gemeente_naam: str, vergaderingen: list[dict], output_dir: Path) -> Path:
    from html_output import agendapunten_html, doc_badges_html, genereer_html_tabel, html_output_path
    html_path = html_output_path(output_dir, gemeente_naam)
    rijen = [
        [
            v["datum"],
            v["titel"],
            agendapunten_html(v.get("agendapunten", [])),
            doc_badges_html(v.get("documenten", []), html_path),
        ]
        for v in vergaderingen
    ]
    return genereer_html_tabel(
        naam=gemeente_naam,
        bron="iBabs Publieksportaal",
        kolommen=["Datum", "Orgaan", "Agendapunten", "Documenten"],
        rijen=rijen,
        output_pad=html_path,
    )


# ---------------------------------------------------------------------------
# Hoofd scrape-functie
# ---------------------------------------------------------------------------

async def scrape_gemeente(
    gemeente: dict, maanden: int = 3, docs: bool = True, output_base: str = "pdfs"
) -> None:
    global SESSION, _config
    naam = gemeente["naam"]
    base_url = gemeente["base_url"]
    output_dir = Path(output_base) / sanitize_filename(naam)
    output_dir.mkdir(parents=True, exist_ok=True)

    if SESSION is not None:
        await SESSION.close()
    _config = ScraperConfig(base_url=base_url, output_dir=output_dir)
    SESSION = create_async_session(_config)

    print(f"\n{'=' * 70}")
    print(f"  Gemeente : {naam}")
    print(f"  Platform : {base_url}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = await haal_vergaderingen(base_url, maanden)
    print(f"    ✓ {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("  Geen vergaderingen gevonden.")
        return

    print("[2] Vergadering-details ophalen (parallel)...")
    await asyncio.gather(*[haal_vergadering_details(v, base_url) for v in vergaderingen])

    print("[3] Overzicht-documenten ophalen...")
    report_documenten = await haal_report_documenten(base_url, maanden)
    if report_documenten:
        vergaderingen.append({
            "uuid": "reports",
            "titel": "Overzichten",
            "datum": "",
            "url": f"{base_url}/Reports",
            "agendapunten": [],
            "documenten": report_documenten,
        })

    alle_docs = [doc for v in vergaderingen for doc in v.get("documenten", [])]
    n_docs = len(alle_docs)
    gedownload = 0

    if docs and n_docs > 0:
        print(f"[4] Documenten downloaden ({n_docs} totaal)...")
        dl_input = [{"url": d["url"], "naam": d["naam"]} for d in alle_docs]
        results = await async_download_documents_parallel(
            SESSION, _config, dl_input, output_dir, require_pdf=True,
        )
        for doc, result in zip(alle_docs, results):
            if result.success:
                doc["local_file"] = str(result.path)
                gedownload += 1
    else:
        print(f"[4] Documenten overgeslagen ({n_docs} beschikbaar).")

    print("[5] Metadata opslaan...")
    meta_pad = output_dir / f"{sanitize_filename(naam)}_metadata.json"
    exporteerbaar = [{k: v for k, v in verg.items() if k != "soup"} for verg in vergaderingen]
    meta_pad.write_text(json.dumps(exporteerbaar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✓ JSON: {meta_pad.name}")

    html_pad = genereer_html(naam, vergaderingen, output_dir)
    print(f"    ✓ HTML: {html_pad.name}")

    print(f"\n{'=' * 70}")
    print(f"  ✓ Klaar!")
    print(f"  Vergaderingen      : {len(vergaderingen)}")
    print(f"  Documenten         : {gedownload}/{n_docs}")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="iBabs scraper (bestuurlijkeinformatie.nl)")
    parser.add_argument("--gemeente", help="Naam van gemeente (fuzzy match)")
    parser.add_argument("--alle", action="store_true", help="Verwerk alle iBabs-gemeenten")
    parser.add_argument("--maanden", type=int, default=3, help="Aantal maanden terug (standaard 3)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--no-docs", action="store_true", help="Geen documenten downloaden")
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()

    gemeenten = haal_ibabs_gemeenten()

    if args.alle:
        doellijst = gemeenten
    elif args.gemeente:
        zoek = args.gemeente.lower()
        doellijst = [g for g in gemeenten if zoek in g["naam"].lower() or zoek in g["slug"].lower()]
        if not doellijst:
            print(f"Gemeente '{args.gemeente}' niet gevonden. Beschikbaar:")
            for g in gemeenten:
                print(f"  {g['naam']} ({g['slug']})")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)

    print(f"iBabs-scraper — {len(doellijst)} gemeente(n) te verwerken")
    print(f"Periode: afgelopen {args.maanden} maanden")

    for g in doellijst:
        await scrape_gemeente(g, maanden=args.maanden, docs=not args.no_docs, output_base=args.output)

    if SESSION is not None:
        await SESSION.close()


if __name__ == "__main__":
    asyncio.run(main())
