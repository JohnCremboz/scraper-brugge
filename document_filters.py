"""Centrale bestandsfilters voor ruwe scrape-output.

De lijsten zijn bewust conservatief: ze modelleren gekende ruis uit eerder
geinspecteerde output, maar bewaren expliciete notulen/zittingsverslagen ook
wanneer een gemeente woorden zoals "bekendmaking" in de bestandsnaam gebruikt.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


BLACKLIST_KEYWORDS = (
    "Meerjarenplan",
    "Jaarrekening",
    "Besluitenlijst",
    "Reglement",
    "verordening",
    "Agenda",
    "Dagorde",
    "Belasting",
    "retributie",
    "Rechtspositieregeling",
    "Opcentiemen",
    "College van burgemeester",
    "Vast bureau",
    "Nota",
    "Model",
    "Deontologische code",
    "Subsidies",
    "Tarieven",
    "Advies",
    "Bijlage",
    "Afsprakennota",
    "Burgemeester",
    "Commissie",
    "Rapport administratif annuel",
    "Plan",
    "Budget",
    "Ordre du jour",
    "Bundel",
    "rapport",
    "jeugdraad",
    "cultuurraad",
    "gecoro",
    "ouderenraad",
    "kerkfabriek",
    "signalisatie",
    "flyer",
    "code",
    "besluit",
    "scenario",
    "analyse",
    "documentatie",
    "seniorenraad",
    "stuurgroep",
    "sportraad",
    "bibliotheekraad",
    "milieuraad",
    "mobiliteitsraad",
    "mondiale raad",
    "welzijnsraad",
    "landbouwraad",
    "bijeenroeping",
    "toelichting",
    "Toezicht_",
    "Overzichtslijst_BB",
    "Mail ",
    "Afbeelding",
    "Foto",
    "beheersovereenkomst",
    "addendum",
)

BLACKLIST_ABBREVIATIONS = (
    "MJP",
    "HR",
    "RPR",
    "VB",
    "CBS",
    "LOK",
    "DCWI",
    "DRWI",
    "DCEK",
    "DREK",
    "APV",
)

BLACKLIST_PREFIXES = (
    r"^SP[_\s]",
    r"^WW[_\s]",
    r"^GRC",
)

BLACKLIST_SUBSTRINGS = (
    "_SP_",
    "_AR_",
    "CR_",
)

ALLOWED_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".json",
    ".html",
    ".htm",
    ".ps1",
)

BEKENDMAKING_KEEP_RE = re.compile(r"\b(notulen|zittingsverslag)\b", re.IGNORECASE)


def normalize_filter_text(value: str) -> str:
    """Normaliseer tekst voor accent- en hoofdletterongevoelige matching."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_text.casefold()


def has_allowed_extension(filename: str | Path) -> bool:
    """Controleer of een bestandsextensie binnen de toegelaten outputtypes valt."""
    return Path(filename).suffix.casefold() in ALLOWED_EXTENSIONS


def matches_blacklist(filename: str | Path) -> bool:
    """Return True als de bestandsnaam overeenkomt met gekende ongewenste output."""
    name = Path(filename).name
    normalized_name = normalize_filter_text(name)

    if not has_allowed_extension(name):
        return True

    for keyword in BLACKLIST_KEYWORDS:
        if normalize_filter_text(keyword) in normalized_name:
            return True

    if _matches_abbreviation(name):
        return True

    for substring in BLACKLIST_SUBSTRINGS:
        if normalize_filter_text(substring) in normalized_name:
            return True

    for prefix in BLACKLIST_PREFIXES:
        if re.search(prefix, name, flags=re.IGNORECASE):
            return True

    if "bekendmaking" in normalized_name and not BEKENDMAKING_KEEP_RE.search(name):
        return True

    return False


def should_keep_output_file(filename: str | Path) -> bool:
    """Gebruik deze predicate bij cleanup van scrape-output."""
    return not matches_blacklist(filename)


def should_consider_scrape_input(filename: str | Path) -> bool:
    """Striktere predicate voor nieuwe scrape-input: alleen PDFs en geen blacklist-hit."""
    return Path(filename).suffix.casefold() == ".pdf" and not matches_blacklist(filename)


def _matches_abbreviation(filename: str) -> bool:
    """Match hoofdletterafkortingen als losse tokens, vergelijkbaar met PowerShell -cmatch."""
    stem = Path(filename).stem
    for abbreviation in BLACKLIST_ABBREVIATIONS:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(abbreviation)}(?![A-Za-z0-9])"
        if re.search(pattern, stem):
            return True
    return False
