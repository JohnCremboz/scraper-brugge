"""
Onderzoeksscript: welke documentnamen gebruiken gemeenten op MeetingBurger-portalen?
Crawlt vergaderingen en toont de frequentste documentnamen.

Gebruik:
    uv run python _meetingburger_onderzoek.py
"""

import re
import sys
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SITES = [
    "https://ranst.meetingburger.net",
    "https://bornem.meetingburger.net",
    "https://brecht.meetingburger.net",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (onderzoeksbot; niet-commercieel)",
    "Accept-Language": "nl-BE,nl;q=0.9",
})

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)
MAX_VERGADERINGEN = 10  # per orgaan, per site


def haal_organen(base_url: str) -> list[dict]:
    organen = []
    gezien: set[str] = set()
    skip = {"search", "bekendmakingen", "pages", ""}
    for url in [base_url, f"{base_url}?AlleVergaderingen=True"]:
        try:
            resp = SESSION.get(url, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = urljoin(base_url, href)
                parsed = urlparse(full)
                if parsed.netloc != urlparse(base_url).netloc:
                    continue
                delen = [s for s in parsed.path.strip("/").split("/") if s]
                if len(delen) == 2 and UUID_RE.match(delen[1]) and delen[0] not in skip:
                    slug = delen[0]
                    if slug not in gezien:
                        gezien.add(slug)
                        naam = a.get_text(strip=True)
                        organen.append({"naam": naam, "slug": slug})
        except Exception as e:
            print(f"  [!] Fout organen {url}: {e}")
    return organen


def haal_vergadering_links(base_url: str, slug: str) -> list[str]:
    urls: list[str] = []
    gezien: set[str] = set()
    for url in [f"{base_url}/{slug}", f"{base_url}/{slug}?AlleVergaderingen=True"]:
        try:
            resp = SESSION.get(url, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                full = urljoin(base_url, a["href"])
                parsed = urlparse(full)
                if parsed.netloc != urlparse(base_url).netloc:
                    continue
                delen = [s for s in parsed.path.strip("/").split("/") if s]
                if len(delen) == 2 and delen[0] == slug and UUID_RE.match(delen[1]):
                    clean = f"{base_url}/{delen[0]}/{delen[1]}"
                    if clean not in gezien:
                        gezien.add(clean)
                        urls.append(clean)
        except Exception as e:
            print(f"  [!] Fout vergaderingen {url}: {e}")
    return urls


def haal_documentnamen(verg_url: str) -> list[str]:
    namen: list[str] = []
    seen_ids: set[str] = set()
    for subpad in [verg_url, f"{verg_url}/agenda", f"{verg_url}/besluitenlijst", f"{verg_url}/notulen"]:
        try:
            resp = SESSION.get(subpad, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "HandleFile.ashx" not in href:
                    continue
                tekst = a.get_text(strip=True)
                if tekst.lower() == "download":
                    continue
                m = re.search(r'[?&]id=([^&]+)', href)
                if not m:
                    continue
                file_id = m.group(1)
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)
                if tekst:
                    namen.append(tekst)
        except Exception:
            pass
    return namen


def classify(naam: str) -> str:
    n = naam.lower()
    for term in ["notulen", "verslag", "zittingsverslag", "besluitenlijst",
                 "uittreksel", "dagorde", "agenda", "ontwerpbesluitenbundel",
                 "besluit", "toelichting", "bijlage", "rapport", "advies",
                 "reglement", "overeenkomst", "contract", "samenstelling"]:
        if term in n:
            return term
    return "overige"


def main():
    totaal_namen: list[str] = []
    totaal_klassen: Counter = Counter()

    for base_url in SITES:
        print(f"\n{'='*60}")
        print(f"SITE: {base_url}")
        print(f"{'='*60}")
        organen = haal_organen(base_url)
        print(f"  {len(organen)} orgaan/organen gevonden: {[o['slug'] for o in organen]}")

        site_namen: list[str] = []

        for org in organen:
            verg_links = haal_vergadering_links(base_url, org["slug"])
            print(f"\n  [{org['slug']}] {len(verg_links)} vergaderingen")
            for verg_url in verg_links[:MAX_VERGADERINGEN]:
                namen = haal_documentnamen(verg_url)
                if namen:
                    print(f"    {verg_url.split('/')[-1][:8]}…  {namen[:5]}")
                site_namen.extend(namen)
                totaal_namen.extend(namen)

        print(f"\n  --- Top documentnamen voor {base_url} ---")
        teller = Counter(n.lower().strip() for n in site_namen)
        for naam, aantal in teller.most_common(30):
            klasse = classify(naam)
            print(f"    {aantal:4d}x  [{klasse}]  {naam[:80]}")

        for naam in site_namen:
            totaal_klassen[classify(naam)] += 1

    print(f"\n\n{'='*60}")
    print("TOTAALOVERZICHT: Klassen over alle sites")
    print(f"{'='*60}")
    for klasse, aantal in totaal_klassen.most_common():
        print(f"  {aantal:5d}x  {klasse}")

    print(f"\n{'='*60}")
    print(f"Top 40 unieke documentnamen (alle sites)")
    print(f"{'='*60}")
    totaal_teller = Counter(n.lower().strip() for n in totaal_namen)
    for naam, aantal in totaal_teller.most_common(40):
        print(f"  {aantal:5d}x  {naam[:90]}")


if __name__ == "__main__":
    main()
