"""
Scraper voor deliberations.be - Metadata van gemeentelijke beslissingen.

deliberations.be is een transparantieplatform van iMio (Plone CMS) dat door
~181 Waalse gemeenten wordt gebruikt voor het publiceren van gemeenteraads-
beslissingen en publicaties.

BELANGRIJKE OPMERKING:
Op dit moment (maart 2026) bevat deliberations.be voornamelijk METADATA
van beslissingen zonder consistente PDF bijlagen. De scraper verzamelt:
- Beslissingstities en beschrijvingen  
- Vergaderdata
- Statussen (projet/definitief)
- Links naar externe documenten (waar beschikbaar)

Output formaten:
- JSON: Gestructureerde data voor verdere verwerking
- HTML: Visueel overzicht met modern design

Structuur:
    https://deliberations.be/{gemeente}/decisions          → beslissingen overzicht
    https://deliberations.be/{gemeente}/decisions/@@faceted_query  → items ophalen
    https://deliberations.be/{gemeente}/publications       → publicaties

Gebruik:
    python scraper_deliberations.py --gemeente liege
    python scraper_deliberations.py --alle --no-pdfs
    python scraper_deliberations.py --lijst  # Lijst alle 178 gemeenten
"""

import argparse
import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from base_scraper import (
    ScraperConfig,
    create_session,
    sanitize_filename,
    robust_get,
    logger,
    download_document,
    DownloadResult,
)

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

BASE_URL = "https://deliberations.be"
SESSION: requests.Session | None = None
_config: ScraperConfig | None = None


def init_session(base_url: str | None = None) -> None:
    """Initialiseer HTTP-sessie."""
    global SESSION, _config, BASE_URL
    if base_url:
        BASE_URL = base_url.rstrip("/")
    _config = ScraperConfig(base_url=BASE_URL, rate_limit_delay=0.3)
    SESSION = create_session(_config)


def _get(url: str) -> requests.Response | None:
    """GET helper — pad wordt relatief aan BASE_URL opgelost."""
    full_url = url if url.startswith("http") else f"{BASE_URL}{url}"
    return robust_get(SESSION, full_url, retries=1, timeout=30)


# ---------------------------------------------------------------------------
# Gemeenten lijst (van CSV)
# ---------------------------------------------------------------------------

def haal_gemeenten_lijst() -> list[str]:
    """Haal lijst van deliberations.be gemeenten uit simba-source.csv."""
    csv_path = Path(__file__).parent / "simba-source.csv"
    if not csv_path.exists():
        logger.warning("simba-source.csv niet gevonden")
        return []
    
    gemeenten = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            bron = row.get('Bron', '')
            if 'deliberations.be' in bron:
                # Extraheer gemeente naam uit URL
                parsed = urlparse(bron)
                gemeente = parsed.path.strip('/').split('/')[0]
                if gemeente:
                    gemeenten.append(gemeente)
    
    return sorted(set(gemeenten))


# ---------------------------------------------------------------------------
# Beslissingen ophalen via faceted query
# ---------------------------------------------------------------------------

