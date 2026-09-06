#!/usr/bin/env python3
"""Re-apply the current fit threshold to rows already crawled and rejected.

6 Eyl 2026: uygunluk esigi 35'ten 25'e indirildi. Esik yalnizca yeni
taramalari etkiler; daha once gezilip 'rejected' yazilmis satirlar skorlari
yeni barajin ustunde olsa bile oyle kalir. Bu betik onlari yeniden
degerlendirir.

Sayfalar yeniden indirilmez: karar, taramada zaten kaydedilmis fit_score ve
fit_tracks sutunlarindan verilir; bu, profile_fit.score_profile icindeki
`qualified = bool(matched_tracks) and score >= min_score` kuralinin aynisidir.
Yayinci kapilari (HTTPS kanit adresi, kisisellestirme, tekillik) degismez.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "nl-job-outreach"))

from country_campaign import COUNTRIES
from profile_fit import QUALIFY_MIN_SCORE

DB = BASE / "leads.sqlite3"

SELECT = """
    SELECT country, COUNT(*) FROM leads
     WHERE country IN ({places})
       AND profile_status = 'rejected'
       AND fit_score >= ?
       AND COALESCE(fit_tracks, '') <> ''
     GROUP BY country ORDER BY COUNT(*) DESC
"""
UPDATE = """
    UPDATE leads SET profile_status = 'qualified'
     WHERE country IN ({places})
       AND profile_status = 'rejected'
       AND fit_score >= ?
       AND COALESCE(fit_tracks, '') <> ''
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=QUALIFY_MIN_SCORE)
    parser.add_argument("--apply", action="store_true",
                        help="olmadan yalnizca ne degisecegini yazar")
    args = parser.parse_args()

    places = ",".join("?" for _ in COUNTRIES)
    values = [*COUNTRIES, args.min_score]
    conn = sqlite3.connect(DB, timeout=60)
    try:
        rows = conn.execute(SELECT.format(places=places), values).fetchall()
        total = sum(count for _, count in rows)
        for country, count in rows:
            print(f"  {country}: {count}")
        print(f"baraj={args.min_score} ile yeniden nitelenecek: {total}")
        if not args.apply:
            print("(deneme calismasi; yazmak icin --apply)")
            return
        changed = conn.execute(UPDATE.format(places=places), values).rowcount
        conn.commit()
        print(f"guncellendi: {changed}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
