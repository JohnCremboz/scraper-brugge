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
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document,
    logger,
    print_summary,
    rate_limited_get,
    sanitize_filename,
    DownloadResult,
)

SESSION = None
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

# Regex om een jaar-sublink te herkennen: pad eindigt op /YYYY of /YYYY-N of /prefix-YYYY
# Prefix mag meerdere woorden bevatten (bijv. conseils-communaux-2022)
_JAAR_LINK_RE = re.compile(r"/((?:[a-z]+-)*20\d{2})(?:-\d+)?/?$", re.IGNORECASE)

# Jaar extraheren uit een URL-pad (bv. /2026, /pv-2026-1, /annee-2026, /conseils-communaux-2022)
_JAAR_IN_URL_RE = re.compile(r"/(?:[a-z-]*?)?(20\d{2})(?:-\d+)?/?$", re.IGNORECASE)

# Plone faceted query URL-suffix
_FACETED_QUERY_SUFFIX = "/@@faceted_query"

# Andenne-stijl zitting query-params: y=2025&d=01-27 (maand-dag), toegepast op urlparse().query
_ANDENNE_ZITTING_RE = re.compile(r"(?:^|&)y=(20\d{2})&d=(\d{2})-(\d{2})")

# Datum uit linktekst: bijv. "19 janvier 2026", "PV 2 mars 2026", "16 février 2026 135.5 KB"
_DATUM_TEKST_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_FR_MAANDEN) + r")\s+(20\d{2})",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Gemeente-configuratie
# ---------------------------------------------------------------------------
# listing_pad:     pad naar de overzichtspagina met procès-verbaux
# naam:            weergavenaam voor output/logging

