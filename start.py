"""
Besluitendatabank — Interactieve scraper-interface (TUI)

Gebruik:
    uv run python start.py
"""

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

# ---------------------------------------------------------------------------
# Configuratie per gemeente/stad
# ---------------------------------------------------------------------------

STEDEN = {
    "Brugge": {
        "script": "scraper.py",
        "site": "besluitvorming.brugge.be",
        "output_standaard": "pdfs_brugge",
        "heeft_browser": True,
    },
    "Halle": {
        "script": "scraper_halle.py",
        "site": "raadpleeg-halle.onlinesmartcities.be",
        "output_standaard": "pdfs_halle",
        "heeft_browser": True,
    },
    "Leuven": {
        "script": "scraper_leuven.py",
        "site": "besluitvorming.leuven.be",
        "output_standaard": "pdfs_leuven",
        "heeft_browser": True,
    },
    "Menen": {
        "script": "scraper_menen.py",
        "site": "menen-echo.cipalschaubroeck.be",
        "output_standaard": "pdfs_menen",
        "heeft_browser": False,
    },
    "Ranst": {
        "script": "scraper_ranst.py",
        "site": "ranst.meetingburger.net",
        "output_standaard": "pdfs_ranst",
        "heeft_browser": False,
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


def toon_overzicht(stad: str, orgaan: str | None, maanden: int,
                   output: str, doc_filter: str | None,
                   agendapunten: bool):
    tabel = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0, 1))
    tabel.add_column("Instelling", style="dim")
    tabel.add_column("Waarde", style="bold")

    tabel.add_row("Gemeente / Stad", stad)
    tabel.add_row("Orgaan", orgaan or "[dim]Alle organen[/dim]")
    tabel.add_row("Periode", f"Laatste {maanden} maand(en)")
    tabel.add_row("Uitvoermap", output)
    tabel.add_row("Documentfilter", doc_filter or "[dim]Geen (alle documenten)[/dim]")
    tabel.add_row("Individuele besluiten", "Ja" if agendapunten else "Nee")

    console.print(Panel(tabel, title="[bold]Overzicht instellingen[/bold]", border_style="cyan"))
    console.print()


