"""
Scraper voor publicatie.gelinkt-notuleren.vlaanderen.be.

Het Vlaamse publicatieplatform voor gelinkt notuleren is een centraal
platform van het Agentschap Binnenlands Bestuur waarop gemeenten hun
notulen en besluitenlijsten publiceren via een JSON API.

URL-structuur in simba-source.csv:
    https://publicatie.gelinkt-notuleren.vlaanderen.be/{gemeente}/{classificatie}
    bv. https://publicatie.gelinkt-notuleren.vlaanderen.be/Baarle-Hertog/Gemeente

API-endpoints:
    /bestuurseenheden?filter[:exact:naam]=...&filter[classificatie][:exact:label]=...
    /zittingen?filter[bestuursorgaan][is-tijdsspecialisatie-van][bestuurseenheid][:id:]=...
    /zittingen/{uuid}?include=notulen,notulen.file,besluitenlijst
    /files/{id}/download

Gebruik:
    uv run python scraper_gelinktnotuleren.py \\
        --base-url https://publicatie.gelinkt-notuleren.vlaanderen.be/Baarle-Hertog/Gemeente \\
        --maanden 12
    uv run python scraper_gelinktnotuleren.py \\
        --base-url https://publicatie.gelinkt-notuleren.vlaanderen.be/Baarle-Hertog/Gemeente \\
        --alle
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from base_scraper import (
    DownloadResult,
    ScraperConfig,
    create_session,
    logger,
    print_summary,
    safe_output_path,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

PLATFORM_BASE = "https://publicatie.gelinkt-notuleren.vlaanderen.be"
PAGINA_GROOTTE = 20


# ---------------------------------------------------------------------------
# Globale staat (één sessie per run)
# ---------------------------------------------------------------------------

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None
_gemeente_naam: str = ""
_classificatie: str = ""


def init_session(base_url: str) -> None:
    """Initialiseer HTTP-sessie en extraheer gemeente/classificatie uit URL."""
    global SESSION, _config, _gemeente_naam, _classificatie

    parsed = urlparse(base_url)
    onderdelen = [p for p in parsed.path.strip("/").split("/") if p]
    if len(onderdelen) >= 2:
        _gemeente_naam = onderdelen[0]
        _classificatie = onderdelen[1]
    elif len(onderdelen) == 1:
        _gemeente_naam = onderdelen[0]
        _classificatie = "Gemeente"
    else:
        logger.error("Ongeldige base-url: %s. Verwacht formaat: .../Gemeente/Classificatie", base_url)
        sys.exit(1)

    _config = ScraperConfig(base_url=PLATFORM_BASE)
    SESSION = create_session(_config)


def _api_get(pad: str, params: dict | None = None) -> dict | None:
    """JSON GET request naar het platform. Geeft None bij fouten."""
    assert SESSION is not None
    url = f"{PLATFORM_BASE}{pad}"
    try:
        r = SESSION.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        logger.debug("API GET %s → %d", url, r.status_code)
    except Exception as exc:
        logger.warning("API GET fout %s: %s", url, exc)
    return None


# ---------------------------------------------------------------------------
# Stap 1 – bestuurseenheid ophalen
# ---------------------------------------------------------------------------

def haal_bestuurseenheid_id(naam: str, classificatie: str) -> str | None:
    """Zoek de bestuurseenheid-ID op via naam en classificatielabel."""
    data = _api_get("/bestuurseenheden", {
        "filter[:exact:naam]": naam,
        "filter[classificatie][:exact:label]": classificatie,
        "include": "classificatie",
    })
    if data and data.get("data"):
        beid = data["data"][0]["id"]
        logger.debug("Bestuurseenheid '%s / %s' gevonden: %s", naam, classificatie, beid)
        return beid
    logger.error(
        "Bestuurseenheid '%s / %s' niet gevonden op %s.", naam, classificatie, PLATFORM_BASE
    )
    return None


# ---------------------------------------------------------------------------
# Stap 2 – zittingen ophalen (gepagineerd)
# ---------------------------------------------------------------------------

def haal_zittingen(
    bestuurseenheid_id: str,
    from_datum: str | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """
    Haal alle zittingen op voor een bestuurseenheid (gepagineerd).

    Returns:
        (lijst van zittingen, dict van geïncludeerde resources {type:id → resource})
    """
    alle_zittingen: list[dict] = []
    alle_included: dict[str, dict] = {}
    pagina = 0

    while True:
        params: dict = {
            "filter[bestuursorgaan][is-tijdsspecialisatie-van][bestuurseenheid][:id:]": bestuurseenheid_id,
            "include": "bestuursorgaan.is-tijdsspecialisatie-van,notulen,besluitenlijst",
            "fields[zittingen]": "geplande-start,gestart-op-tijdstip,notulen,bestuursorgaan,besluitenlijst",
            "fields[notulen]": "id",
            "fields[besluitenlijsten]": "id",
            "sort": "-geplande-start",
            "page[number]": pagina,
            "page[size]": PAGINA_GROOTTE,
        }
        if from_datum:
            params["filter[:gte:gestart-op-tijdstip]"] = from_datum

        data = _api_get("/zittingen", params)
        if not data or not data.get("data"):
            break

        alle_zittingen.extend(data["data"])

        # Verwerk included resources
        for res in data.get("included", []):
            sleutel = f"{res['type']}:{res['id']}"
            alle_included[sleutel] = res

        # Paginering stoppen als er geen volgende pagina is
        links = data.get("links", {})
        if "next" not in links:
            break

        pagina += 1
        time.sleep(0.2)

    logger.info("Gevonden: %d zittingen voor %s/%s", len(alle_zittingen), _gemeente_naam, _classificatie)
    return alle_zittingen, alle_included


def orgaan_naam_voor_zitting(zitting: dict, included: dict[str, dict]) -> str:
    """Bepaal de orgaannaam voor een zitting via de geïncludeerde resources."""
    orgaan_ref = zitting.get("relationships", {}).get("bestuursorgaan", {}).get("data")
    if not orgaan_ref:
        return _gemeente_naam

    # API geeft type 'bestuursorganen' (meervoud) terug als sleutel
    orgaan = included.get(f"bestuursorganen:{orgaan_ref['id']}")
    if not orgaan:
        return _gemeente_naam

    # Volg is-tijdsspecialisatie-van → parent orgaan met naam
    parent_ref = orgaan.get("relationships", {}).get("is-tijdsspecialisatie-van", {}).get("data")
    if not parent_ref:
        return _gemeente_naam

    parent = included.get(f"bestuursorganen:{parent_ref['id']}")
    if parent:
        return parent.get("attributes", {}).get("naam", _gemeente_naam)

    return _gemeente_naam


def datum_voor_zitting(zitting: dict) -> str:
    """Geef de datum (YYYY-MM-DD) van een zitting."""
    tijdstip = (
        zitting.get("attributes", {}).get("gestart-op-tijdstip")
        or zitting.get("attributes", {}).get("geplande-start")
        or ""
    )
    if tijdstip:
        return tijdstip[:10]  # ISO datum-prefix
    return "onbekend"


# ---------------------------------------------------------------------------
# Stap 3 – documentdetails ophalen per zitting
# ---------------------------------------------------------------------------

def haal_zitting_detail(zitting_id: str) -> tuple[dict | None, dict[str, dict]]:
    """
    Haal details op van één zitting, inclusief notulen-bestand en besluitenlijst-inhoud.

    Returns:
        (zitting data dict, included resources dict)
    """
    data = _api_get(f"/zittingen/{zitting_id}", {
        "include": "notulen,notulen.file,besluitenlijst",
    })
    if not data or "data" not in data:
        return None, {}

    included = {
        f"{res['type']}:{res['id']}": res
        for res in data.get("included", [])
    }
    return data["data"], included


# ---------------------------------------------------------------------------
# Stap 4 – download bestand / sla inhoud op
# ---------------------------------------------------------------------------

def download_bestand(
    file_id: str,
    output_dir: Path,
    bestandsnaam: str,
) -> DownloadResult:
    """Download een bestand van /files/{id}/download."""
    assert SESSION is not None and _config is not None
    url = f"{PLATFORM_BASE}/files/{file_id}/download"

    # Zorg dat bestandsnaam eindigt op .html
    if not bestandsnaam.lower().endswith(".html"):
        bestandsnaam += ".html"
    bestandsnaam = sanitize_filename(bestandsnaam)

    try:
        uitvoerpad = safe_output_path(output_dir, filename=bestandsnaam)
    except ValueError as exc:
        return DownloadResult(url=url, success=False, error=str(exc))

    if uitvoerpad.exists():
        return DownloadResult(url=url, success=True, path=uitvoerpad, skipped=True)

    uitvoerpad.parent.mkdir(parents=True, exist_ok=True)

    try:
        r = SESSION.get(url, timeout=60)
        if r.status_code != 200:
            return DownloadResult(url=url, success=False, error=f"HTTP {r.status_code}")

        tijdelijk = uitvoerpad.with_suffix(".tmp")
        try:
            tijdelijk.write_bytes(r.content)
            tijdelijk.replace(uitvoerpad)
        except Exception:
            tijdelijk.unlink(missing_ok=True)
            raise

        return DownloadResult(url=url, success=True, path=uitvoerpad)
    except Exception as exc:
        return DownloadResult(url=url, success=False, error=str(exc))


def sla_inhoud_op(
    inhoud: str,
    output_dir: Path,
    bestandsnaam: str,
    url_hint: str = "",
) -> DownloadResult:
    """Sla inline HTML-inhoud op als bestand."""
    if not bestandsnaam.lower().endswith(".html"):
        bestandsnaam += ".html"
    bestandsnaam = sanitize_filename(bestandsnaam)

    try:
        uitvoerpad = safe_output_path(output_dir, filename=bestandsnaam)
    except ValueError as exc:
        return DownloadResult(url=url_hint, success=False, error=str(exc))

    if uitvoerpad.exists():
        return DownloadResult(url=url_hint, success=True, path=uitvoerpad, skipped=True)

    uitvoerpad.parent.mkdir(parents=True, exist_ok=True)
    uitvoerpad.write_text(inhoud, encoding="utf-8")
    return DownloadResult(url=url_hint, success=True, path=uitvoerpad)


# ---------------------------------------------------------------------------
# Hoofdlogica
# ---------------------------------------------------------------------------

def scrape(
    output_map: str = "pdfs",
    maanden: int = 12,
    alle: bool = False,
) -> None:
    """Scrape notulen en besluitenlijsten voor de geconfigureerde gemeente."""
    assert SESSION is not None

    # Zoek bestuurseenheid-ID op
    beid = haal_bestuurseenheid_id(_gemeente_naam, _classificatie)
    if not beid:
        sys.exit(1)

    # Bepaal datumfilter
    from_datum: str | None = None
    if not alle:
        grens = datetime.now(timezone.utc) - timedelta(days=maanden * 31)
        from_datum = grens.strftime("%Y-%m-%dT00:00:00")

    # Haal zittingen op
    zittingen, included = haal_zittingen(beid, from_datum=from_datum)
    if not zittingen:
        logger.info("Geen zittingen gevonden.")
        return

    # Outputmap: pdfs/{gemeente_naam}/
    gemeente_dir = Path(output_map) / sanitize_filename(_gemeente_naam)

    resultaten: list[DownloadResult] = []

    for zitting in zittingen:
        zitting_id = zitting["id"]
        datum = datum_voor_zitting(zitting)
        orgaan = orgaan_naam_voor_zitting(zitting, included)

        # Controleer of notulen of besluitenlijst beschikbaar zijn
        rels = zitting.get("relationships", {})
        heeft_notulen = bool(rels.get("notulen", {}).get("data"))
        heeft_besluitenlijst = bool(rels.get("besluitenlijst", {}).get("data"))

        if not heeft_notulen and not heeft_besluitenlijst:
            continue

        # Haal details op
        detail, detail_included = haal_zitting_detail(zitting_id)
        if not detail:
            logger.warning("Details ophalen mislukt voor zitting %s", zitting_id)
            continue

        zitting_rels = detail.get("relationships", {})

        # Notulen verwerken
        if heeft_notulen:
            notulen_ref = zitting_rels.get("notulen", {}).get("data")
            if notulen_ref:
                notulen_res = detail_included.get(f"notulen:{notulen_ref['id']}")
                if notulen_res:
                    file_ref = notulen_res.get("relationships", {}).get("file", {}).get("data")
                    if file_ref:
                        # Notulen via bestandsdownload
                        bestandsnaam = f"{datum}_{sanitize_filename(orgaan)}_notulen.html"
                        res = download_bestand(file_ref["id"], gemeente_dir, bestandsnaam)
                        if not res.skipped:
                            _log_resultaat(res, datum, orgaan, "notulen")
                        resultaten.append(res)
                    else:
                        # Notulen via inline inhoud
                        inhoud = notulen_res.get("attributes", {}).get("inhoud", "")
                        if inhoud:
                            bestandsnaam = f"{datum}_{sanitize_filename(orgaan)}_notulen.html"
                            res = sla_inhoud_op(inhoud, gemeente_dir, bestandsnaam, url_hint=f"notulen:{notulen_ref['id']}")
                            if not res.skipped:
                                _log_resultaat(res, datum, orgaan, "notulen")
                            resultaten.append(res)

        # Besluitenlijst verwerken
        if heeft_besluitenlijst:
            bl_ref = zitting_rels.get("besluitenlijst", {}).get("data")
            if bl_ref:
                bl_res = detail_included.get(f"besluitenlijsten:{bl_ref['id']}")
                if bl_res:
                    inhoud = bl_res.get("attributes", {}).get("inhoud", "")
                    if inhoud:
                        bestandsnaam = f"{datum}_{sanitize_filename(orgaan)}_besluitenlijst.html"
                        res = sla_inhoud_op(inhoud, gemeente_dir, bestandsnaam, url_hint=f"besluitenlijsten:{bl_ref['id']}")
                        if not res.skipped:
                            _log_resultaat(res, datum, orgaan, "besluitenlijst")
                        resultaten.append(res)

        time.sleep(0.3)  # Beleefd wachten tussen zitting-detail calls

    print_summary(resultaten, naam=f"{_gemeente_naam}/{_classificatie}")


def _log_resultaat(res: DownloadResult, datum: str, orgaan: str, doc_type: str) -> None:
    """Log het resultaat van een download of opslaan."""
    if res.success:
        logger.info("  ✓ %s – %s %s → %s", datum, orgaan, doc_type, res.path.name if res.path else "?")
    else:
        logger.warning("  ✗ %s – %s %s: %s", datum, orgaan, doc_type, res.error)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor publicatie.gelinkt-notuleren.vlaanderen.be.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python scraper_gelinktnotuleren.py \\
      --base-url https://publicatie.gelinkt-notuleren.vlaanderen.be/Baarle-Hertog/Gemeente \\
      --maanden 12
  uv run python scraper_gelinktnotuleren.py \\
      --base-url https://publicatie.gelinkt-notuleren.vlaanderen.be/Baarle-Hertog/Gemeente \\
      --alle
        """,
    )
    parser.add_argument(
        "--base-url",
        type=str,
        required=True,
        help="URL van de gemeente op het platform, bv. .../Baarle-Hertog/Gemeente",
    )
    parser.add_argument(
        "--maanden", "-m",
        type=int,
        default=12,
        help="Aantal maanden terug om te downloaden (standaard: 12)",
    )
    parser.add_argument(
        "--alle",
        action="store_true",
        help="Download alle beschikbare zittingen (geen datumbeperking)",
    )
    parser.add_argument(
        "--output", "-d",
        type=str,
        default="pdfs",
        help="Uitvoermap (standaard: pdfs)",
    )
    # Compatibiliteitsopties voor scraper_groep.py
    parser.add_argument("--orgaan", "-o", type=str, default=None,
                        help="Niet gebruikt (compatibiliteit met scraper_groep.py)")
    parser.add_argument("--document-filter", "-f", type=str, default=None,
                        help="Niet gebruikt (compatibiliteit)")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")
    parser.add_argument("--zichtbaar", action="store_true",
                        help="Niet van toepassing (compatibiliteit)")

    args = parser.parse_args()

    init_session(args.base_url)
    scrape(
        output_map=args.output,
        maanden=args.maanden,
        alle=args.alle,
    )


if __name__ == "__main__":
    main()