GEMEENTEN: dict[str, dict] = {
    # --- Oorspronkelijk geconfigureerd ---
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
    # --- Verplaatst van deliberations.be naar eigen iMio-site ---
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
        # Jaarsubmappen: /compte-rendu-des-seances/2025, /2026, /2024-1, ...
        # PDFs eindigen op .pdf/view (Plone)
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/compte-rendu-des-seances",
        "subpaginas": True,
        "plone_folder_listing": True,
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
        "ajax_load": True,
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
        "ajax_load": True,  # Plone rendert inhoud pas na ?ajax_load=1
    },
    "www.manage-commune.be": {
        "naam": "Manage",
        # Procès-verbaux is leeg; comptes-rendus bevat alle notulen (alle jaren op één pagina)
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/ordre-du-jour-proces-verbaux/comptes-rendus",
        "ajax_load": True,
    },
    "www.bouillon.be": {
        "naam": "Bouillon",
        # Alle PV's op één pagina (Structuur B); datum via bestandsnaam (YYYYMMDD/YYYY-MM-DD)
        "listing_pad": "/ma-commune/vie-politique/proces-verbal",
    },
    "www.ciney.be": {
        "naam": "Ciney",
        # Structuur A: jaar-subpagina's (2025, 2024, ...); datum via linktekst (French month)
        "listing_pad": "/vie-communale/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.donceel.be": {
        "naam": "Donceel",
        # Structuur B: alles op één pagina (Bootstrap tabs per jaar); lege linkteksten,
        # datum via bestandsnaam (Frans, YYYYMMDD, YYYY-MM-DD). Sommige bestanden
        # hebben identieke namen over jaren heen — alleen eerste wordt gedownload.
        "listing_pad": "/conseil-communal",
    },
    "www.villers-la-ville.be": {
        "naam": "Villers-la-Ville",
        # Structuur B: alles op één pagina; datum via linktekst (French month)
        "listing_pad": "/administration/vie-politique/conseil/compte-rendu-du-conseil-communal",
    },
    "www.beaumont.be": {
        "naam": "Beaumont",
        # Structuur B; lege linkteksten, datum via bestandsnaam (DDMMYYYY met underscores)
        "listing_pad": "/P-V-des-conseils-communaux",
    },
    "www.cerfontaine.be": {
        "naam": "Cerfontaine",
        # Structuur B; datum via linktekst (French month); bevat ook niet-PV docs (PST, ROI)
        "listing_pad": "/ma-commune/politique/le-conseil-communal/pv-approuves-des-dernieres-seances-du-conseil-communal",
    },
    "www.ecaussinnes.be": {
        "naam": "Ecaussinnes",
        # Structuur A: jaar-subpagina's; Plone /view-suffix; datum via linktekst
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "ajax_load": True,
    },
    "www.estaimpuis.be": {
        "naam": "Estaimpuis",
        # Structuur B; WordPress; datum via bestandsnaam (YYYY-MM-DD met koppeltekens)
        "listing_pad": "/pv-du-conseil-communal/",
    },
    "www.frasnes-lez-anvaing.be": {
        "naam": "Frasnes-lez-Anvaing",
        # Structuur B; Plone; multi-part PVs (partie 1/2/3); datum via bestandsnaam
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/resumes-du-conseil-communal-1",
        "ajax_load": True,
    },
    "www.onhaye.be": {
        "naam": "Onhaye",
        # Structuur B; Plone; 134 PDFs; datum via linktekst (French month)
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux-des-assemblees",
    },
    "www.saint-hubert.be": {
        "naam": "Saint-Hubert",
        # Structuur B; custom CMS (LayerSlider); datum via linktekst (French month)
        "listing_pad": "/pv/",
    },
    "www.sivry-rance.be": {
        "naam": "Sivry-Rance",
        # Structuur A: jaar-subpagina's; Plone; lege linkteksten, datum via bestandsnaam
        "listing_pad": "/ma-commune/vie-politique/copy_of_proces-verbaux-des-conseils-communaux",
        "ajax_load": True,
    },
    "www.lessines.be": {
        "naam": "Lessines",
        # Structuur A: jaar-subpagina's; Plone; .pdf/view-suffix (auto-gestript); datum via bestandsnaam
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux/proces-verbaux-des-conseils-communaux-pdf",
    },
    "www.leroeulx.be": {
        "naam": "Le Roeulx",
        # Structuur A: jaar-subpagina's; Plone; .pdf/view-suffix (auto-gestript); datum YYYY-MM-DD in bestandsnaam
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    },
    "www.attert.be": {
        "naam": "Attert",
        # Structuur B: alles op één pagina (/aa subfolder); datum YYYY_MM_DD of Frans in bestandsnaam
        "listing_pad": "/notre-commune/vie-politique/conseil-communal/seances-du-conseil-communal/proces-verbaux-du-conseil-communal/aa",
    },
    "www.beauraing.be": {
        "naam": "Beauraing",
        # Structuur A: jaar-subpagina's (conseils-YYYY / conseils-communaux-YYYY); .pdf/view-suffix;
        # datum via linktekst DD-MM-YYYY (bijv. "CC 27-01-2025")
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "plone_folder_listing": True,
    },
    "www.aywaille.be": {
        "naam": "Aywaille",
        # Structuur A: jaar-subpagina's /YYYY; .pdf/view-suffix;
        # datum via bestandsnaam (bijv. pv-conseil-communal-du-30-01-2025.pdf)
        "listing_pad": "/fr/ma-commune/vie-politique/conseil-communal/proces-verbaux-des-seances-du-conseil",
        "plone_folder_listing": True,
    },
    "www.villedespa.be": {
        "naam": "Spa",
        # Structuur A: jaar-subpagina's /YYYY (2020-2025) + /copy_of_2025 (2026);
        # .pdf/view-suffix; datum via linktekst Frans (bijv. "18 décembre 2025 - Procès verbal")
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/ordres-du-jour-et-proces-verbaux",
        "plone_folder_listing": True,
    },
    "www.profondeville.be": {
        "naam": "Profondeville",
        # Structuur B: alle PDFs op één pagina (OJ + notes + projet-décisions);
        # filenames: conseil-communal-DD-MM-YYYY-type.pdf
        "listing_pad": "/commune/vie-politique/conseil-communal/ordres-du-jour-et-proces-verbaux",
    },
    "www.villedecomines-warneton.be": {
        "naam": "Comines-Warneton",
        # Structuur D: plain listing met sessiepagina's; PDFs in /telechargement/;
        # datum via linktekst Frans (bijv. "Conseil communal du 20 avril 2026")
        "listing_pad": "/fr/ma-commune/politique/conseil-communal",
        "subpaginas": True,
    },
}


# ---------------------------------------------------------------------------
# Sessie helpers
# ---------------------------------------------------------------------------

def init_session(base_url: str) -> None:
    global SESSION, _config, BASE_URL
    BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.3)
    SESSION = create_session(_config)


def haal_organen_statisch() -> list[dict]:
    """iMio-sites hebben geen orgaanindeling — geeft altijd lege lijst terug."""
    return []


def _get(url: str):
    full = url if url.startswith("http") else f"{BASE_URL}{url}"
    return rate_limited_get(SESSION, full, _config)


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(BASE_URL, href)


# ---------------------------------------------------------------------------
# Datum helpers
# ---------------------------------------------------------------------------

