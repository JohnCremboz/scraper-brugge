"""
Onderzoekscript: Welke deliberations.be-gemeenten hebben PDFs op eigen iMio-site?

Controleert voor elke gemeente in simba-source.csv (bron=deliberations.be):
1. Bestaat www.{gemeente}.be ?
2. Heeft die site iMio/Plone (footer bevat IMIO)?
3. Zijn er directe PDF-links voor procès-verbaux?

Gebruik:
    python investigate_imio.py
    python investigate_imio.py --max 20    (eerste N gemeenten)
    python investigate_imio.py --gemeente ciney
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Typische URL-paden die iMio-gemeenten gebruiken voor procès-verbaux
PV_PADEN = [
    "/ma-commune/vie-politique/conseil-communal/proces-verbaux",
    "/ma-ville/vie-politique/conseil-communal/proces-verbaux",
    "/vie-politique/conseil-communal/proces-verbaux",
    "/commune/conseil-communal/proces-verbaux",
    "/administration/conseil-communal/proces-verbaux",
    "/conseil-communal/proces-verbaux",
    "/ma-commune/vie-politique/conseil-communal/pv-du-conseil",
    "/ma-ville/vie-politique/conseil-communal/pv-du-conseil",
    "/vie-politique/conseil-communal/pv-du-conseil",
]

PDF_RE = re.compile(r'href=["\']([^"\']*\.pdf(?:/view)?)["\']', re.IGNORECASE)
IMIO_RE = re.compile(r'imio\.be|IMIO|Site réalisé.*?IMIO|collaboration avec.*?imio', re.IGNORECASE)
JAAR_RE = re.compile(r'href=["\']([^"\']*/(20\d{2})(?:-\d+)?/?)["\']')


def maak_sessie():
    sessie = requests.Session()
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    sessie.mount("http://", adapter)
    sessie.mount("https://", adapter)
    sessie.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) scraper-research/1.0",
        "Accept-Language": "fr-BE,fr;q=0.9",
    })
    return sessie


def haal_pagina(sessie, url, timeout=12):
    """Haal een pagina op, retourneer (status_code, html) of (None, None) bij fout."""
    try:
        r = sessie.get(url, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text
    except Exception as e:
        return None, None


def check_gemeente(sessie, gemeente_naam, bron_url):
    """
    Controleer of een gemeente eigen PDFs heeft op haar iMio-website.
    
    Returns dict met bevindingen.
    """
    resultaat = {
        "naam": gemeente_naam,
        "bron_url": bron_url,
        "eigen_domein": None,
        "imio_site": False,
        "pv_url": None,
        "heeft_pdfs": False,
        "heeft_jaarpaginas": False,
        "pdf_count": 0,
        "aanbeveling": "deliberations.be (ongewijzigd)",
        "opmerkingen": [],
    }

    # Bepaal basis-URL van eigen gemeente-website
    # Probeer www.{lowercase_slug}.be
    slug = gemeente_naam.lower()
    # Normaliseer speciale tekens
    slug = slug.replace("œ", "oe").replace("æ", "ae")
    slug = slug.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    slug = slug.replace("â", "a").replace("à", "a").replace("ä", "a")
    slug = slug.replace("î", "i").replace("ï", "i")
    slug = slug.replace("ô", "o").replace("ö", "o")
    slug = slug.replace("û", "u").replace("ü", "u").replace("ù", "u")
    slug = slug.replace("ç", "c").replace("ñ", "n")
    slug = slug.replace(" ", "-").replace("'", "-").replace("\u2019", "-")
    slug = slug.replace("(", "").replace(")", "")
    slug = re.sub(r"-+", "-", slug).strip("-")

    kandidaten = [
        f"https://www.{slug}.be",
        f"https://{slug}.be",
    ]

    basis_url = None
    for kandidaat in kandidaten:
        status, html = haal_pagina(sessie, kandidaat)
        if status == 200 and html:
            basis_url = kandidaat
            resultaat["eigen_domein"] = kandidaat
            # Controleer iMio-herkenning
            if IMIO_RE.search(html):
                resultaat["imio_site"] = True
            break
        time.sleep(0.1)

    if not basis_url:
        resultaat["opmerkingen"].append("Eigen website niet bereikbaar")
        return resultaat

    if not resultaat["imio_site"]:
        resultaat["opmerkingen"].append("Eigen website is GEEN iMio-site")

    # Nu de procès-verbaux pagina's proberen
    for pad in PV_PADEN:
        pv_url = basis_url + pad
        status, html = haal_pagina(sessie, pv_url)
        time.sleep(0.2)

        if status != 200 or not html:
            continue

        # Op HTML naar PDFs zoeken
        pdfs = PDF_RE.findall(html)
        # Filter: zelfde domein of relatieve paden
        domein = urlparse(basis_url).netloc
        echte_pdfs = [
            p for p in pdfs
            if p.startswith("/") or domein in p
        ]

        # Op HTML naar jaarpagina's zoeken (iMio-structuur A)
        jaarlinks = JAAR_RE.findall(html)
        jaarlinks_zelfde_domein = [
            href for href, jaar in jaarlinks
            if href.startswith("/") or domein in href
        ]

        resultaat["pv_url"] = pv_url
        resultaat["heeft_pdfs"] = len(echte_pdfs) > 0
        resultaat["heeft_jaarpaginas"] = len(jaarlinks_zelfde_domein) > 0
        resultaat["pdf_count"] = len(echte_pdfs)

        if echte_pdfs:
            resultaat["aanbeveling"] = f"EIGEN SITE: {pv_url}"
            resultaat["opmerkingen"].append(
                f"{len(echte_pdfs)} PDF(s) gevonden (pad: {pad})"
            )
            break
        elif jaarlinks_zelfde_domein:
            # Probeer de meest recente jaarpagina
            resultaat["heeft_jaarpaginas"] = True
            jaren = sorted(set(j for _, j in jaarlinks if j), reverse=True)
            resultaat["opmerkingen"].append(
                f"Jaarpagina's gevonden: {jaren[:3]} (pad: {pad}) - PDFs op subpaginas mogelijk"
            )
            # Probeer een jaarpagina te laden
            jaar_url = basis_url + jaarlinks_zelfde_domein[0] if jaarlinks_zelfde_domein[0].startswith("/") else jaarlinks_zelfde_domein[0]
            jaar_status, jaar_html = haal_pagina(sessie, jaar_url)
            time.sleep(0.2)
            if jaar_status == 200 and jaar_html:
                jaar_pdfs = PDF_RE.findall(jaar_html)
                jaar_pdfs_gefilterd = [p for p in jaar_pdfs if p.startswith("/") or domein in p]
                if jaar_pdfs_gefilterd:
                    resultaat["heeft_pdfs"] = True
                    resultaat["pdf_count"] = len(jaar_pdfs_gefilterd)
                    resultaat["aanbeveling"] = f"EIGEN SITE: {pv_url}"
                    resultaat["opmerkingen"].append(
                        f"{len(jaar_pdfs_gefilterd)} PDF(s) gevonden op jaarpagina {jaar_url}"
                    )
                    break
            break  # zelfs zonder jaar-PDFs: pad gevonden, stop de loop

    return resultaat


def haal_gemeenten(max_n=None):
    """Laad deliberations.be gemeenten uit simba-source.csv."""
    csv_path = Path(__file__).parent / "simba-source.csv"
    gemeenten = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            bron = row.get("Bron", "")
            naam = row.get("Gemeente", "") or row.get("Naam", "")
            if "deliberations.be" in bron and naam:
                gemeenten.append((naam, bron))
    gemeenten.sort(key=lambda x: x[0])
    if max_n:
        gemeenten = gemeenten[:max_n]
    return gemeenten


def main():
    parser = argparse.ArgumentParser(description="Onderzoek iMio-sites van deliberations.be-gemeenten")
    parser.add_argument("--max", type=int, help="Maximum aantal te controlleren gemeenten")
    parser.add_argument("--gemeente", help="Specifieke gemeente om te controleren")
    parser.add_argument("--output", default="imio_onderzoek.csv", help="Output CSV bestand")
    args = parser.parse_args()

    sessie = maak_sessie()

    if args.gemeente:
        naam = args.gemeente.capitalize()
        bron = f"https://www.deliberations.be/{args.gemeente.lower()}"
        gemeenten = [(naam, bron)]
    else:
        gemeenten = haal_gemeenten(args.max)

    print(f"Controleert {len(gemeenten)} gemeenten...\n")

    resultaten = []
    te_verplaatsen = []

    for i, (naam, bron) in enumerate(gemeenten, 1):
        print(f"[{i:3d}/{len(gemeenten)}] {naam:<35}", end=" ", flush=True)
        r = check_gemeente(sessie, naam, bron)
        resultaten.append(r)

        if r["heeft_pdfs"]:
            symbool = "OK PDFs"
            te_verplaatsen.append(r)
        elif r["heeft_jaarpaginas"]:
            symbool = "~ jaarlinks (geen PDF direct)"
        elif r["eigen_domein"] and r["imio_site"]:
            symbool = "iMio site (geen PV pad)"
        elif r["eigen_domein"]:
            symbool = "eigen site (niet iMio)"
        else:
            symbool = "geen eigen site"

        print(symbool)
        if r["opmerkingen"]:
            for opmerking in r["opmerkingen"]:
                print(f"         {opmerking}")
        time.sleep(0.3)

    # Schrijf CSV
    output_path = Path(__file__).parent / args.output
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "naam", "bron_url", "eigen_domein", "imio_site",
            "pv_url", "heeft_pdfs", "heeft_jaarpaginas", "pdf_count",
            "aanbeveling", "opmerkingen"
        ], delimiter=";")
        writer.writeheader()
        for r in resultaten:
            r["opmerkingen"] = " | ".join(r["opmerkingen"])
            writer.writerow(r)

    # Overzicht
    print(f"\n{'='*60}")
    print(f"SAMENVATTING van {len(gemeenten)} gecontroleerde gemeenten:")
    print(f"  OK Gemeenten met eigen PDFs:  {len(te_verplaatsen)}")
    print(f"  Resultaten opgeslagen in:   {output_path}")

    if te_verplaatsen:
        print(f"\nGEMEENTEN DIE VERPLAATST MOETEN WORDEN naar eigen site:")
        print(f"{'Naam':<30} {'PV URL'}")
        print("-" * 80)
        for r in te_verplaatsen:
            print(f"{r['naam']:<30} {r['pv_url']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
