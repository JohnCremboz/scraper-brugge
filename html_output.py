"""
Gedeelde HTML-uitvoer hulpfuncties voor scraper-resultaten.

Exporteert:
  html_output_path()       - geconsolideerde locatie voor HTML-overzichtspaden
  doc_badges_html()       - document badges als HTML-string
  agendapunten_html()     - agendapuntenlijst (ul of ol) als HTML-string
  genereer_html_tabel()   - tabel-gebaseerde HTML-pagina (waalse_provincies,
                            provantwerpen, ibabs, vlaamsbrabant, ...)
  genereer_html_kaarten() - kaart-gebaseerde HTML-pagina gegroepeerd per map
                            (irisnet-stijl)
"""

from __future__ import annotations

from pathlib import Path

from base_scraper import sanitize_filename

# ---------------------------------------------------------------------------
# Gedeelde CSS voor tabel-layout
# ---------------------------------------------------------------------------

_TABEL_CSS = """
  body { font-family: sans-serif; margin: 2rem; }
  h1 { color: #003366; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ccc; padding: .5rem .75rem; vertical-align: top; }
  th { background: #003366; color: white; }
  tr:nth-child(even) { background: #f5f5f5; }
  .agendapunten { margin: 0; padding-left: 1.2rem; font-size: .85rem; }
  .documenten { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .4rem; }
  .doc-link { background: #e8f0fe; border: 1px solid #4285f4; border-radius: 3px;
              padding: 2px 6px; font-size: .8rem; text-decoration: none; color: #1a0dab; }
  .doc-link:hover { background: #d2e3fc; }
"""

# ---------------------------------------------------------------------------
# Hulpfuncties voor cel-inhoud
# ---------------------------------------------------------------------------

def html_output_path(output_dir: Path, naam: str, in_output_dir: bool = False) -> Path:
    """Bepaal het standaard pad voor HTML-uitvoerbestanden."""
    basis = output_dir if in_output_dir else output_dir.parent
    return basis / f"{sanitize_filename(naam)}.html"


def doc_badges_html(documenten: list[dict], output_pad: Path) -> str:
    """Render een lijst documenten als badge-links.

    Args:
        documenten: lijst van {'naam', 'url', optioneel 'local_file'}
        output_pad: pad naar het HTML-bestand zelf (voor relatieve links)
    """
    if not documenten:
        return ""
    badges = []
    for doc in documenten:
        if doc.get("local_file"):
            try:
                href = str(Path(doc["local_file"]).relative_to(output_pad.parent))
                extra = ""
            except ValueError:
                href = doc["url"]
                extra = " target='_blank'"
        else:
            href = doc["url"]
            extra = " target='_blank'"
        naam = doc["naam"]
        badges.append(f"<a class='doc-link' href='{href}'{extra}>{naam}</a>")
    return f"<div class='documenten'>{''.join(badges)}</div>"


def agendapunten_html(agendapunten: list[dict], genummerd: bool = False) -> str:
    """Render agendapunten als ul- of ol-lijst.

    Args:
        agendapunten: lijst van {'titel', optioneel 'nr'}
        genummerd: True → <ol> met nummers, False → <ul>
    """
    if not agendapunten:
        return ""
    tag = "ol" if genummerd else "ul"
    items = []
    for ap in agendapunten:
        if genummerd and ap.get("nr"):
            items.append(f"<li><strong>{ap['nr']}</strong> {ap['titel']}</li>")
        else:
            items.append(f"<li>{ap['titel']}</li>")
    return f"<{tag} class='agendapunten'>{''.join(items)}</{tag}>"


# ---------------------------------------------------------------------------
# Tabel-gebaseerde HTML-pagina
# ---------------------------------------------------------------------------

def genereer_html_tabel(
    naam: str,
    bron: str,
    kolommen: list[str],
    rijen: list[list[str]],
    output_pad: Path,
    lang: str = "nl",
    emoji: str = "🏛️",
    paginatitel: str | None = None,
) -> Path:
    """Genereer een tabel-gebaseerde HTML-pagina.

    Args:
        naam:       Naam van de entiteit (gebruikt in <h1> en <title>)
        bron:       Bron-omschrijving (bijv. "provincieantwerpen.be")
        kolommen:   Kolomnamen voor de tabelheader
        rijen:      Lijstvan rijen; elke rij is een lijst HTML-cel-inhoud strings.
                    Aantal elementen per rij moet overeenkomen met len(kolommen).
        output_pad: Volledig pad naar het te schrijven HTML-bestand
        lang:       HTML lang-attribuut (standaard "nl")
        emoji:      Emoji voor de h1 (standaard "🏛️")
        paginatitel: Optionele aangepaste <title>; standaard "{naam}"
    Returns:
        output_pad
    """
    output_pad.parent.mkdir(parents=True, exist_ok=True)
    titel = paginatitel or naam
    th_cells = "".join(f"<th>{k}</th>" for k in kolommen)

    rij_html = []
    for rij in rijen:
        td_cells = "".join(f"<td>{cel}</td>" for cel in rij)
        rij_html.append(f"<tr>{td_cells}</tr>")

    n = len(rijen)
    eenheid = "rij" if n == 1 else "rijen"

    html = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{titel}</title>