def _datum_uit_tekst(tekst: str) -> date | None:
    """Probeer een datum te parsen uit Franstalige linktekst."""
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
    # Patroon: DD-MM-YYYY numeriek (bijv. "CC 27-01-2025", "PV 15-12-2025")
    m = re.search(r"\b(\d{1,2})[-./](\d{2})[-./](20\d{2})\b", tekst)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _datum_uit_pad(pad: str) -> date | None:
    """Probeer een datum te extraheren uit een URL-pad (bijv. /pv-25-03-02.pdf)."""
    naam = Path(urlparse(pad).path).stem.lower()

    # Patroon: DD-MM-YYYY (bijv. compte-rendu-du-24-02-2026, pv-19-03-2025)
    # Moet vóór de generieke regex staan om DD-MM-YYYY niet te verwarren met YY-MM-DD.
    m = re.search(r"[._-](\d{2})[._-](\d{2})[._-](20\d{2})(?!\d)", naam)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Patroon: YYYY-MM-DD ergens in de naam (bijv. pv-2025-01-27)
    m = re.search(r"(20\d{2})[._-](\d{2})[._-](\d{2})(?!\d)", naam)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Patroon: YY-MM-DD (2-cijferig jaar, bijv. /pv-25-03-02.pdf)
    m = re.search(r"(\d{2})[._-](\d{2})[._-](\d{2})(?!\d)", naam)
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Patroon: DDMMYYYY of YYYYMMDD
    m = re.search(r"(\d{8})", naam)
    if m:
        s = m.group(1)
        for fmt in ((s[:4], s[4:6], s[6:8]), (s[6:], s[4:6], s[:4])):
            try:
                return date(int(fmt[0]), int(fmt[1]), int(fmt[2]))
            except ValueError:
                pass

    # Fallback: Franse maandnaam in de bestandsnaam zelf
    # (bijv. "Seance publique 27 JANVIER 2022", "Proces-verbal-02-mars-2026-18-00")
    # Vervang koppeltekens/underscores door spaties zodat de regex matcht.
    return _datum_uit_tekst(naam.replace("_", " ").replace("-", " "))


# ---------------------------------------------------------------------------
# Kern scrape-logica
# ---------------------------------------------------------------------------

def _pdfs_van_pagina(html: str, pagina_url: str) -> list[dict]:
    """
    Extraheer alle PDF-links van een pagina (zelfde domein, .pdf extensie).
    Plone /.pdf/view-links worden genormaliseerd naar directe download-URL.
    """
    soup = BeautifulSoup(html, "lxml")
    basis_netloc = urlparse(pagina_url).netloc
    gezien: set[str] = set()
    pdfs: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = _absolute(href)
        parsed = urlparse(full)

        # Alleen links op hetzelfde domein
        if parsed.netloc != basis_netloc:
            continue

        pad = parsed.path

        # Plone-variant: link eindigt op .pdf/view
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
    """
    Haal alle jaar-subpagina-links op die >= grensjaar zijn.
    Geeft lege lijst terug als er geen jaar-links gevonden worden
    (dan bevat de indexpagina zelf de PDF's — Herstal-stijl).
    """
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
        # Jaar-subpagina moet sub-path van listing URL zijn
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


def _haal_faceted_zitting_urls(listing_pad: str, grensdatum: date) -> list[str]:
    """
    Structuur C: Plone faceted-listing zonder jaar-subpagina's.
    Pagineert via @@faceted_query?b_start=X (20 items/pagina).
    Geeft lijst van zitting-subpagina-URLs terug waarvan de datum >= grensdatum.
    Stopt zodra de eerste te-oude zitting gevonden wordt (listing is nieuwste-eerst).
    """
    index_url = _absolute(listing_pad)
    basis_netloc = urlparse(index_url).netloc
    zitting_urls: list[str] = []
    b_start = 0
    stop = False

    while not stop:
        query_url = f"{index_url}{_FACETED_QUERY_SUFFIX}?b_start={b_start}"
        resp = _get(query_url)
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


def _haal_subpagina_zitting_urls(listing_pad: str, grensdatum: date) -> list[str]:
    """
    Structuur D: plain Plone listing zonder faceted query.
    Alle sessiepagina's staan op één listingpagina als directe kind-links.
    Geeft lijst van zitting-URLs waarvan datum >= grensdatum.
    """
    index_url = _absolute(listing_pad)
    listing_path = urlparse(index_url).path.rstrip("/")
    basis_netloc = urlparse(index_url).netloc

    resp = _get(index_url)
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
        # Geen verdere subpaden (directe kinderen)
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


