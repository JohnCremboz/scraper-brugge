"""Centrale bestandsfilters voor ruwe scrape-output.

De lijsten zijn bewust conservatief: ze modelleren gekende ruis uit eerder
geinspecteerde output, maar bewaren expliciete notulen/zittingsverslagen ook
wanneer een gemeente woorden zoals "bekendmaking" in de bestandsnaam gebruikt.

Synchronisatie met clean_output.ps1: beide bestanden moeten dezelfde logica
weerspiegelen. clean_output.ps1 is de authoratieve referentie voor patronen;
dit bestand voegt Python-specifieke patterns toe (raden, afkortingen, substrings).

Volgorde van toepassing in matches_blacklist():
  1. Whitelist-check (heeft altijd voorrang)
  2. Extensie-check
  3. Keyword-, afkorting-, substring- en prefix-checks
  4. Bekendmaking-uitzondering
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# WHITELIST — heeft voorrang op alle blacklist-regels
# ---------------------------------------------------------------------------

WHITELIST_KEYWORDS: tuple[str, ...] = (
    # NL — benoemingen & vertegenwoordiging
    "aanduiding",
    "representant",
    "vertegenwoordiger",
    "benoeming",
    "aanstelling",
    "voordracht",
    "afvaardiging",
    "mandataris",
    "mandataire",           # FR mandataris
    "mandaat",
    "mandat",               # FR mandaat
    # FR — benoemingen (accenten via normalize gedekt)
    "designation",       # désignation
    "nomination",
    "remplacement",      # bevat "place" → whitelist beschermt
    "delegue",           # délégué
    # Intercommunale AV-beslissingen
    "intercommunale",
    "assemblee generale",   # assemblée générale
    "algemene vergadering",
    # Agendapunten (≠ droge agenda-lijst)
    "agendapunt",
    # Geografische namen die "laan" bevatten
    "vlaanderen",
)

# ---------------------------------------------------------------------------
# BLACKLIST — extensies
# ---------------------------------------------------------------------------

BLACKLIST_EXTENSIONS: tuple[str, ...] = (
    ".wav",
    ".mp3",
    ".msg",
    ".jpg",
    ".jpeg",
    ".bin",
    ".xlsx",
    ".png",
)

# ---------------------------------------------------------------------------
# BLACKLIST — naampatronen (keyword als substring, accent/case-insensitief)
# ---------------------------------------------------------------------------

BLACKLIST_KEYWORDS: tuple[str, ...] = (
    # Financieel & begroting
    "dotatie",              # gemeentelijke dotatie aan politiezone/hulpverleningszone
    "dotation",             # FR dotation communale
    "meerjarenplan",
    "jaarrekening",
    "comptes annuels",      # FR jaarrekening
    "jahresrechnung",       # DE jaarrekening
    "begroting",
    "balans",
    "budget",
    "belasting",
    "taxe",                 # FR belasting
    "redevance",            # FR retributie/vergoeding
    "impot",                # FR belasting (impôt, accent via normalize)
    "steuer",               # DE belasting
    "retributie",
    "opcentiemen",
    "premie",
    "subsidies",
    "tarieven",
    "octroi",
    # Regelgeving & beleid
    "reglement",            # dekt règlement via normalize
    "verordening",
    "rechtspositieregeling",
    "rechtspositie",
    "beleidsplan",
    "actieplan",
    "mobiliteitsplan",
    "deontologische code",
    # Vergaderstukken (procedures, geen inhoud)
    "agenda",
    "dagorde",
    "ordre du jour",
    "bijeenroeping",
    "convocation",          # FR bijeenroeping
    "besluitenlijst",
    "toelichting",
    "bundel",
    "bijlage",
    "addendum",
    "nota",
    "afsprakennota",
    # Contracten & overeenkomsten
    "samenwerkingsovereenkomst",
    "beheersovereenkomst",
    "overeenkomst",
    "convenant",
    "erfpacht",
    "concessie",
    "delegatie",
    # Ruimtelijke ordening & infrastructuur
    "verkaveling",
    "lotissement",          # FR verkaveling
    "rooilijn",
    "omgevingsvergunning",
    "urbanisme",            # FR stedenbouw
    "permis",               # FR vergunning (permis d'urbanisme, permis de bâtir)
    "affectation",          # FR bestemming/onttrekking
    "opmeting",
    "inplanting",
    "schets",
    "architect",
    "aanleg",
    "signalisatie",
    "verkeer",
    "omleiding",
    "verklaring",
    "declaration",          # déclaration via normalize
    "erklarung",            # Erklärung via normalize
    "aanvraag",
    "demande",              # FR aanvraag
    "antrag",               # DE aanvraag
    # Wegenwerken FR
    "voirie",               # FR wegen/wegenwerken
    "chaussee",             # FR rijweg (chaussée via normalize)
    "egout",                # FR riool (égouttage via normalize)
    "refection",            # FR herstelwerken (réfection via normalize)
    # Straatnamen & locaties (documenten over specifieke straten/plaatsen)
    "laan",
    "straat",
    "dreef",
    "plein",
    "markt",
    "place",                # FR plein (whitelist dekt "remplacement")
    "rue",                  # FR straat
    "boulevard",
    "ordonnance",           # FR tijdelijke verkeersverordening
    "travaux",              # FR werken
    "werken",
    "verkoop",
    # Personeel & HR
    "personnel",            # FR personeelsaangelegenheden
    "recrutement",          # FR aanwerving
    "remuneration",         # FR bezoldiging (rémunération via normalize)
    "statut",               # FR/NL statuut (personeels- of verenigingsstatuut)
    "bareme",               # FR barema (barème via normalize)
    "traitement",           # FR wedde / afvalverwerking (traitement des déchets)
    "syndicat",             # FR vakbond / toeristische vereniging
    # GAS & sancties
    "sanction",             # FR/NL GAS-boetes & sanctionerend beleid
    "amende",               # FR boete (amendes administratives)
    # Milieu & afval
    "afval",                # NL afvaldocumenten (belasting, reglement)
    "dechet",               # FR déchets via normalize
    # Kerkfabrieken / fabriques d'église
    "kerkfabriek",
    "fabrique",             # FR kerkfabriek (fabrique d'église)
    "eglise",               # FR kerk — covers église via normalize
    # Overheidsopdrachten NL & FR
    "aanbesteding",         # NL
    "marche public",        # FR overheidsopdracht
    "accord-cadre",         # FR raamovereenkomst
    "cahier des charges",   # FR bestek
    "adjudication",         # FR aanbesteding
    "soumission",           # FR inschrijving/offerte
    "offre",                # FR offerte/aanbieding
    "contrat",              # FR contract/overeenkomst
    # Organen zonder beslissingsbevoegdheid
    "commissie",
    "vast bureau",
    "college van burgemeester",
    "jeugdraad",
    "cultuurraad",
    "gecoro",
    "ouderenraad",
    "seniorenraad",
    "sportraad",
    "bibliotheekraad",
    "milieuraad",
    "mobiliteitsraad",
    "mondiale raad",
    "welzijnsraad",
    "landbouwraad",
    "stuurgroep",
    "kerkfabriek",
    # Documenten
    "rapport",
    "rapport administratif annuel",
    "jaarverslag",
    "advies",
    "avis",                 # FR advies (whitelist dekt "avis intercommunale")
    "question",             # FR vraag/interpellatie
    "vraag",
    "analyse",
    "scenario",
    "studie",               # NL studie/onderzoeksdocument
    "etude",                # FR étude via normalize
    "documentatie",
    "brochure",
    "folder",
    "flyer",
    "fiche",
    "kaart",
    "kennisgeving",
    "melding",
    "beheer",
    # Diverse
    "plan",
    "model",
    "code",
    "brief",
    "initiatief",
    "petitie",
    "erfgoed",
    "carnaval",
    "festival",
    "wedstrijd",
    "schatting",
    "signed",
    "getekend",
    "foto",
    "afbeelding",
    "mail ",                # met spatie → voorkomt match op "email"
    # Technische scraper-artefacten
    "toezicht_",
    "overzichtslijst_bb",
    "burgemeester",
)

# ---------------------------------------------------------------------------
# BLACKLIST — afkortingen (als losse tokens, case-sensitief)
# ---------------------------------------------------------------------------

BLACKLIST_ABBREVIATIONS: tuple[str, ...] = (
    "MJP",
    "HR",
    "RPR",
    "VB",
    "CBS",
    "LOK",
    "JR",
    "HHR",
    "DCWI",
    "DRWI",
    "DCEK",
    "DREK",
    "APV",
)

# ---------------------------------------------------------------------------
# BLACKLIST — bestandsnaam-prefixes (regex, case-insensitief)
# ---------------------------------------------------------------------------

BLACKLIST_PREFIXES: tuple[str, ...] = (
    r"^SP[_\s]",
    r"^WW[_\s]",
    r"^TR[_\s]",   # verkeersregelingen
    r"^GRC",
)

# ---------------------------------------------------------------------------
# BLACKLIST — substrings (exact, na normalisatie)
# ---------------------------------------------------------------------------

BLACKLIST_SUBSTRINGS: tuple[str, ...] = (
    "_SP_",
    "_AR_",
    "CR_",
)

# ---------------------------------------------------------------------------
# Toegelaten extensies voor scraper-output
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".json",
    ".html",
    ".htm",
    ".ps1",
)


def normalize_filter_text(value: str) -> str:
    """Normaliseer tekst voor accent- en hoofdletterongevoelige matching."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_text.casefold()