<style>{_TABEL_CSS}</style>
</head>
<body>
<h1>{emoji} {naam}</h1>
<p>Bron: {bron} — {n} {eenheid}</p>
<table>
  <thead><tr>{th_cells}</tr></thead>
  <tbody>{''.join(rij_html)}</tbody>
</table>
</body>
</html>"""

    output_pad.write_text(html, encoding="utf-8")
    return output_pad


# ---------------------------------------------------------------------------
# Kaart-gebaseerde HTML-pagina (irisnet-stijl)
# ---------------------------------------------------------------------------

def genereer_html_kaarten(
    naam: str,
    bron_url: str,
    docs: list[dict],
    output_pad: Path,
    lang: str = "nl",
    groepeer_op: str = "map",
    datum_veld: str = "datum_item",
    datum_label: str = "Vergadering",
    pub_datum_veld: str = "datum",
    pub_datum_label: str = "Publicatiedatum",
) -> Path:
    """Genereer een kaart-gebaseerde HTML-pagina gegroepeerd per categorie.

    Args:
        naam:             Naam van de entiteit (h1 en <title>)
        bron_url:         Volledige URL van de bronsite (footer)
        docs:             Lijst van document-dicts met minimaal:
                            'titel', 'url', optioneel 'local_path',
                            groepeer_op-veld, datum_veld, pub_datum_veld
        output_pad:       Volledig pad naar het te schrijven HTML-bestand
        lang:             HTML lang-attribuut
        groepeer_op:      Dict-sleutel om op te groeperen (standaard "map")
        datum_veld:       Sleutel voor vergaderingsdatum
        datum_label:      Label voor vergaderingsdatum
        pub_datum_veld:   Sleutel voor publicatiedatum
        pub_datum_label:  Label voor publicatiedatum
    Returns:
        output_pad
    """
    output_pad.parent.mkdir(parents=True, exist_ok=True)

    # Groepeer documenten
    groepen: dict[str, list[dict]] = {}
    for doc in docs:
        sleutel = doc.get(groepeer_op) or "Overig"
        groepen.setdefault(sleutel, []).append(doc)

    secties_html = ""
    for groep_naam, groep_docs in groepen.items():
        gesorteerd = sorted(groep_docs, key=lambda d: d.get(datum_veld, ""), reverse=True)
        kaarten = ""
        for doc in gesorteerd:
            titel = doc.get("titel") or "(onbekende titel)"
            datum = doc.get(datum_veld, "")
            pub_datum = doc.get(pub_datum_veld, "")
            local = doc.get("local_path")
            if local:
                try:
                    href = str(Path(local).relative_to(output_pad.parent))
                except ValueError:
                    href = doc["url"]
            else:
                href = doc["url"]
            kaarten += f"""\
<div class="doc-card">
  <div class="doc-icon">📄</div>
  <div class="doc-info">
    <a href="{href}" class="doc-title">{titel}</a>
    <div class="doc-meta">{datum_label}: {datum} &nbsp;|&nbsp; {pub_datum_label}: {pub_datum}</div>
  </div>
</div>
"""
        secties_html += f"""\
<h2 class="section-title">{groep_naam}</h2>
<div class="doc-grid">
{kaarten}</div>
"""

    html = f"""\
<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{naam}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; background: #f4f6fb; color: #333; }}
    .header {{ background: #1a237e; color: white; padding: 24px 32px; }}
    .header h1 {{ font-size: 1.8em; margin-bottom: 6px; }}
    .header p {{ opacity: 0.8; font-size: 0.95em; }}
    .container {{ max-width: 1100px; margin: 24px auto; padding: 0 20px; }}
    .section-title {{
      color: #1a237e; font-size: 1.15em;
      border-bottom: 2px solid #1a237e;
      padding-bottom: 6px; margin: 28px 0 14px;
    }}
    .doc-grid {{ display: grid; gap: 10px; }}
    .doc-card {{
      background: white; border-radius: 6px; padding: 14px 16px;
      display: flex; align-items: flex-start; gap: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,.1);
    }}
    .doc-icon {{ font-size: 1.4em; flex-shrink: 0; margin-top: 2px; }}
    .doc-title {{ color: #1a237e; font-weight: 600; text-decoration: none; }}
    .doc-title:hover {{ text-decoration: underline; }}
    .doc-meta {{ font-size: 0.83em; color: #777; margin-top: 4px; }}
    .footer {{ text-align: center; padding: 28px; color: #aaa; font-size: 0.85em; }}
    .footer a {{ color: #aaa; }}
  </style>
</head>
<body>
<div class="header">
  <h1>📋 {naam}</h1>
  <p>Publicaties via {bron_url} — {len(docs)} document(en) gevonden</p>
</div>
<div class="container">
{secties_html}
</div>
<div class="footer">
  Bron: <a href="{bron_url}">{bron_url}</a>
</div>
</body>
</html>
"""
    output_pad.write_text(html, encoding="utf-8")
    return output_pad
