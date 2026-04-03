"""
Gegroepeerde scraper — verwerkt alle bekende gemeenten per websitetype
gelezen uit simba-source.csv.

Ondersteunde types:
  smartcities      raadpleeg-*.onlinesmartcities.be · besluitvorming.*.be  (Playwright)
  cipalschaubroeck *-echo.cipalschaubroeck.be/raadpleegomgeving · *.csecho.be  (REST API)
  meetingburger    *.meetingburger.net                                      (REST)
  ingelmunster     www.ingelmunster.be/db_files_2                            (HTML/PDF links)
  lblod            lblod.*.be                                               (HTML/PDF)
  ibabs            *.bestuurlijkeinformatie.nl                              (HTML/PDF)
  vlaamsbrabant    bestuur.vlaamsbrabant.be                                 (HTML)
  hainaut          www.hainaut.be — Conseil provincial                      (HTML)
  luxemburg        province.luxembourg.be — Conseil provincial              (HTML)
  deliberations    deliberations.be · conseilcommunal.be                    (HTML/PDF)
  irisnet          publi.irisnet.be                                         (geen scraper)
  icordis          *.be/file/download — Icordis CMS (LCP nv)               (HTML/PDF)
  gelinktnotuleren publicatie.gelinkt-notuleren.vlaanderen.be               (HTML)
  pubcon           app-pubcon-*.azurewebsites.net/LBLOD                     (HTML/PDF)
  wordpress        */wp-content/uploads* · www.st.vith.be                   (HTML/PDF)
  docodis          *.be/AC-file/docodis — Docodis documentbeheer CMS        (HTML/PDF)
  linkebeek        *.be/download.ashx — LCP agenda-notulen portaal          (HTML/PDF)
  drupal           *.be/sites/*/files of *.be/system/files — Drupal direct PDF   (HTML/PDF)
  imio             iMio/Plone gemeentesites met procès-verbaux              (HTML/PDF)
  overig           Andere bekende sites                                     (handmatig)
  leeg             Geen URL beschikbaar

Gebruik:
    uv run python scraper_groep.py                              # interactieve TUI
    uv run python scraper_groep.py --toon-groepen               # groepsoverzicht
    uv run python scraper_groep.py --type smartcities --alle --maanden 6
    uv run python scraper_groep.py --type cipalschaubroeck --orgaan "Gemeenteraad" --maanden 12
    uv run python scraper_groep.py --type meetingburger --alle --maanden 3
    uv run python scraper_groep.py --gemeente Aalst --alle --maanden 6
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

import questionary
from questionary import Style

CSV_PAD = Path(__file__).parent / "simba-source.csv"
SCRIPT_DIR = Path(__file__).parent

console = Console()


def _url_syntax_ok(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

# ---------------------------------------------------------------------------
# Configuratie per websitetype
# ---------------------------------------------------------------------------

TYPES: dict[str, dict] = {
    "smartcities": {
        "label": "OnlineSmartCities / Besluitvorming",
        "beschrijving": "raadpleeg-*.onlinesmartcities.be · besluitvorming.*.be",
        "scraper": "scraper_onlinesmartcities.py",
        "heeft_browser": True,
        "heeft_agendapunten": True,
        "kleur": "cyan",
    },
    "cipalschaubroeck": {
        "label": "CipalSchaubroeck Echo / CSEcho",
        "beschrijving": "*-echo.cipalschaubroeck.be/raadpleegomgeving · *.csecho.be",
        "scraper": "scraper_menen.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "kleur": "green",
    },
    "meetingburger": {
        "label": "MeetingBurger",
        "beschrijving": "*.meetingburger.net",
        "scraper": "scraper_ranst.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "blue",
    },
    "ingelmunster": {
        "label": "Ingelmunster bekendmakingen",
        "beschrijving": "www.ingelmunster.be/db_files_2 (TYPO3)",
        "scraper": "scraper_drupal.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_blue",
    },
    "lblod": {
        "label": "LBLOD / Linked Open Data",
        "beschrijving": "lblod.*.be — LBLODWeb publicatieportaal",
        "scraper": "scraper_lblod.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "magenta",
    },
    "ibabs": {
        "label": "iBabs Publieksportaal",
        "beschrijving": "*.bestuurlijkeinformatie.nl",
        "scraper": "scraper_ibabs.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "kleur": "bright_cyan",
    },
    "vlaamsbrabant": {
        "label": "Provincie Vlaams-Brabant",
        "beschrijving": "bestuur.vlaamsbrabant.be",
        "scraper": "scraper_vlaamsbrabant.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "kleur": "bright_green",
    },
    "provantwerpen": {
        "label": "Provincie Antwerpen",
        "beschrijving": "provincieantwerpen.be — provincieraad verslagen & notulen",
        "scraper": "scraper_provantwerpen.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_green",
    },
    "hainaut": {
        "label": "Provincie Henegouwen",
        "beschrijving": "hainaut.be — Conseil provincial (ODJ, délibérations, communiqués)",
        "scraper": "scraper_waalse_provincies.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_green",
    },
    "luxemburg": {
        "label": "Provincie Luxemburg",
        "beschrijving": "province.luxembourg.be — Conseil provincial (ODJ & procès-verbaux)",
        "scraper": "scraper_waalse_provincies.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_green",
    },
    "brabantwallon": {
        "label": "Provincie Waals-Brabant",
        "beschrijving": "brabantwallon.be — Conseil provincial (procès-verbaux)",
        "scraper": "scraper_waalse_provincies.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_green",
    },
    "deliberations": {
        "label": "Deliberations.be / ConseilCommunal.be",
        "beschrijving": "deliberations.be · conseilcommunal.be — Waalse gemeenten",
        "scraper": "scraper_deliberations.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "kleur": "bright_magenta",
    },
    "irisnet": {
        "label": "Irisnet (Brussel)",
        "beschrijving": "publi.irisnet.be — Brusselse gemeenten",
        "scraper": "scraper_irisnet.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "brussel": {
        "label": "Stad Brussel",
        "beschrijving": "bruxelles.be / brussel.be — ordres du jour & PV",
        "scraper": "scraper_brussel.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "forest": {
        "label": "Forest / Vorst",
        "beschrijving": "forest.brussels — conseil communal publicaties",
        "scraper": "scraper_drupal.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "molenbeek": {
        "label": "Molenbeek-Saint-Jean",
        "beschrijving": "molenbeek.irisnet.be — conseil communal",
        "scraper": "scraper_molenbeek.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "schaerbeek": {
        "label": "Schaerbeek / Schaarbeek",
        "beschrijving": "1030.be — notulen gemeenteraad via sitemap",
        "scraper": "scraper_schaerbeek.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "icordis": {
        "label": "Icordis CMS (LCP nv)",
        "beschrijving": "*.be/file/download — Vlaamse gemeenten op Icordis CMS",
        "scraper": "scraper_icordis.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_green",
    },
    "gelinktnotuleren": {
        "label": "Gelinkt Notuleren Publicatie",
        "beschrijving": "publicatie.gelinkt-notuleren.vlaanderen.be — Vlaams centraal publicatieplatform",
        "scraper": "scraper_gelinktnotuleren.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_magenta",
    },
    "docodis": {
        "label": "Docodis",
        "beschrijving": "*.be/AC-file/docodis — Docodis documentbeheer CMS",
        "scraper": "scraper_docodis.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "magenta",
    },
    "linkebeek": {
        "label": "LCP agenda-notulen portaal",
        "beschrijving": "*/download.ashx — LCP gemeenteportaal (Linkebeek)",
        "scraper": "scraper_linkebeek.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "cyan",
    },
    "drupal": {
        "label": "Drupal directe PDFs",
        "beschrijving": "*.be/sites/*/files — Drupal-gemeenten met directe PDF-links",
        "scraper": "scraper_drupal.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "green",
    },
    "wordpress": {
        "label": "WordPress / Plone gemeenten",
        "beschrijving": "*/wp-content/uploads* · www.st.vith.be · Waalse Plone-gemeenten — WordPress/Plone",
        "scraper": "scraper_wordpress.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "blue",
    },
    "idelibe": {
        "label": "iDélibé (conseilcommunal.be)",
        "beschrijving": "conseilcommunal.be/commune/{id} — 39 Waalse gemeenten (PV + notes de synthèse)",
        "scraper": "scraper_idelibe.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_cyan",
    },
    "pubcon": {
        "label": "Pubcon (Tobibus LBLOD)",
        "beschrijving": "app-pubcon-*.azurewebsites.net/LBLOD — Pubcon gemeenten",
        "scraper": "scraper_pubcon.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_blue",
    },
    "ixelles": {
        "label": "Ixelles / Elsene",
        "beschrijving": "www.ixelles.be — conseil communal ODJ & PV",
        "scraper": "scraper_ixelles.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "bright_yellow",
    },
    "imio": {
        "label": "iMio/Plone gemeentesites",
        "beschrijving": "Eigen iMio/Plone-site met procès-verbaux — Waalse gemeenten",
        "scraper": "scraper_imio.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "heeft_organen": False,
        "kleur": "bright_magenta",
    },
    "overig": {
        "label": "Overig (aangepaste site)",
        "beschrijving": "Diverse sites — geen gestandaardiseerde scraper beschikbaar",
        "scraper": None,
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "yellow",
    },
    "leeg": {
        "label": "Geen URL",
        "beschrijving": "URL niet opgegeven in de bronlijst",
        "scraper": None,
        "heeft_browser": False,
        "heeft_agendapunten": False,
        "kleur": "red",
    },
}

STIJL = Style([
    ("qmark",        "fg:#00b4d8 bold"),
    ("question",     "bold"),
    ("answer",       "fg:#00b4d8 bold"),
    ("pointer",      "fg:#00b4d8 bold"),
    ("highlighted",  "fg:#00b4d8 bold"),
    ("selected",     "fg:#06d6a0"),
    ("separator",    "fg:#888888"),
    ("instruction",  "fg:#888888 italic"),
])


# ---------------------------------------------------------------------------
# iMio/Plone gemeenten — scraper_imio.py via hostname
_IMIO_HOSTS: frozenset[str] = frozenset({
    "www.viroinval.be", "www.couvin.be", "www.herstal.be", "www.burdinne.be",
    "www.andenne.be", "www.arlon.be", "www.blegny.be", "www.chaumont-gistoux.be",
    "www.daverdisse.be", "www.estinnes.be", "www.froidchapelle.be", "www.gerpinnes.be",
    "www.grace-hollogne.be", "www.heron.be", "www.honnelles.be", "www.jalhay.be",
    "www.jurbise.be", "www.meix-devant-virton.be", "www.mettet.be", "www.paliseul.be",
    "www.philippeville.be", "www.quaregnon.be", "www.saint-ghislain.be",
    "www.thimister-clermont.be", "www.thuin.be", "www.wasseiges.be",
    "www.clavier.be", "www.braine-lalleud.be", "www.villedefontaine.be",
    "www.lahulpe.be", "www.manage-commune.be",
})

# Waalse WordPress/Plone-gemeenten — scraper_wordpress.py via hostname
_WAALSE_WP_HOSTS: frozenset[str] = frozenset({
    "www.bernissart.be", "www.floreffe.be", "www.waterloo.be",
    "www.fernelmont.be", "www.chievres.be", "www.verlaine.be",
    "www.brugelette.be", "www.fosses-la-ville.be", "www.pecq.be",
    "www.herbeumont.be", "www.lalouviere.be", "rumes-online.be",
    "www.antoing.net", "www.ans-ville.be", "www.aubange.be",
    "www.burdinne.be",
    "www.crisnee.be",
    "www.gesves.be",
    "montdelenclus.be",
    "www.orp-jauche.be",
    "www.trooz.be",
    "www.vaux-sur-sure.be",
    "www.hastiere.be",
    "www.pontacelles.be",
    "www.province.namur.be",
    "www.courcelles.eu",
})

# iDélibé commune ID's (www.conseilcommunal.be/commune/{id})
_IDELIBE_COMMUNE_IDS: frozenset[int] = frozenset({
    2, 8, 9, 10, 12, 13, 14, 16, 17, 18, 22, 26, 28, 29, 37, 40,
    41, 44, 46, 53, 58, 63, 64, 65, 66, 67, 68, 69, 70, 72, 78,
    80, 83, 92, 93, 97, 98, 99, 104,
})

# URL-type detectie
# ---------------------------------------------------------------------------

def detecteer_type(url: str) -> str:
    """Classificeer een URL naar een bekend websitetype."""
    if not url or not url.strip():
        return "leeg"
    u = url.lower()
    if "onlinesmartcities.be" in u:
        return "smartcities"
    if re.search(r"(besluitvorming\.|ebesluitvorming\.|ebesluit\.)", u):
        return "smartcities"
    if "cipalschaubroeck.be" in u or "csecho.be" in u:
        return "cipalschaubroeck"
    if "meetingburger.net" in u:
        return "meetingburger"
    if "www.ingelmunster.be/db_files_2" in u:
        return "ingelmunster"
    if re.search(r"\blblod\.", u):
        return "lblod"
    if "bestuurlijkeinformatie.nl" in u:
        return "ibabs"
    if "bestuur.vlaamsbrabant.be" in u:
        return "vlaamsbrabant"
    if "provincieantwerpen.be" in u and "provincieraad" in u:
        return "provantwerpen"
    if "www.hainaut.be" in u:
        return "hainaut"
    if "province.luxembourg.be" in u:
        return "luxemburg"
    if "brabantwallon.be" in u:
        return "brabantwallon"
    # iDélibé must come before the broad deliberations check (both use conseilcommunal.be)
    m = re.search(r"conseilcommunal\.be/commune/(\d+)", u)
    if m and int(m.group(1)) in _IDELIBE_COMMUNE_IDS:
        return "idelibe"
    if "deliberations.be" in u or "conseilcommunal.be" in u:
        return "deliberations"
    if "publi.irisnet.be" in u:
        return "irisnet"
    if "bruxelles.be" in u or "brussel.be" in u:
        return "brussel"
    if "forest.brussels" in u:
        return "forest"
    if "molenbeek.irisnet.be" in u:
        return "molenbeek"
    if "1030.be" in u:
        return "schaerbeek"
    if "/file/download" in u:
        return "icordis"
    if "publicatie.gelinkt-notuleren.vlaanderen.be" in u:
        return "gelinktnotuleren"
    if "app-pubcon-" in u and "azurewebsites.net" in u:
        return "pubcon"
    if "/wp-content/uploads" in u or "www.st.vith.be" in u or "/app/uploads" in u or "/fileadmin/gemeinde_amel" in u or "/pv-et-resumes-du-conseil" in u or "@@folder_listing" in u:
        return "wordpress"
    # iMio/Plone gemeenten op hostname (vóór _WAALSE_WP_HOSTS check)
    if urlparse(url).netloc.lower() in _IMIO_HOSTS:
        return "imio"
    # Waalse WordPress/Plone-gemeenten op hostname
    if urlparse(url).netloc.lower() in _WAALSE_WP_HOSTS:
        return "wordpress"
    if "provincedeliege.be" in u:
        return "drupal"
    if re.search(r"/sites/[^/]+/files", u) or "/system/files" in u:
        return "drupal"
    if "/ac-file/docodis" in u:
        return "docodis"
    if "/download.ashx" in u:
        return "linkebeek"
    if "www.ixelles.be" in u:
        return "ixelles"
    return "overig"


def extraheer_base_url(url: str) -> str:
    """Geef schema + netloc terug (zonder trailing slash)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