def haal_organen(script: str) -> list[str] | None:
    """Roep --lijst-organen op en geef de namen terug."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "uv", "run", "python", script, "--lijst-organen"],
            capture_output=True, text=True, cwd=SCRIPT_DIR, timeout=30
        )
        organen = []
        for regel in result.stdout.splitlines():
            regel = regel.strip()
            if regel.startswith("- "):
                organen.append(regel[2:].strip())
        return organen if organen else None
    except Exception:
        return None


def haal_organen_direct(script: str) -> list[str] | None:
    """Haal organen op door het scraper-script direct te importeren."""
    import importlib.util
    pad = SCRIPT_DIR / script
    try:
        spec = importlib.util.spec_from_file_location("_scraper_tmp", pad)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "haal_organen_statisch"):
            organen = mod.haal_organen_statisch()
            return [o["naam"] for o in organen] if organen else None
        elif hasattr(mod, "haal_organen"):
            if hasattr(mod, "init_session"):
                mod.init_session()
            organen = mod.haal_organen()
            return [o["naam"] for o in organen] if organen else None
        elif hasattr(mod, "toon_organen"):
            # Playwright-gebaseerd: gebruik subprocess
            return None
    except Exception:
        pass
    return None


def laad_organen(stad: str) -> list[str] | None:
    """Laad orgaannamen voor de gekozen stad. Geeft None terug bij fout."""
    script = STEDEN[stad]["script"]
    with console.status("[cyan]Organen ophalen van de website...[/cyan]", spinner="dots"):
        organen = haal_organen_direct(script)
        if not organen:
            organen = haal_organen(script)
    return organen


def bouw_commando(stad: str, orgaan: str | None, maanden: int,
                  output: str, doc_filter: str | None,
                  agendapunten: bool, zichtbaar: bool) -> list[str]:
    """Stel het subproces-commando samen."""
    script = STEDEN[stad]["script"]
    cmd = ["uv", "run", "python", script]

    if orgaan:
        cmd += ["--orgaan", orgaan]
    else:
        cmd += ["--alle"]

    cmd += ["--maanden", str(maanden)]
    cmd += ["--output", output]

    if doc_filter:
        cmd += ["--document-filter", doc_filter]
    if agendapunten:
        cmd += ["--agendapunten"]
    if zichtbaar and STEDEN[stad]["heeft_browser"]:
        cmd += ["--zichtbaar"]

    return cmd


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
            cwd=SCRIPT_DIR,
        )
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

def stap_stad() -> str | None:
    keuze = questionary.select(
        "Welke gemeente of stad wilt u doorzoeken?",
        choices=[
            questionary.Choice(f"{naam}  ({info['site']})", value=naam)
            for naam, info in STEDEN.items()
        ] + [questionary.Choice("── Terug naar hoofdmenu", value="__terug__")],
        style=STIJL,
        use_shortcuts=False,
    ).ask()
    if keuze in (None, "__terug__"):
        return None
    return keuze


def stap_orgaan(stad: str) -> str | None:
    """Vraag welk orgaan. Geeft None terug = alle organen."""
    organen = laad_organen(stad)

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


def stap_maanden() -> int:
    keuze = questionary.select(
        "Hoeveel maanden terug wilt u zoeken?",
        choices=[
            questionary.Choice("1 maand",   value=1),
            questionary.Choice("3 maanden", value=3),
            questionary.Choice("6 maanden", value=6),
            questionary.Choice("12 maanden (1 jaar)",  value=12),
            questionary.Choice("24 maanden (2 jaar)",  value=24),
            questionary.Choice("36 maanden (3 jaar)",  value=36),
            questionary.Choice("Alle beschikbare data", value=999),
        ],
        default=questionary.Choice("12 maanden (1 jaar)", value=12),
        style=STIJL,
    ).ask()
    return keuze


def stap_doc_filter() -> str | None:
    keuze = questionary.select(
        "Wilt u alleen bepaalde documenten downloaden?",
        choices=[
            questionary.Choice("Alle documenten",                  value=None),
            questionary.Choice("Alleen notulen / zittingsverslagen", value="notulen"),
            questionary.Choice("Alleen agenda's",                  value="agenda"),
            questionary.Choice("Alleen besluitenlijsten",          value="besluitenlijst"),
            questionary.Choice("Aangepast filter…",               value="__custom__"),
        ],
        style=STIJL,
    ).ask()

    if keuze == "__custom__":
        filter_tekst = questionary.text(
            "Voer het documentfilter in (woord dat in de bestandsnaam moet voorkomen):",
            style=STIJL,
        ).ask()
        return filter_tekst.strip() if filter_tekst and filter_tekst.strip() else None

    return keuze


def stap_agendapunten() -> bool:
    return questionary.confirm(
        "Ook individuele agendapuntbesluiten downloaden? (trager)",
        default=False,
        style=STIJL,
    ).ask()


def stap_output(stad: str, orgaan: str | None) -> str:
    standaard = STEDEN[stad]["output_standaard"]
    if orgaan:
        veilig = orgaan.lower().replace(" ", "_").replace("/", "_")[:20]
        standaard = f"{standaard}/{veilig}"

    antwoord = questionary.text(
        "In welke map wilt u de bestanden opslaan?",
        default=standaard,
        style=STIJL,
    ).ask()
    return antwoord.strip() if antwoord and antwoord.strip() else standaard


def stap_zichtbaar(stad: str) -> bool:
    if not STEDEN[stad]["heeft_browser"]:
        return False
    return questionary.confirm(
        "Browser zichtbaar maken? (handig bij problemen, maar trager)",
        default=False,
        style=STIJL,
    ).ask()


def stap_bevestiging(stad: str, orgaan: str | None, maanden: int,
                     output: str, doc_filter: str | None,
                     agendapunten: bool) -> bool:
    toon_overzicht(stad, orgaan, maanden, output, doc_filter, agendapunten)
    return questionary.confirm(
        "Alles klopt? Scraper starten?",
        default=True,
        style=STIJL,
    ).ask()


# ---------------------------------------------------------------------------
# Hoofdmenu
# ---------------------------------------------------------------------------

def hoofdmenu():
    while True:
        banner()

        keuze = questionary.select(
            "Wat wilt u doen?",
            choices=[
                questionary.Choice("📥  Documenten downloaden",         value="scrapen"),
                questionary.Choice("📋  Beschikbare organen bekijken",  value="organen"),
                questionary.Choice("🚪  Afsluiten",                     value="afsluiten"),
            ],
            style=STIJL,
            use_shortcuts=False,
        ).ask()

        if keuze in (None, "afsluiten"):
            console.print("\n[dim]Tot ziens![/dim]\n")
            break

        elif keuze == "organen":
            menu_organen()

        elif keuze == "scrapen":
            wizard_scrapen()


def menu_organen():
    console.print()
    stad = stap_stad()
    if stad is None:
        return

    console.print()
    organen = laad_organen(stad)
    if organen:
        tabel = Table(
            title=f"Organen — {stad}",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=False,
        )
        tabel.add_column("Orgaan", style="bold")
        for o in organen:
            tabel.add_row(o)
        console.print(tabel)
    else:
        console.print(f"[yellow]Kon geen organen ophalen voor {stad}.[/yellow]")

    console.print()
    questionary.press_any_key_to_continue("Druk op een toets om terug te gaan...").ask()


def wizard_scrapen():
    console.print()

    # 1. Stad
    stad = stap_stad()
    if stad is None:
        return
    console.print()

    # 2. Orgaan
    orgaan = stap_orgaan(stad)
    console.print()

    # 3. Periode
    maanden = stap_maanden()
    console.print()

    # 4. Documentfilter
    doc_filter = stap_doc_filter()
    console.print()

    # 5. Agendapunten
    agendapunten = stap_agendapunten()
    console.print()

    # 6. Uitvoermap
    output = stap_output(stad, orgaan)
    console.print()

    # 7. Browser zichtbaar (alleen voor Playwright-scrapers)
    zichtbaar = stap_zichtbaar(stad)
    if zichtbaar:
        console.print()

    # 8. Bevestiging
    if not stap_bevestiging(stad, orgaan, maanden, output, doc_filter, agendapunten):
        console.print("[yellow]Geannuleerd.[/yellow]\n")
        return

    console.print()

    # 9. Uitvoeren
    cmd = bouw_commando(stad, orgaan, maanden, output, doc_filter, agendapunten, zichtbaar)
    voer_uit(cmd)

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