def _haal_query_zitting_urls(listing_pad: str, zitting_pad: str, grensdatum: date) -> list[tuple[str, date]]:
    """
    Structuur E: zittingen als query-parameter URLs op een listing pagina (Andenne).
    Verwacht links met patroon: {zitting_pad}?y=YYYY&d=MM-DD
    Geeft lijst van (url, datum)-tuples terug met datum >= grensdatum.
    """
    index_url = _absolute(listing_pad)
    resp = _get(index_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    resultaat: list[tuple[str, date]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        full = _absolute(a["href"])
        parsed = urlparse(full)
        if parsed.path.rstrip("/") != zitting_pad:
            continue
        m = _ANDENNE_ZITTING_RE.search(parsed.query)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if d < grensdatum:
            continue
        if full in seen:
            continue
        seen.add(full)
        resultaat.append((full, d))

    return resultaat


def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
) -> tuple[int, int]:
    """
    Scrape één iMio-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    # Structuur C/D: subpagina's per zitting (PDFs op zitting-subpagina's)
    if config.get("faceted") or config.get("subpaginas"):
        if config.get("faceted"):
            zitting_urls = _haal_faceted_zitting_urls(config["listing_pad"], grensdatum)
        else:
            zitting_urls = _haal_subpagina_zitting_urls(config["listing_pad"], grensdatum)
        alle_pdfs: list[dict] = []
        gezien_urls: set[str] = set()
        for zitting_url in zitting_urls:
            r = _get(zitting_url)
            if not r or r.status_code != 200:
                continue
            for pdf in _pdfs_van_pagina(r.text, zitting_url):
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

    # Structuur E: query-parameter zitting-URLs (Andenne)
    elif config.get("query_zittingen"):
        zitting_tuples = _haal_query_zitting_urls(
            config["listing_pad"], config["zitting_pad"], grensdatum
        )
        alle_pdfs = []
        gezien_urls = set()
        for zitting_url, datum in zitting_tuples:
            r = _get(zitting_url)
            if not r or r.status_code != 200:
                continue
            for pdf in _pdfs_van_pagina(r.text, zitting_url):
                if pdf["url"] not in gezien_urls:
                    gezien_urls.add(pdf["url"])
                    doc_naam = pdf["naam"] or Path(urlparse(pdf["url"]).path).stem
                    pdf["naam"] = f"{datum.isoformat()}_{doc_naam}"
                    alle_pdfs.append(pdf)
        if not alle_pdfs:
            logger.info("   Geen PDF's gevonden voor %s", naam)
            return 0, 0
    else:
        # Haal indexpagina op
        index_url = _absolute(config["listing_pad"])
        ajax_load = config.get("ajax_load", False)
        fetch_url = f"{index_url}?ajax_load=1" if ajax_load else index_url
        resp = _get(fetch_url)
        if not resp or resp.status_code != 200:
            logger.warning("Listing niet bereikbaar: %s (HTTP %s)",
                           index_url, getattr(resp, "status_code", "?"))
            return 0, 0

        # Bepaal te crawlen pagina's
        jaar_urls = _haal_jaarpaginas(resp.text, index_url, grensdatum.year)

        if jaar_urls:
            # Structuur A: haal elke relevante jaarpagina op
            paginas: list[tuple[str, str]] = []
            for jaar_url in jaar_urls:
                fetch_jaar_url = f"{jaar_url}?ajax_load=1" if ajax_load else jaar_url
                r = _get(fetch_jaar_url)
                if r and r.status_code == 200:
                    paginas.append((r.text, jaar_url))
        else:
            # Structuur B: PDFs staan direct op de indexpagina
            paginas = [(resp.text, index_url)]

        # Verzamel PDF-links
        alle_pdfs = []
        gezien_urls = set()

        for pagina_html, pagina_url in paginas:
            for pdf in _pdfs_van_pagina(pagina_html, pagina_url):
                if pdf["url"] in gezien_urls:
                    continue
                gezien_urls.add(pdf["url"])

                # Datum bepalen (uit tekst of bestandsnaam)
                datum = _datum_uit_tekst(pdf["naam"])
                if datum is None:
                    datum = _datum_uit_pad(pdf["url"])

                # Datum-filter: als datum bekend en te oud → overslaan
                if datum is not None and datum < grensdatum:
                    continue

                alle_pdfs.append(pdf)

    if not alle_pdfs:
        logger.info("   Geen PDF's gevonden voor %s", naam)
        return 0, 0

    logger.info("   %d PDF(s) gevonden", len(alle_pdfs))

    # Download
    resultaten: list[DownloadResult] = []
    for pdf in alle_pdfs:
        hint = sanitize_filename(pdf["naam"]) if pdf["naam"] else ""
        result = download_document(
            SESSION, _config,
            pdf["url"],
            gem_dir,
            filename_hint=hint,
            require_pdf=True,
        )
        resultaten.append(result)

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


def main() -> None:
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

    te_verwerken: list[tuple[str, dict]] = []  # (netloc, config)

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [(netloc, conf)]
        init_session(args.base_url)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "")
            if zoek in naam_sleutel or zoek in netloc.replace("-", ""):
                te_verwerken = [(netloc, conf)]
                init_session(f"https://{netloc}")
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
            init_session(f"https://{netloc}")
        gevonden, gedownload = scrape_gemeente(conf, output_root, maanden=args.maanden)
        totaal_geprobeerd += gevonden
        totaal_gedownload += gedownload

    print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    main()