# ---------------------------------------------------------------------------
# CSV lezen + groeperen
# ---------------------------------------------------------------------------

def lees_csv() -> list[dict]:
    """Lees simba-source.csv en geef een lijst van gemeente-dicts terug."""
    gemeenten: list[dict] = []
    with open(CSV_PAD, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader, None)
        if not header or len(header) < 2:
            console.print("[red]CSV-header ontbreekt of heeft te weinig kolommen[/red]")
            return gemeenten
        for lijn_nr, rij in enumerate(reader, start=2):
            gemeente = rij[0].strip() if len(rij) > 0 else ""
            url = rij[1].strip() if len(rij) > 1 else ""
            if not gemeente:
                console.print(f"[yellow]Lijn {lijn_nr}: lege gemeentenaam, rij overgeslagen[/yellow]")
                continue
            if len(rij) < 2:
                console.print(f"[yellow]Lijn {lijn_nr}: te weinig kolommen, rij overgeslagen[/yellow]")
                continue
            if url and not _url_syntax_ok(url):
                console.print(f"[yellow]Lijn {lijn_nr}: ongeldige URL-syntax voor {gemeente}, rij overgeslagen[/yellow]")
                continue
            type_ = detecteer_type(url)
            base_url = extraheer_base_url(url) if url else ""
            gemeenten.append({
                "gemeente": gemeente,
                "url": url,
                "type": type_,
                "base_url": base_url,
            })
    return gemeenten


