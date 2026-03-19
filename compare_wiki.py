"""Compare Wikipedia list of Belgian municipalities with our CSV."""
import urllib.request, json, re, csv

# Fetch full wikitext of table section (section 14)
url = 'https://nl.wikipedia.org/w/api.php?action=parse&page=Tabel_van_Belgische_gemeenten&prop=wikitext&section=14&format=json'
req = urllib.request.Request(url, headers={'User-Agent': 'ScraperBrugge/1.0'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())
    wikitext = data['parse']['wikitext']['*']

# Extract municipality names
pattern = r'align="left"\|\s*\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\](\*{0,2})'
matches = re.findall(pattern, wikitext)
wiki_names = []
for link_target, display_name, stars in matches:
    if 'BE-vlag' in link_target:
        continue
    name = display_name.strip() if display_name else link_target.strip()
    wiki_names.append(name)

# Read CSV
with open('simba-source.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter=';')
    csv_rows = [(row['Gemeente'].strip(), row.get('Bron', '').strip()) for row in reader]
csv_names = set(r[0] for r in csv_rows)

# Known name mappings (CSV French → Wikipedia Dutch)
NAME_MAP = {
    'Arlon': 'Aarlen', 'Ath': 'Aat', 'Auderghem': 'Oudergem',
    'Bassenge': 'Bitsingen', 'Bastogne': 'Bastenaken',
    'Beauvechain': 'Bevekom', 'Berchem-Sainte-Agathe': 'Sint-Agatha-Berchem',
    'Braine-l\'Alleud': 'Eigenbrakel', 'Braine-le-Château': 'Kasteelbrakel',
    'Braine-le-Comte': '\'s-Gravenbrakel', 'Comines-Warneton': 'Komen-Waasten',
    'Ellezelles': 'Elzele', 'Enghien': 'Edingen', 'Estaimpuis': 'Steenput',
    'Flobecq': 'Vloesberg', 'Forest': 'Vorst', 'Gembloux': 'Gembloers',
    'Genappe': 'Genepiën', 'Grez-Doiceau': 'Graven', 'Hannut': 'Hannuit',
    'Huy': 'Hoei', 'Ittre': 'Itter', 'Ixelles': 'Elsene',
    'Jodoigne': 'Geldenaken', 'Jurbise': 'Jurbeke', 'La Hulpe': 'Terhulpen',
    'Lessines': 'Lessen', 'Liège': 'Luik', 'Limbourg': 'Limburg',
    'Lincent': 'Lijsem', 'Molenbeek-Saint-Jean': 'Sint-Jans-Molenbeek',
    'Mons': 'Bergen', 'Mouscron': 'Moeskroen', 'Namur': 'Namen',
    'Nivelles': 'Nijvel', 'Oreye': 'Oerle', 'Perwez': 'Perwijs',
    'Plombières': 'Blieberg', 'Saint-Gilles': 'Sint-Gillis',
    'Saint-Josse-ten-Noode': 'Sint-Joost-ten-Node',
    'Schaerbeek': 'Schaarbeek', 'Silly': 'Opzullik', 'Soignies': 'Zinnik',
    'Tournai': 'Doornik', 'Tubize': 'Tubeke', 'Uccle': 'Ukkel',
    'Waimes': 'Weismes', 'Waremme': 'Borgworm',
    'Watermael-Boitsfort': 'Watermaal-Bosvoorde', 'Wavre': 'Waver',
    'Woluwe-Saint-Lambert': 'Sint-Lambrechts-Woluwe',
    'Woluwe-Saint-Pierre': 'Sint-Pieters-Woluwe',
    'Vis\u00e9': 'Wezet',
}

# Minor spelling differences (CSV → Wikipedia)
SPELLING_MAP = {
    'Blegny': 'Blégny', 'Brussel -Bruxelles': 'Brussel',
    'Fontaine-l\'Évêque': 'Fontaine-l\'Evêque',
    'Le Rœulx': 'Le Roeulx', 'Sint-Genesius-Rhode': 'Sint-Genesius-Rode',
    'Sint-martens-Latem': 'Sint-Martens-Latem',
    'Écaussinnes': 'Ecaussines', 'Étalle': 'Etalle',
    'Erpe-mere': 'Erpe-Mere', 'Kapelle-Op-Den-Bos': 'Kapelle-op-den-Bos',
    'Langemark - Poelkapelle': 'Langemark-Poelkapelle',
    'Tongeren - Borgloon': 'Tongeren-Borgloon',
    'Melle - Merelbeke': 'Merelbeke-Melle',
}

# 2025 fusies: old names → new name
FUSIES_2025 = {
    'Borsbeek': 'Antwerpen',
    'De Pinte': 'Nazareth-De Pinte', 'Nazareth': 'Nazareth-De Pinte',
    'Galmaarden': 'Pajottegem', 'Gooik': 'Pajottegem', 'Herne': 'Pajottegem',
    'Ham': 'Tessenderlo-Ham', 'Tessenderlo': 'Tessenderlo-Ham',
    'Kortessem': 'Hasselt',
    'Meulebeke': 'Tielt',
    'Moerbeke': 'Lokeren',
    'Wachtebeke': 'Lochristi',
}

# 2019 fusies still wrong in CSV
FUSIES_2019 = {
    'Puurs': 'Puurs-Sint-Amands', 'Sint-Amands': 'Puurs-Sint-Amands',
}

# Build normalized CSV set
csv_normalized = set()
provinces = set()
for name in csv_names:
    if name.startswith('Provincie'):
        provinces.add(name)
        continue
    if name in NAME_MAP:
        csv_normalized.add(NAME_MAP[name])
    elif name in SPELLING_MAP:
        csv_normalized.add(SPELLING_MAP[name])
    elif name in FUSIES_2025:
        csv_normalized.add(FUSIES_2025[name])
    elif name in FUSIES_2019:
        csv_normalized.add(FUSIES_2019[name])
    else:
        csv_normalized.add(name)

wiki_set = set(wiki_names)

still_missing = wiki_set - csv_normalized
extra_in_csv = csv_normalized - wiki_set

# Summary
print(f'Wikipedia: {len(wiki_names)} gemeenten')
print(f'CSV: {len(csv_names)} rijen ({len(provinces)} provincies, {len(csv_names)-len(provinces)} gemeenten)')
print(f'Na normalisatie: {len(csv_normalized)} unieke gemeenten in CSV')
print(f'\n--- Provincies in CSV (geen gemeenten): ---')
for p in sorted(provinces):
    print(f'  {p}')

print(f'\n--- Frans/Nederlands naam-verschil ({len(NAME_MAP)} stuks, al correct): ---')
count = 0
for fr, nl in sorted(NAME_MAP.items()):
    if fr in csv_names:
        count += 1
print(f'  {count} van {len(NAME_MAP)} gevonden in CSV')

print(f'\n--- Spelling/accent-verschil ({len(SPELLING_MAP)} stuks): ---')
for old, new in sorted(SPELLING_MAP.items()):
    if old in csv_names:
        print(f'  {old} → {new}')

print(f'\n--- 2025 fusies - verouderde namen in CSV: ---')
for old, new in sorted(FUSIES_2025.items()):
    if old in csv_names:
        print(f'  {old} → {new}')

print(f'\n--- 2019 fusies - verouderde namen in CSV: ---')
for old, new in sorted(FUSIES_2019.items()):
    if old in csv_names:
        print(f'  {old} → {new}')

print(f'\n=== ECHT ONTBREKEND in CSV ({len(still_missing)}): ===')
for n in sorted(still_missing):
    print(f'  + {n}')

print(f'\n=== IN CSV maar NIET op Wikipedia ({len(extra_in_csv)}): ===')
for n in sorted(extra_in_csv):
    print(f'  - {n}')

