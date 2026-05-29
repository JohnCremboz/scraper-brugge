"""
Investigation script: sample ~30 deliberations.be municipalities for structural differences.
Runs parallel HTTP requests using threading for efficiency.
"""

import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

sys.path.insert(0, '/home/gertjan/scraper-brugge')
from scraper_deliberations import init_session, _get, _PAGE_SIZE, BASE_URL, haal_gemeenten_lijst
from bs4 import BeautifulSoup

init_session()

# Full list of municipalities
ALL = haal_gemeenten_lijst()
print(f"Total municipalities: {len(ALL)}\n")

# Sample: large cities + medium + small rural, spread alphabetically
LARGE = ['liege', 'charleroi', 'namur', 'tournai', 'mons', 'mouscron', 'verviers', 'seraing']
MEDIUM = ['marche-en-famenne', 'wavre', 'gembloux', 'huy', 'nivelles', 'soignies', 'ath',
          'ottignies-louvain-la-neuve', 'malmedy', 'spa', 'dinant']
# Pick small ones spread across the alphabet
SMALL_CANDIDATES = [g for g in ALL if g not in LARGE + MEDIUM]
random.seed(42)
# Pick every ~5th small one for spread
SMALL = SMALL_CANDIDATES[::5][:15]

SAMPLE = list(dict.fromkeys(LARGE + MEDIUM + SMALL))  # deduplicate, preserve order
print(f"Sample of {len(SAMPLE)} municipalities:")
for m in SAMPLE:
    print(f"  {m}")
print()


def check_municipality(gemeente: str) -> dict:
    """Check a single municipality: page1, page2, CSS classes, séance date, spot-check a detail page."""
    result = {
        'gemeente': gemeente,
        'status': None,         # 'ok', 'error', 'empty'
        'http_code_p1': None,
        'items_p1': 0,
        'items_p2': 0,
        'has_pagination': False,
        'css_classes': [],
        'has_seance': False,
        'metadata_keys': [],
        'has_pdfs': None,       # None=not checked, True/False
        'pdf_count': 0,
        'anomalies': [],
        'detail_url': None,
    }

    base = f"https://deliberations.be/{gemeente}/decisions/@@faceted_query"

    # --- Page 1 ---
    try:
        resp = _get(base + "?" + urlencode({"b_size": "20", "b_start": "0"}))
    except Exception as e:
        result['status'] = 'error'
        result['anomalies'].append(f"Exception page1: {e}")
        return result

    if resp is None:
        result['status'] = 'error'
        result['anomalies'].append("_get returned None on page1")
        return result

    result['http_code_p1'] = resp.status_code

    if resp.status_code != 200:
        result['status'] = 'error'
        result['anomalies'].append(f"HTTP {resp.status_code} on page1")
        return result

    if not resp.text.strip():
        result['status'] = 'empty'
        result['anomalies'].append("Empty response body on page1")
        return result

    soup = BeautifulSoup(resp.text, 'lxml')
    cards = soup.find_all("div", class_="item-card")
    result['items_p1'] = len(cards)

    if not cards:
        result['status'] = 'empty'
        # Dig deeper: what IS in the response?
        body_text = soup.get_text(strip=True)[:300]
        result['anomalies'].append(f"No item-card divs. Body preview: {body_text!r}")

        # Check if maybe different CSS class
        all_divs = soup.find_all('div', class_=True)
        classes_seen = set()
        for d in all_divs:
            for c in d.get('class', []):
                classes_seen.add(c)
        result['css_classes'] = sorted(classes_seen)[:20]
        return result

    result['status'] = 'ok'

    # Collect CSS classes from first card
    first_card = cards[0]
    result['css_classes'] = first_card.get('class', [])

    # Check metadata keys and séance date in all cards
    all_meta_keys = set()
    for card in cards:
        for row in card.find_all("div", class_="item-metadata-row"):
            label = row.find("div", class_="item-metadata-label")
            if label:
                key = label.get_text(strip=True)
                all_meta_keys.add(key)
                if key in ("Séance", "Date", "Séance :"):
                    result['has_seance'] = True
    result['metadata_keys'] = sorted(all_meta_keys)

    # Check item-card sub-classes for variation
    card_classes_all = set()
    for card in cards:
        for c in card.get('class', []):
            card_classes_all.add(c)
    result['css_classes'] = sorted(card_classes_all)

    # Grab detail URL from first card
    link = first_card.find("a", href=True)
    if link:
        from urllib.parse import urljoin
        result['detail_url'] = urljoin(f"https://deliberations.be/{gemeente}/", link['href'])

    # Note if < 20 items (might indicate no pagination needed OR structure issue)
    if len(cards) < _PAGE_SIZE and len(cards) > 0:
        result['anomalies'].append(f"Only {len(cards)} items on page1 (< {_PAGE_SIZE})")

    # --- Page 2 (b_start=20) ---
    time.sleep(0.3)
    try:
        resp2 = _get(base + "?" + urlencode({"b_size": "20", "b_start": "20"}))
    except Exception as e:
        result['anomalies'].append(f"Exception page2: {e}")
        resp2 = None

    if resp2 and resp2.status_code == 200 and resp2.text.strip():
        soup2 = BeautifulSoup(resp2.text, 'lxml')
        cards2 = soup2.find_all("div", class_="item-card")
        result['items_p2'] = len(cards2)
        if len(cards2) > 0:
            result['has_pagination'] = True
        # Check if page2 returns SAME items as page1 (broken pagination)
        if cards2 and cards:
            first_p1 = cards[0].find("a", href=True)
            first_p2 = cards2[0].find("a", href=True)
            if first_p1 and first_p2 and first_p1.get('href') == first_p2.get('href'):
                result['anomalies'].append("PAGE2 RETURNS SAME ITEMS AS PAGE1 (broken pagination)")

    return result