def groepeer(gemeenten: list[dict]) -> dict[str, list[dict]]:
    groepen: dict[str, list[dict]] = {t: [] for t in TYPES}
    for g in gemeenten:
        groepen[g["type"]].append(g)
    return groepen


# ---------------------------------------------------------------------------
# Bouwen van subproces-commando's
# ---------------------------------------------------------------------------

def sanitize_slug(naam: str) -> str:
    """Zet een gemeentenaam om naar een bestandssysteem-veilige slug."""
    naam = naam.replace(" ", "_").replace("/", "_").replace("'", "")
    naam = re.sub(r"[^a-zA-Z0-9_\-]", "_", naam)
    naam = re.sub(r"_+", "_", naam)
    return naam.strip("_")[:60] or "gemeente"


def bouw_commando(
    gemeente: dict,
    orgaan: str | None,
    maanden: int,
    output_basis: str,
    doc_filter: str | None,
    agendapunten: bool,
    zichtbaar: bool,
) -> list[str] | None:
    """
    Stel het subprocescommando samen voor één gemeente.
    Geeft None terug als het type geen scraper heeft.
    """
    type_ = gemeente["type"]
    config = TYPES[type_]
    if config["scraper"] is None:
        return None

    scraper = config["scraper"]
    slug = sanitize_slug(gemeente["gemeente"])
    output_pad = str(Path(output_basis) / slug)

    cmd = ["uv", "run", "python", scraper]

    if type_ == "ibabs":
        # scraper_ibabs.py kent geen --base-url; de gemeente wordt opgezocht via --gemeente (naam/slug)
        cmd += ["--gemeente", gemeente["gemeente"]]
        if orgaan:
            cmd += ["--orgaan", orgaan]
    else:
        # Voor scrapers waar de gemeente-identiteit in het URL-pad zit (bijv. deliberations.be/{slug}
        # of publicatie.gelinkt-notuleren.vlaanderen.be/{gemeente}/{classificatie}),
        # is de volledige URL nodig; voor alle andere scrapers volstaat base_url (schema+netloc).
        _volledige_url_types = {"deliberations", "gelinktnotuleren"}
        cmd += ["--base-url", gemeente["url"] if type_ in _volledige_url_types else gemeente["base_url"]]

        if type_ == "imio":
            # scraper_imio.py heeft geen --orgaan/--alle (organen-stijl); enkel --base-url + --maanden
            pass
        # irisnet heeft één scraper voor alle Brusselse gemeenten; --alle zou alle 10 scrapen.
        # Geef dus altijd --gemeente mee zodat enkel de gevraagde gemeente gescraped wordt.
        elif type_ == "irisnet":
            if orgaan:
                cmd += ["--orgaan", orgaan]
            cmd += ["--gemeente", gemeente["gemeente"]]
        elif orgaan:
            cmd += ["--orgaan", orgaan]
        else:
            cmd += ["--alle"]

    cmd += ["--maanden", str(maanden)]
    cmd += ["--output", output_pad]

    if doc_filter:
        cmd += ["--document-filter", doc_filter]
    if agendapunten and config["heeft_agendapunten"]:
        cmd += ["--agendapunten"]
    if zichtbaar and config["heeft_browser"]:
        cmd += ["--zichtbaar"]

    return cmd


