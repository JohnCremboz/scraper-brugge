"""
Python-vervanger voor hernoem_per_gemeente.ps1.
Roept hernoem_besluiten.py op per gemeente-submap.
Een crash in PyMuPDF breekt enkel die gemeente af.

Gebruik:
    uv run python hernoem_per_gemeente.py              # dry run
    uv run python hernoem_per_gemeente.py --rename     # effectief hernoemen
    uv run python hernoem_per_gemeente.py --rename --log hernoem_totaal.csv
    uv run python hernoem_per_gemeente.py --root /pad/naar/pdfs
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

ROOT_DEFAULT = Path(__file__).parent / "pdfs"

_RE_HERNOEMD  = re.compile(r"Hernoemd\s*:\s*([\d,]+)")
_RE_GEEN_TITEL = re.compile(r"Geen titel\s*:\s*([\d,]+)")
_RE_FOUTEN    = re.compile(r"Fouten\s*:\s*([\d,]+)")


def _parse_int(pattern: re.Pattern, text: str) -> int:
    m = pattern.search(text)
    return int(m.group(1).replace(",", "")) if m else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hernoem besluiten per gemeente-submap.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--root", default=str(ROOT_DEFAULT),
                        help=f"Basis-uitvoermap (standaard: {ROOT_DEFAULT})")
    parser.add_argument("--rename", action="store_true",
                        help="Effectief hernoemen (zonder: dry run)")
    parser.add_argument("--log", metavar="CSV",
                        help="Schrijf totaaloverzicht naar CSV")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Map niet gevonden: {root}", file=sys.stderr)
        sys.exit(1)

    gemeenten = sorted(d for d in root.iterdir() if d.is_dir())
    totaal = len(gemeenten)
    modus = "HERNOEMEN" if args.rename else "DRY RUN"
    print(f"\n=== hernoem_per_gemeente.py [{modus}] ===")
    print(f"Root: {root}  ({totaal} gemeenten)\n")

    totaal_hernoemd = 0
    totaal_geen_titel = 0
    totaal_fouten = 0
    crashes: list[str] = []
    log_rows: list[dict] = []

    for i, gdir in enumerate(gemeenten, 1):
        naam = gdir.name
        print(f"[{i:>3}/{totaal}] {naam:<40}", end="", flush=True)

        cmd = ["uv", "run", "python", "hernoem_besluiten.py",
               "--gemeente", naam, "--root", str(root)]
        if args.rename:
            cmd.append("--rename")

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(__file__).parent),
                check=False,
            )
            if proc.returncode != 0:
                print(f" [CRASH exit={proc.returncode}]")
                crashes.append(naam)
                continue

            output = proc.stdout or ""
            hernoemd   = _parse_int(_RE_HERNOEMD,   output)
            geen_titel = _parse_int(_RE_GEEN_TITEL, output)
            fouten     = _parse_int(_RE_FOUTEN,     output)

            totaal_hernoemd   += hernoemd
            totaal_geen_titel += geen_titel
            totaal_fouten     += fouten

            label = "hernoemd" if args.rename else "te hernoemen"
            print(f" OK ({label}={hernoemd}, geen_titel={geen_titel})")

            if args.log:
                log_rows.append({
                    "Gemeente":    naam,
                    "Hernoemd":    hernoemd,
                    "Geen_titel":  geen_titel,
                    "Fouten":      fouten,
                    "Crash":       False,
                })

        except Exception as e:
            print(f" [FOUT: {e}]")
            crashes.append(naam)
            if args.log:
                log_rows.append({
                    "Gemeente": naam, "Hernoemd": 0,
                    "Geen_titel": 0, "Fouten": 0, "Crash": True,
                })

    print(f"\n=== Totaal [{modus}] ===")
    print(f"  Gemeenten     : {totaal - len(crashes):>6}")
    label = "Hernoemd" if args.rename else "Te hernoemen"
    print(f"  {label:<14}: {totaal_hernoemd:>6,}")
    print(f"  Geen titel    : {totaal_geen_titel:>6,}")
    if totaal_fouten:
        print(f"  Fouten        : {totaal_fouten:>6,}")
    if crashes:
        print(f"  Crashes ({len(crashes)}): {', '.join(crashes)}")
    print()

    if args.log and log_rows:
        log_path = Path(args.log)
        with open(log_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(log_rows[0].keys()))
            writer.writeheader()
            writer.writerows(log_rows)
        print(f"Log geschreven naar: {log_path}")


if __name__ == "__main__":
    main()
