"""
Blacklist-gebaseerde cleaner voor de pdfs/ scraper-uitvoermap.

Gebruik:
    uv run python clean_output.py              # dry run (veilig, toont wat verwijderd zou worden)
    uv run python clean_output.py --delete     # effectief verwijderen
    uv run python clean_output.py --delete --log removed.csv
    uv run python clean_output.py --root /pad/naar/pdfs
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from document_filters import matches_blacklist

ROOT_DEFAULT = Path(__file__).parent / "pdfs"


def scan(root: Path) -> tuple[list[Path], int]:
    """Geef (te-verwijderen bestanden, totaal gescand) terug."""
    hits: list[Path] = []
    totaal = 0
    huidige_gemeente = ""

    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        totaal += 1

        # Voortgangsindicator per gemeente
        try:
            gemeente = f.relative_to(root).parts[0]
        except (ValueError, IndexError):
            gemeente = ""
        if gemeente != huidige_gemeente:
            if huidige_gemeente:
                print()
            huidige_gemeente = gemeente
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {gemeente:<40}", end="", flush=True)

        if matches_blacklist(f.name):
            hits.append(f)

    if huidige_gemeente:
        print()
    return hits, totaal


def samenvatting_per_extensie(hits: list[Path]) -> None:
    from collections import defaultdict
    ext_count: dict[str, int] = defaultdict(int)
    ext_bytes: dict[str, int] = defaultdict(int)
    for f in hits:
        ext = f.suffix.lower() or "(geen)"
        ext_count[ext] += 1
        try:
            ext_bytes[ext] += f.stat().st_size
        except OSError:
            pass

    totaal_mb = sum(ext_bytes.values()) / 1_048_576
    print("Gevonden bestanden:")
    for ext in sorted(ext_count):
        mb = ext_bytes[ext] / 1_048_576
        print(f"  {ext:<10} {ext_count[ext]:>5} bestanden   {mb:>8.1f} MB")
    print(f"  {'TOTAAL':<10} {len(hits):>5} bestanden   {totaal_mb:>8.1f} MB")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Blacklist-gebaseerde cleaner voor scraper-uitvoer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--root", default=str(ROOT_DEFAULT),
        help=f"Uitvoermap (standaard: {ROOT_DEFAULT})",
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="Verwijder bestanden effectief (zonder: dry run)",
    )
    parser.add_argument(
        "--log", metavar="CSV",
        help="Schrijf verwijderde bestanden naar CSV-logbestand",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Map niet gevonden: {root}", file=sys.stderr)
        sys.exit(1)

    modus = "VERWIJDEREN" if args.delete else "DRY RUN"
    print()
    print(f"=== clean_output.py [{modus}] ===")
    print(f"Root: {root}")
    print()
    print("Scannen...")

    hits, totaal = scan(root)
    duur = ""  # timing optioneel

    print(f"{totaal} bestanden gescand, {len(hits)} op blacklist.")
    print()

    if not hits:
        print("Geen bestanden gevonden die overeenkomen met de blacklist.")
        return

    samenvatting_per_extensie(hits)

    log_rows: list[dict] = []
    for f in hits:
        try:
            rel = f.relative_to(root)
            gemeente = rel.parts[0] if rel.parts else ""
        except ValueError:
            rel = f
            gemeente = ""
        log_rows.append({
            "Gemeente": gemeente,
            "Extensie": f.suffix.lower(),
            "Bestand": f.name,
            "Pad": str(f),
            "GrootteMB": round(f.stat().st_size / 1_048_576, 3) if f.exists() else 0,
            "Verwijderd": False,
        })

    if not args.delete:
        print("Dry run — geen bestanden verwijderd.")
        print("Voer opnieuw uit met --delete om effectief te verwijderen.")
    else:
        deleted = 0
        errors = 0
        totaal_hits = len(hits)
        for f, row in zip(hits, log_rows):
            try:
                f.unlink()
                row["Verwijderd"] = True
                deleted += 1
                print(f"[{deleted:>6}/{totaal_hits}] Verwijderd: {f.name}")
            except OSError as e:
                errors += 1
                print(f"[{'FOUT':>6}/{totaal_hits}] {f.name}: {e}", file=sys.stderr)
        summary = f"{deleted} bestanden verwijderd"
        if errors:
            summary += f", {errors} fouten"
        print(summary)

    if args.log:
        log_path = Path(args.log)
        with open(log_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(log_rows[0].keys()))
            writer.writeheader()
            writer.writerows(log_rows)
        print(f"Log geschreven naar: {log_path}")

    print()


if __name__ == "__main__":
    main()
