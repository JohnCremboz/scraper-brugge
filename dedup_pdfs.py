"""
dedup_pdfs.py — Spoort byte-identieke duplicaat-PDF's op binnen elke gemeente-map.

De scraper download soms hetzelfde document twee keer onder een andere naam
(bv. wanneer de bron-site dezelfde PDF onder meerdere links aanbiedt). Dat geeft
paren als "foo.pdf" / "foo_2.pdf" met exact dezelfde inhoud.

Detectie: groepeer per (map, bestandsgrootte), en bevestig met een SHA-256
hash dat de inhoud ook werkelijk identiek is — een gelijke grootte alleen is
geen bewijs.

Keuze welke kopie blijft staan:
  1. Bestand zonder "_N"-suffix vóór ".pdf" heeft voorrang (origineel).
  2. Anders: kortste naam, dan oudste wijzigingsdatum.

Gebruik:
    uv run python dedup_pdfs.py                       # dry run, alle gemeenten
    uv run python dedup_pdfs.py --delete               # effectief verwijderen
    uv run python dedup_pdfs.py --gemeente Villers-le-Bouillet
    uv run python dedup_pdfs.py --delete --log dedup.csv
"""

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path

_SUFFIX_RE = re.compile(r'^(?P<basis>.+?)_(?P<nummer>\d+)$')


def _hash(pad: Path, blokgrootte: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(pad, "rb") as f:
        while True:
            blok = f.read(blokgrootte)
            if not blok:
                break
            h.update(blok)
    return h.hexdigest()


def _kies_origineel(groep: list[Path]) -> Path:
    """Kies welk bestand uit een groep identieke bestanden de 'originele' naam heeft."""
    zonder_suffix = [p for p in groep if not _SUFFIX_RE.match(p.stem)]
    kandidaten = zonder_suffix if zonder_suffix else groep
    return min(kandidaten, key=lambda p: (len(p.name), p.stat().st_mtime))


def _gemeente(pad: Path, root: Path) -> str:
    try:
        return pad.relative_to(root).parts[0]
    except ValueError:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Verwijder byte-identieke duplicaat-PDF's per gemeente-map.")
    parser.add_argument("--delete", action="store_true",
                        help="Effectief verwijderen (zonder: dry run)")
    parser.add_argument("--root", default="pdfs",
                        help="Root-map (standaard: pdfs)")
    parser.add_argument("--gemeente", default=None,
                        help="Beperk tot een specifieke gemeente")
    parser.add_argument("--log", default=None,
                        help="Schrijf log naar CSV")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Map niet gevonden: {root}")
        sys.exit(1)

    zoekpad = root / args.gemeente if args.gemeente else root
    if not zoekpad.exists():
        print(f"Map niet gevonden: {zoekpad}")
        sys.exit(1)

    modus = "VERWIJDEREN" if args.delete else "DRY RUN"
    print(f"\n=== dedup_pdfs.py [{modus}] ===")
    print(f"Root: {zoekpad}\n")

    # Stap 1: groepeer per (map, grootte) — goedkope eerste filter
    print("Scannen...", flush=True)
    per_map_grootte: dict[tuple[Path, int], list[Path]] = {}
    teller = 0
    for pad in zoekpad.rglob("*.pdf"):
        if not pad.is_file():
            continue
        teller += 1
        try:
            grootte = pad.stat().st_size
        except OSError:
            continue
        per_map_grootte.setdefault((pad.parent, grootte), []).append(pad)

    kandidaat_groepen = [g for g in per_map_grootte.values() if len(g) > 1]
    print(f"  {teller:,} PDF's gescand, {len(kandidaat_groepen)} groep(en) met gelijke grootte\n")

    # Stap 2: bevestig met hash
    logregels = []
    duplicaten_gevonden = 0
    bytes_te_besparen = 0
    verwijderd = 0
    fouten = 0

    for groep in kandidaat_groepen:
        per_hash: dict[str, list[Path]] = {}
        for pad in groep:
            try:
                h = _hash(pad)
            except Exception as e:
                fouten += 1
                print(f"  [FOUT] kan {pad.name} niet hashen: {e}")
                continue
            per_hash.setdefault(h, []).append(pad)

        for h, identieke in per_hash.items():
            if len(identieke) < 2:
                continue
            origineel = _kies_origineel(identieke)
            duplicaten = [p for p in identieke if p != origineel]
            grootte = origineel.stat().st_size

            print(f"  Duplicaat ({len(identieke)}x, {grootte / 1024:.0f} KB) — behouden: {origineel.name}")
            for dup in duplicaten:
                duplicaten_gevonden += 1
                bytes_te_besparen += grootte
                status = "ongewijzigd (dry)"
                if args.delete:
                    try:
                        dup.unlink()
                        status = "verwijderd"
                        verwijderd += 1
                    except Exception as e:
                        status = f"fout: {e}"
                        fouten += 1
                        print(f"    [FOUT] kon {dup.name} niet verwijderen: {e}")
                print(f"    [{status}] {dup.name}")
                logregels.append({
                    "gemeente": _gemeente(dup, root),
                    "behouden": origineel.name,
                    "duplicaat": dup.name,
                    "map": str(dup.parent.relative_to(root)),
                    "grootte_kb": round(grootte / 1024, 1),
                    "hash": h[:16],
                    "status": status,
                })

    print(f"\nResultaat:")
    print(f"  Duplicaten gevonden : {duplicaten_gevonden:>6,}")
    print(f"  Te besparen         : {bytes_te_besparen / 1024 / 1024:>9.1f} MB")
    if args.delete:
        print(f"  Verwijderd          : {verwijderd:>6,}")
    if fouten:
        print(f"  Fouten              : {fouten:>6,}")

    if not args.delete and duplicaten_gevonden > 0:
        print(f"\nDry run - niets verwijderd. Voer opnieuw uit met --delete.")

    if args.log:
        with open(args.log, "w", newline="", encoding="utf-8") as f:
            velden = ["gemeente", "behouden", "duplicaat", "map", "grootte_kb", "hash", "status"]
            w = csv.DictWriter(f, fieldnames=velden)
            w.writeheader()
            w.writerows(logregels)
        print(f"\nLog geschreven naar: {args.log}")


if __name__ == "__main__":
    main()
