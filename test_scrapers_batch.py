import random
import subprocess
import sys
from pathlib import Path

from scraper_groep import TYPES, lees_csv, groepeer, bouw_commando

def main():
    gemeenten = lees_csv()
    groepen = groepeer(gemeenten)

    scrapers_done = set()
    
    for type_key, config in TYPES.items():
        scraper = config.get("scraper")
        if not scraper:
            continue
        
        # We want to test each scraper script once.
        if scraper in scrapers_done:
            continue
            
        leden = [g for g in groepen.get(type_key, []) if g["url"]]
        if not leden:
            print(f"Geen URLs gevonden voor type {type_key} (scraper {scraper})")
            continue
            
        gemeente = random.choice(leden)
        cmd = bouw_commando(
            gemeente, 
            orgaan=None, 
            maanden=3, 
            output_basis="pdfs_batch_test",
            doc_filter=None, 
            agendapunten=False, 
            zichtbaar=False
        )
        
        if not cmd:
            continue
            
        scrapers_done.add(scraper)
            
        print(f"==================================================")
        print(f"Test scraper: {scraper} (Type: {type_key})")
        print(f"Gemeente: {gemeente['gemeente']} ({gemeente['url']})")
        print(f"Commando: {' '.join(cmd)}")
        print(f"==================================================")
        sys.stdout.flush()
        
        try:
            # Stuur stderr ook naar stdout
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,  # 5 minuten max per scraper
            )
            if proc.returncode == 0:
                print(f"✅ SUCCES")
                # Print laatste 10 regels output voor wat context
                lines = proc.stdout.strip().split('\n')
                print('\n'.join(lines[-10:]))
            else:
                print(f"❌ GEFAALD (exit code {proc.returncode})")
                print("--- Output ---")
                print(proc.stdout)
                print("--------------")
        except subprocess.TimeoutExpired as e:
            print(f"⏳ TIMEOUT (5m overschreden)")
            print("--- Output tot timeout ---")
            if hasattr(e, 'stdout') and e.stdout:
                print(e.stdout.decode('utf-8', errors='replace'))
            print("--------------------------")
        except Exception as e:
            print(f"❌ FOUT: {e}")
            
        print("\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
