"""
Besluitendatabank — Interactieve scraper-interface (TUI)

Gebruik:
    uv run python start.py
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from scraper_groep import detecteer_type, extraheer_base_url, lees_csv, TYPES

# ---------------------------------------------------------------------------
# Gemeenten met een eigen dedicated scraper (geen --base-url nodig)
# ---------------------------------------------------------------------------

DEDICATED: dict[str, dict] = {
    "Brugge": {
        "script": "scraper.py",
        "heeft_browser": True,
        "heeft_agendapunten": True,
    },
    "Leuven": {
        "script": "scraper_leuven.py",
        "heeft_browser": True,
        "heeft_agendapunten": True,
    },
    "Ingelmunster": {
        "script": "scraper_ingelmunster.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
    },
    "Provincie Vlaams-Brabant": {
        "script": "scraper_vlaamsbrabant.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
    },
    "Provincie Antwerpen": {
        "script": "scraper_provantwerpen.py",
        "heeft_browser": False,
        "heeft_agendapunten": False,
    },
    "Kalmthout": {
        "script": "scraper_ibabs.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "gemeente_arg": True,
    },
    "Stabroek": {
        "script": "scraper_ibabs.py",
        "heeft_browser": False,
        "heeft_agendapunten": True,
        "gemeente_arg": True,
    },
}

# ---------------------------------------------------------------------------
# Stijl
# ---------------------------------------------------------------------------

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

console = Console()
SCRIPT_DIR = Path(__file__).parent


def is_no_console_error(exc: BaseException) -> bool:
    """Detecteer prompt_toolkit NoConsoleScreenBufferError via de exception-chain."""
    huidig: BaseException | None = exc
    while huidig is not None:
        if huidig.__class__.__name__ == "NoConsoleScreenBufferError":
            return True
        huidig = huidig.__cause__ or huidig.__context__
    return False


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def banner():
    console.print()
    console.print(Panel(
        "[bold cyan]Besluitendatabank[/bold cyan]\n"
        "[dim]Gemeentelijke besluiten & notulen downloader[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


def scraper_info(gemeente: dict) -> tuple[str | None, bool, bool]:
    """Geef (script, heeft_browser, heeft_agendapunten) terug voor een gemeente."""
    naam = gemeente["gemeente"]
    if naam in DEDICATED:
        d = DEDICATED[naam]
        return d["script"], d["heeft_browser"], d["heeft_agendapunten"]
    config = TYPES.get(gemeente["type"], {})
    scraper = config.get("scraper")
    return scraper, config.get("heeft_browser", False), config.get("heeft_agendapunten", False)


def haal_organen_direct(gemeente: dict) -> list[str] | None:
    """Haal organen op door het scraper-module direct te laden (zonder subprocess)."""
    naam = gemeente["gemeente"]
    if naam in DEDICATED:
        script = DEDICATED[naam]["script"]
        base_url = None
    else:
        script = TYPES.get(gemeente["type"], {}).get("scraper")
        if script is None:
            return None
        base_url = gemeente.get("base_url", "")

    pad = SCRIPT_DIR / script
    try:
        spec = importlib.util.spec_from_file_location("_scraper_tmp", pad)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Patch BASE_URL zodat de module de juiste gemeente bevraagt
        if base_url and hasattr(mod, "BASE_URL"):
            mod.BASE_URL = base_url
            if hasattr(mod, "KALENDER_URL"):
                mod.KALENDER_URL = f"{base_url}/zittingen/kalender"
            if hasattr(mod, "CONTEXT"):
                context = mod.CONTEXT
                if hasattr(mod, "LIJST_URL"):
                    mod.LIJST_URL = f"{base_url}{context}/zittingen/lijst"
                if hasattr(mod, "KALENDER_API"):
                    mod.KALENDER_API = f"{base_url}{context}/calendar/fetchcalendar"
                if hasattr(mod, "ZOEKEN_URL"):
                    mod.ZOEKEN_URL = f"{base_url}{context}/zoeken"

        if hasattr(mod, "init_session"):
            try:
                mod.init_session(base_url)
            except TypeError:
                try:
                    mod.init_session()
                except Exception:
                    pass
            except Exception:
                pass

        if hasattr(mod, "haal_organen_statisch"):
            organen = mod.haal_organen_statisch()
            return [o["naam"] for o in organen] if organen else None
        elif hasattr(mod, "haal_organen"):
            organen = mod.haal_organen()
            return [o["naam"] for o in organen] if organen else None
    except Exception:
        pass
    return None


def bouw_commando(
    gemeente: dict,
    orgaan: str | None,
    maanden: int,
    output: str,
    doc_filter: str | None,
    agendapunten: bool,
    zichtbaar: bool,
) -> list[str] | None:
    """Stel het subproces-commando samen voor een gemeente."""
    script, heeft_browser, heeft_agendapunten = scraper_info(gemeente)
    if script is None:
        return None

    cmd = ["uv", "run", "python", script]

    # Dedicated scrapers hebben hun URL al ingebakken.
    # Voor deliberations.be zit de gemeente-slug in het pad → volledige URL doorgeven.
    if gemeente["gemeente"] not in DEDICATED:
        base = gemeente["url"] if gemeente.get("type") == "deliberations" else gemeente["base_url"]
        cmd += ["--base-url", base]

    # iBabs dedicated scrapers krijgen --gemeente ipv --alle
    dedicated_info = DEDICATED.get(gemeente["gemeente"], {})
    if dedicated_info.get("gemeente_arg"):
        cmd += ["--gemeente", gemeente["gemeente"]]
    elif gemeente.get("type") == "irisnet":
        # irisnet heeft één scraper voor alle Brusselse gemeenten; --gemeente beperkt tot één.
        if orgaan:
            cmd += ["--orgaan", orgaan]
        cmd += ["--gemeente", gemeente["gemeente"]]
    elif orgaan:
        cmd += ["--orgaan", orgaan]
    else:
        cmd += ["--alle"]

    cmd += ["--maanden", str(maanden)]
    cmd += ["--output", output]

    if doc_filter:
        cmd += ["--document-filter", doc_filter]
    if agendapunten and heeft_agendapunten:
        cmd += ["--agendapunten"]
    if zichtbaar and heeft_browser:
        cmd += ["--zichtbaar"]

    return cmd


def toon_overzicht(gemeente: dict, orgaan: str | None, maanden: int,
                   output: str, doc_filter: str | None, agendapunten: bool):
    script, _, _ = scraper_info(gemeente)
    type_label = TYPES.get(gemeente["type"], {}).get("label", gemeente["type"])

    tabel = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0, 1))
    tabel.add_column("Instelling", style="dim")
    tabel.add_column("Waarde", style="bold")

    tabel.add_row("Gemeente / Stad", gemeente["gemeente"])
    tabel.add_row("Website", gemeente.get("url") or "[dim]—[/dim]")
    tabel.add_row("Type", type_label)
    tabel.add_row("Scraper", script or "[red]geen scraper beschikbaar[/red]")
    tabel.add_row("Orgaan", orgaan or "[dim]Alle organen[/dim]")
    tabel.add_row("Periode", f"Laatste {maanden} maand(en)")
    tabel.add_row("Uitvoermap", output)
    tabel.add_row("Documentfilter", doc_filter or "[dim]Geen (alle documenten)[/dim]")
    tabel.add_row("Individuele besluiten", "Ja" if agendapunten else "Nee")

    console.print(Panel(tabel, title="[bold]Overzicht instellingen[/bold]", border_style="cyan"))
    console.print()


def voer_uit(cmd: list[str]):
    """Start het scraper-commando en stream de uitvoer live."""
    console.print(Rule("[bold cyan]Scraper gestart[/bold cyan]", style="cyan"))
    console.print(f"[dim]Commando: {' '.join(cmd)}[/dim]\n")

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(SCRIPT_DIR),
        )
        assert proc.stdout is not None
        for regel in proc.stdout:
            console.print(regel, end="")
        proc.wait()
        duur = time.time() - start
        console.print()
        if proc.returncode == 0:
            console.print(Panel(
                f"[bold green]✓ Klaar in {duur:.0f} seconden.[/bold green]",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[bold red]✗ Scraper beëindigd met foutcode {proc.returncode}.[/bold red]",
                border_style="red",
            ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Gestopt door gebruiker.[/yellow]")
    except Exception as e:
        console.print(f"[red]Fout bij uitvoeren: {e}[/red]")


# ---------------------------------------------------------------------------
# Wizard-stappen
# ---------------------------------------------------------------------------

def stap_gemeente(alle_gemeenten: list[dict]) -> dict | None:
    """Laat de gebruiker een gemeente kiezen via autocomplete."""
    alle = sorted(alle_gemeenten, key=lambda g: g["gemeente"])
    naam_naar_gemeente = {g["gemeente"]: g for g in alle}
    namen = [g["gemeente"] for g in alle]

    gekozen_naam = questionary.autocomplete(
        "Typ (deel van) de gemeentenaam:",
        choices=namen,
        style=STIJL,
        validate=lambda v: v in naam_naar_gemeente or "Kies een gemeente uit de lijst.",
    ).ask()

    if not gekozen_naam:
        return None
    return naam_naar_gemeente.get(gekozen_naam)


def stap_orgaan(gemeente: dict) -> str | None:
    """Vraag welk orgaan. Geeft None terug = alle organen."""
    script, _, _ = scraper_info(gemeente)
    if script is None:
        return None

    with console.status("[cyan]Organen ophalen van de website...[/cyan]", spinner="dots"):
        organen = haal_organen_direct(gemeente)

    if organen:
        keuzes = (
            [questionary.Choice("Alle organen", value="__alle__")]
            + [questionary.Choice(o, value=o) for o in organen]
        )
        keuze = questionary.select(
            "Welk orgaan wilt u raadplegen?",
            choices=keuzes,
            style=STIJL,
        ).ask()
        if keuze in (None, "__alle__"):
            return None
        return keuze
    else:
        console.print("[yellow]  Organen konden niet automatisch worden opgehaald.[/yellow]")
        antwoord = questionary.text(
            "Typ de naam van het orgaan (leeglaten = alle organen):",
            style=STIJL,
        ).ask()
        return antwoord.strip() if antwoord and antwoord.strip() else None


def stap_maanden(orgaan: str | None = None) -> int:
    standaard = 36 if orgaan and "gemeenteraad" in orgaan.lower() else 12
    keuze = questionary.select(
        "Hoeveel maanden terug wilt u zoeken?",
        choices=[
            questionary.Choice("1 maand",               value=1),
            questionary.Choice("3 maanden",              value=3),
            questionary.Choice("6 maanden",              value=6),
            questionary.Choice("12 maanden (1 jaar)",    value=12),
            questionary.Choice("24 maanden (2 jaar)",    value=24),
            questionary.Choice("36 maanden (3 jaar)",    value=36),
            questionary.Choice("Alle beschikbare data",  value=999),
        ],
        default=standaard,
        style=STIJL,
    ).ask()
    return keuze


def stap_doc_filter(orgaan: str | None = None) -> str | None:
    standaard = "notulen" if orgaan and "gemeenteraad" in orgaan.lower() else None
    keuze = questionary.select(
        "Wilt u alleen bepaalde documenten downloaden?",
        choices=[
            questionary.Choice("Alle documenten",                    value=None),
            questionary.Choice("Alleen notulen / zittingsverslagen", value="notulen"),
            questionary.Choice("Alleen agenda's",                    value="agenda"),
            questionary.Choice("Alleen besluitenlijsten",            value="besluitenlijst"),
            questionary.Choice("Aangepast filter…",                 value="__custom__"),
        ],
        default=standaard,
        style=STIJL,
    ).ask()

    if keuze == "__custom__":
        filter_tekst = questionary.text(
            "Voer het documentfilter in (woord dat in de bestandsnaam moet voorkomen):",
            style=STIJL,
        ).ask()
        return filter_tekst.strip() if filter_tekst and filter_tekst.strip() else None

    return keuze


def stap_agendapunten(gemeente: dict) -> bool:
    _, _, heeft_agendapunten = scraper_info(gemeente)
    if not heeft_agendapunten:
        return False
    return questionary.confirm(
        "Ook individuele agendapuntbesluiten downloaden? (trager)",
        default=False,
        style=STIJL,
    ).ask()


def stap_output(gemeente: dict, orgaan: str | None) -> str:
    slug = gemeente["gemeente"].lower().replace(" ", "_").replace("/", "_")
    slug = slug[:30]
    standaard = f"pdfs/{slug}"
    if orgaan:
        veilig = orgaan.lower().replace(" ", "_").replace("/", "_")[:20]
        standaard = f"{standaard}/{veilig}"

    antwoord = questionary.text(
        "In welke map wilt u de bestanden opslaan?",
        default=standaard,
        style=STIJL,
    ).ask()
    return antwoord.strip() if antwoord and antwoord.strip() else standaard


def stap_zichtbaar(gemeente: dict) -> bool:
    _, heeft_browser, _ = scraper_info(gemeente)
    if not heeft_browser:
        return False
    return questionary.confirm(
        "Browser zichtbaar maken? (handig bij problemen, maar trager)",
        default=False,
        style=STIJL,
    ).ask()


# ---------------------------------------------------------------------------
# Hoofdmenu
# ---------------------------------------------------------------------------

def hoofdmenu():
    alle_gemeenten = lees_csv()

    while True:
        banner()

        keuze = questionary.select(
            "Wat wilt u doen?",
            choices=[
                questionary.Choice("📥  Enkele gemeente scrapen",          value="scrapen"),
                questionary.Choice("📦  Batch scrapen per websitetype",    value="batch"),
                questionary.Choice("📋  Beschikbare organen bekijken",     value="organen"),
                questionary.Choice("🩺  Bronnenlijst controleren",         value="health"),
                questionary.Choice("🚪  Afsluiten",                        value="afsluiten"),
            ],
            style=STIJL,
            use_shortcuts=False,
        ).ask()

        if keuze in (None, "afsluiten"):
            console.print("\n[dim]Tot ziens![/dim]\n")
            break

        elif keuze == "organen":
            menu_organen(alle_gemeenten)

        elif keuze == "health":
            wizard_health_check()

        elif keuze == "scrapen":
            wizard_scrapen(alle_gemeenten)

        elif keuze == "batch":
            wizard_batch()


def menu_organen(alle_gemeenten: list[dict]):
    console.print()
    gemeente = stap_gemeente(alle_gemeenten)
    if gemeente is None:
        return

    script, _, _ = scraper_info(gemeente)
    console.print()

    if script is None:
        console.print(f"[yellow]Geen scraper beschikbaar voor {gemeente['gemeente']} "
                      f"({TYPES.get(gemeente['type'], {}).get('label', '?')}).[/yellow]")
    else:
        with console.status("[cyan]Organen ophalen...[/cyan]", spinner="dots"):
            organen = haal_organen_direct(gemeente)
        if organen:
            tabel = Table(
                title=f"Organen — {gemeente['gemeente']}",
                box=box.ROUNDED,
                border_style="cyan",
                show_header=False,
            )
            tabel.add_column("Orgaan", style="bold")
            for o in organen:
                tabel.add_row(o)
            console.print(tabel)
        else:
            console.print(f"[yellow]Kon geen organen ophalen voor {gemeente['gemeente']}.[/yellow]")
            console.print("[dim]Playwright-gebaseerde scrapers vereisen een browser. "
                          "Gebruik --lijst-organen vanuit de terminal.[/dim]")

    console.print()
    questionary.press_any_key_to_continue("Druk op een toets om terug te gaan...").ask()


def wizard_scrapen(alle_gemeenten: list[dict]):
    console.print()

    # 1. Gemeente
    gemeente = stap_gemeente(alle_gemeenten)
    if gemeente is None:
        return

    script, _, _ = scraper_info(gemeente)
    if script is None:
        console.print(
            f"\n[yellow]⚠  Geen scraper beschikbaar voor {gemeente['gemeente']} "
            f"(type: {TYPES.get(gemeente['type'], {}).get('label', gemeente['type'])}).[/yellow]\n"
        )
        questionary.press_any_key_to_continue("Druk op een toets...").ask()
        return

    console.print()

    # 2. Orgaan
    orgaan = stap_orgaan(gemeente)
    console.print()

    # 3. Periode
    maanden = stap_maanden(orgaan)
    console.print()

    # 4. Documentfilter
    doc_filter = stap_doc_filter(orgaan)
    console.print()

    # 5. Agendapunten
    agendapunten = stap_agendapunten(gemeente)
    console.print()

    # 6. Uitvoermap
    output = stap_output(gemeente, orgaan)
    console.print()

    # 7. Browser zichtbaar (alleen voor Playwright-scrapers)
    zichtbaar = stap_zichtbaar(gemeente)
    if zichtbaar:
        console.print()

    # 8. Bevestiging
    toon_overzicht(gemeente, orgaan, maanden, output, doc_filter, agendapunten)
    if not questionary.confirm("Alles klopt? Scraper starten?", default=True, style=STIJL).ask():
        console.print("[yellow]Geannuleerd.[/yellow]\n")
        return

    console.print()

    # 9. Uitvoeren
    cmd = bouw_commando(gemeente, orgaan, maanden, output, doc_filter, agendapunten, zichtbaar)
    if cmd:
        voer_uit(cmd)

    console.print()
    questionary.press_any_key_to_continue("Druk op een toets om terug te gaan naar het hoofdmenu...").ask()


def wizard_health_check():
    """Voer een health check uit op de bronnenlijst."""
    console.print()
    console.print(Rule("[bold cyan]Bronnenlijst controleren[/bold cyan]", style="cyan"))

    url_check = questionary.confirm(
        "Ook HTTP-bereikbaarheid testen? (duurt ~30-60 seconden extra)",
        default=False,
        style=STIJL,
    ).ask()
    if url_check is None:
        return

    alleen_problemen = questionary.confirm(
        "Alleen rijen met problemen tonen?",
        default=False,
        style=STIJL,
    ).ask()
    if alleen_problemen is None:
        return

    console.print()

    cmd = ["uv", "run", "python", "health_check.py"]
    if url_check:
        cmd.append("--url-check")
    if alleen_problemen:
        cmd.append("--alleen-problemen")

    console.print(f"[dim]Commando: {' '.join(cmd)}[/dim]\n")
    try:
        subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    except KeyboardInterrupt:
        console.print("\n[yellow]Onderbroken.[/yellow]")
    except Exception as e:
        console.print(f"[red]Fout bij starten van health_check.py: {e}[/red]")

    console.print()
    questionary.press_any_key_to_continue("Druk op een toets om terug te gaan naar het hoofdmenu...").ask()


def wizard_batch():
    """Start de interactieve TUI van scraper_groep.py als subproces."""
    console.print()
    console.print(Rule("[bold cyan]Batch scrapen[/bold cyan]", style="cyan"))
    console.print("[dim]scraper_groep.py wordt gestart...[/dim]\n")
    try:
        subprocess.run(
            ["uv", "run", "python", "scraper_groep.py"],
            cwd=str(SCRIPT_DIR),
        )
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Fout bij starten van scraper_groep.py: {e}[/red]")
    console.print()
    questionary.press_any_key_to_continue("Druk op een toets om terug te gaan naar het hoofdmenu...").ask()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        hoofdmenu()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Gestopt.[/dim]\n")
        sys.exit(0)
    except Exception as e:
        if is_no_console_error(e):
            console.print()
            console.print(Panel(
                "[bold yellow]Geen compatibele Windows-console gedetecteerd.[/bold yellow]\n\n"
                "Deze interactieve TUI gebruikt `questionary`/`prompt_toolkit` en werkt op Windows "
                "alleen in een echte terminal (cmd/PowerShell/Windows Terminal).\n\n"
                "Probeer:\n"
                "- Starten vanuit een terminal: [bold]uv run python start.py[/bold]\n"
                "- In PyCharm: schakel [italic]Emulate terminal in output console[/italic] in",
                border_style="yellow",
                title="Console vereist",
            ))
            sys.exit(1)
        raise
