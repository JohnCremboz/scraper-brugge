"""Snelle gerichte test: documentnamen op meetingburger.net portalen."""
import re
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter

# Harde socket-timeout om hangende SSL-verbindingen te voorkomen
socket.setdefaulttimeout(6)

SITES = [
    ("https://ranst.meetingburger.net", "gr"),
    ("https://bornem.meetingburger.net", None),
    ("https://brecht.meetingburger.net", None),
]

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def get_verg_urls(base_url: str, slug: str) -> list[str]:
    result = []
    seen: set[str] = set()
    for url in [f"{base_url}/{slug}", f"{base_url}/{slug}?AlleVergaderingen=True"]:
        try:
            r = s.get(url, timeout=10, allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.find_all("a", href=True):
                full = urljoin(base_url, a["href"])
                p = urlparse(full)
                if p.netloc != urlparse(base_url).netloc:
                    continue
                delen = [d for d in p.path.strip("/").split("/") if d]
                if len(delen) == 2 and delen[0] == slug and UUID_RE.match(delen[1]):
                    clean = f"{base_url}/{slug}/{delen[1]}"
                    if clean not in seen:
                        seen.add(clean)
                        result.append(clean)
        except Exception as e:
            print(f"  [!] {url}: {e}")
    return result


def get_organen(base_url: str) -> list[str]:
    gezien: set[str] = set()
    skip = {"search", "bekendmakingen", "pages", ""}
    slugs = []
    try:
        r = s.get(base_url, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            full = urljoin(base_url, a["href"])
            p = urlparse(full)
            if p.netloc != urlparse(base_url).netloc:
                continue
            delen = [d for d in p.path.strip("/").split("/") if d]
            if len(delen) == 2 and UUID_RE.match(delen[1]) and delen[0] not in skip:
                if delen[0] not in gezien:
                    gezien.add(delen[0])
                    slugs.append(delen[0])
    except Exception as e:
        print(f"  [!] organen {base_url}: {e}")
    return slugs


def _get_docs_inner(verg_url: str) -> list[str]:
    namen = []
    seen: set[str] = set()
    try:
        r = s.get(verg_url, timeout=(4, 6), allow_redirects=True)
        if r.status_code != 200:
            return namen
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "HandleFile.ashx" not in href:
                continue
            t = a.get_text(strip=True)
            if not t or t.lower() == "download":
                continue
            m = re.search(r"[?&]id=([^&]+)", href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                namen.append(t)
    except Exception:
        pass
    return namen


def get_docs(verg_url: str) -> list[str]:
    """Haalt documenten op met harde 10-seconden thread-timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_get_docs_inner, verg_url)
        try:
            return future.result(timeout=10)
        except (FuturesTimeout, Exception):
            return []


totaal_namen: list[str] = []

for base_url, geforceerde_slug in SITES:
    print(f"\n{'='*60}")
    print(f"SITE: {base_url}")
    slugs = get_organen(base_url)
    print(f"  Organen: {slugs}")
    if geforceerde_slug and geforceerde_slug not in slugs:
        slugs = [geforceerde_slug] + slugs
    # CBS/VABU = privaat, overslaan voor het onderzoek
    slugs = [s for s in slugs if s not in ("cbs", "vabu")]
    site_namen: list[str] = []
    for slug in slugs[:5]:
        print(f"\n  [{slug}]")
        verg_urls = get_verg_urls(base_url, slug)
        print(f"    {len(verg_urls)} vergaderingen, verwerk eerste 8...")
        for verg_url in verg_urls[:8]:
            docs = get_docs(verg_url)
            if docs:
                print(f"      {verg_url.split('/')[-1][:8]}  ->  {docs[:5]}")
            site_namen.extend(docs)
            totaal_namen.extend(docs)
    print(f"\n  Top namen voor {base_url.split('//')[1]}:")
    for naam, n in Counter(t.lower() for t in site_namen).most_common(20):
        print(f"    {n:3d}x  {naam[:80]}")

print(f"\n{'='*60}")
print("TOTAAL over alle sites:")
for naam, n in Counter(t.lower() for t in totaal_namen).most_common(30):
    print(f"  {n:4d}x  {naam[:80]}")
