"""
filter_inhoud.py — Content-based PDF filter voor mandaatrelevante documenten.

Extraheert tekst uit elke PDF en verwijdert documenten zonder mandaatgerelateerde
inhoud. Gescande PDFs (geen extraheerbare tekst) worden standaard bewaard.

Gebruik:
    uv run python filter_inhoud.py              # dry run, toont statistieken
    uv run python filter_inhoud.py --delete     # verwijder irrelevante PDFs
    uv run python filter_inhoud.py --delete --log uitgesloten.csv
    uv run python filter_inhoud.py --gemeente Gent  # filter op 1 gemeente
"""

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mandaat_filter import is_relevant_bytes


def verwerk_bestand(pad: Path) -> dict:
    content = pad.read_bytes()
    relevant, is_gescand = is_relevant_bytes(content)
    if is_gescand:
        beslissing = "bewaard_gescand"
    elif relevant:
        beslissing = "bewaard_relevant"
    else:
        beslissing = "verwijderen"
    return {
        "pad": pad,
        "gemeente": pad.parts[-2] if len(pad.parts) >= 2 else "",
        "bestand": pad.name,
        "beslissing": beslissing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verwijder PDFs zonder mandaatgerelateerde inhoud.",
    )
    parser.add_argument("--delete", action="store_true",
                        help="Verwijder irrelevante bestanden (zonder: dry run)")
    parser.add_argument("--root", type=str, default="pdfs",
                        help="Root-map met gemeente-submappen (standaard: pdfs)")
    parser.add_argument("--gemeente", type=str, default=None,
                        help="Beperk tot een specifieke gemeente")
    parser.add_argument("--log", type=str, default=None,
                        help="Schrijf log van verwijderde bestanden naar CSV")
    parser.add_argument("--workers", type=int, default=8,
                        help="Aantal parallelle workers (standaard: 8)")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Map niet gevonden: {root}")
        sys.exit(1)

    if args.gemeente:
        gemeente_map = root / args.gemeente
        if not gemeente_map.exists():
            print(f"Gemeente niet gevonden: {gemeente_map}")
            sys.exit(1)
        bestanden = list(gemeente_map.rglob("*.pdf"))
    else:
        bestanden = list(root.rglob("*.pdf"))

    totaal = len(bestanden)
    modus = "VERWIJDEREN" if args.delete else "DRY RUN"
    print(f"\n=== filter_inhoud.py [{modus}] ===")
    print(f"Root    : {root.resolve()}")
    print(f"PDFs    : {totaal:,}")
    print(f"Workers : {args.workers}")
    print()

    if totaal == 0:
        print("Geen PDFs gevonden.")
        return

    resultaten = []
    verwerkt = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(verwerk_bestand, p): p for p in bestanden}
        for future in as_completed(futures):
            resultaten.append(future.result())
            verwerkt += 1
            if verwerkt % 500 == 0 or verwerkt == totaal:
                print(f"  {verwerkt:>6}/{totaal}  verwerkt...", end="\r", flush=True)

    print()

    relevant = [r for r in resultaten if r["beslissing"] == "bewaard_relevant"]
    gescand  = [r for r in resultaten if r["beslissing"] == "bewaard_gescand"]
    weg      = [r for r in resultaten if r["beslissing"] == "verwijderen"]

    print(f"\nResultaat:")
    print(f"  Bewaard (relevant) : {len(relevant):>6,}")
    print(f"  Bewaard (gescand)  : {len(gescand):>6,}  (geen tekst extraheerbaar)")
    print(f"  Te verwijderen     : {len(weg):>6,}")
    weg_mb = sum(r["pad"].stat().st_size for r in weg if r["pad"].exists()) / 1024 / 1024
    print(f"  Vrij te maken      : {weg_mb:>9,.1f} MB")

    if args.delete:
        verwijderd = 0
        fouten = 0
        for r in weg:
            try:
                r["pad"].unlink()
                verwijderd += 1
            except Exception as e:
                print(f"\n  [FOUT] {r['pad'].name}: {e}")
                fouten += 1
        samenvatting = f"{verwijderd:,} bestanden verwijderd"
        if fouten:
            samenvatting += f", {fouten} fouten"
        print(f"\n  {samenvatting}.")
    else:
        print(f"\nDry run - niets verwijderd. Voer opnieuw uit met --delete.")

    if args.log:
        with open(args.log, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["gemeente", "bestand", "beslissing", "pad"])
            writer.writeheader()
            for r in sorted(resultaten, key=lambda x: (x["gemeente"], x["bestand"])):
                writer.writerow({k: r[k] for k in ["gemeente", "bestand", "beslissing", "pad"]})
        print(f"  Log geschreven naar: {args.log}")

    print()


if __name__ == "__main__":
    main()
