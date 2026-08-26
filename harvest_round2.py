#!/usr/bin/env python3
"""Ikinci tur sehir taramasi: oncelikli ulkelerin henuz taranmamis sehirleri.

Birinci turda 62 sehir tarandi. Bu tur, kampanyanin en cok cevap aldigi
ulkelere (DE, NL, IE, PL, PT) yogunlasip yeni sehirler ekler.
"""
from __future__ import annotations

import subprocess
import sys
import time

# (sehir, ulke kodu) - birinci turda taranmayanlar
SEHIRLER = [
    # Almanya - kampanyanin en buyuk kolu
    ("Stuttgart", "DE"), ("Düsseldorf", "DE"), ("Leipzig", "DE"), ("Dresden", "DE"),
    ("Hannover", "DE"), ("Nürnberg", "DE"), ("Bremen", "DE"), ("Bochum", "DE"),
    ("Wuppertal", "DE"), ("Bielefeld", "DE"), ("Bonn", "DE"), ("Münster", "DE"),
    ("Karlsruhe", "DE"), ("Augsburg", "DE"), ("Wiesbaden", "DE"), ("Mönchengladbach", "DE"),
    ("Braunschweig", "DE"), ("Kiel", "DE"), ("Aachen", "DE"), ("Halle (Saale)", "DE"),
    ("Freiburg im Breisgau", "DE"), ("Krefeld", "DE"), ("Mainz", "DE"), ("Lübeck", "DE"),
    ("Erfurt", "DE"), ("Rostock", "DE"), ("Kassel", "DE"), ("Hagen", "DE"),
    ("Saarbrücken", "DE"), ("Potsdam", "DE"),
    # Hollanda
    ("Eindhoven", "NL"), ("Tilburg", "NL"), ("Haarlem", "NL"), ("Zwolle", "NL"),
    ("Leiden", "NL"), ("Maastricht", "NL"), ("Apeldoorn", "NL"), ("Amersfoort", "NL"),
    ("Enschede", "NL"), ("Dordrecht", "NL"),
    # Irlanda
    ("Cork", "IE"), ("Waterford", "IE"), ("Drogheda", "IE"), ("Kilkenny", "IE"),
    # Polonya
    ("Wrocław", "PL"), ("Katowice", "PL"), ("Szczecin", "PL"), ("Lublin", "PL"),
    ("Bydgoszcz", "PL"), ("Białystok", "PL"), ("Gdynia", "PL"), ("Rzeszów", "PL"),
    # Portekiz + Malta + digerleri
    ("Coimbra", "PT"), ("Faro", "PT"), ("Funchal", "PT"),
    ("Valletta", "MT"), ("Sliema", "MT"),
    ("Antwerpen", "BE"), ("Brussel", "BE"),
    ("Tartu", "EE"), ("Kaunas", "LT"), ("Plovdiv", "BG"),
]


def main() -> None:
    only = sys.argv[1].upper().split(",") if len(sys.argv) > 1 else None
    hedef = [(s, c) for s, c in SEHIRLER if not only or c in only]
    print(f"{len(hedef)} sehir taranacak", flush=True)
    for index, (sehir, ulke) in enumerate(hedef, 1):
        cmd = ["python3", "scrape.py", "osm", "--area", sehir, "--country", ulke]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        print(f"[{index}/{len(hedef)}] {ulke} {sehir}: {out.splitlines()[-1] if out else 'sonuc yok'}",
              flush=True)
        time.sleep(4)  # Overpass'i yormamak icin
    print("BITTI", flush=True)


if __name__ == "__main__":
    main()