def has_allowed_extension(filename: str | Path) -> bool:
    """Controleer of een bestandsextensie binnen de toegelaten outputtypes valt."""
    return Path(filename).suffix.casefold() in ALLOWED_EXTENSIONS


def _matches_whitelist(normalized_name: str) -> bool:
    """Return True als de bestandsnaam een whitelist-keyword bevat."""
    for keyword in WHITELIST_KEYWORDS:
        if normalize_filter_text(keyword) in normalized_name:
            return True
    return False


def _matches_abbreviation(filename: str) -> bool:
    """Match hoofdletterafkortingen als losse tokens, vergelijkbaar met PowerShell -cmatch."""
    stem = Path(filename).stem
    for abbreviation in BLACKLIST_ABBREVIATIONS:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(abbreviation)}(?![A-Za-z0-9])"
        if re.search(pattern, stem):
            return True
    return False


def matches_blacklist(filename: str | Path) -> bool:
    """Return True als de bestandsnaam overeenkomt met gekende ongewenste output.

    Volgorde: whitelist → extensie → keywords → afkortingen → substrings → prefixes.
    """
    name = Path(filename).name
    normalized_name = normalize_filter_text(name)

    # 1. Whitelist heeft altijd voorrang
    if _matches_whitelist(normalized_name):
        return False

    # 2. Extensie-blacklist (let op: .docx staat in BLACKLIST_EXTENSIONS maar ook
    #    in ALLOWED_EXTENSIONS voor scrapers die Word-docs verwachten; de extensie-
    #    blacklist is bedoeld voor clean_output.ps1-pariteit, niet voor download-filter)
    if Path(filename).suffix.casefold() in BLACKLIST_EXTENSIONS:
        return True

    if not has_allowed_extension(name):
        return True

    # 3. Keyword-check
    for keyword in BLACKLIST_KEYWORDS:
        if normalize_filter_text(keyword) in normalized_name:
            return True

    # 4. Afkorting-check
    if _matches_abbreviation(name):
        return True

    # 5. Substring-check
    for substring in BLACKLIST_SUBSTRINGS:
        if normalize_filter_text(substring) in normalized_name:
            return True

    # 6. Prefix-check
    for prefix in BLACKLIST_PREFIXES:
        if re.search(prefix, name, flags=re.IGNORECASE):
            return True

    # 7. Bekendmaking: blacklist tenzij het ook notulen/zittingsverslag bevat
    if "bekendmaking" in normalized_name:
        if not re.search(r"\b(notulen|zittingsverslag)\b", name, re.IGNORECASE):
            return True

    return False


def should_keep_output_file(filename: str | Path) -> bool:
    """Gebruik deze predicate bij cleanup van scrape-output."""
    return not matches_blacklist(filename)


def should_consider_scrape_input(filename: str | Path) -> bool:
    """Striktere predicate voor nieuwe scrape-input: alleen PDFs en geen blacklist-hit."""
    return Path(filename).suffix.casefold() == ".pdf" and not matches_blacklist(filename)
