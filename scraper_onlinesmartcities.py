"""
Scraper voor onlinesmartcities.be - Gemeentelijke beslissingen en documenten.

onlinesmartcities.be is een platform gebruikt door 68 voornamelijk Vlaamse gemeenten
voor het publiceren van gemeenteraadszittingen, agenda's en beslissingen.

URL structuur:
    https://raadpleeg-{gemeente}.onlinesmartcities.be/
    /zittingen/lijst                                    → meetings overzicht
    /zittingen/{meeting_id}                             → meeting details
    /zittingen/{meeting_id}/agendapunten/{item_id}      → agendapunt details  
    /document/{document_id}                             → document download

Output formaten:
- JSON: Gestructureerde data voor verdere verwerking
- HTML: Visueel overzicht met modern design
- Documenten: PDF en Word bestanden

Gebruik:
    python scraper_onlinesmartcities.py --gemeente aalst
    python scraper_onlinesmartcities.py --alle --no-docs
    python scraper_onlinesmartcities.py --lijst
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    DownloadResult,
    create_session,
    download_document,
    robust_get,
    sanitize_filename,
)

# Globale variabelen
SESSION = None
_config = None

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

def init_session() -> None:
    """Initialiseer HTTP sessie"""
    global SESSION, _config
    _config = ScraperConfig(
        base_url="https://onlinesmartcities.be",
        rate_limit_delay=0.5,
        max_retries=3,
        timeout=30,
    )
    SESSION = create_session(_config)


def _get(url: str) -> requests.Response | None:
    """GET met rate limiting."""
    resp = robust_get(SESSION, url, retries=1, timeout=_config.timeout if _config else 30)
    if resp is not None:
        time.sleep(_config.rate_limit_delay if _config else 0.5)
    return resp


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def haal_gemeenten_lijst() -> list[dict]:
    """
    Haal lijst van gemeenten die onlinesmartcities.be gebruiken uit simba-source.csv
    
    Returns lijst van {naam, url_slug} dicts
    """
    gemeenten = []
    
    try:
        with open('simba-source.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                bron = row.get('Bron', '')
                if 'onlinesmartcities.be' in bron:
                    # Extract slug from URL: https://raadpleeg-aalst.onlinesmartcities.be/
                    match = re.search(r'raadpleeg-([^.]+)\.onlinesmartcities\.be', bron)
                    if match:
                        slug = match.group(1)
                        gemeenten.append({
                            'naam': row.get('Gemeente', ''),
                            'slug': slug,
                        })
    except FileNotFoundError:
        print("❌ simba-source.csv niet gevonden")
        return []
    
    return gemeenten


# ---------------------------------------------------------------------------
# Meetings ophalen
# ---------------------------------------------------------------------------

def haal_meetings(gemeente_slug: str, maanden: int = 3) -> list[dict]:
    """
    Haal meetings van de laatste X maanden op
    
    Returns lijst van {id, titel, datum, locatie, orgaan} dicts
    """
    base_url = f"https://raadpleeg-{gemeente_slug}.onlinesmartcities.be"
    meetings = []
    
    # Loop door laatste X maanden
    huidige_datum = date.today()
    
    for i in range(maanden):
        # Bereken maand en jaar
        target_date = huidige_datum - timedelta(days=30*i)
        month = target_date.month
        year = target_date.year
        
        url = f"{base_url}/zittingen/lijst?month={month:02d}&year={year}"
        resp = _get(url)
        
        if resp is None:
            continue
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Zoek meeting links
        meeting_links = soup.find_all('a', class_='meeting-detail', href=True)
        
        for link in meeting_links:
            href = link.get('href')
            
            # Extract meeting ID
            match = re.search(r'/zittingen/([^/]+)$', href)
            if not match:
                continue
            
            meeting_id = match.group(1)
            
            # Parse meeting info
            text = link.get_text(strip=True)
            
            # Extract datum (formaat: "orgaan datum - tijd locatie")
            datum_match = re.search(r'(\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2})', text)
            datum = datum_match.group(1) if datum_match else None
            
            meetings.append({
                'id': meeting_id,
                'titel': text,
                'datum': datum,
                'url': urljoin(base_url, href),
            })
    
    return meetings


# ---------------------------------------------------------------------------
# Meeting details ophalen
# ---------------------------------------------------------------------------

def haal_meeting_details(gemeente_slug: str, meeting_id: str) -> dict:
    """
    Haal details van een specifieke meeting op inclusief agendapunten
    
    Returns dict met metadata en agendapunten
    """
    base_url = f"https://raadpleeg-{gemeente_slug}.onlinesmartcities.be"
    meeting_url = f"{base_url}/zittingen/{meeting_id}"
    
    resp = _get(meeting_url)
    if resp is None:
        return {}
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Titel
    title_elem = soup.find('h1')
    titel = title_elem.get_text(strip=True) if title_elem else meeting_id
    
    # Metadata
    metadata = {'titel': titel}
    
    # Haal agendapunten op
    agendapunten = []
    
    # Zoek naar agendapunt links
    agendapunt_links = soup.find_all('a', href=re.compile(r'/agendapunten/'))
    
    seen_ids = set()
    for link in agendapunt_links:
        href = link.get('href')
        
        # Extract agendapunt ID
        match = re.search(r'/agendapunten/([^/?]+)', href)
        if not match:
            continue
        
        agendapunt_id = match.group(1)
        
        # Skip duplicates
        if agendapunt_id in seen_ids:
            continue
        seen_ids.add(agendapunt_id)
        
        # Get title from link or surrounding context
        title_text = link.get_text(strip=True)
        
        # Zoek naar nummer in de tekst (bijv. "12026_GR_00118")
        nummer_match = re.search(r'\d{5}_[A-Z]+_\d+', title_text)
        nummer = nummer_match.group(0) if nummer_match else None
        
        agendapunten.append({
            'id': agendapunt_id,
            'nummer': nummer,
            'titel': title_text[:200],  # Beperk lengte
            'url': urljoin(base_url, href),
        })
    
    metadata['agendapunten'] = agendapunten
    return metadata


# ---------------------------------------------------------------------------
# Agendapunt documenten ophalen
# ---------------------------------------------------------------------------

def haal_agendapunt_documenten(agendapunt_url: str) -> list[dict]:
    """
    Haal documenten van een agendapunt op
    
    Returns lijst van {id, naam, url} dicts
    """
    resp = _get(agendapunt_url)
    if resp is None:
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    documenten = []
    
    # Zoek document links
    doc_links = soup.find_all('a', href=re.compile(r'/document/'))
    
    for link in doc_links:
        href = link.get('href')
        
        # Extract document ID
        match = re.search(r'/document/([^/?]+)', href)
        if not match:
            continue
        
        doc_id = match.group(1)
        
        # Document naam
        doc_naam = link.get_text(strip=True)
        if not doc_naam:
            doc_naam = f"document_{doc_id}"
        
        # Voeg .pdf toe als er geen extensie is
        if not any(doc_naam.lower().endswith(ext) for ext in ['.pdf', '.doc', '.docx']):
            doc_naam += '.pdf'
        
        # Parse base URL from agendapunt URL
        parsed = urlparse(agendapunt_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        documenten.append({
            'id': doc_id,
            'naam': doc_naam,
            'url': urljoin(base_url, href),
        })
    
    return documenten


# ---------------------------------------------------------------------------
# HTML Output Generator  
# ---------------------------------------------------------------------------

def genereer_html(metadata: dict, output_path: Path) -> None:
    """
    Genereer een HTML overzicht van de metadata
    """
    gemeente = metadata['gemeente']
    datum = metadata['datum']
    meetings = metadata.get('meetings', [])
    
    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{gemeente.title()} - OnlineSmartCities</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
            margin-bottom: 30px;
            font-size: 2.2em;
        }}
        
        .meta-info {{
            background: #ecf0f1;
            padding: 20px;
            border-radius: 6px;
            margin-bottom: 30px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}
        
        .meta-item {{
            display: flex;
            flex-direction: column;
        }}
        
        .meta-label {{
            font-weight: 600;
            color: #7f8c8d;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .meta-value {{
            font-size: 1.3em;
            color: #2c3e50;
            margin-top: 5px;
        }}
        
        .meeting {{
            background: #fff;
            border: 1px solid #e0e0e0;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 4px;
        }}
        
        .meeting-title {{
            font-size: 1.3em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
        }}
        
        .meeting-date {{
            color: #7f8c8d;
            font-size: 0.95em;
            margin-bottom: 15px;
        }}
        
        .agendapunt {{
            background: #f8f9fa;
            padding: 15px;
            margin: 10px 0;
            border-radius: 4px;
            border-left: 3px solid #95a5a6;
        }}
        
        .agendapunt-title {{
            font-weight: 600;
            color: #34495e;
            margin-bottom: 8px;
        }}
        
        .agendapunt-nummer {{
            font-family: monospace;
            background: #e8f4f8;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.85em;
            margin-right: 8px;
        }}
        
        .documenten {{
            margin-top: 10px;
        }}
        
        .document-link {{
            display: inline-block;
            background: #3498db;
            color: white;
            padding: 6px 12px;
            border-radius: 4px;
            text-decoration: none;
            font-size: 0.9em;
            margin: 4px 4px 4px 0;
            transition: background 0.2s;
        }}
        
        .document-link:hover {{
            background: #2980b9;
        }}
        
        .footer {{
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            text-align: center;
            color: #7f8c8d;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🏛️ {gemeente.title()}</h1>
        
        <div class="meta-info">
            <div class="meta-item">
                <span class="meta-label">Gemeente</span>
                <span class="meta-value">{gemeente.title()}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Datum scraping</span>
                <span class="meta-value">{datum}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Zittingen</span>
                <span class="meta-value">{len(meetings)}</span>
            </div>
        </div>
"""
    
    # Meetings
    for meeting in meetings:
        html += f"""
        <div class="meeting">
            <div class="meeting-title">{meeting.get('titel', 'Zitting')}</div>
            <div class="meeting-date">{meeting.get('datum', 'Datum onbekend')}</div>
"""
        
        # Agendapunten
        for agendapunt in meeting.get('agendapunten', []):
            nummer_html = f'<span class="agendapunt-nummer">{agendapunt["nummer"]}</span>' if agendapunt.get('nummer') else ''
            
            html += f"""
            <div class="agendapunt">
                <div class="agendapunt-title">
                    {nummer_html}{agendapunt['titel']}
                </div>
"""
            
            # Documenten
            if agendapunt.get('documenten'):
                html += '                <div class="documenten">\n'
                for doc in agendapunt['documenten']:
                    # Gebruik lokaal bestand als het bestaat, anders online URL
                    if doc.get('local_file'):
                        # Link naar lokaal bestand (relatieve pad)
                        doc_link = doc['local_file']
                        target = ""  # Geen target voor lokale bestanden
                    else:
                        # Fallback naar online URL
                        doc_link = doc["url"]
                        target = ' target="_blank"'
                    
                    html += f'                    <a href="{doc_link}" class="document-link"{target}>📄 {doc["naam"]}</a>\n'
                html += '                </div>\n'
            
            html += '            </div>\n'
        
        html += '        </div>\n'
    
    # Footer
    html += f"""
        <div class="footer">
            <p>Gegenereerd op {datum} • Platform: OnlineSmartCities.be</p>
        </div>
    </div>
</body>
</html>
"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Hoofdlogica - Scrape gemeente
# ---------------------------------------------------------------------------

def scrape_gemeente(
    gemeente_slug: str,
    gemeente_naam: str,
    output_dir: Path,
    maanden: int = 3,
    download_docs: bool = True,
) -> tuple[int, int]:
    """
    Scrape een gemeente van onlinesmartcities.be
    
    Returns: (aantal_meetings, aantal_documenten)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*70}")
    print(f"  Gemeente: {gemeente_naam}")
    print(f"  Slug: {gemeente_slug}")
    print(f"  URL: https://raadpleeg-{gemeente_slug}.onlinesmartcities.be")
    print(f"  Output: {output_dir}")
    print(f"{'='*70}\n")
    
    # 1. Haal meetings op
    print(f"[1] Meetings ophalen (laatste {maanden} maanden)...")
    meetings = haal_meetings(gemeente_slug, maanden)
    print(f"    ✓ {len(meetings)} meetings gevonden")
    
    if not meetings:
        print("\n    [!] Geen meetings gevonden voor deze gemeente")
        return 0, 0
    
    # 2. Haal details op voor elk meeting
    print("\n[2] Meeting details ophalen...")
    
    for meeting in tqdm(meetings, desc="Meetings verwerken"):
        details = haal_meeting_details(gemeente_slug, meeting['id'])
        meeting.update(details)
        
        # Haal documenten op per agendapunt
        for agendapunt in meeting.get('agendapunten', []):
            documenten = haal_agendapunt_documenten(agendapunt['url'])
            agendapunt['documenten'] = documenten
    
    # 3. Download documenten en update metadata met lokale paden
    doc_count = 0
    if download_docs:
        print("\n[3] Documenten downloaden...")
        
        for meeting in meetings:
            for agendapunt in meeting.get('agendapunten', []):
                for doc in agendapunt.get('documenten', []):
                    if SESSION and _config:
                        filename = sanitize_filename(doc['naam'])
                        result = download_document(
                            SESSION,
                            _config,
                            doc['url'],
                            output_dir,
                            filename,
                            require_pdf=False,
                        )
                        
                        if result.success and not result.skipped:
                            doc_count += 1
                            # Voeg lokaal bestandspad toe aan metadata
                            doc['local_file'] = filename
                        else:
                            doc['local_file'] = None
        
        print(f"    ✓ {doc_count} documenten gedownload")
    
    # 4. Sla metadata op als JSON en HTML
    print("\n[4] Metadata opslaan...")
    
    metadata = {
        'gemeente': gemeente_naam,
        'slug': gemeente_slug,
        'datum': date.today().isoformat(),
        'aantal_meetings': len(meetings),
        'meetings': meetings,
    }
    
    # JSON output
    json_file = output_dir / f"{gemeente_slug}_metadata.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"    ✓ JSON: {json_file.name}")
    
    # HTML output
    html_file = output_dir / f"{gemeente_slug}.html"
    genereer_html(metadata, html_file)
    print(f"    ✓ HTML: {html_file.name}")
    
    return len(meetings), doc_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor onlinesmartcities.be gemeenten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  python scraper_onlinesmartcities.py --lijst
  python scraper_onlinesmartcities.py --gemeente aalst
  python scraper_onlinesmartcities.py --alle --maanden 6
        """,
    )
    
    parser.add_argument("--gemeente", "-g", type=str,
                        help="Gemeente slug (gebruik --lijst om opties te zien)")
    parser.add_argument("--alle", action="store_true",
                        help="Scrape alle onlinesmartcities.be gemeenten")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon lijst van beschikbare gemeenten")
    parser.add_argument("--output-dir", "-o", type=str, default="pdfs",
                        help="Output directory (standaard: pdfs)")
    parser.add_argument("--maanden", "-m", type=int, default=3,
                        help="Aantal maanden terug te scrapen (standaard: 3)")
    parser.add_argument("--no-docs", action="store_true",
                        help="Sla alleen metadata op, geen documenten downloaden")
    
    args = parser.parse_args()
    
    init_session()
    
    # Lijst gemeenten
    if args.lijst:
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            print("❌ Geen gemeenten gevonden in simba-source.csv")
            return
        
        print(f"\n📋 OnlineSmartCities.be gemeenten (uit simba-source.csv):\n")
        for i, gem in enumerate(gemeenten, 1):
            print(f"  {i:3}. {gem['slug']:<25} ({gem['naam']})")
        
        print(f"\n   Totaal: {len(gemeenten)} gemeenten")
        return
    
    # Validatie
    if not args.gemeente and not args.alle:
        print("❌ Geef --gemeente of --alle op (gebruik --lijst voor opties)")
        sys.exit(1)
    
    output_dir = Path(args.output_dir)
    download_docs = not args.no_docs
    
    # Scrape enkele gemeente
    if args.gemeente:
        # Zoek gemeente info
        gemeenten = haal_gemeenten_lijst()
        gemeente_info = next((g for g in gemeenten if g['slug'] == args.gemeente), None)
        
        if not gemeente_info:
            print(f"❌ Gemeente '{args.gemeente}' niet gevonden")
            print("   Gebruik --lijst om beschikbare gemeenten te zien")
            sys.exit(1)
        
        totaal_meetings, totaal_docs = scrape_gemeente(
            gemeente_info['slug'],
            gemeente_info['naam'],
            output_dir / gemeente_info['slug'],
            args.maanden,
            download_docs,
        )
        
        print(f"\n{'='*70}")
        print(f"  ✅ Klaar!")
        print(f"  Meetings verzameld: {totaal_meetings}")
        print(f"  Documenten gedownload: {totaal_docs}")
        print(f"{'='*70}")
        return
    
    # Scrape alle gemeenten
    if args.alle:
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            print("❌ Geen gemeenten gevonden in simba-source.csv")
            sys.exit(1)
        
        print(f"\n🚀 Scraping {len(gemeenten)} gemeenten...\n")
        
        totaal_meetings = 0
        totaal_docs = 0
        
        for i, gem in enumerate(gemeenten, 1):
            print(f"\n[{i}/{len(gemeenten)}] {gem['naam']}")
            meetings, docs = scrape_gemeente(
                gem['slug'],
                gem['naam'],
                output_dir / gem['slug'],
                args.maanden,
                download_docs,
            )
            totaal_meetings += meetings
            totaal_docs += docs
            
            # Wacht even tussen gemeenten
            if i < len(gemeenten):
                time.sleep(1)
        
        print(f"\n{'='*70}")
        print(f"  ✅ ALLE GEMEENTEN KLAAR!")
        print(f"  Gemeenten: {len(gemeenten)}")
        print(f"  Totaal meetings: {totaal_meetings}")
        print(f"  Totaal documenten: {totaal_docs}")
        print(f"  Output: {output_dir.resolve()}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
