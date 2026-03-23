"""
Health checker voor simba-source.csv

Controleert:
  - CSV-integriteit (duplicaten, lege rijen, formaat)
  - Type-detectie per URL (overig/leeg = geen scraper beschikbaar)
  - Aanwezigheid van scraper-bestanden op schijf
  - URL-bereikbaarheid (opt. via --url-check, parallel)

Gebruik:
    uv run python health_check.py
    uv run python health_check.py --url-check
    uv run python health_check.py --type overig
    uv run python health_check.py --alleen-problemen
    uv run python health_check.py --url-check --werkers 30 --timeout 8
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from scraper_groep import detecteer_type, TYPES, CSV_PAD, SCRIPT_DIR

console = Console()

_STATUS_OK   = "ok"
_STATUS_FOUT = "fout"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# Types waarbij de CSV-URL een bestandspad-template is (bijv. /file/download);
# voor de bereikbaarheidscheck wordt alleen scheme+host gebruikt.
_URL_IS_TEMPLATE: frozenset[str] = frozenset({"icordis", "drupal", "ingelmunster", "forest"})

# Types die stelselmatig 403 teruggeven bij directe requests (bot-bescherming),
# maar waarvan de scraper met eigen sessie/headers wél werkt.
_TYPE_VERWACHT_403: frozenset[str] = frozenset({"lblod", "vlaamsbrabant"})


# ---------------------------------------------------------------------------
# CSV inlezen
# ---------------------------------------------------------------------------

def _lees_csv(pad: Path) -> tuple[list[dict], list[str]]:
    """Lees de CSV; geef (rijen, globale-meldingen) terug."""
    rijen: list[dict] = []
    meldingen: list[str] = []

    with open(pad, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader, None)
        if not header or len(header) < 2:
            meldingen.append("CSV-header ontbreekt of heeft te weinig kolommen")
            return rijen, meldingen

        for lijn_nr, rij in enumerate(reader, start=2):
            gemeente = rij[0].strip() if len(rij) > 0 else ""
            url      = rij[1].strip() if len(rij) > 1 else ""
            if not gemeente:
                continue
            rijen.append({"gemeente": gemeente, "url": url, "lijn": lijn_nr})

    return rijen, meldingen


# ---------------------------------------------------------------------------
# Structurele checks (geen netwerk)
# ---------------------------------------------------------------------------

def _url_syntax_ok(url: str) -> bool:
    if not url:
        return False
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _structurele_checks(rijen: list[dict]) -> list[dict]:
    """Voeg type, scraper en probleemlijst toe aan elke rij."""
    namen_gezien: dict[str, int] = {}
    urls_gezien: dict[str, int] = {}
    resultaten: list[dict] = []

    for r in rijen:
        gemeente = r["gemeente"]
        url      = r["url"]
        lijn     = r["lijn"]
        problemen: list[str] = []

        type_   = detecteer_type(url)
        scraper = TYPES.get(type_, {}).get("scraper")

        if gemeente in namen_gezien:
            problemen.append(f"duplicaat naam (ook lijn {namen_gezien[gemeente]})")
        else:
            namen_gezien[gemeente] = lijn

        if url and url in urls_gezien:
            problemen.append(f"duplicaat URL (ook lijn {urls_gezien[url]})")
        elif url:
            urls_gezien[url] = lijn

        if not url:
            problemen.append("lege URL — geen scraper mogelijk")
        elif not _url_syntax_ok(url):
            problemen.append("ongeldige URL-syntax (verwacht http/https + host)")
        elif type_ == "overig":
            problemen.append("type 'overig' — geen scraper beschikbaar")

        resultaten.append({
            **r,
            "type":      type_,
            "scraper":   scraper,
            "problemen": problemen,
            "status":    _STATUS_FOUT if problemen else _STATUS_OK,
            "http":      None,
        })

    return resultaten


# ---------------------------------------------------------------------------
# Scraper-bestand checks
# ---------------------------------------------------------------------------

def _check_scrapers() -> list[tuple[str, bool]]:
    """Controleer of elk uniek scraper-bestand aanwezig is."""
    gezien: set[str] = set()
    resultaat: list[tuple[str, bool]] = []
    for info in TYPES.values():
        scraper = info.get("scraper")
        if not scraper or scraper in gezien:
            continue
        gezien.add(scraper)
        resultaat.append((scraper, (SCRIPT_DIR / scraper).exists()))
    return sorted(resultaat)


# ---------------------------------------------------------------------------
# URL-bereikbaarheid (optioneel, parallel)
# ---------------------------------------------------------------------------

def _head_of_get(url: str, timeout: int) -> requests.Response:
    """Probeer HEAD; val terug op GET als de server 404 of 405 geeft.

    Sommige servers (o.a. Plone/Zope-sites zoals deliberations.be) sturen 404
    op een HEAD-request maar 200 op GET — val in dat geval terug op GET.
    """
    headers = {"User-Agent": _UA}
    resp = requests.head(url, timeout=timeout, headers=headers, allow_redirects=True)
    if resp.status_code in (404, 405):
        resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
    return resp


def _check_url_voor_type(r: dict) -> str:
    """Geef de te-controleren URL terug.

    Voor types waarbij de CSV-URL een bestandspad-template is worden alle
    paden weggegooid en wordt alleen de origin (scheme://host) gecheckt.
    """
    if r.get("type") in _URL_IS_TEMPLATE:
        p = urlparse(r["url"])
        return f"{p.scheme}://{p.netloc}"
    return r["url"]


def _check_url(gemeente: str, url: str, timeout: int) -> dict:
    if not _url_syntax_ok(url):
        return {"gemeente": gemeente, "http_status": None, "fout": "ongeldige URL"}
    try:
        resp = _head_of_get(url, timeout)
        return {"gemeente": gemeente, "http_status": resp.status_code, "fout": None}
    except requests.exceptions.SSLError:
        return {"gemeente": gemeente, "http_status": None, "fout": "SSL-fout"}
    except requests.exceptions.ConnectionError as e:
        msg = str(e).lower()
        oorzaak = "DNS mislukt" if any(w in msg for w in ("getaddrinfo", "nodename", "name or service")) else "verbinding geweigerd"
        return {"gemeente": gemeente, "http_status": None, "fout": oorzaak}
    except requests.exceptions.Timeout:
        return {"gemeente": gemeente, "http_status": None, "fout": f"timeout (>{timeout}s)"}
    except Exception as e:
        return {"gemeente": gemeente, "http_status": None, "fout": str(e)[:60]}


def _url_check(resultaten: list[dict], timeout: int, werkers: int) -> list[dict]:
    te_checken = [r for r in resultaten if _url_syntax_ok(r["url"])]
    console.print(f"\n[dim]URL-check: {len(te_checken)} URLs testen (timeout={timeout}s, werkers={werkers})...[/dim]")

    index = {r["gemeente"]: r for r in resultaten}
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=werkers) as pool:
        futures = {
            pool.submit(_check_url, r["gemeente"], _check_url_voor_type(r), timeout): r["gemeente"]
            for r in te_checken
        }
        gedaan = 0
        for future in as_completed(futures):
            gedaan += 1
            res = future.result()
            rij = index.get(res["gemeente"])
            if not rij:
                continue

            rij["http"] = res
            code = res.get("http_status")
            fout = res.get("fout")
            type_ = rij.get("type", "")

            if fout:
                rij["problemen"].append(f"HTTP: {fout}")
                rij["status"] = _STATUS_FOUT
            elif code == 403 and type_ in _TYPE_VERWACHT_403:
                # Site is bereikbaar maar beschermt zich tegen directe requests;
                # de eigen scraper werkt met gepaste headers/sessie.
                pass
            elif code and code >= 400:
                rij["problemen"].append(f"HTTP {code}")
                rij["status"] = _STATUS_FOUT

            if gedaan % 100 == 0 or gedaan == len(te_checken):
                elapsed = time.monotonic() - start
                console.print(f"  [dim]{gedaan}/{len(te_checken)} ({elapsed:.0f}s)[/dim]")

    return list(index.values())


# ---------------------------------------------------------------------------
# Rapportage
# ---------------------------------------------------------------------------

def _toon_scrapers(scraper_check: list[tuple[str, bool]]) -> None:
    console.print(Rule("[bold]Scraper-bestanden[/bold]", style="dim"))
    tabel = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
    tabel.add_column("Bestand", style="cyan", min_width=35)
    tabel.add_column("Status",  justify="center", width=14)

    for scraper, aanwezig in scraper_check:
        tabel.add_row(scraper, "[green]OK[/green]" if aanwezig else "[red]ONTBREEKT[/red]")
    console.print(tabel)


def _http_cel(rij: dict) -> str:
    """Formatteer de HTTP-statuscel (of lege string als niet gecheckt)."""
    http = rij.get("http")
    if http is None:
        return ""
    code = http.get("http_status")
    fout = http.get("fout")
    if fout:
        return f"[red]{fout}[/red]"
    if code:
        kleur = "green" if code < 300 else ("yellow" if code < 400 else "red")
        return f"[{kleur}]{code}[/{kleur}]"
    return ""


def _toon_resultaten(resultaten: list[dict], alleen_problemen: bool, type_filter: str | None, url_check: bool) -> None:
    console.print(Rule("[bold]Gemeente-overzicht[/bold]", style="dim"))

    gefilterd = resultaten
    if type_filter:
        gefilterd = [r for r in gefilterd if r["type"] == type_filter]
    if alleen_problemen:
        gefilterd = [r for r in gefilterd if r["status"] != _STATUS_OK]

    if not gefilterd:
        console.print("[dim]  Niets te tonen (alles OK of filter leeg)[/dim]\n")
        return

    tabel = Table(box=box.SIMPLE, show_header=True, header_style="bold", expand=False)
    tabel.add_column("#",          style="dim",  width=4,   justify="right")
    tabel.add_column("Gemeente",   style="bold", min_width=24)
    tabel.add_column("Type",       style="cyan", min_width=14)
    if url_check:
        tabel.add_column("HTTP",   justify="center", width=16)
    tabel.add_column("Bevindingen")

    for idx, r in enumerate(gefilterd, 1):
        sts_sym = "[green]v[/green]" if r["status"] == _STATUS_OK else "[red]![/red]"
        bevindingen = "; ".join(r["problemen"]) if r["problemen"] else "[dim]-[/dim]"
        rij_data = [str(idx), f"{sts_sym} {r['gemeente']}", r["type"]]
        if url_check:
            rij_data.append(_http_cel(r))
        rij_data.append(bevindingen)
        tabel.add_row(*rij_data)

    console.print(tabel)


def _toon_samenvatting(resultaten: list[dict], scraper_check: list[tuple[str, bool]], url_check: bool) -> None:
    console.print(Rule("[bold]Samenvatting[/bold]", style="dim"))

    totaal      = len(resultaten)
    ok          = sum(1 for r in resultaten if r["status"] == _STATUS_OK)
    fouten      = sum(1 for r in resultaten if r["status"] == _STATUS_FOUT)
    lege_urls   = sum(1 for r in resultaten if not r["url"])
    overig      = sum(1 for r in resultaten if r["type"] == "overig")
    ontbreekt   = sum(1 for _, aanwezig in scraper_check if not aanwezig)

    # Verdeling per type
    type_telling: dict[str, int] = {}
    for r in resultaten:
        type_telling[r["type"]] = type_telling.get(r["type"], 0) + 1

    type_tabel = Table(
        box=box.SIMPLE, show_header=True, header_style="bold",
        title="[bold]Verdeling per type[/bold]", title_justify="left",
    )
    type_tabel.add_column("Type",       style="cyan", min_width=18)
    type_tabel.add_column("Scraper",    style="dim",  min_width=30)
    type_tabel.add_column("Aantal",     justify="right")

    for type_, count in sorted(type_telling.items(), key=lambda x: -x[1]):
        scraper = TYPES.get(type_, {}).get("scraper") or "-"
        kleur = "red" if type_ in ("overig", "leeg") else ""
        type_label = f"[{kleur}]{type_}[/{kleur}]" if kleur else type_
        tabel_scraper = f"[red]{scraper}[/red]" if type_ in ("overig", "leeg") else scraper
        type_tabel.add_row(type_label, tabel_scraper, str(count))
    type_tabel.add_row("[bold]TOTAAL[/bold]", "", f"[bold]{totaal}[/bold]")
    console.print(type_tabel)

    # Status panel
    http_ok        = sum(1 for r in resultaten if r.get("http") and r["http"].get("http_status") and r["http"]["http_status"] < 300)
    http_beschermd = sum(1 for r in resultaten if r.get("http")
                         and r["http"].get("http_status") == 403
                         and r.get("type") in _TYPE_VERWACHT_403)
    http_fout      = sum(1 for r in resultaten
                         if r.get("http")
                         and (r["http"].get("fout") or (r["http"].get("http_status") or 0) >= 400)
                         and not (r["http"].get("http_status") == 403 and r.get("type") in _TYPE_VERWACHT_403))

    regels = [
        f"  Totaal in CSV:               [bold]{totaal}[/bold]",
        f"  Zonder problemen  (OK):      [green]{ok}[/green]",
        f"  Met problemen     (!!):      {'[red]' if fouten else '[green]'}{fouten}{'[/red]' if fouten else '[/green]'}",
        f"  Lege URLs:                   {'[yellow]' if lege_urls else '[green]'}{lege_urls}{'[/yellow]' if lege_urls else '[/green]'}",
        f"  Onbekend type (overig):      {'[yellow]' if overig else '[green]'}{overig}{'[/yellow]' if overig else '[/green]'}",
        f"  Scraper-bestanden:           {'[green]alle aanwezig[/green]' if not ontbreekt else f'[red]{ontbreekt} ontbreekt[/red]'}",
    ]
    if url_check:
        regels += [
            f"  URLs bereikbaar (2xx):       [green]{http_ok}[/green]",
            f"  URLs beschermd (403, OK):    [dim]{http_beschermd}[/dim]",
            f"  URLs met HTTP-fout:          {'[red]' if http_fout else '[green]'}{http_fout}{'[/red]' if http_fout else '[/green]'}",
        ]

    alles_ok = fouten == 0 and not ontbreekt
    console.print(Panel("\n".join(regels), title="[bold]Status[/bold]",
                        border_style="green" if alles_ok else "red"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Health checker voor simba-source.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  uv run python health_check.py
  uv run python health_check.py --url-check
  uv run python health_check.py --type overig
  uv run python health_check.py --alleen-problemen
  uv run python health_check.py --url-check --werkers 30 --timeout 8
""",
    )
    parser.add_argument("--csv",              default=str(CSV_PAD),
                        help="pad naar CSV (default: simba-source.csv)")
    parser.add_argument("--url-check",        action="store_true",
                        help="ook HTTP-bereikbaarheid testen (langzaam, ~30-60s)")
    parser.add_argument("--type",             metavar="TYPE",
                        help="filter op type (bijv. overig, wordpress, deliberations)")
    parser.add_argument("--alleen-problemen", action="store_true",
                        help="toon alleen rijen met problemen")
    parser.add_argument("--werkers",          type=int, default=20,
                        help="parallelle HTTP-werkers bij --url-check (default: 20)")
    parser.add_argument("--timeout",          type=int, default=10,
                        help="HTTP timeout in seconden bij --url-check (default: 10)")
    args = parser.parse_args()

    csv_pad = Path(args.csv)
    if not csv_pad.exists():
        console.print(f"[red]Fout: CSV niet gevonden: {csv_pad}[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold]Health Checker — Besluitendatabank[/bold]\n"
        f"[dim]CSV: {csv_pad.resolve()}[/dim]\n"
        f"[dim]URL-check: {'aan' if args.url_check else 'uit (gebruik --url-check om te activeren)'}[/dim]",
        border_style="blue",
    ))

    rijen, meldingen = _lees_csv(csv_pad)
    for m in meldingen:
        console.print(f"[red][!] {m}[/red]")

    resultaten = _structurele_checks(rijen)

    scraper_check = _check_scrapers()
    _toon_scrapers(scraper_check)

    if args.url_check:
        resultaten = _url_check(resultaten, timeout=args.timeout, werkers=args.werkers)

    _toon_resultaten(resultaten, alleen_problemen=args.alleen_problemen,
                     type_filter=args.type, url_check=args.url_check)
    _toon_samenvatting(resultaten, scraper_check, url_check=args.url_check)


if __name__ == "__main__":
    main()
