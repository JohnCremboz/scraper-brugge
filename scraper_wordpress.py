"""
Scraper voor WordPress/TYPO3/Plone-gemeenten met directe PDF-links.

Ondersteunde gemeenten:
  Bütgenbach, Kelmis, Lontzen, Raeren, Burg-Reuland, Eupen, Sankt Vith,
  Woluwe-Saint-Pierre, Amel, Büllingen, Amay, Anhée,
  Bernissart, Floreffe, La Louvière, Waterloo, Fernelmont, Chièvres,
  Verlaine, Fosses-la-Ville, Brugelette, Pecq, Herbeumont, Rumes,
  Antoing, Ans, Crisnée, Gesves, Mont-de-l'Enclus, Orp-Jauche, Trooz, Vaux-sur-Sûre,
  Hastière

URL-patroon in simba-source.csv: */wp-content/uploads* of www.st.vith.be of */app/uploads* of */fileadmin/gemeinde_amel* of */pv-et-resumes-du-conseil* of hostname in _WAALSE_WP_HOSTS

Gebruik:
    uv run python scraper_wordpress.py --gemeente butgenbach --maanden 12
    uv run python scraper_wordpress.py --alle --maanden 6
    uv run python scraper_wordpress.py --lijst
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
# Gemeente-configuratie
# ---------------------------------------------------------------------------
# listing_pad:       pad naar de pagina met PDF-links
# listing_paden:     lijst van paden (meerdere listingpagina's)
# jaar_navigatie:    True → gebruik jaarpagina's (Sankt Vith/Plone)
# jaar_pad_re:       regex om jaarpagina-links te herkennen
# pdf_re:            patroon om PDF-links te herkennen (default: WP uploads)
# extra_pdf_domeinen: extra domeinen waarvan PDF-links geaccepteerd worden

_WP_PDF_RE = re.compile(r"/wp-content/uploads/.*\.pdf", re.IGNORECASE)
_ST_VITH_PDF_RE = re.compile(r"/de/buergerservice-politik/.*\.pdf", re.IGNORECASE)
_ST_VITH_JAAR_RE = re.compile(r"protokolle/(\d{4})$")
_WOLUWE_PDF_RE = re.compile(r"/app/uploads/.*\.pdf", re.IGNORECASE)
_AMEL_PDF_RE = re.compile(r"/fileadmin/gemeinde_amel_uploads/Protokolle/.*\.pdf", re.IGNORECASE)
_AMAY_PDF_RE = re.compile(r"/pv-et-resumes-du-conseil/proces-verbaux/\d{4}/.*\.pdf", re.IGNORECASE)
# Plone: links eindigen op .pdf/view → strip /view voor directe download
_PLONE_PDF_VIEW_RE = re.compile(r"\.pdf/view$", re.IGNORECASE)

# Waalse Plone-gemeenten
_WATERLOO_PDF_RE = re.compile(r"/ma-commune/vie-politique/conseil-communal/proces-verbaux/.*\.pdf", re.IGNORECASE)
_FERNELMONT_PDF_RE = re.compile(r"/ma-commune/vie-politique/le-conseil-communal/proces-verbaux-des-conseils-communaux-1/.*\.pdf", re.IGNORECASE)
_CHIEVRES_PDF_RE = re.compile(r"/ma-commune/vie-politique/pv-des-conseils-communaux/.*\.pdf", re.IGNORECASE)
_VERLAINE_PDF_RE = re.compile(r"/ma-commune/vie-politique/le-conseil-communal/proces-verbaux/.*\.pdf", re.IGNORECASE)
_FOSSES_PDF_RE = re.compile(r"/ma-commune/vie-politique/conseil-communal/publications/pv-des-conseils-communaux/.*\.pdf", re.IGNORECASE)
_BRUGELETTE_PDF_RE = re.compile(r"/ma-commune/vie-politique/conseil-communal/seance-publique/.*\.pdf", re.IGNORECASE)
_PECQ_PDF_RE = re.compile(r"/vie-administrative/vie-politique/conseil-communal/proces-verbaux/proces-verbaux-du-conseil/.*\.pdf", re.IGNORECASE)
_HERBEUMONT_PDF_RE = re.compile(r"/ma-commune/vie-politique/conseil-communal/seances-du-conseil-communal/seances-\d{4}/.*\.pdf", re.IGNORECASE)
_LALOUVIERE_PDF_RE = re.compile(r"/ma-ville/vie-politique/conseil-communal/ordre-du-jour-et-pv-des-conseils/\d{4}/.*\.pdf", re.IGNORECASE)
_BERNISSART_PDF_RE = re.compile(r"/wp-content/uploads/.*pv[_-].*\.pdf", re.IGNORECASE)
_RUMES_PDF_RE = re.compile(r"/wp-content/uploads/.*(?:cc|pv).*\.pdf", re.IGNORECASE)
_ANTOING_PDF_RE = re.compile(
    r"/poces-verbaux-des-conseils-communaux/[^/]+/proces-verbaux-pdf/.*\.pdf",
    re.IGNORECASE,
)
_ANS_PDF_RE = re.compile(
    r"/ma-ville/vie-politique/conseil-communal/proces-verbaux/\d{4}/.*\.pdf",
    re.IGNORECASE,
)
# Orp-Jauche: WP-slugs die direct als PDF worden geserveerd (geen /wp-content/uploads/)
# Bijv. /pv-28-janvier/, /pv-conseil-communal-16-decembre/, /cs2548-0-0-pv-pour-site-internet/
_ORP_JAUCHE_PDF_RE = re.compile(
    r"/(pv-[a-z0-9-]+|cs\d+[^/]*)/?$",
    re.IGNORECASE,
)
_TROOZ_PDF_RE = re.compile(
    r"/ma-commune/vie-politique/fichier/proces-verbaux/\d{4}/[^/]+/.*\.pdf",
    re.IGNORECASE,
)

# Franse maandnamen → maandnummer
_FR_MAANDEN: dict[str, int] = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

GEMEENTEN: dict[str, dict] = {
    "butgenbach.be": {
        "naam": "Bütgenbach",
        "listing_pad": "/buergerservice/verwaltung/sekretariat/",
        # PDF: /wp-content/uploads/YYYY/MM/GR20251216-Internet.pdf
        # Datum in bestandsnaam: YYYYMMDD na prefix "GR"
    },
    "www.kelmis.be": {
        "naam": "Kelmis",
        "listing_pad": "/politik/gemeinderat",
        # PDF: /wp-content/uploads/YYYY/MM/Protokoll-Ratssitzung-DD.M.YYYY.pdf
        # Datum in bestandsnaam: DD.M.YYYY of DD.MM.YYYY
    },
    "lontzen.be": {
        "naam": "Lontzen",
        "listing_pad": "/gemeinderat",
        # PDF: /wp-content/uploads/YYYY/MM/bestandsnaam.pdf
        # Datum uit URL-pad /YYYY/MM/
    },
    "www.raeren.be": {
        "naam": "Raeren",
        "listing_pad": "/gemeinderat",
        "extra_pdf_domeinen": ["static.raeren.be"],
        # PDF: https://static.raeren.be/wp-content/uploads/YYYYMMDD*.pdf
    },
    "www.burg-reuland.be": {
        "naam": "Burg-Reuland",
        "listing_paden": [
            "/unsere-gemeinde/politik/sitzungen/tagesordnungen-des-gemeinderats",
            "/unsere-gemeinde/politik/sitzungen/sitzungsprotokolle-des-gemeinderats",
        ],
        # PDF: /wp-content/uploads/to-gemeinderat-vom-DD-MM-YYYY*.pdf
    },
    "www.eupen.be": {
        "naam": "Eupen",
        "listing_pad": "/politik-verwaltung/politik/stadtrat/",
        # PDF: /wp-content/uploads/YYYY/MM/*.pdf
    },
    "www.st.vith.be": {
        "naam": "Sankt Vith",
        "listing_pad": "/de/buergerservice-politik/politik/stadtrat/protokolle/",
        "jaar_navigatie": True,
        "pdf_re": _ST_VITH_PDF_RE,
        # PDF: /de/.../protokolle/YYYY/YYYY/YYYYMMDD_sitzung-des-stadtrates_protokoll.pdf
    },
    "www.woluwe1150.be": {
        "naam": "Woluwe-Saint-Pierre",
        "listing_pad": "/vie-politique/conseil-communal/ordres-du-jour-et-proces-verbaux/",
        "pdf_re": _WOLUWE_PDF_RE,
        "datum_in_tekst": True,  # datum staat in linktekst als "du DD/MM/YY"
    },
    "www.amel.be": {
        "naam": "Amel",
        "listing_pad": "/archiv/protokolle",
        "pdf_re": _AMEL_PDF_RE,
        "datum_in_tekst": True,  # datum staat in linktekst als "Protokoll DD.MM.YYYY"
    },
    "buellingen.be": {
        "naam": "Büllingen",
        "listing_pad": "/politik/ratsprotokolle/",
        "datum_in_tekst": True,   # "Gemeinderatsprotokoll 2026-01-29"
        "gebruik_playwright": True,  # Sucuri WAF blokkeert requests; Playwright omzeilt dit
    },
    "www.amay.be": {
        "naam": "Amay",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/pv-et-resumes-du-conseil/proces-verbaux",
        "pdf_re": _AMAY_PDF_RE,
        # Datum in bestandsnaam: conseil-DD-MM-YY.pdf → datum_uit_pad() herkenning
    },
    "www.anhee.be": {
        "naam": "Anhée",
        "listing_paden": [
            "/ma-commune/vie-politique/conseil-communal/comptes-rendus-2026/@@folder_listing",
            "/ma-commune/vie-politique/conseil-communal/comptes-rendus-2026/comptes-rendus-2024/@@folder_listing",
        ],
        "plone_folder_listing": True,  # links eindigen op .pdf/view → strip /view
        "datum_in_tekst": True,  # "C.R. 06.02.2025" → DD.MM.YYYY in linktekst
    },
    "www.bernissart.be": {
        "naam": "Bernissart",
        "listing_pad": "/le-conseil-communal/",
        "pdf_re": _BERNISSART_PDF_RE,
        # Bestandsnaam: PV-internet-DD-MM-YYYY.pdf of pv-cc-DD-MM-YYYY.pdf of pv-cc-cpas-DD-MM-YY.pdf
    },
    "www.floreffe.be": {
        "naam": "Floreffe",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/",
        # Datum uit URL-pad /wp-content/uploads/YYYY/MM/
    },
    "www.lalouviere.be": {
        "naam": "La Louvière",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/ordre-du-jour-et-pv-des-conseils",
        "pdf_re": _LALOUVIERE_PDF_RE,
        "datum_in_tekst": True,  # "Ordre du jour Conseil 27.01.2026.pdf247.2 KB" → DD.MM.YYYY
    },
    "www.waterloo.be": {
        "naam": "Waterloo",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
        "pdf_re": _WATERLOO_PDF_RE,
        "datum_in_tekst": True,  # "PV de la séance du 9 février 2026" → Frans maandnaam
    },
    "www.fernelmont.be": {
        "naam": "Fernelmont",
        "listing_pad": "/ma-commune/vie-politique/le-conseil-communal/proces-verbaux-des-conseils-communaux-1",
        "pdf_re": _FERNELMONT_PDF_RE,
        "datum_in_tekst": True,  # "Procès-verbal du 22 janvier 2026" → Frans maandnaam
    },
    "www.chievres.be": {
        "naam": "Chièvres",
        "listing_pad": "/ma-commune/vie-politique/pv-des-conseils-communaux",
        "pdf_re": _CHIEVRES_PDF_RE,
        "datum_in_tekst": True,  # "Conseil communal du 28 janvier 2026" → Frans maandnaam
    },
    "www.verlaine.be": {
        "naam": "Verlaine",
        "listing_pad": "/ma-commune/vie-politique/le-conseil-communal/proces-verbaux",
        "pdf_re": _VERLAINE_PDF_RE,
        "datum_in_tekst": True,   # "Préparatif PV Conseil communal du 11 août 2025" → Frans maandnaam
        "plone_folder_listing": True,  # links eindigen op .pdf/view → strip /view
    },
    "www.fosses-la-ville.be": {
        "naam": "Fosses-la-Ville",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/publications/pv-des-conseils-communaux",
        "pdf_re": _FOSSES_PDF_RE,
        "datum_in_tekst": True,  # "Conseil du 13/01/25544.6 KB" → DD/MM/YY
    },
    "www.brugelette.be": {
        "naam": "Brugelette",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/seance-publique",
        "pdf_re": _BRUGELETTE_PDF_RE,
        "datum_in_tekst": True,  # "Ordre du jour de la séance du 19.03.2026" → DD.MM.YYYY
    },
    "www.pecq.be": {
        "naam": "Pecq",
        "listing_pad": "/vie-administrative/vie-politique/conseil-communal/proces-verbaux/proces-verbaux-du-conseil",
        "pdf_re": _PECQ_PDF_RE,
        "datum_in_tekst": True,   # "PV de la séance du 27 janvier 2025" → Frans maandnaam
        "plone_folder_listing": True,  # links eindigen op .pdf/view → strip /view
    },
    "www.herbeumont.be": {
        "naam": "Herbeumont",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/seances-du-conseil-communal",
        "pdf_re": _HERBEUMONT_PDF_RE,
        # Datum uit URL-pad: /seances-YYYY/DD-maandnaam.pdf
        # of via DD-MM-YYYY in bestandsnaam (pv-seance-cc-20-01-2025.pdf)
    },
    "www.rumes.be": {
        "naam": "Rumes",
        "listing_pad": "/accueil/vie-politique/le-conseil-communal/proces-verbal/",
        "pdf_re": _RUMES_PDF_RE,
        # Bestandsnaam: CC2023-09-28.pdf → YYYY-MM-DD
    },
    "www.antoing.net": {
        "naam": "Antoing",
        "listing_pad": "/ma-commune/vie-politique/poces-verbaux-des-conseils-communaux",
        "subfolder_crawl": True,
        # Root-pagina bevat jaar-subfolderlinks met onregelmatige slugs
        # (copies van "proces-verbaux-2020" voor 2021-2025)
        "subfolder_re": r"/poces-verbaux-des-conseils-communaux/(?!@@)[^/]+$",
        "pdf_re": _ANTOING_PDF_RE,
        # Bestandsnaam: pvcc{DDMMYYYY}.pdf of pvcc-commun-{DDMMYYYY}.pdf
    },
    "www.ans-ville.be": {
        "naam": "Ans",
        "listing_pad": "/ma-ville/vie-politique/conseil-communal/proces-verbaux",
        "subfolder_crawl": True,
        "subfolder_re": r"/conseil-communal/proces-verbaux/20\d{2}$",
        "subfolder_jaar_in_url": True,
        "pdf_re": _ANS_PDF_RE,
        # Bestandsnaam: proces-verbal-DD-maandnaam-YYYY-*.pdf
    },
    "www.aubange.be": {
        "naam": "Aubange",
        "listing_pad": "/vie-politique/conseil-communal-2/pv-des-conseils-communaux/",
        "extra_pdf_domeinen": ["ik.imagekit.io"],
        # PDFs gehost op ImageKit CDN (ik.imagekit.io/aubange/wp-content/uploads/...)
        # Datumformaten: YYYY.MM.DD, YY.MM.DD of YYYY-MM-DD in bestandsnaam
    },
    "www.burdinne.be": {
        "naam": "Burdinne",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/pv-du-conseil",
        "subfolder_crawl": True,
        # Jaarsubfolders: pv-2026, pv-2025, pv-2024-1 (slug niet altijd = jaar!)
        "subfolder_re": r"/pv-du-conseil/pv-20\d{2}(-\d+)?$",
        "subfolder_jaar_in_url": True,
        "subfolder_jaar_re": r"pv-(20\d{2})",  # jaar zit VOOR de eventuele -N suffix
        "plone_folder_listing": True,   # links eindigen op .pdf/view → strip /view
        "datum_in_tekst": True,         # "PV CC 27.01.26" → DD.MM.YY in linktekst
    },
    "www.crisnee.be": {
        "naam": "Crisnée",
        "listing_pad": "/politique/conseil-communal/pv-du-conseil-communal",
        "pdf_re": r"/images/conseil/pv/.*\.pdf",
        "datum_in_tekst": True,         # "PV séance du 16 février 2026" in linktekst
    },
    "www.gesves.be": {
        "naam": "Gesves",
        "schema": "http",               # site ondersteunt geen HTTPS
        "listing_pad": "/proces-verbaux-des-seances-du-conseil/",
        "datum_in_tekst": True,         # "28 janvier 2026" in linktekst
    },
    "montdelenclus.be": {
        "naam": "Mont-de-l'Enclus",
        "listing_pad": "/commune/politique/conseil-communal/proces-verbal/",
        "pdf_re": r"/webbbcontent/uploads/.*\.pdf",
        "ssl_verify": False,            # www.montdelenclus.be heeft ongeldig SSL-cert
        "datum_in_tekst": True,         # "Proces verbal 29 janvier 2026" in linktekst
    },
    "www.orp-jauche.be": {
        "naam": "Orp-Jauche",
        "listing_pad": "/proces-verbaux-conseil-communal/",
        "gebruik_playwright": True,     # Elementor-accordion, JS-rendered content
        "pdf_re": _ORP_JAUCHE_PDF_RE,
        "datum_in_tekst": True,         # "28 JANVIER 2025" in knoptekst
    },
    "www.trooz.be": {
        "naam": "Trooz",
        "listing_pad": "/ma-commune/vie-politique/fichier/proces-verbaux",
        "subfolder_crawl": True,
        "subfolder_re": r"/ma-commune/vie-politique/fichier/proces-verbaux/(20\d{2})/?$",
        "subfolder_jaar_in_url": True,
        "subfolder_jaar_re": r"(20\d{2})/?$",
        "pdf_re": _TROOZ_PDF_RE,
    },
    "www.vaux-sur-sure.be": {
        "naam": "Vaux-sur-Sûre",
        "listing_pad": "/ma-commune/vie-politique/conseil-communal/calendrier-et-ordres-du-jour",
        "pdf_re": r"/ma-commune/vie-politique/conseil-communal/calendrier-et-ordres-du-jour/.*\.pdf",
    },
    "www.hastiere.be": {
        "naam": "Hastière",
        "letsgocity": True,
        "portal": "hastiere",
        "pv_menu_uid": "f2e9b70a-af93-4d97-abf5-29cb5b307617",
    },
    "www.courcelles.eu": {
        "naam": "Courcelles",
        "letsgocity": True,
        "portal": "courcelles-eu",
        "pv_slug": "proces-verbaux",
    },
    "www.pontacelles.be": {
        "naam": "Pont-à-Celles",
        "listing_pad": "/services/le-conseil-communal/proces-verbaux/",
        "datum_in_tekst": True,  # linktekst is de vergaderdatum als "DD/MM/YYYY"
    },
    "www.province.namur.be": {
        "naam": "Provincie Namen - Namur",
        # Huidige-jaar-PVs op /conseil-provincial/05-pv-des-seances/
        # Archief 2025 op /documents-du-conseil/2025-2/ (jaarlijks bijwerken)
        "listing_paden": [
            "/conseil-provincial/05-pv-des-seances/",
            "/documents-du-conseil/2025-2/",
        ],
        "datum_in_tekst": True,  # linktekst = "23 janvier 2026" (Frans maandnaam)
    },
}


# ---------------------------------------------------------------------------
# Sessie-initialisatie
# ---------------------------------------------------------------------------

def init_session(base_url: str, ssl_verify: bool = True) -> None:
    global SESSION, _config, BASE_URL
    parsed = urlparse(base_url)
    BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
    _config = ScraperConfig(
        base_url=BASE_URL,
        rate_limit_delay=0.5,
        timeout=30,
    )
    SESSION = create_session(_config)
    if not ssl_verify:
        SESSION.verify = False
    # WordPress-sites verwachten een browser User-Agent
    SESSION.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })


def _get(url: str):
    return rate_limited_get(SESSION, url, _config)


def _get_via_playwright(url: str) -> str | None:
    """Haal een pagina op via Playwright (omzeilt JS-challenges zoals Sucuri WAF)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=20000)
            html = page.content()
            browser.close()
            return html
    except PlaywrightTimeout:
        logger.warning("Playwright timeout voor %s", url)
        return None
    except Exception as exc:
        logger.warning("Playwright fout voor %s: %s", url, exc)
        return None