def check_pdfs(result: dict) -> dict:
    """Spot-check the detail page of the first item for PDFs."""
    detail_url = result.get('detail_url')
    if not detail_url:
        result['has_pdfs'] = False
        return result

    time.sleep(0.3)
    try:
        resp = _get(detail_url)
    except Exception as e:
        result['anomalies'].append(f"Detail page exception: {e}")
        result['has_pdfs'] = False
        return result

    if resp is None or resp.status_code != 200:
        result['anomalies'].append(f"Detail page HTTP {resp.status_code if resp else 'None'}")
        result['has_pdfs'] = False
        return result

    soup = BeautifulSoup(resp.text, 'lxml')
    pdf_links = []
    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        if ".pdf" in href or ".docx" in href or ".doc" in href or "download" in href:
            pdf_links.append(link["href"])

    result['has_pdfs'] = len(pdf_links) > 0
    result['pdf_count'] = len(pdf_links)
    if not pdf_links:
        result['anomalies'].append("No PDF/doc links on detail page (first item)")

    return result


print("=" * 70)
print("Phase 1: Checking @@faceted_query for all sampled municipalities...")
print("=" * 70)

results = {}
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = {executor.submit(check_municipality, g): g for g in SAMPLE}
    for future in as_completed(futures):
        g = futures[future]
        try:
            r = future.result()
            results[g] = r
            status_icon = {"ok": "OK ", "empty": "EMPTY", "error": "ERR"}.get(r['status'], "???")
            print(f"  [{status_icon}] {g:<40} p1={r['items_p1']:3d} p2={r['items_p2']:3d} seance={r['has_seance']}")
        except Exception as e:
            results[g] = {'gemeente': g, 'status': 'exception', 'anomalies': [str(e)],
                          'items_p1': 0, 'items_p2': 0, 'has_pagination': False,
                          'has_seance': False, 'css_classes': [], 'metadata_keys': [],
                          'has_pdfs': None, 'pdf_count': 0, 'detail_url': None}
            print(f"  [EXC] {g:<40} Exception: {e}")

print()
print("=" * 70)
print("Phase 2: Spot-checking detail pages for PDFs (ok municipalities only)...")
print("=" * 70)

ok_results = [r for r in results.values() if r.get('status') == 'ok' and r.get('detail_url')]
with ThreadPoolExecutor(max_workers=6) as executor:
    futures = {executor.submit(check_pdfs, r): r['gemeente'] for r in ok_results}
    for future in as_completed(futures):
        g = futures[future]
        try:
            r = future.result()
            pdf_icon = "PDF+" if r.get('has_pdfs') else "no-pdf"
            print(f"  [{pdf_icon}] {g:<40} pdfs={r['pdf_count']}")
        except Exception as e:
            print(f"  [ERR] {g:<40} {e}")

print()
print("=" * 70)
print("FULL RESULTS TABLE")
print("=" * 70)
print(f"{'Municipality':<40} {'Status':<7} {'P1':>4} {'P2':>4} {'Pag':>4} {'Seance':>6} {'PDFs':>5}  Anomalies")
print("-" * 120)

anomaly_municipalities = []
for g in SAMPLE:
    r = results.get(g, {})
    status = r.get('status', 'N/A')
    p1 = r.get('items_p1', 0)
    p2 = r.get('items_p2', 0)
    pag = "Y" if r.get('has_pagination') else "N"
    seance = "Y" if r.get('has_seance') else "N"
    pdfs = str(r.get('pdf_count', '?')) if r.get('has_pdfs') is not None else '?'
    anomalies = r.get('anomalies', [])
    anom_str = "; ".join(anomalies) if anomalies else "-"
    print(f"{g:<40} {status:<7} {p1:>4} {p2:>4} {pag:>4} {seance:>6} {pdfs:>5}  {anom_str[:60]}")
    if anomalies or status != 'ok':
        anomaly_municipalities.append(g)

print()
print("=" * 70)
print("ANOMALY DETAILS")
print("=" * 70)
for g in anomaly_municipalities:
    r = results.get(g, {})
    print(f"\n--- {g} ---")
    print(f"  Status: {r.get('status')}")
    print(f"  HTTP:   {r.get('http_code_p1')}")
    print(f"  CSS classes on cards: {r.get('css_classes', [])}")
    print(f"  Metadata keys: {r.get('metadata_keys', [])}")
    for a in r.get('anomalies', []):
        print(f"  ! {a}")

print()
print("=" * 70)
print("CSS CLASS VARIATION ACROSS ALL SAMPLED MUNICIPALITIES")
print("=" * 70)
from collections import Counter
all_card_class_combos = Counter()
for g, r in results.items():
    if r.get('status') == 'ok':
        combo = tuple(sorted(r.get('css_classes', [])))
        all_card_class_combos[combo] += 1

for combo, count in all_card_class_combos.most_common():
    print(f"  {count:2d}x  {list(combo)}")

print()
print("=" * 70)
print("METADATA KEY VARIATION")
print("=" * 70)
meta_key_counter = Counter()
for g, r in results.items():
    if r.get('status') == 'ok':
        for k in r.get('metadata_keys', []):
            meta_key_counter[k] += 1

for key, count in meta_key_counter.most_common():
    print(f"  {count:2d}x  {key!r}")

print()
print("=" * 70)
print(f"SUMMARY: {len(SAMPLE)} sampled, "
      f"{sum(1 for r in results.values() if r.get('status')=='ok')} ok, "
      f"{sum(1 for r in results.values() if r.get('status')=='empty')} empty, "
      f"{sum(1 for r in results.values() if r.get('status')=='error')} error")