def haal_beslissingen(gemeente: str, max_items: int = 100) -> list[dict]:
    """
    Haal beslissingen op voor een gemeente.
    
    Returns lijst van {titel, url, datum, status, metadata} dicts.
    """
    url = f"{BASE_URL}/{gemeente}/decisions/@@faceted_query"
    params = {
        'b_size': str(max_items),
        'b_start': '0',
    }
    
    resp = _get(url + '?' + '&'.join(f"{k}={v}" for k, v in params.items()))
    if resp is None:
        return []
    
    soup = BeautifulSoup(resp.text, 'lxml')
    items = soup.find_all('div', class_='item-card')
    
    beslissingen = []
    for item in items:
        # Haal link en titel
        link = item.find('a', href=True)
        if not link:
            continue
        
        item_url = urljoin(f"{BASE_URL}/{gemeente}", link['href'])
        
        # Haal titel (vaak in <h3> of als link text)
        titel_elem = item.find(['h2', 'h3', 'h4'])
        titel = titel_elem.get_text(strip=True) if titel_elem else link.get_text(strip=True)
        
        # Haal metadata
        metadata = {}
        for row in item.find_all('div', class_='item-metadata-row'):
            label_elem = row.find('div', class_='item-metadata-label')
            value_elem = row.find('div', class_='item-metadata-value')
            if label_elem and value_elem:
                label = label_elem.get_text(strip=True)
                value = value_elem.get_text(strip=True)
                metadata[label] = value
        
        # Bepaal status (projet vs definitief)
        status = "definitief"
        if item.find('div', class_=lambda x: x and 'in_project' in str(x)):
            status = "projet"
        elif "Projet" in item.get_text():
            status = "projet"
        
        # Parse datum indien beschikbaar
        datum_str = metadata.get('Séance') or metadata.get('Date')
        datum = None
        if datum_str:
            # Probeer datum te parsen (bijv. "02 Mars 2026")
            try:
                from datetime import datetime
                # Simpele parse - kan worden verbeterd
                if '(' in datum_str:
                    datum_str = datum_str.split('(')[0].strip()
                # TODO: Betere datum parsing
            except:
                pass
        
        beslissingen.append({
            'titel': titel,
            'url': item_url,
            'datum': datum,
            'status': status,
            'metadata': metadata,
        })
    
    return beslissingen


# ---------------------------------------------------------------------------
# Publicaties ophalen
# ---------------------------------------------------------------------------

def haal_publicaties(gemeente: str, max_items: int = 100) -> list[dict]:
    """Haal publicaties op voor een gemeente (zelfde structuur als beslissingen)."""
    url = f"{BASE_URL}/{gemeente}/publications/@@faceted_query"
    params = {
        'b_size': str(max_items),
        'b_start': '0',
    }
    
    resp = _get(url + '?' + '&'.join(f"{k}={v}" for k, v in params.items()))
    if resp is None:
        return []
    
    soup = BeautifulSoup(resp.text, 'lxml')
    items = soup.find_all('div', class_='item-card')
    
    publicaties = []
    for item in items:
        link = item.find('a', href=True)
        if not link:
            continue
        
        item_url = urljoin(f"{BASE_URL}/{gemeente}", link['href'])
        titel = link.get_text(strip=True)
        
        publicaties.append({
            'titel': titel,
            'url': item_url,
        })
    
    return publicaties


# ---------------------------------------------------------------------------
# Zoek documenten op item pagina
# ---------------------------------------------------------------------------

def zoek_documenten(item_url: str) -> list[dict]:
    """
    Zoek documenten (PDF, Word) op een item detail pagina.
    
    Returns lijst van {url, naam, type} dicts.
    """
    resp = _get(item_url)
    if resp is None:
        return []
    
    soup = BeautifulSoup(resp.text, 'lxml')
    documenten = []
    
    for link in soup.find_all('a', href=True):
        href = link['href']
        text = link.get_text(strip=True)
        href_lower = href.lower()
        
        # Zoek PDF, Word documenten of download links
        doc_type = None
        default_name = 'document'
        
        if '.pdf' in href_lower:
            doc_type = 'pdf'
            default_name = 'document.pdf'
        elif '.doc' in href_lower or '.docx' in href_lower:
            doc_type = 'word'
            # Bepaal extensie
            if '.docx' in href_lower:
                default_name = 'document.docx'
            else:
                default_name = 'document.doc'
        elif 'download' in href_lower:
            # Generieke download link, probeer type te raden
            doc_type = 'unknown'
            default_name = 'document'
        
        if doc_type:
            full_url = urljoin(item_url, href)
            documenten.append({
                'url': full_url,
                'naam': text or default_name,
                'type': doc_type,
            })
    
    return documenten


# ---------------------------------------------------------------------------
# HTML Output Generator
# ---------------------------------------------------------------------------