def _absolute(pad: str) -> str:
    if pad.startswith("http"):
        return pad
    return urljoin(BASE_URL + "/", pad.lstrip("/"))


# ---------------------------------------------------------------------------
# Datum extractie
# ---------------------------------------------------------------------------

def datum_uit_pad(pad: str) -> date | None:
    """Probeer datum te destilleren uit een WordPress/Plone-bestandspad of -naam."""
    # Bestandsnaam: YYYYMMDD (bijv. GR20251216-Internet.pdf, 20250127_sitzung...pdf)
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Bestandsnaam: YYYY.MM.DD (bijv. 2025.02.17-PV.pdf, Aubange)
    m = re.search(r"(?<!\d)(20\d{2})\.(\d{2})\.(\d{2})(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Bestandsnaam: DD.M.YYYY of DD.MM.YYYY (bijv. Protokoll-Ratssitzung-18.12.2023.pdf)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Bestandsnaam: YYYY-MM-DD (bijv. pv-2025-01-27.pdf, CC2023-09-28.pdf)
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # URL-pad: /wp-content/uploads/YYYY/MM/ (upload-datum, minder nauwkeurig)
    m = re.search(r"/wp-content/uploads/(20\d{2})/(\d{2})/", pad)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            pass

    # Bestandsnaam: DD-MM-YY aan het einde (bijv. pv-...-conseil-21-01-26.pdf, Amay/Plone)
    m = re.search(r"-(\d{2})-(\d{2})-(\d{2})(?:-en-pdf)?\.pdf$", pad, re.IGNORECASE)
    if m:
        try:
            jaar = 2000 + int(m.group(3))
            return date(jaar, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Bestandsnaam: DD-MM-YYYY met 4-cijferig jaar (bijv. pv-seance-cc-20-01-2025.pdf)
    m = re.search(r"-(\d{2})-(\d{2})-(20\d{2})(?!\d)", pad)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # URL-pad: /seances-YYYY/DD[er]-maandnaam.pdf (Herbeumont)
    # bijv. /seances-2025/08-decembre.pdf of /seances-2025/1er-septembre.pdf
    m_jaar = re.search(r"/seances-(20\d{2})/", pad)
    if m_jaar:
        jaar = int(m_jaar.group(1))
        m_dag = re.search(r"/(\d{1,2})[a-z]*[-_]([a-zéûèêô]{3,9})\.pdf$", pad, re.IGNORECASE)
        if m_dag:
            maand_nr = _FR_MAANDEN.get(m_dag.group(2).lower())
            if maand_nr:
                try:
                    return date(jaar, maand_nr, int(m_dag.group(1)))
                except ValueError:
                    pass
        try:
            return date(jaar, 1, 1)
        except ValueError:
            pass

    # Bestandsnaam: DD-maandnaam-YYYY met Franse maandnaam
    # bijv. proces-verbal-27-janvier-2025-web.pdf (Ans)
    m = re.search(r"[_-](\d{1,2})-([a-zéûèêô]{3,9})-(20\d{2})(?=[^a-z]|$)", pad, re.IGNORECASE)
    if m:
        maand_nr = _FR_MAANDEN.get(m.group(2).lower())
        if maand_nr:
            try:
                return date(int(m.group(3)), maand_nr, int(m.group(1)))
            except ValueError:
                pass

    # URL-pad: /DD-maandnaam-YYYY/ als mapnaam (bijv. Trooz)
    # bijv. /proces-verbaux/2025/20-janvier-2025/pv-...pdf
    m = re.search(r"/(\d{1,2})-([a-zéûèêô]+)-(20\d{2})/", pad, re.IGNORECASE)
    if m:
        maand_nr = _FR_MAANDEN.get(m.group(2).lower())
        if maand_nr:
            try:
                return date(int(m.group(3)), maand_nr, int(m.group(1)))
            except ValueError:
                pass

    # Bestandsnaam: DDMMYYYY direct vóór extensie (bijv. pvcc18122025.pdf, Antoing)
    m = re.search(r"[^0-9](\d{2})(0[1-9]|1[0-2])(20\d{2})\.[a-z]{2,4}$", pad, re.IGNORECASE)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Bestandsnaam: YY.MM.DD aan het begin van bestandsnaam (bijv. 26.02.09-PV.pdf, Aubange)
    # Negatieve lookbehind zodat YYYY.MM.DD (4-cijf.) niet hier terechtkomt.
    m = re.search(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{2})(?!\d)", pad)
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


def datum_uit_linktekst(tekst: str) -> date | None:
    """
    Parse datum uit linktekst:
      - "du 03/03/26" of "du 27/01/2026"       (slash, Woluwe)
      - "Protokoll 13.01.2026"                  (punt, Amel en andere Duitstaligen)
      - "PV de la séance du 9 février 2026"     (Frans maandnaam, Waalse gemeenten)
    Ondersteunt zowel 2-cijferig jaar (YY → 20YY) als 4-cijferig jaar.
    """
    # Slash-notatie: DD/MM/YY of DD/MM/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", tekst)
    if m:
        dag, maand, jaar_str = int(m.group(1)), int(m.group(2)), m.group(3)
        jaar = int(jaar_str) if len(jaar_str) == 4 else 2000 + int(jaar_str)
        try:
            return date(jaar, maand, dag)
        except ValueError:
            pass
    # Punt-notatie: DD.MM.YYYY
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})(?!\d)", tekst)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    # ISO-notatie: YYYY-MM-DD (bijv. "Gemeinderatsprotokoll 2026-01-29")
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})(?!\d)", tekst)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # Frans maandnaam: "9 février 2026" of "28 janvier 2026"
    m = re.search(r"(\d{1,2})\s+([a-zéûèêô]{3,9})\.?\s+(20\d{2})(?!\d)", tekst, re.IGNORECASE)
    if m:
        maand_nr = _FR_MAANDEN.get(m.group(2).lower())
        if maand_nr:
            try:
                return date(int(m.group(3)), maand_nr, int(m.group(1)))
            except ValueError:
                pass
    return None



