"""Centrale bestandsfilters voor ruwe scrape-output.

## Architectuurprincipe: blacklist + whitelist + compound

Het filtermodel werkt in drie lagen, toegepast in vaste volgorde:

1. **Whitelist** (`WHITELIST_KEYWORDS`) — mandaat-relevante ankertermen die een
   bestand altijd vrijwaren, ongeacht wat andere regels zeggen.  Voeg hier
   termen toe die onmiskenbaar op een raadsbeslissing over vertegenwoordiging
   of mandaat wijzen (bijv. "mandaat", "vertegenwoordiger").

2. **Blacklist** (`BLACKLIST_KEYWORDS`, `BLACKLIST_ABBREVIATIONS`,
   `BLACKLIST_PREFIXES`, `BLACKLIST_SUBSTRINGS`) — enkelvoudige termen die op
   zichzelf voldoende zijn om een bestand uit te sluiten (bijv. "Agenda",
   "Reglement", "RUP").  Gebruik dit alleen wanneer de term nooit in een
   mandaat-relevant document voorkomt.

3. **Compound blacklist** (`COMPOUND_BLACKLIST`) — paren van termen die *samen*
   een niet-mandaat-context vormen, maar individueel te breed zijn om te
   blokkeren.  Voorbeeld: "goedkeuring" is op zichzelf mandaat-relevant, maar
   "goedkeuring" + "ontwerpakte" duidt altijd op een notariële grondakte bij
   wegenwerken.  Voeg hier combinaties toe wanneer één term te veel
   nevenschade zou geven als alleenstaande blacklist-entry.

Volgorde is bewust: whitelist wint altijd van blacklist en compound, zodat een
toekomstige agressievere blacklist-regel geen mandaatdocumenten kan treffen.
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
    "parkeer",
    "kermis",
    "kermesse",
    "vergunning",
    "seingever",
    "baan",
    "wegen",
    "protocol",
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
    "toestemming",              # nutsinfrastructuur, politietoezicht — nooit mandaatrelevant
    "concessie",                # NL: patrimonium/exploitatie gemeentelijke eigendommen
    "concession",               # FR: idem; delegaties beschermd via WHITELIST_KEYWORDS
    "bail",                     # FR: huurcontracten (bail emphytéotique, bail à ferme, …)
    "stratégique transversal",   # FR: Programme/Plan Stratégique Transversal = Meerjarenplan (spaties)
    "stratégique-transversal",   # idem met koppeltekens (slugified bestandsnamen)
    "matériel roulant",          # FR: voertuigen/rollend materieel — aankopen, reparaties, verkopen
    "matériel-roulant",          # idem met koppeltekens
    "dotation",                  # FR: gemeentelijke bijdrage aan politie-/brandweerzone, CPAS, academie — puur financieel
    "entretien",                 # FR: onderhoudsopdrachten (wegen, gebouwen, voertuigen, riolen) — nooit mandaatrelevant
    "contrat",                   # FR: beheers-/huur-/dienstencontracten — designation/représentant beschermd via whitelist
    "convention",                # FR: samenwerkings-/bezettings-/dienstenconventies — delegation/représentant beschermd via whitelist
    "marche",                    # FR: overheidsopdrachten (marché public/conjoint/emprunt/fournitures) — délégation/désignation beschermd via whitelist
    "batiment",                  # FR: patrimonium/gebouwbeheer (bâtiment communal/public) — nooit mandaatrelevant
    "cimetiere",                 # FR: kerkhofbeheer (grafconcessies, hernemingen, verordeningen) — délégation/désignation beschermd via whitelist
    "reparation",                # FR: herstellingen (wegen, daken, voertuigen, …) — nooit mandaatrelevant
    "etude",                     # FR: technische/infrastructurele studies — désignation/représentant beschermd via whitelist
    "dechet",                    # FR: afvalbeheer (tarieven, collecte, coût-vérité) — mandat/délégation à intercommunale beschermd via whitelist
    "personnel",                 # FR: personeelsadministratie (statuten, barema's, aanwervingen) — délégation/désignation beschermd via whitelist
    "fabrique",                  # FR: kerkfabrieken (tutelle, budgetten, subsidies) — nooit mandaatrelevant
    "delinquance",               # FR: milieu-overtredingen, administratieve sancties (SAC) — nooit mandaatrelevant
    "patrimoine",                # FR: patrimonium (verkopen, aankopen, overdrachten) — délégation/désignation beschermd via whitelist
    "bilan",                     # FR: balansen, activiteitsverslagen, jaarrekeningen — nooit mandaatrelevant
    "avantage",                  # FR: sociale voordelen (scholen, personeel) — nooit mandaatrelevant
    "urbanisme",                 # FR: ruimtelijke ordening (vergunningen, schema's) — CCATM/Maison de l'Urbanisme beschermd via whitelist
    "amenagement",               # FR: inrichting/aménagement du territoire — désignation/représentant beschermd via whitelist
    "acquisition",               # FR: aankopen (voertuigen, machines, percelen) — désignation Comité d'Acquisition beschermd via whitelist
    "immersion",                 # FR: taalimmersie-onderwijs + overstromingszones (ZIT) — nooit mandaatrelevant
    "subside",                   # FR: subsidies aan vzw's/sport/cultuur — délégation/désignation beschermd via whitelist
    "subvention",                # FR: idem (alternatieve schrijfwijze) — délégation beschermd via whitelist
    "redevance",                 # FR: gemeentelijke heffingen (zalen, maaltijden, begraafplaatsen) — nooit mandaatrelevant
    "domaine public",            # FR: openbaar domein (innames, redevances, overdrachten) — nooit mandaatrelevant
    "taxe",                      # FR: gemeentelijke belastingen (taxe de déversement, taxe immondices, …) — nooit mandaatrelevant
    "tax on pylons",             # Waals programma GSM-masten (TOP III) — financieel/infrastructuur, nooit mandaatrelevant
    "arrete",                    # FR: arrêtés du bourgmestre/de police/royaux/ministériels — nooit raadsbeslissing over mandaat
    "population",                # FR: bevolkingsstatistieken, schoolpopulatie, bevolkingsregisterdienst — nomination/délégation beschermd via whitelist
    "interpellation citoyenne",  # FR: burgervragen aan de gemeenteraad — geen mandaatbeslissing
    "modification budgetaire",       # FR: begrotingswijzigingen — puur financieel, nooit mandaatrelevant
    "modification budgétaire",       # FR: idem met accent
    "modifications budgetaires",     # FR: meervoud
    "modifications budgétaires",     # FR: meervoud met accent
    "sanctionerend ambtenaar",       # NL: GAS-handhaving — interne gemeentelijke aanstelling, geen extern mandaat
    "fonctionnaire sanctionnateur",  # FR: idem (m)
    "fonctionnaire sanctionnatrice", # FR: idem (f)
    "sterilisatie",                  # NL: dierenwelzijn/medisch — nooit mandaatrelevant
    "sterilisation",                 # FR: idem (zonder accent)
    "stérilisation",                 # FR: idem met accent
    "handycity",                     # inclusie-charter personen met handicap — nooit mandaatrelevant
    "auditcharter",                  # intern auditframework — nooit mandaatrelevant
    "charte IA",                     # AI-gebruiksbeleid
    "charte informatique",           # IT-gebruiksbeleid
    "charte de déontologie",         # deontologiecharter gemeenteraad
    "charte de deontologie",         # idem zonder accent
    "charte PEFC",                   # bosbeheercertificering
    "charte de nourrissage",         # katten voederbeleid
    "intelligence artificielle",     # AI-charters en -beleid — nooit mandaatrelevant
    "droit de chasse",               # FR: jachtrecht — patrimonium, nooit mandaatrelevant
    "location de chasse",            # FR: jachtverhuur
    "location des chasses",          # FR: idem meervoud
    "baux de chasse",                # FR: jachtpachten
    "bail de chasse",                # FR: jachtpacht (enkelvoud)
    "devis forestier",               # FR: bosbeheercijfers/ramingen — nooit mandaatrelevant
    "regime forestier",              # FR: bosbeheersregime (technisch)
    "régime forestier",              # FR: idem met accent
    "parcelle forestiere",           # FR: bosgrondperceel
    "parcelle forestière",           # FR: idem met accent
    "parcelles forestieres",         # FR: meervoud
    "parcelles forestières",         # FR: meervoud met accent
    "chemin forestier",              # FR: boswegen
    "camera",                        # NL/FR: bewakingscamera's, ANPR, cameratoezicht — ordehandhaving, nooit mandaatrelevant
    "caméra",                        # FR: idem met accent
    "kerkfabriek",                   # NL: kerkfabrieken (budgetten, jaarrekeningen, leningen) — pendant van FR "fabrique"
    "technische dienst",             # NL: operationele beslissingen technische dienst — nooit mandaatrelevant
    "financiele dienst",             # NL: financiële dienst — budgetten, leningen, belastingen, nooit mandaatrelevant
    "financiële dienst",             # NL: idem met accent
    "vrije tijd",                    # NL: vrijetijdsdienst — operationeel, commissie/samenstelling beschermd via whitelist
    "circulation",                   # FR: verkeersreglementering — designation/commissie beschermd via whitelist
    "braderie",                      # NL/FR: koopjesmarkt/braderie — nooit mandaatrelevant
    "feest",                         # NL: feesten, schoolfeesten, buurtfeesten — samenstelling/aanstelling beschermd via whitelist
    "fete",                          # FR: fête (normaliseert van fête) — designation/demission beschermd via whitelist
    "viering",                       # NL: vieringen, huldigingen, kampioenvieringen — nooit mandaatrelevant
    "principebeslissing",            # NL: principebeslissingen (sociale toelagen, aankopen) — samenwerkingsverbanden beschermd via whitelist
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
    "RUP",
    "GRUP",
    "PRUP",
    "PST",   # Programme Stratégique Transversal = FR equivalent van Meerjarenplan
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

# Beide termen moeten aanwezig zijn om te filteren.  Zo blijft "goedkeuring" op
# zich toegestaan, maar worden specifieke niet-mandaat-combinaties geblokkeerd.
COMPOUND_BLACKLIST = (
    # NL: goedkeuring van niet-mandaat-contexten
    ("goedkeuring", "ontwerpakte"),        # notariële grondaktes bij wegenwerken
    ("goedkeuring", "inname"),             # innames openbaar domein bij wegenwerken
    ("goedkeuring", "omgevingsvergunning"),# omgevingsvergunningen

    # FR: approbation van niet-mandaat-contexten
    # "approbation" alleen is mandaatrelevant (PV, AG, représentation);
    # onderstaande combinaties duiden altijd op financieel toezicht of operationele besluiten.
    ("approbation", "fabrique"),    # kerkfabriekrekeningen (tutelle)
    ("approbation", "exercice"),    # jaarrekeningen / comptes de l'exercice
    ("approbation", "tutelle"),     # tutelle spéciale (CPAS, kerkfabrieken)
    ("approbation", "dotation"),    # dotaties hulpverleningszone / brandweer
    ("approbation", "fourniture"),  # leveringen / aanbestedingen
    ("approbation", "redevance"),   # retributies / heffingen
    ("approbation", "cahier"),      # cahier des charges / bestekken
)

# Termen die een document altijd vrijwaren, ook als een andere regel zou matchen.
# Bevat zowel NL als FR ankertermen; normalize_filter_text() verwijdert accenten
# zodat "délégation" → "delegation" ook DELEGATION en délégation matcht.
WHITELIST_KEYWORDS = (
    "mandaat",
    "vertegenwoordiger",
    "afgevaardigde",
    "mandat",        # FR: mandaat (NL "mandaat" ≠ FR "mandat", aparte substring)
    "délégation",    # FR: delegatie van raadsbevoegdheden
    "représentant",  # FR: vertegenwoordiger
    "délégué",       # FR: afgevaardigde
    "nomination",    # FR/NL: benoeming bestuurder, commissaris, secretaris
    "designation",   # FR: aanduiding (désignation normaliseert naar designation)
    "commissie",     # NL: raadscommissies en andere commissies — samenstelling altijd mandaatrelevant
    "samenstelling", # NL: samenstelling van een raad/bestuur/commissie — altijd mandaatrelevant
    "aanstelling",             # NL: aanstelling van leden in comités, raden, besturen
    "demission",               # FR: démission uit raad/bestuur (normaliseert van démission) — vervanging altijd mandaatrelevant
    "samenwerkingsverbanden",  # NL: participatie in samenwerkingsverbanden — vertegenwoordigers aanduiden
    "algemene vergadering",    # NL/FR: algemene vergadering van intercommunale/vzw — altijd mandaatrelevant
)

BEKENDMAKING_KEEP_RE = re.compile(r"\b(notulen|zittingsverslag)\b", re.IGNORECASE)
# "motion" als zelfstandig woord = politieke motie (filter); deel van "promotion" = valse positief (bewaren)
_MOTION_WORD_RE = re.compile(r"(?<![a-z])motion(?![a-z])", re.IGNORECASE)
# "compte" zonder "rendu" = financiële rekening (kerkfabriek, CPAS, ASBL, …); met "rendu" = compte-rendu (vergaderverslag → KEEP)
COMPTES_KEEP_RE = re.compile(r"rendu", re.IGNORECASE)


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

    # Whitelist gaat voor: mandaat-relevante termen beschermen altijd.
    for term in WHITELIST_KEYWORDS:
        if normalize_filter_text(term) in normalized_name:
            return False

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

    if "compte" in normalized_name and not COMPTES_KEEP_RE.search(normalized_name):
        return True

    if _MOTION_WORD_RE.search(normalized_name) and "promotion" not in normalized_name:
        return True

    for terms in COMPOUND_BLACKLIST:
        if all(normalize_filter_text(t) in normalized_name for t in terms):
            return True

    return False


def should_keep_output_file(filename: str | Path) -> bool:
    """Gebruik deze predicate bij cleanup van scrape-output."""
    return not matches_blacklist(filename)


def should_consider_scrape_input(filename: str | Path) -> bool:
    """Striktere predicate voor nieuwe scrape-input: alleen PDFs en geen blacklist-hit."""
    return Path(filename).suffix.casefold() == ".pdf" and not matches_blacklist(filename)


def _matches_abbreviation(filename: str) -> bool:
    """Match afkortingen als losse tokens, ongeacht hoofdletters.

    Woordgrens-lookaround voorkomt dat afkortingen als substrings van langere
    woorden matchen (bijv. "lok" in "loket", "rup" in "rupture").
    """
    stem = Path(filename).stem
    for abbreviation in BLACKLIST_ABBREVIATIONS:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(abbreviation)}(?![A-Za-z0-9])"
        if re.search(pattern, stem, flags=re.IGNORECASE):
            return True
    return False
