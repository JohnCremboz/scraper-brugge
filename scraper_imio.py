"""
Scraper voor iMio/Plone-gemeenten — procès-verbaux rechtstreeks van gemeentesite.

iMio is een Waalse IT-dienstverlener die gemeentesites bouwt op Plone CMS.
Anders dan deliberations.be (gecentraliseerde metadatabank) staan de eigenlijke
PDF-notulen hier op het gemeentelijk domein zelf.

Structuur (twee varianten — auto-gedetecteerd):
  A) Jaar-subpagina's:
       /{prefix}/proces-verbaux          → bevat links naar /2026, /2025, /2024-1 …
       /{prefix}/proces-verbaux/2026     → bevat directe .pdf-links
  B) Alles op één pagina:
       /{prefix}/proces-verbaux          → bevat directe .pdf-links (Herstal-stijl)

Datum-filtering:
  - Structuur A: enkel jaarpagina's >= grensdatum.year worden opgehaald.
  - Structuur B + A (jaar-pagina's): datum geparsed uit linktekst ("19 janvier 2026")
    of uit bestandsnaam; documenten ouder dan grensdatum worden overgeslagen.

Gebruik:
    uv run python scraper_imio.py --gemeente viroinval --maanden 12
    uv run python scraper_imio.py --gemeente herstal --maanden 6
    uv run python scraper_imio.py --alle --maanden 12
    uv run python scraper_imio.py --lijst
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    async_download_documents_parallel,
    async_rate_limit,
    create_async_session,
    logger,
    print_summary,
    sanitize_filename,
    DownloadResult,
)

SESSION: aiohttp.ClientSession | None = None
_config: ScraperConfig | None = None
BASE_URL = ""

# ---------------------------------------------------------------------------
# Française maandnamen → maandnummer
# ---------------------------------------------------------------------------

_FR_MAANDEN: dict[str, int] = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

_JAAR_LINK_RE = re.compile(r"/((?:[a-z]+-)*20\d{2})(?:-\d+)?/?$", re.IGNORECASE)
_JAAR_IN_URL_RE = re.compile(r"/(?:[a-z-]*?)?(20\d{2})(?:-\d+)?/?$", re.IGNORECASE)
_FACETED_QUERY_SUFFIX = "/@@faceted_query"
_DATUM_TEKST_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_FR_MAANDEN) + r")\s+(20\d{2})",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------

GEMEENTEN: dict[str, dict] = {
    "www.viroinval.be": {
        "naam": "Viroinval",
        "listing_pad": "/fr/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.couvin.be": {
        "naam": "Couvin",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.herstal.be": {
        "naam": "Herstal",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.burdinne.be": {
        "naam": "Burdinne",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/pv-du-conseil",
    },
    "www.andenne.be": {
        "naam": "Andenne",
        "listing_pad": "/conseil-communal/proces-verbaux",
    },
    "www.arlon.be": {
        "naam": "Arlon",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.blegny.be": {
        "naam": "Blégny",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.chaumont-gistoux.be": {
        "naam": "Chaumont-Gistoux",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.daverdisse.be": {
        "naam": "Daverdisse",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.estinnes.be": {
        "naam": "Estinnes",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.froidchapelle.be": {
        "naam": "Froidchapelle",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.gerpinnes.be": {
        "naam": "Gerpinnes",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.grace-hollogne.be": {
        "naam": "Grâce-Hollogne",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.heron.be": {
        "naam": "Héron",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.honnelles.be": {
        "naam": "Honnelles",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.jalhay.be": {
        "naam": "Jalhay",
        "listing_pad": "/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.jurbise.be": {
        "naam": "Jurbise",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.meix-devant-virton.be": {
        "naam": "Meix-devant-Virton",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.mettet.be": {
        "naam": "Mettet",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.paliseul.be": {
        "naam": "Paliseul",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.philippeville.be": {
        "naam": "Philippeville",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.quaregnon.be": {
        "naam": "Quaregnon",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.saint-ghislain.be": {
        "naam": "Saint-Ghislain",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.thimister-clermont.be": {
        "naam": "Thimister-Clermont",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.thuin.be": {
        "naam": "Thuin",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.wasseiges.be": {
        "naam": "Wasseiges",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.clavier.be": {
        "naam": "Clavier",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.braine-lalleud.be": {
        "naam": "Braine-l'Alleud",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.villedefontaine.be": {
        "naam": "Fontaine-l'Évêque",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/conseil-communal-proces-verbaux",
        "faceted": True,
    },
    "www.lahulpe.be": {
        "naam": "La Hulpe",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "ajax_load": True,
    },
    "www.manage-commune.be": {
        "naam": "Manage",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/ordre-du-jour-proces-verbaux/comptes-rendus",
        "ajax_load": True,
    },
    "www.bouillon.be": {
        "naam": "Bouillon",
        "listing_pad": "/ma-commune/vie-politique/proces-verbal",
    },
    "www.ciney.be": {
        "naam": "Ciney",
        "listing_pad": "/vie-communale/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.donceel.be": {
        "naam": "Donceel",
        "listing_pad": "/conseil-communal",
    },
    "www.villers-la-ville.be": {
        "naam": "Villers-la-Ville",
        "listing_pad": "/administration/vie-politique/conseil/compte-rendu-du-conseil-communal",
    },
    "www.beaumont.be": {
        "naam": "Beaumont",
        "listing_pad": "/P-V-des-conseils-communaux",
    },
    "www.cerfontaine.be": {
        "naam": "Cerfontaine",
        "listing_pad": "/ma-commune/politique/le-conseil-communal/pv-approuves-des-dernieres-seances-du-conseil-communal",
    },
    "www.ecaussinnes.be": {
        "naam": "Ecaussinnes",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "ajax_load": True,
    },
    "www.estaimpuis.be": {
        "naam": "Estaimpuis",
        "listing_pad": "/pv-du-conseil-communal/",
    },
    "www.frasnes-lez-anvaing.be": {
        "naam": "Frasnes-lez-Anvaing",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/resumes-du-conseil-communal-1",
        "ajax_load": True,
    },
    "www.onhaye.be": {
        "naam": "Onhaye",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux-des-assemblees",
    },
    "www.saint-hubert.be": {
        "naam": "Saint-Hubert",
        "listing_pad": "/pv/",
    },
    "www.sivry-rance.be": {
        "naam": "Sivry-Rance",
        "listing_pad": "/ma-commune/vie-politique/copy_of_proces-verbaux-des-conseils-communaux",
        "ajax_load": True,
    },
    "www.lessines.be": {
        "naam": "Lessines",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux/proces-verbaux-des-conseils-communaux-pdf",
    },
    "www.leroeulx.be": {
        "naam": "Le Roeulx",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.attert.be": {
        "naam": "Attert",
        "listing_pad": "/notre-commune/vie-politique/conseil-communal/seances-du-conseil-communal/proces-verbaux-du-conseil-communal/aa",
    },
    "www.beauraing.be": {
        "naam": "Beauraing",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "plone_folder_listing": True,
    },
    "www.villedespa.be": {
        "naam": "Spa",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/ordres-du-jour-et-proces-verbaux",
        "plone_folder_listing": True,
    },
    "www.profondeville.be": {
        "naam": "Profondeville",
        "listing_pad": "/commune/vie-politique/conseil-communal/ordres-du-jour-et-proces-verbaux",
    },
    "www.villedecomines-warneton.be": {
        "naam": "Comines-Warneton",
        "listing_pad": "/fr/ma-commune/politique/conseil-communal",
        "subpaginas": True,
    },
}


# ---------------------------------------------------------------------------
# Sessie helpers
# ---------------------------------------------------------------------------

@dataclass
class _Resp:
    status_code: int
    text: str


async def init_session(base_url: str) -> None:
    global SESSION, _config, BASE_URL
    if SESSION is not None:
        await SESSION.close()
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.3)
    SESSION = create_async_session(_config)


def haal_organen_statisch() -> list[dict]:
    return []


async def _get(url: str) -> _Resp | None:
    full = url if url.startswith("http") else f"{BASE_URL}{url}"
    if SESSION is None or _config is None:
        return None
    try:
        await async_rate_limit(_config)
        async with SESSION.get(
            full, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            text = await resp.text()
            return _Resp(status_code=resp.status, text=text)
    except Exception as exc:
        logger.debug("GET mislukt %s: %s", full, exc)
        return None


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(BASE_URL, href)


# ---------------------------------------------------------------------------
# Datum helpers (sync — geen HTTP)
# ---------------------------------------------------------------------------

def _datum_uit_tekst(tekst: str) -> date | None:
    m = _DATUM_TEKST_RE.search(tekst)
    if m:
        dag = int(m.group(1))
        maand = _FR_MAANDEN.get(m.group(2).lower())
        jaar = int(m.group(3))
        if maand is not None:
            try:
                return date(jaar, maand, dag)
            except ValueError:
                pass
    m = re.search(r"\b(\d{1,2})[-./](\d{2})[-./](20\d{2})\b", tekst)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _datum_uit_pad(pad: str) -> date | None:
    naam = Path(urlparse(pad).path).stem.lower()

    m = re.search(r"[._-](\d{2})[._-](\d{2})[._-](20\d{2})(?!\d)", naam)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    m = re.search(r"(20\d{2})[._-](\d{2})[._-](\d{2})(?!\d)", naam)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    m = re.search(r"(\d{2})[._-](\d{2})[._-](\d{2})(?!\d)", naam)
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    m = re.search(r"(\d{8})", naam)
    if m:
        s = m.group(1)
        for fmt in ((s[:4], s[4:6], s[6:8]), (s[6:], s[4:6], s[:4])):
            try:
                return date(int(fmt[0]), int(fmt[1]), int(fmt[2]))
            except ValueError:
                pass

    return _datum_uit_tekst(naam.replace("_", " ").replace("-", " "))


# ---------------------------------------------------------------------------
# Kern scrape-logica (sync HTML parsers)
# ---------------------------------------------------------------------------

def _pdfs_van_pagina(html: str, pagina_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    basis_netloc = urlparse(pagina_url).netloc
    gezien: set[str] = set()
    pdfs: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = _absolute(href)
        parsed = urlparse(full)

        if parsed.netloc != basis_netloc:
            continue

        pad = parsed.path
        if pad.lower().endswith(".pdf/view"):
            full = full[: full.lower().rfind("/view")]
            pad = urlparse(full).path

        if not pad.lower().endswith(".pdf"):
            continue

        if full in gezien:
            continue
        gezien.add(full)

        tekst = a.get_text(" ", strip=True)
        pdfs.append({"url": full, "naam": tekst or Path(pad).name})

    return pdfs


def _haal_jaarpaginas(html: str, index_url: str, grensjaar: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    basis_netloc = urlparse(index_url).netloc
    basis_pad = urlparse(index_url).path.rstrip("/")
    jaar_urls: list[str] = []
    seen_urls: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = _absolute(href)
        if urlparse(full).netloc != basis_netloc:
            continue
        link_pad = urlparse(full).path.rstrip("/")
        if not link_pad.startswith(basis_pad + "/"):
            continue
        pad = urlparse(full).path
        m = _JAAR_IN_URL_RE.search(pad)
        if not m:
            continue
        jaar = int(m.group(1))
        if jaar >= grensjaar and full not in seen_urls:
            seen_urls.add(full)
            jaar_urls.append(full)

    return jaar_urls


# ---------------------------------------------------------------------------
# Async subpagina-fetchers
# ---------------------------------------------------------------------------

async def _haal_faceted_zitting_urls(listing_pad: str, grensdatum: date) -> list[str]:
    """Structuur C: paginering via @@faceted_query (sequential — stop-conditioneel)."""
    index_url = _absolute(listing_pad)
    basis_netloc = urlparse(index_url).netloc
    zitting_urls: list[str] = []
    b_start = 0
    stop = False

    while not stop:
        query_url = f"{index_url}{_FACETED_QUERY_SUFFIX}?b_start={b_start}"
        resp = await _get(query_url)
        if not resp or resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        links = [
            a for a in soup.find_all("a", href=True)
            if urlparse(_absolute(a["href"])).netloc == basis_netloc
            and listing_pad.rstrip("/") in a["href"]
            and not a["href"].rstrip("/").endswith(listing_pad.rstrip("/"))
        ]

        if not links:
            break

        for a in links:
            tekst = a.get_text(" ", strip=True)
            datum = _datum_uit_tekst(tekst)
            if datum is not None and datum < grensdatum:
                stop = True
                break
            zitting_urls.append(_absolute(a["href"]))

        b_start += 20

    return zitting_urls


async def _haal_subpagina_zitting_urls(listing_pad: str, grensdatum: date) -> list[str]:
    """Structuur D: directe kind-links op listingpagina."""
    index_url = _absolute(listing_pad)
    listing_path = urlparse(index_url).path.rstrip("/")
    basis_netloc = urlparse(index_url).netloc

    resp = await _get(index_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    zitting_urls: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        full = _absolute(a["href"])
        parsed = urlparse(full)
        if parsed.netloc != basis_netloc:
            continue
        pad = parsed.path.rstrip("/")
        if not pad.startswith(listing_path + "/"):
            continue
        rest = pad[len(listing_path) + 1:]
        if "/" in rest or not rest:
            continue
        if full in seen:
            continue
        seen.add(full)
        tekst = a.get_text(" ", strip=True)
        datum = _datum_uit_tekst(tekst)
        if datum is not None and datum < grensdatum:
            continue
        zitting_urls.append(full)

    return zitting_urls


async def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
) -> tuple[int, int]:
    """Scrape één iMio-gemeente. Returns (totaal_geprobeerd, totaal_gedownload)."""
    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    # Structuur C/D: subpagina's per zitting
    if config.get("faceted") or config.get("subpaginas"):
        if config.get("faceted"):
            zitting_urls = await _haal_faceted_zitting_urls(config["listing_pad"], grensdatum)
        else:
            zitting_urls = await _haal_subpagina_zitting_urls(config["listing_pad"], grensdatum)

        # Haal alle zitting-subpagina's parallel op
        async def _haal_zitting(url: str) -> list[dict]:
            r = await _get(url)
            if not r or r.status_code != 200:
                return []
            return _pdfs_van_pagina(r.text, url)

        zitting_results = await asyncio.gather(*[_haal_zitting(u) for u in zitting_urls])

        gezien_urls: set[str] = set()
        alle_pdfs: list[dict] = []
        for pdfs in zitting_results:
            for pdf in pdfs:
                if pdf["url"] not in gezien_urls:
                    gezien_urls.add(pdf["url"])
                    datum = _datum_uit_pad(pdf["url"])
                    if datum is None:
                        datum = _datum_uit_tekst(pdf["naam"])
                    if datum is not None and datum < grensdatum:
                        continue
                    alle_pdfs.append(pdf)

        if not alle_pdfs:
            logger.info("   Geen PDF's gevonden voor %s", naam)
            return 0, 0
    else:
        # Structuur A/B: index + optioneel jaarpagina's
        index_url = _absolute(config["listing_pad"])
        ajax_load = config.get("ajax_load", False)
        fetch_url = f"{index_url}?ajax_load=1" if ajax_load else index_url
        resp = await _get(fetch_url)
        if not resp or resp.status_code != 200:
            logger.warning("Listing niet bereikbaar: %s (HTTP %s)",
                           index_url, getattr(resp, "status_code", "?"))
            return 0, 0

        jaar_urls = _haal_jaarpaginas(resp.text, index_url, grensdatum.year)

        if jaar_urls:
            # Structuur A: haal jaarpagina's parallel op
            async def _haal_jaar(url: str) -> tuple[str, str] | None:
                fetch = f"{url}?ajax_load=1" if ajax_load else url
                r = await _get(fetch)
                return (r.text, url) if r and r.status_code == 200 else None

            jaar_results = await asyncio.gather(*[_haal_jaar(u) for u in jaar_urls])
            paginas = [r for r in jaar_results if r is not None]
        else:
            # Structuur B: PDFs direct op indexpagina
            paginas = [(resp.text, index_url)]

        alle_pdfs = []
        gezien_urls = set()

        for pagina_html, pagina_url in paginas:
            for pdf in _pdfs_van_pagina(pagina_html, pagina_url):
                if pdf["url"] in gezien_urls:
                    continue
                gezien_urls.add(pdf["url"])

                datum = _datum_uit_tekst(pdf["naam"])
                if datum is None:
                    datum = _datum_uit_pad(pdf["url"])

                if datum is not None and datum < grensdatum:
                    continue

                alle_pdfs.append(pdf)

    if not alle_pdfs:
        logger.info("   Geen PDF's gevonden voor %s", naam)
        return 0, 0

    logger.info("   %d PDF(s) gevonden", len(alle_pdfs))

    docs = [
        {"url": pdf["url"], "naam": sanitize_filename(pdf["naam"]) if pdf["naam"] else ""}
        for pdf in alle_pdfs
    ]
    resultaten = await async_download_documents_parallel(
        SESSION, _config, docs, gem_dir, require_pdf=True,
    )

    gedownload = sum(1 for r in resultaten if r.success and not r.skipped)
    print_summary(resultaten, naam=naam)
    return len(resultaten), gedownload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _zoek_gemeente(netloc: str) -> dict | None:
    netloc = netloc.lower().lstrip("www.")
    for key, conf in GEMEENTEN.items():
        if netloc in key.lower() or key.lower().lstrip("www.") == netloc:
            return conf
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor iMio/Plone-gemeenten (procès-verbaux)"
    )
    parser.add_argument("--gemeente", help="Gemeente-naam of domeinnaam")
    parser.add_argument("--base-url", dest="base_url",
                        help="Volledige basis-URL (https://www.gemeente.be)")
    parser.add_argument("--alle", action="store_true",
                        help="Scrape alle geconfigureerde gemeenten")
    parser.add_argument("--lijst", "--lijst-gemeenten", action="store_true",
                        dest="lijst", help="Toon alle ondersteunde gemeenten")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Aantal maanden terug (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    args = parser.parse_args()

    if args.lijst:
        print("Ondersteunde gemeenten:")
        for netloc, conf in GEMEENTEN.items():
            print(f"  {conf['naam']:30s}  https://{netloc}/")
        return

    te_verwerken: list[tuple[str, dict]] = []

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [(netloc, conf)]
        await init_session(args.base_url)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "")
            if zoek in naam_sleutel or zoek in netloc.replace("-", ""):
                te_verwerken = [(netloc, conf)]
                await init_session(f"https://{netloc}")
                break
        if not te_verwerken:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden. Gebruik --lijst.")
            sys.exit(1)
    elif args.alle:
        te_verwerken = list(GEMEENTEN.items())
    else:
        parser.print_help()
        sys.exit(1)

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    totaal_geprobeerd = 0
    totaal_gedownload = 0

    for netloc, conf in te_verwerken:
        if args.alle:
            await init_session(f"https://{netloc}")
        gevonden, gedownload = await scrape_gemeente(conf, output_root, maanden=args.maanden)
        totaal_geprobeerd += gevonden
        totaal_gedownload += gedownload

    if SESSION is not None:
        await SESSION.close()

    print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    asyncio.run(main())