# ---------------------------------------------------------------------------
# Batch scrapen
# ---------------------------------------------------------------------------

def scrape_batch(
    gemeenten: list[dict],
    orgaan: str | None,
    maanden: int,
    output_basis: str,
    doc_filter: str | None = None,
    agendapunten: bool = False,
    zichtbaar: bool = False,
    pauze: float = 2.0,
) -> None:
    """Doorloop alle gemeenten en voer de juiste scraper uit voor elk."""
    totaal = len(gemeenten)
    geslaagd = 0
    overgeslagen = 0
    mislukt = 0

    console.print(Rule(
        f"[bold cyan]Batch scrapen — {totaal} gemeente(n)[/bold cyan]",
        style="cyan",
    ))
    console.print()

    for idx, gemeente in enumerate(gemeenten, 1):
        naam = gemeente["gemeente"]
        type_ = gemeente["type"]
        url = gemeente["url"]

        console.print(
            f"[bold cyan][{idx}/{totaal}][/bold cyan] "
            f"[bold]{naam}[/bold]  [dim]{url or '(geen URL)'}[/dim]"
        )

        cmd = bouw_commando(
            gemeente, orgaan, maanden, output_basis,
            doc_filter, agendapunten, zichtbaar,
        )

        if cmd is None:
            console.print(
                f"  [yellow][!] Type '{type_}' heeft geen scraper -- overgeslagen.[/yellow]"
            )
            overgeslagen += 1
            continue

        console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                cwd=str(SCRIPT_DIR),
            )
            assert proc.stdout is not None
            for lijn in proc.stdout:
                try:
                    console.print(f"  {lijn}", end="", markup=False, highlight=False)
                except Exception:
                    sys.stdout.buffer.write(f"  {lijn}".encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
            proc.wait()
            if proc.returncode == 0:
                geslaagd += 1
            else:
                console.print(f"  [red][FOUT] Exitcode {proc.returncode}[/red]")
                mislukt += 1
        except Exception as e:
            console.print(f"  [red][FOUT] Bij starten scraper: {e}[/red]")
            mislukt += 1

        if idx < totaal:
            time.sleep(pauze)

    console.print()
    console.print(Rule("[bold]Resultaat[/bold]", style="dim"))
    console.print(f"  Geslaagd:     [green]{geslaagd}[/green]")
    console.print(f"  Overgeslagen: [yellow]{overgeslagen}[/yellow]")
    console.print(f"  Mislukt:      [red]{mislukt}[/red]")
    console.print()


# ---------------------------------------------------------------------------
# Weergave: groepsoverzicht
# ---------------------------------------------------------------------------

def toon_groepen(gemeenten: list[dict]) -> None:
    """Toon een overzichtstabel van alle groepen en hun gemeenten."""
    groepen = groepeer(gemeenten)
    totaal_met_url = sum(1 for g in gemeenten if g["url"])

    console.print()
    console.print(Panel(
        f"[bold cyan]simba-source.csv — Groepsoverzicht per websitetype[/bold cyan]\n"
        f"[dim]{len(gemeenten)} gemeenten · {totaal_met_url} met URL[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()

    for type_key, config in TYPES.items():
        leden = groepen[type_key]
        if not leden:
            continue

        kleur = config["kleur"]
        scraper_label = (
            f"[green][OK] {config['scraper']}[/green]"
            if config["scraper"]
            else "[red]geen scraper[/red]"
        )

        tabel = Table(
            box=box.SIMPLE_HEAD,
            border_style="dim",
            show_header=True,
            padding=(0, 1),
        )
        tabel.add_column("Gemeente", style="bold", no_wrap=True, min_width=28)
        tabel.add_column("URL", style="dim")

        for g in leden:
            tabel.add_row(g["gemeente"], g["url"] or "[dim italic]—[/dim italic]")

        titel = (
            f"[bold {kleur}]{config['label']}[/bold {kleur}]"
            f"  [dim]({len(leden)} gemeente(n))[/dim]"
            f"  {scraper_label}"
        )
        console.print(Panel(tabel, title=titel, border_style=kleur))
        console.print()


# ---------------------------------------------------------------------------
# Interactieve TUI
# ---------------------------------------------------------------------------

def banner() -> None:
    console.print()
    console.print(Panel(
        "[bold cyan]Gegroepeerde Scraper[/bold cyan]\n"
        "[dim]Batch-download van gemeenten per websitetype (simba-source.csv)[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


def tui_main(gemeenten: list[dict]) -> None:
    """Interactieve TUI voor het configureren en starten van een batch-scrape."""
    banner()
    groepen = groepeer(gemeenten)

    # ── Kies websitetype ──────────────────────────────────────────────────
    scraapbare_types = [t for t, c in TYPES.items() if c["scraper"] is not None]
    totaal_scraapbaar = sum(len(groepen[t]) for t in scraapbare_types)
    keuzes_type = [
        questionary.Choice(
            title=f"★  Alle types — {totaal_scraapbaar} gemeente(n)",
            value="__alle_types__",
        )
    ] + [
        questionary.Choice(
            title=f"{TYPES[t]['label']}  ({len(groepen[t])} gemeente(n))  — {TYPES[t]['beschrijving']}",
            value=t,
        )
        for t in scraapbare_types
    ]

    gekozen_type = questionary.select(
        "Welk websitetype wil je scrapen?",
        choices=keuzes_type,
        style=STIJL,
    ).ask()
    if not gekozen_type:
        return

    # ── Kies gemeenten ────────────────────────────────────────────────────
    if gekozen_type == "__alle_types__":
        te_verwerken = [g for t in scraapbare_types for g in groepen[t]]
        heeft_agendapunten_optie = any(TYPES[t]["heeft_agendapunten"] for t in scraapbare_types)
        heeft_browser_optie = any(TYPES[t]["heeft_browser"] for t in scraapbare_types)
        type_label = f"Alle types ({len(te_verwerken)} gemeenten)"
    else:
        leden = groepen[gekozen_type]
        config = TYPES[gekozen_type]

        keuze_selectie = questionary.select(
            f"Welke {config['label']}-gemeenten verwerken?",
            choices=[
                questionary.Choice(f"Alle {len(leden)} gemeenten", "alle"),
                questionary.Choice("Kies een selectie", "kies"),
            ],
            style=STIJL,
        ).ask()
        if not keuze_selectie:
            return

        if keuze_selectie == "kies":
            gekozen_namen = questionary.checkbox(
                "Selecteer gemeenten:",
                choices=[g["gemeente"] for g in leden],
                style=STIJL,
            ).ask()
            if not gekozen_namen:
                return
            te_verwerken = [g for g in leden if g["gemeente"] in gekozen_namen]
        else:
            te_verwerken = leden

        heeft_agendapunten_optie = config["heeft_agendapunten"]
        heeft_browser_optie = config["heeft_browser"]
        type_label = config["label"]

    # ── Orgaan ───────────────────────────────────────────────────────────
    orgaan_keuze = questionary.select(
        "Orgaan filteren?",
        choices=[
            questionary.Choice("Gemeenteraad", "Gemeenteraad"),
            questionary.Choice("Alle organen (geen filter)", None),
            questionary.Choice("College van burgemeester en schepenen",
                               "College van burgemeester en schepenen"),
            questionary.Choice("Zelf invoeren…", "__invoer__"),
        ],
        default="Gemeenteraad",
        style=STIJL,
    ).ask()

    if orgaan_keuze == "__invoer__":
        orgaan = questionary.text("Orgaannaam:", style=STIJL).ask() or None
    else:
        orgaan = orgaan_keuze

    # ── Maanden ───────────────────────────────────────────────────────────
    standaard_maanden = "36" if orgaan and "gemeenteraad" in orgaan.lower() else "12"
    maanden_str = questionary.text(
        "Hoeveel maanden terugzoeken?",
        default=standaard_maanden,
        style=STIJL,
    ).ask()
    try:
        maanden = max(1, int(maanden_str or standaard_maanden))
    except ValueError:
        maanden = int(standaard_maanden)

    # ── Uitvoermap ────────────────────────────────────────────────────────
    output = questionary.text(
        "Basis-uitvoermap (per gemeente een submap):",
        default="pdfs",
        style=STIJL,
    ).ask() or "pdfs"

    # ── Documentfilter ────────────────────────────────────────────────────
    doc_filter_keuze = questionary.select(
        "Documentfilter?",
        choices=[
            questionary.Choice("Alleen notulen", "notulen"),
            questionary.Choice("Geen (alle documenten)", None),
            questionary.Choice("Zelf invoeren…", "__invoer__"),
        ],
        default="notulen" if orgaan and "gemeenteraad" in orgaan.lower() else None,
        style=STIJL,
    ).ask()

    if doc_filter_keuze == "__invoer__":
        doc_filter = questionary.text("Filter:", style=STIJL).ask() or None
    else:
        doc_filter = doc_filter_keuze

    # ── Agendapunten ──────────────────────────────────────────────────────
    agendapunten = False
    if heeft_agendapunten_optie:
        agendapunten = questionary.confirm(
            "Individuele agendapunt-besluiten meenemen? (trager)",
            default=False,
            style=STIJL,
        ).ask() or False

    # ── Browser tonen ─────────────────────────────────────────────────────
    zichtbaar = False
    if heeft_browser_optie:
        zichtbaar = questionary.confirm(
            "Browser zichtbaar tonen? (voor debuggen)",
            default=False,
            style=STIJL,
        ).ask() or False

    # ── Overzicht + bevestiging ───────────────────────────────────────────
    console.print()
    tabel = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0, 1))
    tabel.add_column("", style="dim")
    tabel.add_column("", style="bold")
    tabel.add_row("Type", type_label)
    tabel.add_row("Gemeenten", str(len(te_verwerken)))
    tabel.add_row("Orgaan", orgaan or "[dim]Alle organen[/dim]")
    tabel.add_row("Periode", f"Laatste {maanden} maand(en)")
    tabel.add_row("Uitvoermap", f"{output}/<gemeente>/")
    tabel.add_row("Documentfilter", doc_filter or "[dim]Geen[/dim]")
    if heeft_agendapunten_optie:
        tabel.add_row("Agendapunten", "Ja" if agendapunten else "Nee")
    console.print(Panel(tabel, title="[bold]Overzicht[/bold]", border_style="cyan"))
    console.print()

    bevestig = questionary.confirm("Starten?", default=True, style=STIJL).ask()
    if not bevestig:
        console.print("[yellow]Geannuleerd.[/yellow]")
        return

    scrape_batch(
        te_verwerken, orgaan, maanden, output,
        doc_filter, agendapunten, zichtbaar,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # Zorg dat stdout UTF-8 gebruikt op Windows (anders cp1252)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Gegroepeerde scraper voor alle bekende gemeenten per websitetype.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Websitetypes:
  smartcities      raadpleeg-*.onlinesmartcities.be + besluitvorming.*.be
  cipalschaubroeck *-echo.cipalschaubroeck.be/raadpleegomgeving + *.csecho.be
  meetingburger    *.meetingburger.net
  ingelmunster     www.ingelmunster.be/db_files_2
  lblod            lblod.*.be — LBLODWeb publicatieportaal
  overig           Andere sites (geen scraper beschikbaar)

Voorbeelden:
  uv run python scraper_groep.py                              # interactieve TUI
  uv run python scraper_groep.py --toon-groepen               # groepsoverzicht
  uv run python scraper_groep.py --type smartcities --alle --maanden 6
  uv run python scraper_groep.py --type cipalschaubroeck --orgaan "Gemeenteraad" --maanden 12
  uv run python scraper_groep.py --type meetingburger --alle --maanden 3
  uv run python scraper_groep.py --gemeente Aalst --alle --maanden 6
        """,
    )
    parser.add_argument(
        "--toon-groepen", action="store_true",
        help="Toon een overzicht van alle groepen en gemeenten, en stop",
    )
    parser.add_argument(
        "--type", choices=list(TYPES.keys()), default=None,
        help="Beperk de batch tot dit websitetype",
    )
    parser.add_argument(
        "--gemeente", type=str, default=None,
        help="Verwerk één specifieke gemeente op naam (deel-match)",
    )
    parser.add_argument(
        "--orgaan", "-o", type=str, default=None,
        help="Filter op orgaannaam (bv. 'Gemeenteraad')",
    )
    parser.add_argument(
        "--alle", action="store_true",
        help="Geen orgaanfilter (scrape alle organen)",
    )
    parser.add_argument(
        "--maanden", "-m", type=int, default=12,
        help="Aantal maanden terug te doorzoeken (standaard: 12)",
    )
    parser.add_argument(
        "--output", "-d", type=str, default="pdfs",
        help="Basis-uitvoermap; per gemeente een submap (standaard: pdfs)",
    )
    parser.add_argument(
        "--document-filter", "-f", type=str, default=None,
        help="Filter documenten op naam (bv. 'notulen')",
    )
    parser.add_argument(
        "--notulen", action="store_true",
        help="Shorthand voor --document-filter notulen",
    )
    parser.add_argument(
        "--agendapunten", "-a", action="store_true",
        help="Individuele agendapunt-besluiten meenemen (trager, indien ondersteund)",
    )
    parser.add_argument(
        "--zichtbaar", action="store_true",
        help="Toon de browser (voor types met Playwright)",
    )
    parser.add_argument(
        "--pauze", type=float, default=2.0,
        help="Wachttijd in seconden tussen gemeenten (standaard: 2.0)",
    )

    args = parser.parse_args()

    if args.notulen and not args.document_filter:
        args.document_filter = "notulen"

    gemeenten = lees_csv()

    # ── --toon-groepen ────────────────────────────────────────────────────
    if args.toon_groepen:
        toon_groepen(gemeenten)
        return

    # ── Bepaal welke gemeenten te verwerken ───────────────────────────────
    if args.gemeente:
        naam_lower = args.gemeente.lower()
        te_verwerken = [
            g for g in gemeenten
            if naam_lower in g["gemeente"].lower()
        ]
        if not te_verwerken:
            console.print(f"[red]Geen gemeente gevonden met naam '{args.gemeente}'.[/red]")
            sys.exit(1)

    elif args.type:
        te_verwerken = [g for g in gemeenten if g["type"] == args.type]
        if not te_verwerken:
            console.print(f"[red]Geen gemeenten gevonden voor type '{args.type}'.[/red]")
            sys.exit(1)

    elif args.orgaan or args.alle:
        # Alle scraapbare gemeenten (types met een scraper)
        te_verwerken = [
            g for g in gemeenten if TYPES[g["type"]]["scraper"] is not None
        ]

    else:
        # Geen CLI-argumenten → interactieve TUI
        tui_main(gemeenten)
        return

    if not args.orgaan and not args.alle:
        # Types zonder orgaan-concept (bv. imio: enkel PV's) vereisen geen --orgaan/--alle
        type_config = TYPES.get(args.type or "", {})
        if type_config.get("heeft_organen", True):
            console.print("[red]Geef --orgaan of --alle op.[/red]")
            sys.exit(1)

    scrape_batch(
        te_verwerken,
        orgaan=None if args.alle else args.orgaan,
        maanden=args.maanden,
        output_basis=args.output,
        doc_filter=args.document_filter,
        agendapunten=args.agendapunten,
        zichtbaar=args.zichtbaar,
        pauze=args.pauze,
    )


if __name__ == "__main__":
    main()
