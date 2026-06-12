"""
hernoem_besluiten.py — Hernoemt Besluit_N.pdf en Uittreksels_*.pdf naar beschrijvende namen.

Ondersteunde PDF-formaten (Besluit):
  Type 1 (uittreksel-stijl): bevat 'Betreft:' lijn
  Type 2 (besluit-stijl):    bevat besluitcode patroon YYYY_XXX_NNNNN

Ondersteunde PDF-formaten (Uittreksels):
  Datum en ID staan al in de bestandsnaam; titel wordt na de ledenlijst geëxtraheerd.
  Nieuw: Uittreksels_DD-MM-YYYY_ID_<titel>.pdf

Gebruik:
    uv run python hernoem_besluiten.py                         # dry run Besluit + Uittreksels
    uv run python hernoem_besluiten.py --rename                # effectief hernoemen
    uv run python hernoem_besluiten.py --gemeente Assenede     # 1 gemeente
    uv run python hernoem_besluiten.py --alleen-besluiten      # enkel Besluit_N.pdf
    uv run python hernoem_besluiten.py --alleen-uittreksels    # enkel Uittreksels_*.pdf
    uv run python hernoem_besluiten.py --log hernoem.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import fitz  # pymupdf

MAANDEN = {
    "januari": "01", "februari": "02", "maart": "03", "april": "04",
    "mei": "05", "juni": "06", "juli": "07", "augustus": "08",
    "september": "09", "oktober": "10", "november": "11", "december": "12",
    "janvier": "01", "février": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "août": "08",
    "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12",
}

CODE_RE    = re.compile(r'\d{4}_[A-Za-z]+(?:_[A-Za-z]+)*_\d+')
# Drie specifieke patronen, van meest naar minst specifiek:
#   "Zitting van maandag 26 januari 2026"
#   "Besluit van de burgemeester van donderdag 4 december 2025"
#   "Séance du lundi 12 mai 2025"
_DATUM_PATRONEN = [
    re.compile(r'(?:zitting|vergadering)\s+van\s+(?:\w+\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})', re.IGNORECASE),
    re.compile(r'besluit\s+van\s+de\s+\w+\s+van\s+\w+\s+(\d{1,2})\s+(\w+)\s+(\d{4})', re.IGNORECASE),
    re.compile(r'(?:séance|réunion)\s+du\s+\w+\s+(\d{1,2})\s+(\w+)\s+(\d{4})', re.IGNORECASE),
]
DATUM_RE = _DATUM_PATRONEN[0]  # behoud voor backwards-compat; gebruik _DATUM_PATRONEN in code
STOP_RE    = re.compile(
    r'^(Samenstelling|Aanwezig|Afwezig|Zijn eveneens|Het college|De raad|De burgemeester|'
    r'Gelet op|Overwegende|Vu |Considérant|Art\.|Artikel)',
    re.IGNORECASE
)
BETREFT_RE = re.compile(r'^(?:Betreft|Objet)\s*:\s*(.+)', re.IGNORECASE)
# "Betreft:" zonder titel op dezelfde regel — titel volgt op de regels erna
# (bv. Aalter: "Betreft:" / "Collegebeslissing" / "betreffende" / <titel>).
_BETREFT_KAAL_RE = re.compile(r'^(?:Betreft|Objet)\s*:?\s*$', re.IGNORECASE)
MAX_TITEL  = 90  # ruimer zodat titels minder afgekapt worden

# Type 3 (Besluit): publicatieformaat met aparte 'Besluit'-kop en statuswoord
# bv. Leuven/Turnhout/Lummen: "Besluit" / "Zitting van ..." / <titel> / "Goedgekeurd"
_BESLUIT_HEADER_RE = re.compile(r'^Besluit$', re.IGNORECASE)
_BESLUIT_STATUS_RE = re.compile(
    r'^(Goedgekeurd|Afgekeurd|Behandeld|Aangenomen|Verworpen|Geweigerd|'
    r'Aangehouden|Verdaagd|Ingetrokken|Kennis ?genomen|Uitgesteld)'
    r'(?:\s+met\s+eenparigheid\s+van\s+stemmen)?\.?$',
    re.IGNORECASE
)
_BESLUIT_KOP_SKIP_RE = re.compile(
    r'^(Zitting|Vergadering|Raad van|Gemeenteraad|Commissie|OCMW|AGM|'
    r'Schepencollege|College van|Vast bureau|Bijzonder comité)\b',
    re.IGNORECASE
)

# Generieke prefixen die niets toevoegen aan de titel
_GENERIEKE_PREFIXEN = re.compile(
    r'^(?:'
    r'(?:\w+beslissing|(?:\w+\s+)?besluit(?:\s+van\s+\w+)?)\s+(?:van\s+\w+\s+)?'
    r'(?:bureau|college|raad|burgemeester|schepenen)\s+betreffende\s+(?:de\s+|het\s+|een\s+)?|'
    r'collegebeslissing\s+betreffende\s+(?:de\s+|het\s+|een\s+)?|'
    r'burgemeesterbeslissing\s+betreffende\s+(?:de\s+|het\s+|een\s+)?|'
    r'beslissing\s+(?:van\s+(?:de\s+|het\s+)?\w[\w\s]+?\s+)?betreffende\s+(?:de\s+|het\s+|een\s+)?'
    r')',
    re.IGNORECASE
)


def _sanitize(tekst: str, max_len: int = MAX_TITEL) -> str:
    """Verwijder tekens die niet geldig zijn in bestandsnamen."""
    tekst = re.sub(r'[\\/*?:"<>|]', ' ', tekst)
    # Verwijder Unicode Private Use Area tekens (bv.  bullet uit Symbol-font)
    tekst = re.sub(r'[-]', '', tekst)
    tekst = re.sub(r'\s+', ' ', tekst).strip()
    return tekst[:max_len].rstrip()


def _strip_generiek(tekst: str) -> str:
    """Verwijder generieke beslissings-prefixen die niets toevoegen."""
    return _GENERIEKE_PREFIXEN.sub('', tekst).strip()


def _is_structuurregel(regel: str) -> bool:
    """True voor kop-/sectie-/labelregels die nooit deel uitmaken van een titel.

    "Samenstelling" als kale sectiekop (gevolgd door een ledenlijst) verschilt
    van "Samenstelling ..." als begin van een titel (bv. Leuven: "Samenstelling
    bijzonder comité voor de sociale dienst - ...") — enkel de korte, op
    zichzelf staande variant is een structuurregel.
    """
    if re.match(r'^Samenstelling\b', regel, re.IGNORECASE):
        return len(regel) <= 20
    return bool(
        STOP_RE.match(regel)
        or _SECTIE_SKIP_RE.match(regel)
        or _BESLUIT_STATUS_RE.match(regel)
        or _BESLUIT_KOP_SKIP_RE.match(regel)
        or re.match(r'^[\d./\s-]+$', regel)
    )


def _verzamel_titel_terug(regels, vanaf: int, ondergrens: int) -> list:
    """Verzamel aaneengesloten, over meerdere regels gewrapte titelregels die
    eindigen vlak vóór regel-index `vanaf` (bv. het statuswoord)."""
    blok = []
    j = vanaf - 1
    while j > ondergrens and len(blok) < 4:
        regel = regels[j]
        if _is_structuurregel(regel):
            break
        # Korte regels vlak ná een kop ("Zitting van ...") zijn doorgaans een
        # rubriek-/dienstlabel (bv. "Vrije Tijd"), geen titel-vervolg.
        if (len(regel) <= 12 and j > ondergrens
                and _BESLUIT_KOP_SKIP_RE.match(regels[j - 1])):
            break
        blok.insert(0, regel)
        j -= 1
    return blok if any(len(r) > 12 for r in blok) else []


def _verzamel_titel_vooruit(regels, vanaf: int, bovengrens: int):
    """Verzamel aaneengesloten, over meerdere regels gewrapte titelregels die
    beginnen vlak ná regel-index `vanaf` (bv. het statuswoord); stopt bij een
    besluitcode of structuurregel. Geeft (titelregels, code) terug."""
    blok = []
    code = ""
    j = vanaf + 1
    while j < bovengrens and len(blok) < 4:
        regel = regels[j]
        code_m = CODE_RE.search(regel)
        if code_m:
            stuk = regel[:code_m.start()].strip(' (-')
            if stuk:
                blok.append(stuk)
            code = code_m.group(0)
            break
        is_struct = _is_structuurregel(regel)
        if is_struct:
            # Uitzondering: samengestelde titels zoals "OCMW X - Meerjarenplan..."
            # beginnen met een instellingsnaam (_SECTIE_SKIP_RE) maar zijn geen
            # sectiescheiders — ze bevatten een inhoudvol deel ná het koppelteken.
            if _SECTIE_SKIP_RE.match(regel) and re.search(r' [-–] .{15,}', regel):
                is_struct = False
        if is_struct:
            break
        blok.append(regel)
        j += 1
    return (blok, code) if any(len(r) > 12 for r in blok) else ([], "")


def _datum_str(dag: str, maand: str, jaar: str) -> str:
    m = MAANDEN.get(maand.lower(), "00")
    return f"{jaar}-{m}-{dag.zfill(2)}"


_LEEG = {"datum": "", "code": "", "titel": "", "type": None}

# Patronen voor gedeeltelijke besluitcodes (over twee regels gesplitst),
# bv. "2025_MINTUS" + "AV_00019" bij Brugge OCMW.
_PARTIAL_CODE_RE = re.compile(r'^\d{4}_[A-Za-z]|^[A-Z]{2,}_\d+')


def _scan_titel_na_besluit_kop(regels: list, besluit_idx: int) -> list:
    """Scant vooruit ná de 'Besluit'-kop en geeft de eerste inhoudslijn(en) terug.

    Slaat datum-, service- en coderegelslabels over; stopt bij een stop-marker.
    Gebruik: statuswoord staat VÓÓR de 'Besluit'-kop (variant Brugge OCMW).
    """
    blok = []
    for r in regels[besluit_idx + 1 : besluit_idx + 25]:
        if STOP_RE.match(r) or _BESLUIT_STATUS_RE.match(r):
            break
        if _BESLUIT_KOP_SKIP_RE.match(r):
            continue
        if r.isupper() or len(r) <= 3:
            continue
        if re.match(r'^[\d./\s-]+$', r):
            continue
        if _PARTIAL_CODE_RE.match(r):
            continue
        if len(r) > 10:
            blok.append(r)
            if len(blok) >= 3:
                break
    return blok


def extraheer_info(pdf_pad: Path) -> dict:
    """
    Geeft {'datum': ..., 'code': ..., 'titel': ..., 'type': ...} terug.
    Lege strings als niet gevonden. 'type' is 1, 2, of None.
    Vangt alle uitzonderingen op — geeft altijd een dict terug.
    """
    try:
        doc = fitz.open(str(pdf_pad))
    except Exception as e:
        return {**_LEEG, "fout": f"open: {e}"}

    try:
        if doc.page_count == 0:
            return {**_LEEG, "fout": "leeg PDF"}
        tekst = doc[0].get_text()
    except Exception as e:
        return {**_LEEG, "fout": f"lezen: {e}"}
    finally:
        try:
            doc.close()
        except Exception:
            pass

    regels = [r.strip() for r in tekst.splitlines() if r.strip()]
    if not regels:
        # Gescand of leeg — geen tekst extraheerbaar
        return {**_LEEG, "fout": "geen tekst (gescand?)"}

    # Datum — probeer alle patronen op elke regel
    datum = ""
    for r in regels:
        if datum:
            break
        for patroon in _DATUM_PATRONEN:
            try:
                m = patroon.search(r)
                if m:
                    maand_nm = m.group(2).lower()
                    if maand_nm in MAANDEN:
                        datum = _datum_str(m.group(1), maand_nm, m.group(3))
                        break
            except Exception:
                continue

    # Type 2: besluitcode (YYYY_XXX_NNNNN) + titel op volgende regels
    for i, r in enumerate(regels):
        try:
            m = CODE_RE.search(r)
            if not m:
                continue
            code = m.group(0)
            titelregels = []
            for r2 in regels[i + 1:]:
                if _is_structuurregel(r2):
                    break
                titelregels.append(r2)
            titel = _sanitize(" ".join(titelregels))
            if titel:
                return {"datum": datum, "code": code, "titel": titel, "type": 2}
        except Exception:
            continue

    # Type 1: 'Betreft:' / 'Objet:' lijn
    for i, r in enumerate(regels):
        try:
            m = BETREFT_RE.match(r)
            if m:
                titelregels = [m.group(1).strip()]
            elif _BETREFT_KAAL_RE.match(r):
                titelregels = []
            else:
                continue
            for r2 in regels[i + 1:]:
                if STOP_RE.match(r2) or BETREFT_RE.match(r2):
                    break
                titelregels.append(r2)
            titel = _sanitize(_strip_generiek(" ".join(titelregels)))
            if titel:
                return {"datum": datum, "code": "", "titel": titel, "type": 1}
        except Exception:
            continue

    # Type 3: publicatieformaat met aparte 'Besluit'-kop + statuswoord
    # (Leuven/Turnhout/Lummen): titel staat vlak vóór of vlak ná het statuswoord.
    besluit_idx = next((i for i, r in enumerate(regels) if _BESLUIT_HEADER_RE.match(r)), -1)
    if besluit_idx >= 0:
        for i in range(besluit_idx + 1, min(besluit_idx + 15, len(regels))):
            if not _BESLUIT_STATUS_RE.match(regels[i]):
                continue

            titelregels = _verzamel_titel_terug(regels, i, besluit_idx)
            if titelregels:
                code = ""
            else:
                titelregels, code = _verzamel_titel_vooruit(regels, i, len(regels))
            titel_raw = " ".join(titelregels)

            titel = _sanitize(_strip_generiek(titel_raw))
            if titel:
                return {"datum": datum, "code": code, "titel": titel, "type": 4}
            break

        # Variant: statuswoord staat VÓÓR de 'Besluit'-kop (bv. Brugge MINTUS/OCMW).
        # In dat geval scannen we vooruit ná besluit_idx en slaan we
        # datum-, service- en coderegels over.
        if not any(
            _BESLUIT_STATUS_RE.match(regels[i])
            for i in range(besluit_idx + 1, min(besluit_idx + 15, len(regels)))
        ):
            status_voor = any(
                _BESLUIT_STATUS_RE.match(regels[j])
                for j in range(max(0, besluit_idx - 5), besluit_idx)
            )
            if status_voor:
                titelregels = _scan_titel_na_besluit_kop(regels, besluit_idx)
                if titelregels:
                    titel = _sanitize(_strip_generiek(" ".join(titelregels)))
                    if titel:
                        return {"datum": datum, "code": "", "titel": titel, "type": 5}

    return {**_LEEG, "datum": datum, "fout": "geen titelpatroon herkend"}


# Patronen voor ledenlijst-einde detectie in uittreksels
_LEDENLIJST_RE = re.compile(
    r'^(Aanwezig|Afwezig|Verontschuldigd|Dossierbeheerder|Aanwezigen|'
    r'Burgemeester|Schepenen?|Raadsleden?|Voorzitter|Algemeen directeur|'
    r'Présents?|Absents?|Excusés?)',
    re.IGNORECASE
)
_SECTIE_SKIP_RE = re.compile(
    r'^(Besprekingen en besluiten van|Besprekingen|Besluiten van|'
    r'Uittreksel uit|UITTREKSEL UIT|Provincie|Arrondissement|'
    r'GEMEENTE|STAD|OCMW|Gemeente|Stad)',
    re.IGNORECASE
)
_UITTREKSEL_STOP_RE = re.compile(
    r'^(Bevoegdheid|Motivering|Juridische grond|De gemeenteraad|De raad|'
    r'De burgemeester|Het college|De voorzitter|Gelet op|Art\.|Artikel|'
    r'Overwegingen?|Rechtsgr|Compétence|Motiv|Vu |Considérant|'
    r'Naam mandataris|Naam behandelend|Bevoegd voor|Wetgeving|Grondslag)',
    re.IGNORECASE
)
# Verwijzingszinnen ("Zie voorstel in het document ...") zijn nooit een titel —
# als de terugwaartse zoektocht zo'n regel raakt, zit ze in de verkeerde sectie
# (bv. Zuienkerke: "Advies" / "Zie voorstel in het document Toelichting" werd
# foutief als titel herkend terwijl de echte titel veel eerder stond, ná "4.").
_VERWIJZING_RE = re.compile(
    r'^(Zie\s+(voorstel|bijlage|advies|document|hierboven|hieronder|verder)|'
    r'Voir\s+(la\s+|le\s+)?(proposition|annexe|document|ci-dessus|ci-dessous)|'
    r'Cf\.|Zie\s+punt)',
    re.IGNORECASE
)
_HANDTEKENING_RE = re.compile(
    r'^(Elektronisch ondertekend|Digitaal ondertekend|Dit document is|'
    r'Verifieerbaar|Aangevraagd door|getekend op|Signed by|'
    r'Dit is een digitaal|Ondertekend door)',
    re.IGNORECASE
)
_AGENDANR_RE = re.compile(r'^\d+[A-Z]?\.\s+|^[A-Z]+\d+\.\s+')


# Woorden die aangeven dat een lijn nog tot de ledenlijst behoort.
# Negatieve lookbehind voor koppelteken zodat samengestelde woorden als
# "aangewezen-burgemeester" niet als ledenlijstregel worden herkend.
_ROL_WOORDEN_RE = re.compile(
    r'(?<!-)\b(burgemeester|schepen|raadslid|raadsleden|voorzitter|directeur|'
    r'secretaris|griffier|diensthoofd|bestuurssecretaris|klerk|'
    r'lid\s+vast\s+bureau|dossierbeheerder)\b',
    re.IGNORECASE
)
# Ledenlijst-NAAM-regel: "Jan Janssens, burgemeester" — rolwoord staat aan het
# einde ná een komma. In tegenstelling tot _ROL_WOORDEN_RE (die ook normale
# zinnen als "Besluit van de burgemeester bij hoogdringendheid ..." treft en
# zo titels afkapt), matcht dit alleen echte naamlijst-items.
_LEDENLIJST_NAAM_RE = re.compile(
    r',\s*(burgemeesters?|schepenen?|raadsled(?:en)?|voorzitters?|'
    r'algemeen directeur|secretaris|griffier|diensthoofd|'
    r'bestuurssecretaris|klerk|lid\s+vast\s+bureau)\s*[;,.]?\s*$',
    re.IGNORECASE
)
# LBLOD-specifieke sectie-separator
_BESPREKINGEN_RE = re.compile(r'^Besprekingen en besluiten van', re.IGNORECASE)


def extraheer_uittreksel_titel(pdf_pad: Path) -> dict:
    """
    Extraheert de titel uit een Uittreksels_DD-MM-YYYY_ID.pdf bestand.

    Strategie 1 (LBLOD): zoek 'Besprekingen en besluiten van het' separator,
                          neem regels direct daarna als titel.
    Strategie 2 (klassiek): vind het einde van alle Aanwezig/Afwezig-blokken,
                             sla regels met rolwoorden over, neem eerste inhoudslijn.
    """
    m = re.match(r'Uittreksels_(\d{2}-\d{2}-\d{4})_(\d+)', pdf_pad.stem, re.IGNORECASE)
    datum_bestand = ""
    id_bestand = ""
    if m:
        dag, maand, jaar = m.group(1).split("-")
        datum_bestand = f"{jaar}-{maand}-{dag}"
        id_bestand = m.group(2)

    try:
        doc = fitz.open(str(pdf_pad))
    except Exception as e:
        return {**_LEEG, "fout": f"open: {e}"}
    try:
        if doc.page_count == 0:
            return {**_LEEG, "fout": "leeg PDF"}
        tekst = doc[0].get_text()
    except Exception as e:
        return {**_LEEG, "fout": f"lezen: {e}"}
    finally:
        try:
            doc.close()
        except Exception:
            pass

    regels = [r.strip() for r in tekst.splitlines() if r.strip()]
    if not regels:
        return {**_LEEG, "fout": "geen tekst (gescand?)"}

    titelregels = []

    try:
        # --- Strategie 1: LBLOD separator 'Besprekingen en besluiten van' ---
        for i, r in enumerate(regels):
            if _BESPREKINGEN_RE.match(r):
                for r2 in regels[i + 1:]:
                    if _UITTREKSEL_STOP_RE.match(r2) or len(r2) < 5:
                        break
                    titelregels.append(r2)
                    if len(titelregels) >= 3:
                        break
                break

        # --- Strategie 2: agenda-nummerpaatroon (bijv. '15D. Bestuurlijk Beheer.') ---
        # Alleen na de ledenlijst, alleen op het begin of na een spatie (niet mid-paginanummer)
        if not titelregels:
            agendanr_re = re.compile(r'(?<!\d)(\d{1,3}[A-Z]\.\s+[A-Z][a-z]|\d{2,3}\.\s+[A-Z][a-z])')
            last_leden_idx2 = max(
                (i for i, r in enumerate(regels) if _LEDENLIJST_RE.match(r)), default=-1
            )
            zoek_vanaf = max(last_leden_idx2, 0)
            for r in regels[zoek_vanaf:]:
                if _UITTREKSEL_STOP_RE.match(r):
                    break
                m = agendanr_re.search(r)
                if m:
                    vanaf = r[m.start():]
                    titel_raw = _AGENDANR_RE.sub("", vanaf).strip()
                    if len(titel_raw) > 10 and not _HANDTEKENING_RE.match(titel_raw):
                        titelregels.append(titel_raw)
                        break

        # --- Strategie 0: eerste regel is "Categorie: Titel" (bv. Ichtegem LBLOD) ---
        # Sommige LBLOD-uittreksels beginnen direct met de beslissingstekst vóór
        # de formele "UITTREKSEL UIT..."-header en ledenlijst.
        if not titelregels and regels:
            m0 = re.match(r'^[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ\s]+:\s+(.{15,})$', regels[0])
            if m0 and not _SECTIE_SKIP_RE.match(regels[0]):
                titelregels = [m0.group(1)]

        # --- Strategie 3: klassiek uittreksel (na ledenlijst) ---
        if not titelregels:
            last_leden_idx = -1
            for i, r in enumerate(regels):
                # Echte ledenlijst-labels: korte standalone label (≤15 tekens of
                # eindigend op ':'), of een naamregel eindigend op ", rol".
                # Sluit body-tekst uit die toevallig eindigt op ';', zoals
                # "schepenen kan toevertrouwen;" (vals positief Berlare).
                if _LEDENLIJST_RE.match(r) and (
                    r.rstrip().endswith(':') or len(r) <= 15 or _LEDENLIJST_NAAM_RE.search(r)
                ):
                    last_leden_idx = i

            # Fallback: geen ledenlijst gevonden (bijv. burgemeesterbeslissing)
            # Begin dan na de document-headers (provincie, gemeente, datum)
            if last_leden_idx < 0:
                for i, r in enumerate(regels):
                    if not (_SECTIE_SKIP_RE.match(r) or r.isupper() or
                            re.match(r'^\d{1,2}\s+\w+\s+\d{4}$', r, re.IGNORECASE)):
                        last_leden_idx = max(i - 1, 0)
                        break

            if last_leden_idx >= 0:
                for r in regels[last_leden_idx + 1:]:
                    if _UITTREKSEL_STOP_RE.match(r):
                        break
                    if _LEDENLIJST_RE.match(r):
                        continue
                    if _SECTIE_SKIP_RE.match(r):
                        continue
                    if _ROL_WOORDEN_RE.search(r):
                        continue
                    if len(r) < 12:
                        continue
                    if _HANDTEKENING_RE.match(r):
                        continue
                    if r.startswith('(') and re.search(r'wet|decreet|besluit', r, re.IGNORECASE):
                        continue
                    # Korte ALL-CAPS regels zijn doorgaans sectiekoppen (bv. "SAMENSTELLING"),
                    # maar lange ALL-CAPS regels kunnen legitieme titels zijn (bv. Torhout).
                    if r.isupper() and len(r) <= 20:
                        continue
                    titelregels.append(r)
                    if len(titelregels) >= 3:
                        break

        # --- Strategie 4: terugwaartse zoektocht vanaf eerste stopmarkering ---
        # Vangt gevallen waar de ledenlijst-detectie faalt door valse-positieve
        # rolwoorden in de handtekeningstrook (bv. een alleenstaande regel
        # "burgemeester" helemaal onderaan het document, zoals bij Wielsbeke/
        # Mechelen) of een afwijkende lay-out tussen ledenlijst en titel.
        if not titelregels:
            # Niet zomaar de EERSTE stopmarkering nemen: een vroege ALL-CAPS
            # labelregel als "DE BURGEMEESTER" (documentkop, vóór de titel,
            # zoals bij Zelzate) matcht _UITTREKSEL_STOP_RE evengoed als de
            # echte na-titel-marker "HET COLLEGE," (Berlare) of "Bevoegdheid"
            # (Zonnebeke). Probeer daarom elke kandidaat op volgorde en gebruik
            # de eerste die een bruikbaar titelblok oplevert.
            kandidaten = [i for i, r in enumerate(regels) if _UITTREKSEL_STOP_RE.match(r)]
            for stop_idx in kandidaten:
                if stop_idx <= 0:
                    continue
                blok = []
                j = stop_idx - 1
                while j >= 0 and len(blok) < 4:
                    r = regels[j]
                    if (_LEDENLIJST_NAAM_RE.search(r) or _LEDENLIJST_RE.match(r)
                            or _SECTIE_SKIP_RE.match(r) or _HANDTEKENING_RE.match(r)
                            or _VERWIJZING_RE.match(r)):
                        break
                    if r.isupper():
                        # Structurele labelregel zoals "DE BURGEMEESTER" of
                        # "HET COLLEGE," die de titel omringt — overslaan,
                        # niet opnemen en niet stoppen. Andere ALL-CAPS regels
                        # (datumstempel, documentkop) markeren de grens van de
                        # documentkop-zone — daar stoppen we wel.
                        if _UITTREKSEL_STOP_RE.match(r):
                            j -= 1
                            continue
                        break
                    if len(r) < 12:
                        break
                    blok.insert(0, r)
                    j -= 1
                if blok:
                    titelregels = blok
                    break
    except Exception:
        pass

    if not titelregels:
        return {**_LEEG, "datum": datum_bestand, "fout": "geen titel na ledenlijst"}

    titel_raw = _AGENDANR_RE.sub("", " ".join(titelregels)).strip()
    titel = _sanitize(titel_raw)
    if not titel:
        return {**_LEEG, "datum": datum_bestand, "fout": "lege titel na sanitize"}

    return {"datum": datum_bestand, "code": id_bestand, "titel": titel, "type": 3}


def bepaal_nieuwe_naam_uittreksel(info: dict, oud: Path, gebruikte_namen: set) -> str | None:
    """
    Voegt de titel toe aan de bestaande Uittreksels_DD-MM-YYYY_ID naam.
    Uittreksels_16-06-2025_64483.pdf → Uittreksels_16-06-2025_64483_<titel>.pdf
    """
    titel = info["titel"]
    if not titel:
        return None

    # Houd het originele prefix (Uittreksels_DD-MM-YYYY_ID) intact
    m = re.match(r'(Uittreksels_[\d-]+_\d+)(?:_\d+)?', oud.stem, re.IGNORECASE)
    if not m:
        return None
    prefix = m.group(1)

    def _bouw(suffix: str = "") -> str:
        # Knip alléén de titel in (niet het samengestelde geheel), zodat het
        # prefix en een eventuele dedup-suffix nooit door de afkapping verdwijnen.
        vast = len(prefix) + 1 + (len(suffix) + 1 if suffix else 0)
        titel_kort = _sanitize(titel, max(MAX_TITEL - vast, 10))
        kern = f"{prefix}_{titel_kort}"
        if suffix:
            kern = f"{kern}_{suffix}"
        return kern + ".pdf"

    kandidaat = _bouw()

    if kandidaat.lower() == oud.name.lower():
        return None

    if kandidaat.lower() in gebruikte_namen:
        m2 = re.search(r'(\d+)$', oud.stem)
        suffix = m2.group(1) if m2 else oud.stem[-4:]
        kandidaat_alt = _bouw(suffix)
        if kandidaat_alt.lower() not in gebruikte_namen:
            kandidaat = kandidaat_alt

    return kandidaat


def bepaal_nieuwe_naam(info: dict, oud: Path, gebruikte_namen: set) -> str | None:
    """Geeft de nieuwe bestandsnaam (zonder pad) of None als niet hernoemd wordt."""
    titel = info["titel"]
    code  = info["code"]
    datum = info["datum"]

    if not titel:
        return None

    if code:
        basis = f"{code}_{titel}"
    elif datum:
        basis = f"{datum}_{titel}"
    else:
        basis = titel

    basis = _sanitize(basis)
    kandidaat = basis + ".pdf"

    if kandidaat.lower() == oud.name.lower():
        return None  # al correct

    # Conflictoplossing: gebruik het originele bestandsnummer als suffix
    # zodat "2026-01-26_vergunning_2" niet informatieloos is
    if kandidaat.lower() in gebruikte_namen:
        # Extraheer getal uit originele naam (Besluit_1007 → 1007)
        m = re.search(r'(\d+)', oud.stem)
        oud_nr = m.group(1) if m else oud.stem
        kandidaat_met_nr = f"{basis}_{oud_nr}.pdf"
        if kandidaat_met_nr.lower() not in gebruikte_namen:
            kandidaat = kandidaat_met_nr
        else:
            # Laatste redmiddel: sequentieel
            n = 2
            while kandidaat.lower() in gebruikte_namen:
                kandidaat = f"{basis}_{n}.pdf"
                n += 1

    return kandidaat


def main() -> None:
    sys.stdout.reconfigure(errors="replace")  # voorkomt crash op rare Unicode in bestandsnamen
    parser = argparse.ArgumentParser(description="Hernoem Besluit_N.pdf en Uittreksels_*.pdf.")
    parser.add_argument("--rename", action="store_true",
                        help="Effectief hernoemen (zonder: dry run)")
    parser.add_argument("--root", default="pdfs",
                        help="Root-map (standaard: pdfs)")
    parser.add_argument("--gemeente", default=None,
                        help="Beperk tot een specifieke gemeente")
    parser.add_argument("--log", default=None,
                        help="Schrijf log naar CSV")
    parser.add_argument("--alleen-besluiten", action="store_true",
                        help="Verwerk alleen Besluit_N.pdf")
    parser.add_argument("--alleen-uittreksels", action="store_true",
                        help="Verwerk alleen Uittreksels_*.pdf")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Map niet gevonden: {root}")
        sys.exit(1)

    zoekpad = root / args.gemeente if args.gemeente else root

    doe_besluiten    = not args.alleen_uittreksels
    doe_uittreksels  = not args.alleen_besluiten

    # BesluitenLijst-bestanden worden door de cleaner verwijderd — sla ze over.
    bestanden_besluit = [
        p for p in (sorted(zoekpad.rglob("Besluit*.pdf")) if doe_besluiten else [])
        if not re.match(r'^Besluitenlijst\b', p.name, re.IGNORECASE)
    ]
    bestanden_uittreksel  = sorted(zoekpad.rglob("Uittreksels_*.pdf")) if doe_uittreksels  else []
    # Sla Uittreksels die al een titel hebben over (naam bevat meer dan datum+ID)
    bestanden_uittreksel = [
        p for p in bestanden_uittreksel
        if re.match(r'^Uittreksels_[\d-]+_\d+(?:_\d+)?\.pdf$', p.name, re.IGNORECASE)
    ]

    bestanden = bestanden_besluit + bestanden_uittreksel
    totaal    = len(bestanden)

    modus = "HERNOEMEN" if args.rename else "DRY RUN"
    print(f"\n=== hernoem_besluiten.py [{modus}] ===")
    print(f"Bestanden : {totaal:,}")
    print()

    if totaal == 0:
        print("Geen Besluit*.pdf bestanden gevonden.")
        return

    logregels = []
    hernoemd = 0
    overgeslagen = 0
    geen_titel = 0
    fouten = 0
    verwerkt = 0

    # Verzamel bestaande namen per map om conflicten te detecteren
    namen_per_map: dict[Path, set] = {}

    def _gemeente(pad: Path) -> str:
        parts = list(pad.parts)
        return parts[parts.index("pdfs") + 1] if "pdfs" in parts else ""

    for pad in bestanden:
        verwerkt += 1
        if verwerkt % 500 == 0:
            print(f"  {verwerkt:>6}/{totaal}  verwerkt ({hernoemd} hernoemd, {geen_titel} geen titel, {fouten} fouten)...", flush=True)

        if pad.parent not in namen_per_map:
            try:
                namen_per_map[pad.parent] = {f.name.lower() for f in pad.parent.iterdir() if f.is_file()}
            except Exception:
                namen_per_map[pad.parent] = set()

        gebruikte_namen = namen_per_map[pad.parent]

        # Kies extractor op basis van bestandsnaam
        is_uittreksel = pad.name.lower().startswith("uittreksels_")
        info = extraheer_uittreksel_titel(pad) if is_uittreksel else extraheer_info(pad)

        if not info["titel"]:
            reden = info.get("fout", "onbekend")
            geen_titel += 1
            logregels.append({
                "gemeente": _gemeente(pad), "oud": pad.name, "nieuw": "",
                "status": f"geen_titel: {reden}",
                "type": info["type"], "datum": info["datum"], "code": info["code"],
            })
            continue

        nieuwe_naam = (bepaal_nieuwe_naam_uittreksel if is_uittreksel else bepaal_nieuwe_naam)(
            info, pad, gebruikte_namen
        )
        if nieuwe_naam is None:
            overgeslagen += 1
            logregels.append({
                "gemeente": _gemeente(pad), "oud": pad.name, "nieuw": pad.name,
                "status": "ongewijzigd",
                "type": info["type"], "datum": info["datum"], "code": info["code"],
            })
            continue

        # Registreer nieuwe naam in de set om toekomstige conflicten te voorkomen
        gebruikte_namen.discard(pad.name.lower())
        gebruikte_namen.add(nieuwe_naam.lower())

        if args.rename:
            try:
                pad.rename(pad.parent / nieuwe_naam)
                status = "hernoemd"
                hernoemd += 1
            except Exception as e:
                fouten += 1
                status = f"fout: {e}"
                print(f"  [FOUT] {pad.name}: {e}", flush=True)
        else:
            status = "hernoemd (dry)"
            hernoemd += 1

        print(f"  [{status}] {pad.name[:40]:<40} -> {nieuwe_naam}")
        logregels.append({
            "gemeente": pad.parts[list(pad.parts).index("pdfs") + 1] if "pdfs" in pad.parts else "",
            "oud": pad.name, "nieuw": nieuwe_naam, "status": status,
            "type": info["type"], "datum": info["datum"], "code": info["code"],
        })

    print(f"\nResultaat:")
    print(f"  Hernoemd       : {hernoemd:>6,}")
    print(f"  Ongewijzigd    : {overgeslagen:>6,}")
    print(f"  Geen titel     : {geen_titel:>6,}")
    if fouten:
        print(f"  Fouten         : {fouten:>6,}")
    print(f"  Totaal         : {totaal:>6,}")

    if not args.rename and hernoemd > 0:
        print(f"\nDry run - niets hernoemd. Voer opnieuw uit met --rename.")

    if args.log:
        with open(args.log, "w", newline="", encoding="utf-8") as f:
            velden = ["gemeente", "oud", "nieuw", "status", "type", "datum", "code"]
            w = csv.DictWriter(f, fieldnames=velden)
            w.writeheader()
            # Zorg dat alle rijen dezelfde velden hebben (fout-veld weggooien uit log)
            for r in logregels:
                r.pop("fout", None)
            w.writerows(logregels)
        print(f"  Log geschreven naar: {args.log}")

    print()


if __name__ == "__main__":
    main()
