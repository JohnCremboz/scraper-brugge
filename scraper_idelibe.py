"""
Scraper voor iDélibé / conseilcommunal.be — 39 Waalse gemeenten.

iDélibé is een gemeentelijk e-government platform van Civadis/Stesud.
Per zitting publiceert het platform:
  - "Procès-verbal" / "PV …" documenten (PDF) — formele notulen
  - "Note de synthèse" documenten (PDF of DOCX) — besluitenoverzicht

Niet opgehaald: "Ordre du jour", "Préparatif de séance", "Convocation"

REST API (publiek, geen authenticatie):
  GET /ApiCitoyen/public/v1/communes
  GET /ApiCitoyen/public/v1/commune/{id}/seances
  GET /ApiCitoyen/public/v1/commune/{id}/seance/{id}
  GET /ApiCitoyen/public/v1/point/{id}   → bestand (PDF of DOCX)

URL in simba-source.csv: https://www.conseilcommunal.be/commune/{id}

Gebruik:
    uv run python scraper_idelibe.py --gemeente beauvechain --maanden 12
    uv run python scraper_idelibe.py --alle --maanden 6
    uv run python scraper_idelibe.py --lijst
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document,
    logger,
    print_summary,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

_API_BASE = "https://www.conseilcommunal.be/ApiCitoyen/public/v1"

# iDélibé commune ID → (naam, heeft_documenten)
# heeft_documenten=True → gemeente heeft PVs of notes de synthèse
# heeft_documenten=False → enkel agenda/prep, wordt toch gescand maar geeft doorgaans 0 resultaten
GEMEENTEN: dict[int, str] = {
    2: "Waterloo",
    8: "Pecq",
    9: "Habay",
    10: "Villers-le-Bouillet",
    12: "Soumagne",
    13: "Hastière",
    14: "Modave",
    16: "Hannut",
    17: "Gouvy",
    18: "Ramillies",
    22: "Esneux",
    26: "Neufchâteau",
    28: "Messancy",
    29: "Fosses-la-Ville",
    37: "Bassenge",
    40: "Saint-Nicolas",
    41: "Tintigny",
    44: "Gedinne",
    46: "Chaudfontaine",
    53: "Bièvre",
    58: "Jodoigne",
    63: "Fernelmont",
    64: "Neupré",
    65: "Floreffe",
    66: "Aywaille",
    67: "Herve",
    68: "Verlaine",
    69: "Plombières",
    70: "Rumes",
    72: "Visé",
    78: "Beauvechain",
    80: "Perwez",
    83: "Leuze-en-Hainaut",
    92: "Flobecq",
    93: "Musson",
    97: "Silly",
    98: "Tenneville",
    99: "Hotton",
    104: "Ouffet",
}

# Keywords die een PV of besluitenoverzicht aanduiden (inclusie, lowercase)
_PV_KEYWORDS = (
    "pv",              # "PV citoyen", "PV CC...", "PVpublic..."
    "procès-verbal",
    "proces-verbal",
    "procès verbal",
    "proces verbal",
    "note de synthèse",
    "note de synth",
    "note explicative",
)

# Keywords die een agenda/prep aanduiden (exclusie, lowercase)
_SKIP_KEYWORDS = (
    "ordre du jour",
    "preparatif",
    "préparatif",
    "convocation",
    "invitation",
)

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None
_OUTPUT_ROOT = Path("pdfs")


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def _normalise(naam: str) -> str:
    """Verwijder accenten en zet om naar lowercase voor vergelijking."""
    return "".join(
        c for c in unicodedata.normalize("NFD", naam.lower())
        if unicodedata.category(c) != "Mn"
    ).replace(" ", "").replace("-", "")


def gemeente_id_uit_naam(naam: str) -> int | None:
    """Zoek commune-ID op basis van genormaliseerde naam."""
    zoek = _normalise(naam)
    for cid, cnaam in GEMEENTEN.items():
        if _normalise(cnaam) == zoek:
            return cid
    return None


def gemeente_id_uit_url(url: str) -> int | None:
    """Haal commune-ID uit URL: https://www.conseilcommunal.be/commune/{id}"""
    m = re.search(r"/commune/(\d+)", url)
    return int(m.group(1)) if m else None


def init_sessie() -> None:
    global SESSION, _config
    _config = ScraperConfig(base_url=_API_BASE, rate_limit_delay=0.2)
    SESSION = create_session(_config)


