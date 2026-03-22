"""Tijdelijk onderzoeksscript — te verwijderen na gebruik."""
import re
import sys
import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"

def check(url, label):
    try:
        r = s.get(url, timeout=20)
    except Exception as e:
        print(f"=== {label} === ERROR: {e}")
        return
    soup = BeautifulSoup(r.text, "html.parser")
    gen = soup.find("meta", attrs={"name": "generator"})
    gen_txt = gen["content"] if gen else "?"
    pdfs = [(a.get_text(strip=True)[:50], a["href"][:80])
            for a in soup.find_all("a", href=True)
            if ".pdf" in a["href"].lower()]
    print(f"=== {label} ===")
    print(f"  Status: {r.status_code} | Generator: {gen_txt} | PDFs: {len(pdfs)}")
    for p in pdfs[:4]:
        print(f"  PDF: {p}")
    # Key snippet
    body = soup.find("main") or soup.find("body")
    snippet = body.get_text(separator=" | ", strip=True)[:400] if body else r.text[:400]
    print(f"  Tekst: {snippet[:300]}")
    print()

mode = sys.argv[1] if len(sys.argv) > 1 else "all"

if mode in ("file_download", "all"):
    for gem, url in [
        ("Blankenberge", "https://www.blankenberge.be/file/download"),
        ("Eeklo",        "https://www.eeklo.be/file/download"),
        ("Kortenberg",   "https://www.kortenberg.be/file/download"),
        ("Pelt",         "https://www.gemeentepelt.be/file/download"),
        ("Lanaken",      "https://www.lanaken.be/file/download"),
    ]:
        check(url, f"file_download/{gem}")

if mode in ("cobra", "all"):
    check("https://www.dendermonde.be/cobra/gemeenteraad", "cobra/Dendermonde")
    check("https://www.evergem.be/cobra/Gemeenteraad", "cobra/Evergem")
    check("https://www.oudenaarde.be/sites/default/files/cobra", "cobra-drupal/Oudenaarde")

if mode in ("eigen", "all"):
    for gem, url in [
        ("Baarle-Hertog",   "https://www.baarle-hertog.be/bekendmakingen"),
        ("Boechout",        "https://www.boechout.be/"),
        ("Damme",           "https://www.damme.be/"),
        ("Hoogstraten",     "https://www.hoogstraten.be/besluiten-gemeenteraad"),
        ("Lendelede",       "https://www.lendelede.be/agenda-besluitenlijst-en-notulen-gemeenteraad"),
        ("Mesen",           "https://www.mesen.be/agenda-en-verslagen-gemeenteraad"),
        ("Temse",           "https://www.temse.be"),
        ("Wielsbeke",       "https://www.wielsbeke.be/agendas-en-verslagen"),
        ("Vleteren",        "https://www.vleteren.be/bestuur/informatie-en-inspraak/openbaarheid-van-bestuur"),
        ("Wervik",          "https://www.wervik.be/media"),
        ("Voeren",          "https://www.voeren.be/upload/pdf"),
        ("Heuvelland",      "https://heuvelland.paddlecms.net/"),
        ("Oosterzele",      "https://oosterzele.cdn.nomatron.be/"),
        ("Zuienkerke",      "https://www.mebosoft.be/zuienkerke_gemeente"),
    ]:
        check(url, f"eigen/{gem}")

if mode in ("drupal", "all"):
    for gem, url in [
        ("Dilbeek",                "https://www.dilbeek.be/gemeenteraad"),
        ("Knokke-Heist",           "https://www.knokke-heist.be/gemeenteraad"),
        ("Zoersel",                "https://www.zoersel.be/gemeenteraad"),
        ("Willebroek",             "https://www.willebroek.be/gemeenteraad"),
        ("Rijkevorsel",            "https://www.rijkevorsel.be/gemeenteraad"),
        ("Scherpenheuvel-Zichem",  "https://www.scherpenheuvel-zichem.be/gemeenteraad"),
    ]:
        check(url, f"drupal/{gem}")
