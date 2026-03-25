"""Snelle gerichte test: alleen gr en rmw van Ranst, max 12 vergaderingen."""
import re
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import Counter

socket.setdefaulttimeout(6)

BASE_URL = "https://ranst.meetingburger.net"
ORGANS = ["gr", "rmw", "bb"]
MAX_VERG = 12

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def get_verg_urls(slug: str) -> list[str]:
    result, seen = [], set()
    for url in [f"{BASE_URL}/{slug}", f"{BASE_URL}/{slug}?AlleVergaderingen=True"]:
        try:
            r = s.get(url, timeout=(4, 6))
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.find_all("a", href=True):
                full = urljoin(BASE_URL, a["href"])
                p = urlparse(full)
                if p.netloc != urlparse(BASE_URL).netloc:
                    continue
                delen = [d for d in p.path.strip("/").split("/") if d]
                if len(delen) == 2 and delen[0] == slug and UUID_RE.match(delen[1]):
                    clean = f"{BASE_URL}/{slug}/{delen[1]}"
                    if clean not in seen:
                        seen.add(clean)
                        result.append(clean)
        except Exception as e:
            print(f"  [!] {url}: {e}")
    return result


def _fetch_docs(verg_url: str) -> list[str]:
    namen, seen = [], set()
    try:
        r = s.get(verg_url, timeout=(4, 6))
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
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_fetch_docs, verg_url)
        try:
            return future.result(timeout=9)
        except Exception:
            return []


totaal: list[str] = []

for slug in ORGANS:
    print(f"\n[{slug}]")
    verg_urls = get_verg_urls(slug)
    print(f"  {len(verg_urls)} vergaderingen, verwerk eerste {MAX_VERG}:")
    organ_namen: list[str] = []
    for verg_url in verg_urls[:MAX_VERG]:
        docs = get_docs(verg_url)
        if docs:
            print(f"    {verg_url.split('/')[-1][:8]}  {docs}")
        organ_namen.extend(docs)
        totaal.extend(docs)
    print(f"  Subtotaal: {len(organ_namen)} documenten")

print(f"\n{'='*60}")
print("Alle documentnamen (case-insensitief, gesorteerd op frequentie):")
for naam, n in Counter(t.lower() for t in totaal).most_common(40):
    print(f"  {n:3d}x  {naam[:90]}")