def _get(pad: str) -> requests.Response | None:
    """GET helper met retries."""
    url = pad if pad.startswith("http") else f"{_API_BASE}{pad}"
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        logger.warning(f"Request fout voor {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Documenttypering
# ---------------------------------------------------------------------------

def _is_besluit(titre: str, bestandsnaam: str) -> bool:
    """True als dit een PV of note de synthèse is (geen agenda/prep)."""
    t = (titre or bestandsnaam or "").lower()

    # Explicitly skip agenda/prep documents
    for skip in _SKIP_KEYWORDS:
        if skip in t:
            return False

    # Accept PV/note keywords
    for kw in _PV_KEYWORDS:
        if kw in t:
            return True

    # Accept short "pv…" prefix in bestandsnaam (bijv. "PVpublic20260211.pdf")
    fname = (bestandsnaam or "").lower()
    if re.match(r"^pv[\w]", fname):
        return True

    return False


# ---------------------------------------------------------------------------
# API-aanroepen
# ---------------------------------------------------------------------------

def haal_zittingen(commune_id: int) -> list[dict]:
    """Haal alle zittingen op voor een commune."""
    r = _get(f"/commune/{commune_id}/seances")
    if r is None:
        return []
    try:
        data = r.json().get("Data", {})
        return data.get("Sessions", [])
    except Exception as e:
        logger.warning(f"JSON-fout bij zittingen commune {commune_id}: {e}")
        return []


def haal_documenten_van_zitting(commune_id: int, zitting_id: int) -> list[dict]:
    """
    Haal PV/note-documenten op voor een zitting.
    Geeft lijst van {id, naam, bestandsnaam, datum}.
    """
    r = _get(f"/commune/{commune_id}/seance/{zitting_id}")
    if r is None:
        return []
    try:
        data = r.json().get("Data", {})
    except Exception as e:
        logger.warning(f"JSON-fout bij zitting {zitting_id}: {e}")
        return []

    punten = data.get("Points", [])
    docs = []
    for p in punten:
        if not p.get("isFile"):
            continue

        titre = p.get("Titre") or ""
        fname = p.get("FileName") or ""

        if not _is_besluit(titre, fname):
            continue

        # Datum uit SeanceDate veld (ISO string "2025-12-29T20:00:00")
        datum = None
        seance_date = p.get("SeanceDate")
        if seance_date:
            try:
                datum = datetime.fromisoformat(seance_date).date()
            except ValueError:
                pass

        docs.append({
            "point_id": p["Id"],
            "naam": titre or Path(fname).stem,
            "bestandsnaam": fname,
            "datum": datum,
        })
    return docs


# ---------------------------------------------------------------------------
# Scraper hoofdfunctie
# ---------------------------------------------------------------------------

def scrape_gemeente(commune_id: int, naam: str, maanden: int = 12,
                    document_filter: str | None = None) -> tuple[int, int]:
    """Download PV/note-documenten voor één iDélibé-gemeente."""
    grensdatum = date.today() - timedelta(days=maanden * 30)
    logger.info(f"▶  {naam}  (grensdatum={grensdatum})")

    gem_dir = _OUTPUT_ROOT / sanitize_filename(naam)
    gem_dir.mkdir(parents=True, exist_ok=True)

    zittingen = haal_zittingen(commune_id)
    if not zittingen:
        logger.warning(f"  Geen zittingen gevonden voor {naam}")
        return 0, 0

    alle_docs: list[dict] = []
    for z in zittingen:
        docs = haal_documenten_van_zitting(commune_id, z["Id"])
        for d in docs:
            if d["datum"] is not None and d["datum"] < grensdatum:
                continue
            if document_filter:
                if (document_filter.lower() not in d["naam"].lower() and
                        document_filter.lower() not in d["bestandsnaam"].lower()):
                    continue
            alle_docs.append(d)

    logger.info(f"  {len(alle_docs)} document(en) gevonden")

    totaal = nieuw = overgeslagen = mislukt = 0
    resultaten = []

    for d in alle_docs:
        totaal += 1
        download_url = f"{_API_BASE}/point/{d['point_id']}"

        ext = Path(d["bestandsnaam"]).suffix.lower() or ".pdf"
        doc_naam = d["naam"]
        if not doc_naam.lower().endswith(ext):
            doc_naam = f"{doc_naam}{ext}"

        result = download_document(
            SESSION, _config,
            download_url,
            gem_dir,
            filename_hint=doc_naam,
            require_pdf=False,   # ook DOCX accepteren
        )
        resultaten.append(result)

        if result.skipped:
            overgeslagen += 1
        elif result.success:
            nieuw += 1
        else:
            mislukt += 1
            logger.warning(f"  Mislukt: {download_url} — {d['naam']}")

    print_summary(resultaten, naam)
    return totaal, nieuw


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="iDélibé scraper — 39 Waalse gemeenten")
    parser.add_argument("--gemeente", help="Naam van de gemeente (bijv. beauvechain)")
    parser.add_argument("--alle", action="store_true", help="Scrape alle gemeenten")
    parser.add_argument("--lijst", action="store_true", help="Toon lijst van gemeenten")
    parser.add_argument("--maanden", type=int, default=12,
                        help="Aantal maanden terug (standaard: 12)")
    parser.add_argument("--filter", dest="document_filter",
                        help="Filter op documentnaam (bijv. 'PV')")
    parser.add_argument("--output", default="pdfs",
                        help="Output-directory (standaard: pdfs/)")
    args = parser.parse_args()

    global _OUTPUT_ROOT
    _OUTPUT_ROOT = Path(args.output)
    _OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    if args.lijst:
        print("Ondersteunde gemeenten (iDélibé):")
        for cid, naam in sorted(GEMEENTEN.items(), key=lambda x: x[1]):
            print(f"  {naam:<28}  id={cid}")
        return

    init_sessie()

    if args.gemeente:
        cid = gemeente_id_uit_naam(args.gemeente)
        if cid is None:
            logger.error(f"Gemeente '{args.gemeente}' niet gevonden. Gebruik --lijst.")
            sys.exit(1)
        scrape_gemeente(cid, GEMEENTEN[cid], args.maanden, args.document_filter)

    elif args.alle:
        totaal_g = totaal_n = 0
        for cid, naam in sorted(GEMEENTEN.items(), key=lambda x: x[1]):
            g, n = scrape_gemeente(cid, naam, args.maanden, args.document_filter)
            totaal_g += g
            totaal_n += n
        logger.info(f"\nTotaal: {totaal_g} geprobeerd, {totaal_n} gedownload.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