# ---------------------------------------------------------------------------
# PDF-links verzamelen
# ---------------------------------------------------------------------------

def _pdfs_van_html(
    html: str,
    base_url: str,
    pdf_re: re.Pattern = _WP_PDF_RE,
    extra_domeinen: list[str] | None = None,
) -> list[dict]:
    """Verzamel alle PDF-links uit HTML, geef {'url', 'naam'} terug."""
    base_netloc = urlparse(base_url).netloc
    extra = set(extra_domeinen or [])
    soup = BeautifulSoup(html, "lxml")
    gezien: set[str] = set()
    resultaat: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = href if href.startswith("http") else urljoin(base_url + "/", href.lstrip("/"))
        parsed = urlparse(full_url)
        netloc = parsed.netloc
        pad = parsed.path

        # Domein moet overeenkomen (hoofddomein of extra CDN-domein).
        # Strip "www." voor vergelijking zodat www.X en X als hetzelfde gelden.
        if (netloc != base_netloc and
                netloc.lstrip("www.") != base_netloc.lstrip("www.") and
                netloc not in extra):
            continue

        # PDF-patroon controleren
        if netloc in extra:
            # Extra domeinen: elk .pdf-bestand accepteren
            if not pad.lower().endswith(".pdf"):
                continue
        else:
            if not pdf_re.search(pad):
                continue

        if full_url in gezien:
            continue
        gezien.add(full_url)
        naam = a.get_text(strip=True) or Path(pad).name
        resultaat.append({"url": full_url, "naam": naam})

    return resultaat