def genereer_html(metadata: dict, output_path: Path) -> None:
    """
    Genereer een HTML overzicht van de metadata.
    """
    gemeente = metadata['gemeente']
    datum = metadata['datum']
    beslissingen = metadata.get('beslissingen', [])
    publicaties = metadata.get('publicaties', [])
    
    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{gemeente.title()} - Deliberations.be</title>
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
        
        h2 {{
            color: #2c3e50;
            margin-top: 40px;
            margin-bottom: 20px;
            font-size: 1.8em;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .badge {{
            background: #3498db;
            color: white;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.7em;
            font-weight: 600;
        }}
        
        .item {{
            background: #fff;
            border: 1px solid #e0e0e0;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 4px;
            transition: all 0.2s ease;
        }}
        
        .item:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            transform: translateY(-2px);
        }}
        
        .item-title {{
            font-size: 1.15em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
            line-height: 1.4;
        }}
        
        .item-title a {{
            color: #2c3e50;
            text-decoration: none;
            transition: color 0.2s;
        }}
        
        .item-title a:hover {{
            color: #3498db;
        }}
        
        .item-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-top: 10px;
        }}
        
        .item-meta-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.9em;
            color: #7f8c8d;
        }}
        
        .item-meta-item strong {{
            color: #555;
        }}
        
        .status {{
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        
        .status.projet {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .status.definitif {{
            background: #d4edda;
            color: #155724;
        }}
        
        .status.unknown {{
            background: #e2e3e5;
            color: #383d41;
        }}
        
        .metadata {{
            background: #f8f9fa;
            padding: 12px;
            border-radius: 4px;
            margin-top: 10px;
            font-size: 0.9em;
        }}
        
        .metadata-row {{
            display: grid;
            grid-template-columns: 150px 1fr;
            gap: 10px;
            padding: 6px 0;
            border-bottom: 1px solid #e9ecef;
        }}
        
        .metadata-row:last-child {{
            border-bottom: none;
        }}
        
        .metadata-label {{
            font-weight: 600;
            color: #6c757d;
        }}
        
        .metadata-value {{
            color: #495057;
        }}
        
        .documenten {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }}
        
        .doc-link {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 12px;
            background: #e8f4fd;
            color: #2980b9;
            border: 1px solid #bee3f8;
            border-radius: 20px;
            text-decoration: none;
            font-size: 0.85em;
            transition: all 0.2s;
        }}
        
        .doc-link:hover {{
            background: #2980b9;
            color: #fff;
            border-color: #2980b9;
        }}
        
        .footer {{
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            text-align: center;
            color: #7f8c8d;
            font-size: 0.9em;
        }}
        
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #95a5a6;
        }}
        
        .empty-state svg {{
            width: 80px;
            height: 80px;
            margin-bottom: 20px;
            opacity: 0.5;
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
                <span class="meta-label">Beslissingen</span>
                <span class="meta-value">{len(beslissingen)}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">Publicaties</span>
                <span class="meta-value">{len(publicaties)}</span>
            </div>
        </div>
"""
    
    # Beslissingen sectie
    if beslissingen:
        html += f"""
        <h2>📋 Beslissingen <span class="badge">{len(beslissingen)}</span></h2>
"""
        for item in beslissingen:
            status_class = 'projet' if item.get('status') == 'projet' else 'definitif' if item.get('status') == 'definitif' else 'unknown'
            status_text = item.get('status', 'onbekend').title()
            
            html += f"""
        <div class="item">
            <div class="item-title">
                <a href="{item['url']}" target="_blank">{item['titel']}</a>
            </div>
            <div class="item-meta">
                <div class="item-meta-item">
                    <span class="status {status_class}">{status_text}</span>
                </div>
            </div>
"""
            
            if item.get('metadata'):
                html += """
            <div class="metadata">
"""
                for key, value in item['metadata'].items():
                    if value:
                        html += f"""
                <div class="metadata-row">
                    <div class="metadata-label">{key}:</div>
                    <div class="metadata-value">{value}</div>
                </div>
"""
                html += """
            </div>
"""
            
            if item.get('documenten'):
                html += """
            <div class="documenten">
"""
                for doc in item['documenten']:
                    icon = '📄' if doc.get('type') == 'pdf' else '📝'
                    if doc.get('local_file'):
                        html += f"""
                <a class="doc-link" href="{doc['local_file']}">{icon} {doc['naam']}</a>
"""
                    else:
                        html += f"""
                <a class="doc-link" href="{doc['url']}" target="_blank">{icon} {doc['naam']}</a>
"""
                html += """
            </div>
"""
            
            html += """
        </div>
"""
    else:
        html += """
        <h2>📋 Beslissingen</h2>
        <div class="empty-state">
            <p>Geen beslissingen gevonden</p>
        </div>
"""
    
    # Publicaties sectie
    if publicaties:
        html += f"""
        <h2>📰 Publicaties <span class="badge">{len(publicaties)}</span></h2>
"""
        for item in publicaties:
            status_class = 'projet' if item.get('status') == 'projet' else 'definitif' if item.get('status') == 'definitif' else 'unknown'
            status_text = item.get('status', 'onbekend').title()
            
            html += f"""
        <div class="item">
            <div class="item-title">
                <a href="{item['url']}" target="_blank">{item['titel']}</a>
            </div>
            <div class="item-meta">
                <div class="item-meta-item">
                    <span class="status {status_class}">{status_text}</span>
                </div>
            </div>
"""
            
            if item.get('metadata'):
                html += """
            <div class="metadata">
"""
                for key, value in item['metadata'].items():
                    if value:
                        html += f"""
                <div class="metadata-row">
                    <div class="metadata-label">{key}:</div>
                    <div class="metadata-value">{value}</div>
                </div>
"""
                html += """
            </div>
"""
            
            if item.get('documenten'):
                html += """
            <div class="documenten">
"""
                for doc in item['documenten']:
                    icon = '📄' if doc.get('type') == 'pdf' else '📝'
                    if doc.get('local_file'):
                        html += f"""
                <a class="doc-link" href="{doc['local_file']}">{icon} {doc['naam']}</a>
"""
                    else:
                        html += f"""
                <a class="doc-link" href="{doc['url']}" target="_blank">{icon} {doc['naam']}</a>
"""
                html += """
            </div>
"""
            
            html += """
        </div>
"""
    else:
        html += """
        <h2>📰 Publicaties</h2>
        <div class="empty-state">
            <p>Geen publicaties gevonden</p>
        </div>
"""
    
    # Footer
    html += f"""
        <div class="footer">
            <p>Gegenereerd op {datum} • Bron: <a href="https://deliberations.be/{gemeente}" target="_blank">deliberations.be/{gemeente}</a></p>
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
    gemeente: str,
    output_dir: Path,
    max_items: int = 100,
    download_pdfs: bool = True,
) -> tuple[int, int]:
    """
    Scrape een gemeente van deliberations.be.
    
    Returns: (aantal_items, aantal_documenten)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*70}")
    print(f"  Gemeente: {gemeente}")
    print(f"  URL: {BASE_URL}/{gemeente}")
    print(f"  Output: {output_dir}")
    print(f"{'='*70}\n")
    
    # 1. Haal beslissingen op
    print("[1] Beslissingen ophalen...")
    beslissingen = haal_beslissingen(gemeente, max_items)
    print(f"    ✓ {len(beslissingen)} beslissingen gevonden")
    
    # 2. Haal publicaties op
    print("\n[2] Publicaties ophalen...")
    publicaties = haal_publicaties(gemeente, max_items)
    print(f"    ✓ {len(publicaties)} publicaties gevonden")
    
    alle_items = beslissingen + publicaties
    
    if not alle_items:
        print("\n    [!] Geen items gevonden voor deze gemeente")
        return 0, 0
    
    # 3. Sla metadata op als JSON
    print("\n[3] Metadata opslaan...")
    
    metadata = {
        'gemeente': gemeente,
        'datum': date.today().isoformat(),
        'aantal_beslissingen': len(beslissingen),
        'aantal_publicaties': len(publicaties),
        'beslissingen': beslissingen,
        'publicaties': publicaties,
    }
    
    # JSON output
    metadata_file = output_dir / f"{gemeente}_metadata.json"
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"    ✓ JSON: {metadata_file.name}")
    
    # 4. Zoek en download documenten (PDF, Word) (indien gewenst)
    doc_count = 0
    if download_pdfs:
        print(f"\n[4] Documenten zoeken (PDF, Word)...")
        
        for item in tqdm(alle_items[:20], desc="Items controleren"):  # Beperk tot eerste 20 voor snelheid
            documenten = zoek_documenten(item['url'])
            
            if documenten:
                doc_types = ', '.join(set(d['type'] for d in documenten))
                print(f"\n    ✓ {len(documenten)} document(en) gevonden ({doc_types}): {item['titel'][:50]}")
                
                # Bewaar documenten in item metadata voor HTML
                item['documenten'] = []
                
                for doc in documenten:
                    # Download document (PDF of Word)
                    if SESSION and _config:
                        # Voor Word documenten, require_pdf=False
                        require_pdf = (doc['type'] == 'pdf')
                        
                        result = download_document(
                            SESSION,
                            _config,
                            doc['url'],
                            output_dir,
                            doc['naam'],
                            require_pdf=require_pdf,
                        )
                        
                        if result.success and not result.skipped:
                            doc_count += 1
                            doc_type_label = doc['type'].upper()
                            print(f"      → [{doc_type_label}] {result.path.name}")
                            # Voeg lokaal bestand toe
                            item['documenten'].append({
                                'naam': doc['naam'],
                                'url': doc['url'],
                                'local_file': result.path.name,
                                'type': doc['type']
                            })
                        else:
                            # Voeg zonder lokaal bestand toe
                            item['documenten'].append({
                                'naam': doc['naam'],
                                'url': doc['url'],
                                'local_file': None,
                                'type': doc['type']
                            })
        
        if doc_count == 0:
            print("    ⚠ Geen documenten gevonden/gedownload")
    
    # 5. Update metadata en genereer HTML na documenten
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    # HTML output
    html_file = output_dir / f"{gemeente}.html"
    genereer_html(metadata, html_file)
    print(f"\n    ✓ HTML: {html_file.name}")
    
    return len(alle_items), doc_count


def haal_organen_statisch() -> list[dict]:
    """Deliberations.be heeft geen orgaanindeling — geeft altijd lege lijst terug."""
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper voor deliberations.be gemeenten (metadata + documenten)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  python scraper_deliberations.py --lijst
  python scraper_deliberations.py --gemeente liege
  python scraper_deliberations.py --gemeente braine-lalleud --max-items 50
  python scraper_deliberations.py --base-url https://deliberations.be/liege --output pdfs/liege
  python scraper_deliberations.py --alle --output-dir data/deliberations
        """,
    )

    # ── Standaard interface (scraper_groep.py / start.py compatibel) ────────
    parser.add_argument("--base-url", type=str,
                        help="Volledige gemeente-URL (bijv. https://deliberations.be/liege)")
    parser.add_argument("--orgaan", type=str,
                        help="Orgaan — deliberations.be heeft geen organen, wordt genegeerd")
    parser.add_argument("--maanden", type=int, default=None,
                        help="Periode in maanden (wordt omgezet naar --max-items)")
    parser.add_argument("--output", type=str, default=None,
                        help="Uitvoermap (alias voor --output-dir)")
    parser.add_argument("--document-filter", type=str,
                        help="Documentfilter — wordt genegeerd voor deliberations.be")
    parser.add_argument("--agendapunten", action="store_true",
                        help="Individuele besluiten — wordt genegeerd voor deliberations.be")
    parser.add_argument("--zichtbaar", action="store_true",
                        help="Browser zichtbaar — deliberations.be gebruikt geen browser")

    # ── Eigen interface ──────────────────────────────────────────────────────
    parser.add_argument("--gemeente", "-g", type=str,
                        help="Gemeente-slug (bijv. liege); gebruik --lijst voor opties")
    parser.add_argument("--alle", action="store_true",
                        help="Scrape alle deliberations.be gemeenten (zonder --base-url)")
    parser.add_argument("--lijst", action="store_true",
                        help="Toon lijst van beschikbare gemeenten")
    parser.add_argument("--output-dir", "-o", type=str, default="pdfs",
                        help="Output directory (standaard: pdfs)")
    parser.add_argument("--max-items", "-n", type=int, default=100,
                        help="Maximum aantal items per gemeente (standaard: 100)")
    parser.add_argument("--no-pdfs", action="store_true",
                        help="Sla alleen metadata op, geen documenten downloaden")

    args = parser.parse_args()

    # ── Standaard-args vertalen naar eigen args ──────────────────────────────
    # --base-url https://deliberations.be/liege  →  --gemeente liege
    if args.base_url and not args.gemeente:
        slug = urlparse(args.base_url).path.strip("/").split("/")[0]
        if slug:
            args.gemeente = slug

    # --output pad  →  --output-dir pad (alleen als niet al opgegeven)
    if args.output and args.output_dir == "pdfs":
        args.output_dir = args.output

    # --maanden N  →  max_items (ruwe schatting: ~8 items/maand)
    if args.maanden is not None and args.max_items == 100:
        args.max_items = max(50, args.maanden * 8)

    # --alle met --base-url = "alle organen van die gemeente" = gewoon scrapen
    # --alle zonder --base-url = alle deliberations-gemeenten (origineel gedrag)

    init_session()

    # ── Lijst tonen ──────────────────────────────────────────────────────────
    if args.lijst:
        print("\n📋 Deliberations.be gemeenten (uit simba-source.csv):\n")
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            print("   [!] Geen gemeenten gevonden in CSV")
            print("   Tip: Zorg dat simba-source.csv bestaat met deliberations.be URLs")
            return
        for i, gemeente in enumerate(gemeenten, 1):
            print(f"  {i:3}. {gemeente}")
        print(f"\n   Totaal: {len(gemeenten)} gemeenten")
        return

    # ── Validatie ────────────────────────────────────────────────────────────
    if not args.gemeente and not args.alle:
        print("❌ Geef --gemeente, --base-url of --alle op (gebruik --lijst voor opties)")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    download_pdfs = not args.no_pdfs

    # ── Enkele gemeente ──────────────────────────────────────────────────────
    if args.gemeente:
        totaal_items, totaal_docs = scrape_gemeente(
            args.gemeente,
            output_dir / args.gemeente,
            args.max_items,
            download_pdfs,
        )
        print(f"\n{'='*70}")
        print(f"  ✅ Klaar!")
        print(f"  Items verzameld: {totaal_items}")
        print(f"  Documenten gedownload: {totaal_docs}")
        print(f"{'='*70}")
        return

    # ── Alle gemeenten ───────────────────────────────────────────────────────
    if args.alle:
        gemeenten = haal_gemeenten_lijst()
        if not gemeenten:
            print("❌ Geen gemeenten gevonden in simba-source.csv")
            sys.exit(1)
        
        print(f"\n🚀 Scraping {len(gemeenten)} gemeenten...\n")
        
        totaal_items = 0
        totaal_docs = 0
        
        for i, gemeente in enumerate(gemeenten, 1):
            print(f"\n[{i}/{len(gemeenten)}] {gemeente}")
            items, docs = scrape_gemeente(
                gemeente,
                output_dir / gemeente,
                args.max_items,
                download_pdfs,
            )
            totaal_items += items
            totaal_docs += docs
            
            # Wacht even tussen gemeenten
            if i < len(gemeenten):
                time.sleep(1)
        
        print(f"\n{'='*70}")
        print(f"  ✅ ALLE GEMEENTEN KLAAR!")
        print(f"  Gemeenten: {len(gemeenten)}")
        print(f"  Totaal items: {totaal_items}")
        print(f"  Totaal documenten: {totaal_docs}")
        print(f"  Output: {output_dir.resolve()}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
