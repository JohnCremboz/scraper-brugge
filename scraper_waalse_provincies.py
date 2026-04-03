"""
scraper_waalse_provincies.py — Unified scraper voor de 3 Waalse provincies

Ondersteunde provincies (auto-detectie via --base-url of --provincie):
  hainaut       hainaut.be          Province de Hainaut (3 subpagina's: ODJ, délibérations, communiqués)
  luxemburg     province.luxembourg.be   Province de Luxembourg (ODJ & PV, h3-jaar -> li-structuur)
  brabantwallon brabantwallon.be    Province de Brabant wallon (directe PV-links)

Gebruik:
    uv run python scraper_waalse_provincies.py --provincie hainaut --maanden 12
    uv run python scraper_waalse_provincies.py --provincie luxemburg --maanden 6
    uv run python scraper_waalse_provincies.py --provincie brabantwallon --maanden 18
    uv run python scraper_waalse_provincies.py --base-url https://www.hainaut.be --maanden 12
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    download_document,
    robust_get,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# Gedeelde constanten
# ---------------------------------------------------------------------------

MAANDEN_FR: dict[str, int] = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}

SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def _get(url: str) -> requests.Response | None:
    return robust_get(SESSION, url)


# ---------------------------------------------------------------------------
# Gedeelde datum-parsers
# ---------------------------------------------------------------------------

def _parse_datum_du(tekst: str, fallback_jaar: int | None = None) -> date | None:
    """
    Parset datums van de vorm "du DD mois YYYY" of "du DD mois" (jaar via fallback).
    Ondersteunt ook "de mois YYYY" (dag onbekend -> dag 1).
    """
    # 1. "du 18 décembre 2025"
    m = re.search(r"\bdu\s+(\d{1,2})\w*\s+(\w+)\s+(\d{4})\b", tekst, re.IGNORECASE)
    if m:
        dag, maand_str, jaar = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        maand = MAANDEN_FR.get(maand_str)
        if maand:
            try:
                return date(jaar, maand, dag)
            except ValueError:
                pass

    # 2. "du 27 mars" + fallback_jaar
    m = re.search(r"\bdu\s+(\d{1,2})\w*\s+(\w+)\b", tekst, re.IGNORECASE)
    if m and fallback_jaar:
        dag, maand_str = int(m.group(1)), m.group(2).lower()
        maand = MAANDEN_FR.get(maand_str)
        if maand:
            try:
                return date(fallback_jaar, maand, dag)
            except ValueError:
                pass

    # 3. "de décembre 2024" (dag onbekend)
    m = re.search(r"\bde\s+(\w+)\s+(\d{4})\b", tekst, re.IGNORECASE)
    if m:
        maand_str, jaar = m.group(1).lower(), int(m.group(2))
        maand = MAANDEN_FR.get(maand_str)
        if maand:
            try:
                return date(jaar, maand, 1)
            except ValueError:
                pass

    return None


# ---------------------------------------------------------------------------
# Province-specifieke parsers
# ---------------------------------------------------------------------------

def _haal_hainaut(base_url: str, maanden: int) -> list[dict]:
    """
    Hainaut: 3 subpagina's scrapen (ODJ, projets de délibérations, communiqués).
    Elke pagina: h3-titel met datum -> PDF-link in volgend sibling-blok.
    """
    paginas = [
        (f"{base_url}/la-province/les-publications-legales-et-institutionnelles/ordres-du-jour",
         "Ordre du jour"),
        (f"{base_url}/la-province/les-publications-legales-et-institutionnelles/projets-des-deliberations-du-conseil-provincial",
         "Projets de délibérations"),
        (f"{base_url}/la-province/les-publications-legales-et-institutionnelles/communiques-du-conseil-et-du-college-provincial",
         "Communiqué"),
    ]
    cutoff = date.today() - relativedelta(months=maanden)
    vergaderingen: dict[str, dict] = {}

    for pagina_url, doc_type in paginas:
        resp = _get(pagina_url)
        if not resp:
            print(f"  [!] Kan pagina niet laden: {pagina_url}")
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for h3 in soup.find_all("h3"):
            titel = h3.get_text(strip=True)
            datum = _parse_datum_du(titel)
            if datum is None or datum < cutoff:
                continue

            link = None
            for sibling in h3.find_next_siblings():
                a = sibling.find("a", href=re.compile(r"\.pdf", re.IGNORECASE))
                if a:
                    link = a
                    break
                if sibling.name == "h3":
                    break

            if not link:
                continue

            datum_str = datum.isoformat()
            pdf_url = urljoin(base_url, link["href"])
            link_naam = link.get("title") or link.get_text(strip=True) or titel

            if datum_str not in vergaderingen:
                vergaderingen[datum_str] = {
                    "datum": datum_str,
                    "orgaan": "Conseil provincial",
                    "documenten": [],
                }
            vergaderingen[datum_str]["documenten"].append({
                "naam": f"{doc_type} — {link_naam}",
                "url": pdf_url,
                "type": "pdf",
                "local_file": None,
            })

    return sorted(vergaderingen.values(), key=lambda v: v["datum"], reverse=True)


def _haal_luxemburg(base_url: str, maanden: int) -> list[dict]:
    """
    Luxemburg: 1 pagina, h3-jaar -> ul -> li-items.
    Datum staat in li-tekst: "27 mars - 14h : ...". Meerdere PDFs per li mogelijk.
    """
    pagina_url = (
        f"{base_url}/province-de-luxembourg/publications-legales-institutionnelles/ordre_du_jour"
    )
    resp = _get(pagina_url)
    if not resp:
        print("  [!] Kan pagina niet laden")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    main = soup.find("main")
    if not main:
        print("  [!] Geen <main> element gevonden")
        return []

    cutoff = date.today() - relativedelta(months=maanden)
    vergaderingen: list[dict] = []

    for h3 in main.find_all("h3"):
        tekst = h3.get_text(strip=True)
        if not re.match(r"^\d{4}$", tekst):
            continue
        jaar = int(tekst)
        if date(jaar, 12, 31) < cutoff:
            break

        ul = h3.find_next_sibling("ul")
        if not ul:
            continue

        for li in ul.find_all("li", recursive=False):
            li_tekst = li.get_text(" ", strip=True)
            # Verwijder ordinale suffix ("1 er" -> "1")
            li_tekst_norm = re.sub(r"(\d)\s+er\b", r"\1", li_tekst, flags=re.IGNORECASE)
            m = re.match(r"^(\d{1,2})\s+(\w+)", li_tekst_norm.strip())
            if not m:
                continue
            dag, maand_str = int(m.group(1)), m.group(2).lower()
            maand = MAANDEN_FR.get(maand_str)
            if not maand:
                continue
            try:
                datum = date(jaar, maand, dag)
            except ValueError:
                continue
            if datum < cutoff:
                continue

            pdf_links = li.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))
            if not pdf_links:
                continue

            vergaderingen.append({
                "datum": datum.isoformat(),
                "orgaan": "Conseil provincial",
                "documenten": [
                    {
                        "naam": a.get_text(strip=True) or "document",
                        "url": urljoin(base_url, a["href"]),
                        "type": "pdf",
                        "local_file": None,
                    }
                    for a in pdf_links
                ],
            })

    vergaderingen.sort(key=lambda v: v["datum"], reverse=True)
    return vergaderingen


def _jaar_uit_url(href: str) -> int | None:
    """Extraheer jaargetal uit URL-pad zoals /conseil-provincial-2025/."""
    m = re.search(r"conseil-provincial-(\d{4})", href)
    if m:
        return int(m.group(1))
    m = re.search(r"/(\d{4})/", href)
    if m:
        return int(m.group(1))
    return None


def _haal_brabantwallon(base_url: str, maanden: int) -> list[dict]:
    """
    Brabant wallon: 1 pagina met directe PDF-links naar procès-verbaux.
    Datum staat in linktekst; jaar eventueel uit URL-pad als fallback.
    """
    pagina_url = (
        f"{base_url}/le-brabant-wallon/vie-politique/publications-officielles"
        "/proces-verbaux-du-conseil-provincial"
    )
    resp = _get(pagina_url)
    if not resp:
        print("  [!] Kan pagina niet laden")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    main = soup.find("main") or soup.find(id="content")
    if not main:
        print("  [!] Geen <main> element gevonden")
        return []

    cutoff = date.today() - relativedelta(months=maanden)
    vergaderingen: list[dict] = []

    for a in main.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE)):
        tekst_raw = a.get_text(strip=True)
        naam = re.sub(r"\s*\d+[\.,]\d+\s*(KB|MB)\s*$", "", tekst_raw, flags=re.IGNORECASE).strip()
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin(base_url, href)

        fallback_jaar = _jaar_uit_url(href)
        datum = _parse_datum_du(tekst_raw, fallback_jaar=fallback_jaar)
        if datum is None or datum < cutoff:
            continue

        vergaderingen.append({
            "datum": datum.isoformat(),
            "orgaan": "Conseil provincial",
            "documenten": [{
                "naam": naam or "Proces-verbal",
                "url": href,
                "type": "pdf",
                "local_file": None,
            }],
        })

    vergaderingen.sort(key=lambda v: v["datum"], reverse=True)
    return vergaderingen


# ---------------------------------------------------------------------------
# Provincie-configuratie
# ---------------------------------------------------------------------------

@dataclass
class ProvincieConfig:
    sleutel: str
    naam: str
    base_url: str
    hostname: str        # voor auto-detectie via --base-url
    parser: object       # callable (base_url, maanden) -> list[dict]
    bron_label: str      # korte omschrijving voor HTML footer


PROVINCIES: dict[str, ProvincieConfig] = {
    "hainaut": ProvincieConfig(
        sleutel="hainaut",
        naam="Provincie Henegouwen - Hainaut",
        base_url="https://www.hainaut.be",
        hostname="hainaut.be",
        parser=_haal_hainaut,
        bron_label="hainaut.be",
    ),
    "luxemburg": ProvincieConfig(
        sleutel="luxemburg",
        naam="Provincie Luxemburg - Luxembourg",
        base_url="https://province.luxembourg.be",
        hostname="province.luxembourg.be",
        parser=_haal_luxemburg,
        bron_label="province.luxembourg.be",
    ),
    "brabantwallon": ProvincieConfig(
        sleutel="brabantwallon",
        naam="Provincie Waals-Brabant - Brabant wallon",
        base_url="https://www.brabantwallon.be",
        hostname="brabantwallon.be",
        parser=_haal_brabantwallon,
        bron_label="brabantwallon.be",
    ),
}


def _detecteer_provincie(base_url: str) -> ProvincieConfig | None:
    """Detecteer provincie op basis van hostname in de base_url."""
    u = (base_url or "").lower()
    for cfg in PROVINCIES.values():
        if cfg.hostname in u:
            return cfg
    return None


# ---------------------------------------------------------------------------
# Gedeelde output-logica
# ---------------------------------------------------------------------------

def genereer_html(vergaderingen: list[dict], cfg: ProvincieConfig, output_dir: Path) -> Path:
    from html_output import doc_badges_html, genereer_html_tabel, html_output_path
    html_path = html_output_path(output_dir, cfg.naam)
    rijen = [
        [v["datum"], v["orgaan"], doc_badges_html(v.get("documenten", []), html_path)]
        for v in vergaderingen
    ]
    return genereer_html_tabel(
        naam=cfg.naam,
        bron=cfg.bron_label,
        kolommen=["Datum", "Orgaan", "Documenten"],
        rijen=rijen,
        output_pad=html_path,
        lang="fr",
    )


def scrape(provincie_sleutel: str, maanden: int = 6, output_base: str = "pdfs") -> None:
    global SESSION, _config

    cfg = PROVINCIES.get(provincie_sleutel)
    if not cfg:
        print(f"[!] Onbekende provincie: {provincie_sleutel!r}")
        print(f"    Kies uit: {', '.join(PROVINCIES)}")
        return

    output_dir = Path(output_base) / sanitize_filename(cfg.naam)
    output_dir.mkdir(parents=True, exist_ok=True)

    _config = ScraperConfig(base_url=cfg.base_url, output_dir=output_dir)
    SESSION = create_session(_config)

    print(f"\n{'=' * 70}")
    print(f"  Naam     : {cfg.naam}")
    print(f"  Bron     : {cfg.base_url}")
    print(f"  Output   : {output_dir}")
    print(f"{'=' * 70}")

    print(f"[1] Vergaderingen ophalen (afgelopen {maanden} maanden)...")
    vergaderingen = cfg.parser(cfg.base_url, maanden)
    print(f"    v {len(vergaderingen)} vergaderingen gevonden")

    if not vergaderingen:
        print("  Geen vergaderingen gevonden.")
        return

    n_pdfs = sum(len(v["documenten"]) for v in vergaderingen)
    gedownload = 0

    print(f"[2] PDF's downloaden ({n_pdfs} totaal)...")
    for v in tqdm(vergaderingen, desc="Downloaden"):
        for doc in v["documenten"]:
            result = download_document(
                SESSION, _config, doc["url"], output_dir,
                filename_hint=f"{v['datum']}_{sanitize_filename(doc['naam'])}.pdf",
            )
            if result and result.success:
                doc["local_file"] = str(result.path)
                gedownload += 1

    print("[3] Opslaan...")
    meta_pad = output_dir / f"{sanitize_filename(cfg.naam)}_metadata.json"
    meta_pad.write_text(json.dumps(vergaderingen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    v JSON: {meta_pad.name}")

    html_pad = genereer_html(vergaderingen, cfg, output_dir)
    print(f"    v HTML: {html_pad.name}")

    print(f"\n{'=' * 70}")
    print(f"  Klaar!")
    print(f"  Vergaderingen : {len(vergaderingen)}")
    print(f"  Documenten    : {n_pdfs} ({gedownload} PDF's gedownload)")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor Waalse provincies (Hainaut, Luxemburg, Brabant wallon)"
    )
    parser.add_argument(
        "--provincie",
        choices=list(PROVINCIES),
        help="Te scrapen provincie",
    )
    parser.add_argument("--maanden", type=int, default=6, help="Aantal maanden terug (standaard 6)")
    parser.add_argument("--output", "-d", type=str, default="pdfs", help="Uitvoermap")
    # Standaard TUI-argumenten; --base-url wordt gebruikt voor auto-detectie provincie
    parser.add_argument("--base-url", type=str, default="", help="Basis-URL (auto-detectie provincie)")
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--orgaan", type=str)
    parser.add_argument("--agendapunten", action="store_true")
    parser.add_argument("--zichtbaar", action="store_true")
    parser.add_argument("--document-filter", type=str)
    args = parser.parse_args()

    # Provincie bepalen: expliciete --provincie heeft voorrang op --base-url
    provincie_sleutel: str | None = args.provincie
    if not provincie_sleutel and args.base_url:
        cfg = _detecteer_provincie(args.base_url)
        if cfg:
            provincie_sleutel = cfg.sleutel

    if not provincie_sleutel:
        parser.error(
            "Geef --provincie op of gebruik --base-url met een herkende hostname.\n"
            f"  Kies uit: {', '.join(PROVINCIES)}"
        )

    scrape(provincie_sleutel, maanden=args.maanden, output_base=args.output)


if __name__ == "__main__":
    main()