# ---------------------------------------------------------------------------
# Hoofd-scrapefunctie
# ---------------------------------------------------------------------------

def haal_organen_statisch() -> list[dict]:
    return [{"naam": "Gemeinderat", "uuid": "gemeinderat"}]


def _zoek_gemeente(netloc: str) -> dict | None:
    for sleutel, conf in GEMEENTEN.items():
        if sleutel == netloc or sleutel.lstrip("www.") == netloc.lstrip("www."):
            return conf
    return None


# ---------------------------------------------------------------------------
# LetsGoCity-platform (bijv. Hastière, Courcelles)
# ---------------------------------------------------------------------------

_LGC_MAPI = "https://mapi.letsgocity.be"
_LGC_FILES = "https://files.letsgocity.be"


def _lgc_pdfs_uit_content(data: list, grensdatum, grensjaar_hint: str | None = None) -> list[dict]:
    """Extraheer PDF-items uit een LetsGoCity content-response."""
    pdfs: list[dict] = []
    for block in data:
        if block.get("type") != "lgc-file":
            continue
        for file_item in block.get("items", []):
            uid = file_item.get("uid", "")
            if not uid:
                continue
            if "pdf" not in file_item.get("contentType", "").lower():
                continue
            filename = file_item.get("filename") or f"{uid}.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            datum = datum_uit_pad(filename)
            if datum is not None and datum < grensdatum:
                continue
            datum_str = datum.isoformat() if datum else (grensjaar_hint or "")
            pdfs.append({"url": f"{_LGC_FILES}/{uid}", "naam": filename, "datum": datum_str})
    return pdfs


