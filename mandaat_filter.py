"""
mandaat_filter.py — Gedeelde filterlogica voor mandaatrelevante documenten.

Importeer dit in filter_inhoud.py (post-processing) en base_scraper.py
(at-scrape-time filtering).
"""

from __future__ import annotations

import fitz  # pymupdf

# ---------------------------------------------------------------------------
# Sleutelwoorden — document wordt bewaard als MINSTENS EEN term matcht
# ---------------------------------------------------------------------------
MANDAAT_TERMEN: list[str] = [
    # --- NEDERLANDS ---

    # Aanstellingen en vertegenwoordiging
    "aanduiding",
    "aangewezen",
    "aangesteld",
    "aanstelling",
    "afgevaardigde",
    "afvaardiging",
    "vertegenwoordiger",
    "voordracht",
    "kandidaatstelling",
    "volmacht",
    "benoeming",
    "plaatsvervanger",

    # Organen
    "intercommunale",
    "intergemeentelijk",
    "autonoom gemeentebedrijf",
    "autonoom provinciebedrijf",
    "zorgbedrijf",
    "politiezone",
    "brandweerzone",
    "hulpverleningszone",
    "raad van bestuur",
    "algemene vergadering",
    "welzijnsvereniging",
    "eerstelijnszone",
    "elz ",  # afkorting Eerstelijnszone, spatie voorkomt valse matches

    # Bezoldiging
    "presentiegeld",
    "zitpenning",
    "bezoldiging",
    "bestuursvergoeding",
    "mandaatsvergoeding",

    # Mandaatbegrippen
    "mandaat",
    "mandataris",
    "mandatarissen",

    # Veelvoorkomende Vlaamse intercommunales
    "fluvius",
    "igean",
    "farys",
    "pontes",
    "pidpa",
    "ivago",
    "imog",
    "mirom",
    "iveka",
    "iveg",
    "ivio",
    "ivla",
    "ivm ",
    "veneco",
    "westlede",
    "provant",
    "eandis",
    "infrax",
    "tmvw",
    "riopact",
    "poolstok",
    "aan-z",
    "welzijnsband",
    "zefier",
    "ligo",
    "comeet",
    "meetjesman",

    # --- FRANÇAIS ---

    # Désignations et représentation
    "désignation",
    "désigné",
    "nommé",
    "délégué",
    "représentant",
    "remplacement",
    "candidature",
    "procuration",
    "nomination",
    "suppléant",
    "mandataire",

    # Organes
    "intercommunal",
    "régie communale autonome",
    "zone de police",
    "conseil d'administration",
    "assemblée générale",
    "zone de secours",
    "association d'aide sociale",

    # Rémunération
    "jeton de présence",
    "rémunération",
    "indemnité de mandat",
    "indemnité de fonction",

    # Mandataire en installatie
    "serment",          # prestation de serment = eed/installatie burgemeester/schepenen
    "bourgmestre",      # burgemeester (FR) — installatie/mandate
    "échevin",          # schepen (FR) — installatie/mandate
    "pacte de majorité",# coalitieakkoord — altijd mandaatcontext
    "élection",         # verkiezing CPAS-raadsleden, schepenen, voorzitters
    "démission",        # ontslag uit mandaat (conseiller/président CPAS, schepen, …)

    # Intercommunales wallonnes
    "ores",
    "swde",
    "ipalle",
    "idelux",
    "aive",
    "ideta",
    "inasep",
    "igretec",
    "vivalia",
    "isppc",
    "resa ",
    "aiesh",
    "bep ",
    "igh ",
    "tecteo",
    "ieg ",

    # --- DEUTSCH (Ostbelgien) ---

    # Benennung und Vertretung
    "benennung",
    "ernannt",
    "delegierter",
    "vertreter",
    "kandidatur",
    "vollmacht",
    "ernennung",
    "stellvertreter",

    # Organe
    "interkommunal",
    "verwaltungsrat",
    "generalversammlung",
    "gemeinderat",
    "schöffenrat",
    "öshz",
    "polizeizone",
    "hilfeleistungszone",

    # Vergütung
    "sitzungsgeld",
    "vergütung",
    "mandatsvergütung",

    # Mandat
    "mandatsträger",
]

_TERMEN_LC: list[str] = [t.lower() for t in MANDAAT_TERMEN]


def is_relevant(tekst: str) -> bool:
    """Geeft True als de tekst minstens één mandaatterm bevat."""
    tekst_lc = tekst.lower()
    return any(term in tekst_lc for term in _TERMEN_LC)


def is_relevant_bytes(content: bytes) -> tuple[bool, bool]:
    """
    Extraheer tekst uit PDF-bytes en controleer relevantie.

    Returns:
        (relevant, is_gescand)
        is_gescand=True als er geen extraheerbare tekst is (scan/afbeelding).
        Gescande PDFs gelden als relevant (bewaren bij twijfel).
    """
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        tekst = " ".join(pagina.get_text() for pagina in doc)
        doc.close()
    except Exception:
        return True, True  # fout bij openen: bewaren

    if len(tekst.strip()) < 50:
        return True, True  # gescand: bewaren

    return is_relevant(tekst), False