def _scrape_letsgocity(config: dict, output_dir: Path, maanden: int = 12) -> tuple[int, int]:
    """Scrape een gemeente op het LetsGoCity-platform (bijv. Hastière, Courcelles).

    Gebruikt de mapi.letsgocity.be REST API — geen HTML scraping nodig.
    PDF-bestanden staan op files.letsgocity.be/{uid}.

    Config-opties:
        portal          LetsGoCity portal-ID (bv. "hastiere", "courcelles-eu")
        pv_menu_uid     UID van het PV-menu → lijst jaarspagina's (Hastière-stijl)
        pv_slug         Directe content-slug met alle PVs op één pagina (Courcelles-stijl)
    """
    import requests as _req  # lokale import om globale SESSION niet te verstoren

    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    portal = config["portal"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    logger.info("▶  %s  [LetsGoCity]  (grensdatum=%s)", naam, grensdatum)

    alle_pdfs: list[dict] = []

    if "pv_menu_uid" in config:
        # Hasitère-stijl: menu-UID → lijst jaarspagina's
        menu_url = (
            f"{_LGC_MAPI}/core-content-service/api/v1/web/portal"
            f"/{portal}/menu/{config['pv_menu_uid']}"
        )
        try:
            resp = _req.get(menu_url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Kon LetsGoCity-menu niet ophalen: %s", exc)
            return 0, 0

        for item in resp.json().get("data", []):
            action_path = item.get("action", {}).get("path", "")
            if "/information/" not in action_path:
                continue
            slug = action_path.split("/information/")[-1].split("?")[0].rstrip("/")

            jaar_m = re.search(r"(20\d{2})$", slug)
            if jaar_m and int(jaar_m.group(1)) < grensdatum.year:
                continue

            content_url = (
                f"{_LGC_MAPI}/core-content-service/api/v1/web/portal"
                f"/{portal}/content/{slug}?env=desktop&maps=false"
            )
            try:
                cr = _req.get(content_url, timeout=30)
                cr.raise_for_status()
            except Exception as exc:
                logger.warning("Kon inhoud niet ophalen (%s): %s", slug, exc)
                continue

            alle_pdfs.extend(
                _lgc_pdfs_uit_content(
                    cr.json().get("data", []),
                    grensdatum,
                    jaar_m.group(1) if jaar_m else None,
                )
            )

    elif "pv_slug" in config:
        # Courcelles-stijl: directe content-slug met alle jaren op één pagina
        content_url = (
            f"{_LGC_MAPI}/core-content-service/api/v1/web/portal"
            f"/{portal}/content/{config['pv_slug']}?env=desktop&maps=false"
        )
        try:
            cr = _req.get(content_url, timeout=30)
            cr.raise_for_status()
        except Exception as exc:
            logger.error("Kon LetsGoCity-inhoud niet ophalen: %s", exc)
            return 0, 0
        alle_pdfs.extend(_lgc_pdfs_uit_content(cr.json().get("data", []), grensdatum))

    else:
        logger.error("LetsGoCity-config vereist 'pv_menu_uid' of 'pv_slug'")
        return 0, 0

    logger.info("   %d PDF(s) gevonden", len(alle_pdfs))

    alle_resultaten: list[DownloadResult] = []
    for pdf in alle_pdfs:
        hint = sanitize_filename(
            f"{pdf['datum']}_{pdf['naam']}" if pdf.get("datum") else pdf["naam"]
        )
        result = download_document(
            SESSION, _config,
            pdf["url"],
            gem_dir,
            filename_hint=hint,
            require_pdf=True,
        )
        alle_resultaten.append(result)

    gedownload = sum(1 for r in alle_resultaten if r.success and not r.skipped)
    print_summary(alle_resultaten, naam=naam)
    return len(alle_resultaten), gedownload


def scrape_gemeente(
    config: dict,
    output_dir: Path,
    maanden: int = 12,
    document_filter: str | None = None,
) -> tuple[int, int]:
    """Scrape één WordPress-gemeente.

    Returns:
        (totaal_geprobeerd, totaal_gedownload)
    """
    # LetsGoCity-platform heeft een volledig eigen API-stroom
    if config.get("letsgocity"):
        return _scrape_letsgocity(config, output_dir, maanden)

    grensdatum = date.today() - timedelta(days=maanden * 31)
    naam = config["naam"]
    gem_dir = output_dir / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    _pdf_re_raw = config.get("pdf_re", _WP_PDF_RE)
    pdf_re = re.compile(_pdf_re_raw, re.IGNORECASE) if isinstance(_pdf_re_raw, str) else _pdf_re_raw
    extra_domeinen = config.get("extra_pdf_domeinen")

    logger.info("▶  %s  (grensdatum=%s)", naam, grensdatum)

    # Bepaal listing-URL's
    if "listing_paden" in config:
        listing_urls = [_absolute(p) for p in config["listing_paden"]]
    else:
        listing_urls = [_absolute(config["listing_pad"])]

    # Verzamel alle PDF-bronpagina's
    paginas: list[tuple[str, str]] = []

    for listing_url in listing_urls:
        if config.get("gebruik_playwright"):
            html = _get_via_playwright(listing_url)
            if html:
                paginas.append((html, listing_url))
            else:
                logger.warning("Playwright kon pagina niet ophalen: %s", listing_url)
            continue

        resp = _get(listing_url)
        if not resp or resp.status_code != 200:
            logger.warning("Listing niet bereikbaar: %s (HTTP %s)",
                           listing_url, getattr(resp, "status_code", "?"))
            continue

        html = resp.text

        if config.get("jaar_navigatie"):
            # Jaarpagina-navigatie (Sankt Vith / Plone)
            soup = BeautifulSoup(html, "lxml")
            gezien_jaren: set[int] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = href if href.startswith("http") else _absolute(href)
                m = _ST_VITH_JAAR_RE.search(urlparse(full).path)
                if m:
                    jaar = int(m.group(1))
                    if jaar >= grensdatum.year and jaar not in gezien_jaren:
                        gezien_jaren.add(jaar)
                        r = _get(full)
                        if r and r.status_code == 200:
                            paginas.append((r.text, full))
        elif config.get("subfolder_crawl"):
            # Twee-niveau crawl: root → jaarsubmappen → PDFs
            # bijv. Antoing (vaste slugs) en Ans (jaar in URL)
            sf_re = re.compile(config["subfolder_re"])
            soup = BeautifulSoup(html, "lxml")
            sf_links: list[str] = []
            gezien_sf: set[str] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = href if href.startswith("http") else _absolute(href)
                path = urlparse(full).path
                if sf_re.search(path) and full not in gezien_sf:
                    gezien_sf.add(full)
                    sf_links.append(full)
            if config.get("subfolder_jaar_in_url"):
                # Jaar staat in URL: filter op grensdatum.year
                # Optionele 'subfolder_jaar_re' om jaar te extraheren (default: jaar aan einde van URL)
                jaar_re_pat = re.compile(config.get("subfolder_jaar_re", r"(20\d{2})/?$"))
                sf_links = [
                    sf for sf in sf_links
                    if (mj := jaar_re_pat.search(sf)) and int(mj.group(1)) >= grensdatum.year
                ]
            else:
                # Jaar niet betrouwbaar in URL: neem laatste N submappen
                n_sf = max(2, maanden // 12 + 1)
                sf_links = sf_links[-n_sf:]
            for sf in sf_links:
                r = _get(sf)
                if r and r.status_code == 200:
                    paginas.append((r.text, sf))
            if not sf_links and config.get("subfolder_fallback_direct"):
                # Geen subfolders gevonden op deze URL (bijv. huidige-jaar-pagina
                # in listing_paden naast een archief-URL): behandel als directe listing.
                paginas.append((html, listing_url))
        else:
            paginas.append((html, listing_url))

    if not paginas:
        return 0, 0

    # Verzamel alle PDFs
    alle_pdfs: list[dict] = []

    if config.get("datum_in_tekst"):
        # Datum staat in de linktekst (bijv. Woluwe "du DD/MM/YY").
        # Itereer alle links op de pagina en koppel elke PDF-link aan de
        # meest recentelijk geziene datum (uit een voorgaande OdJ-link).
        for pagina_html, pagina_url in paginas:
            base_netloc = urlparse(pagina_url).netloc
            soup = BeautifulSoup(pagina_html, "lxml")
            gezien: set[str] = set()
            huidige_datum: date | None = None

            for a in soup.find_all("a", href=True):
                tekst = a.get_text(strip=True)
                href = a["href"]
                full_url = href if href.startswith("http") else _absolute(href)

                # Probeer datum uit linktekst te extraheren
                d = datum_uit_linktekst(tekst)
                if d is not None:
                    huidige_datum = d

                parsed = urlparse(full_url)
                if parsed.netloc != base_netloc and parsed.netloc.lstrip("www.") != base_netloc.lstrip("www."):
                    continue

                # Plone: links eindigen op .pdf/view → strip /view voor directe download
                if config.get("plone_folder_listing") and _PLONE_PDF_VIEW_RE.search(parsed.path):
                    full_url = full_url[: full_url.rfind("/view")]
                    parsed = urlparse(full_url)
                elif not pdf_re.search(parsed.path):
                    continue

                if full_url in gezien:
                    continue
                gezien.add(full_url)

                datum = huidige_datum
                # Fallback: als linktekst geen datum bevatte, probeer bestandsnaam
                if datum is None:
                    datum = datum_uit_pad(parsed.path)
                if datum is not None and datum < grensdatum:
                    continue
                if document_filter:
                    if (document_filter.lower() not in tekst.lower() and
                            document_filter.lower() not in full_url.lower()):
                        continue

                datum_str = datum.isoformat() if datum else "onbekend"
                doc_naam = tekst or Path(parsed.path).name
                alle_pdfs.append({"url": full_url, "naam": doc_naam, "datum": datum_str})
    else:
        for pagina_html, pagina_url in paginas:
            pdfs = _pdfs_van_html(pagina_html, pagina_url, pdf_re, extra_domeinen)
            for pdf in pdfs:
                pad = urlparse(pdf["url"]).path
                datum = datum_uit_pad(pad)
                if datum is not None and datum < grensdatum:
                    continue
                if document_filter:
                    naam_lower = pdf["naam"].lower()
                    url_lower = pdf["url"].lower()
                    if (document_filter.lower() not in naam_lower and
                            document_filter.lower() not in url_lower):
                        continue
                alle_pdfs.append(pdf)

    logger.info("   %d PDF(s) gevonden", len(alle_pdfs))

    alle_resultaten: list[DownloadResult] = []
    for pdf in alle_pdfs:
        datum_str = pdf.get("datum", "")
        bestandsnaam = Path(urlparse(pdf["url"]).path.rstrip("/")).name
        # Slug-URLs (bijv. /pv-28-janvier/) hebben geen .pdf-extensie: voeg toe
        if bestandsnaam and not Path(bestandsnaam).suffix:
            bestandsnaam += ".pdf"
        hint = sanitize_filename(f"{datum_str}_{bestandsnaam}" if datum_str else bestandsnaam)
        result = download_document(
            SESSION, _config,
            pdf["url"],
            gem_dir,
            filename_hint=hint or pdf["naam"],
            require_pdf=True,
        )
        alle_resultaten.append(result)

    gedownload = sum(1 for r in alle_resultaten if r.success and not r.skipped)
    print_summary(alle_resultaten, naam=naam)
    return len(alle_resultaten), gedownload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor WordPress-gemeenten (Duitstalige regio)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="",
                        help="Basis-URL van de gemeente (bijv. https://www.kelmis.be)")
    parser.add_argument("--gemeente", help="Gemeentenaam (bijv. kelmis)")
    parser.add_argument("--alle", action="store_true",
                        help="Alle ondersteunde gemeenten verwerken")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon ondersteunde gemeenten en stop")
    parser.add_argument("--orgaan", help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Terugkijkperiode in maanden (standaard: 12)")
    parser.add_argument("--output", default="pdfs",
                        help="Uitvoermap (standaard: pdfs)")
    parser.add_argument("--document-filter", help="Filter op bestandsnaam (bijv. protokoll)")
    parser.add_argument("--notulen", action="store_true",
                        help="Shorthand voor --document-filter protokoll")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--lijst-organen", action="store_true",
                        help="Toon organen (compatibiliteit)")
    parser.add_argument("--debug", action="store_true",
                        help="Uitgebreide logging")
    args = parser.parse_args()

    if args.debug:
        from base_scraper import set_log_level
        set_log_level("DEBUG")

    if args.notulen and not args.document_filter:
        args.document_filter = "protokoll"

    if args.lijst or args.lijst_organen:
        print("Ondersteunde gemeenten:")
        for netloc, conf in GEMEENTEN.items():
            print(f"  {conf['naam']:25s}  https://{netloc}/")
        return

    te_verwerken: list[dict] = []

    if args.base_url:
        netloc = urlparse(args.base_url).netloc
        conf = _zoek_gemeente(netloc)
        if not conf:
            print(f"[!] Geen configuratie gevonden voor {netloc}")
            sys.exit(1)
        te_verwerken = [conf]
        init_session(args.base_url)
    elif args.gemeente:
        zoek = args.gemeente.lower().replace("-", "").replace(" ", "").replace("ü", "u").replace("û", "u")
        for netloc, conf in GEMEENTEN.items():
            naam_sleutel = conf["naam"].lower().replace("-", "").replace(" ", "").replace("ü", "u").replace("û", "u")
            if zoek in naam_sleutel or zoek in netloc:
                te_verwerken = [conf]
                init_session(f"{conf.get('schema', 'https')}://{netloc}",
                             ssl_verify=conf.get('ssl_verify', True))
                break
        if not te_verwerken:
            print(f"[!] Gemeente '{args.gemeente}' niet gevonden. Gebruik --lijst.")
            sys.exit(1)
    elif args.alle:
        te_verwerken = list(GEMEENTEN.values())
    else:
        parser.print_help()
        sys.exit(1)

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    totaal_geprobeerd = 0
    totaal_gedownload = 0

    for conf in te_verwerken:
        if args.alle and not args.base_url:
            netloc = next(k for k, v in GEMEENTEN.items() if v is conf)
            init_session(f"{conf.get('schema', 'https')}://{netloc}",
                         ssl_verify=conf.get('ssl_verify', True))

        gevonden, gedownload = scrape_gemeente(
            conf,
            output_root,
            maanden=args.maanden,
            document_filter=args.document_filter,
        )
        totaal_geprobeerd += gevonden
        totaal_gedownload += gedownload

    print(f"\nKlaar. Totaal: {totaal_geprobeerd} geprobeerd, {totaal_gedownload} gedownload.")


if __name__ == "__main__":
    main()
