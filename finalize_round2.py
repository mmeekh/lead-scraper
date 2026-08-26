#!/usr/bin/env python3
"""Mail cikarma bitince kalan isi otomatik tamamlar.

Sira: bekle -> disa aktar -> uygunluk filtresi -> dogru etiketleme ->
mukerrer eleme -> kampanyaya ekle -> DOGRULA (basarisizsa geri al).

Guvenlik: kampanya dosyasi once yedeklenir. Ekleme sonrasi kampanyanin kendi
yukleyicisi + preflight + tum satirlarin render testi calisir; herhangi biri
patlarsa dosya yedekten geri yuklenir, yani bot bozulmus listeyle uyanmaz.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/projects/lead-scraper")
sys.path.insert(0, "/root/projects/nl-job-outreach")

BASE = Path("/root/projects/lead-scraper")
CAMPAIGN = Path("/root/projects/nl-job-outreach/firmalar.csv")
BACKUP = Path("/root/projects/nl-job-outreach/firmalar.csv.oto-yedek")

# kaynak -> kampanya etiketi (ulke kodundan turetilemeyen ozel kollar)
SOURCE_TAG = {
    "maritime-yard": "TR-YARD",
    "maritime-cruise": "CRUISE-EN",
    "contractor-tr": "TR-INSAAT",
    "contractor-intl": "GULF-EN",
}


def bekle() -> None:
    while subprocess.run(["pgrep", "-f", "scrape.py emails"],
                         capture_output=True).returncode == 0:
        time.sleep(20)
    print("mail cikarma bitti", flush=True)


def main() -> None:
    bekle()

    # 1) disa aktar (export zaten e-posta + kurum bazinda mukerrer eliyor)
    subprocess.run(["python3", "scrape.py", "export", "--out", "tur2-ham.csv"],
                   cwd=BASE, check=True)

    # 2) uygunluk filtresi: OSM etiket hatalarini (koclik, terapi, muhendislik) ele
    subprocess.run(["python3", "filter_relevant.py", "tur2-ham.csv", "tur2-uygun.csv"],
                   cwd=BASE, check=True)

    # 3) ozel kollari dogru etiketle (denizcilik, muteahhit)
    conn = sqlite3.connect(BASE / "leads.sqlite3")
    conn.row_factory = sqlite3.Row
    kaynak = {r["domain"]: r["source"] for r in conn.execute("SELECT domain, source FROM leads")}
    conn.close()

    ham = list(csv.DictReader(open(BASE / "tur2-ham.csv", encoding="utf-8")))
    uygun = {r["email"] for r in csv.DictReader(open(BASE / "tur2-uygun.csv", encoding="utf-8"))}

    satirlar = []
    for row in ham:
        src = kaynak.get(row["site"]) or kaynak.get(row["email"])
        if src in SOURCE_TAG:
            row["oncelik"] = SOURCE_TAG[src]          # ozel kol: filtreden muaf
        elif row["email"] not in uygun:
            continue                                   # profile uygun degil
        satirlar.append(row)

    if not satirlar:
        print("eklenecek yeni firma yok")
        return

    hedef = BASE / "tur2-eklenecek.csv"
    with open(hedef, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["oncelik", "firma", "sehir", "email", "site", "dil_notu"])
        writer.writeheader()
        writer.writerows(satirlar)
    print(f"{len(satirlar)} satir hazir -> {hedef}", flush=True)

    # 4) kampanyaya ekle (yedekle, ekle, dogrula, gerekirse geri al)
    shutil.copy2(CAMPAIGN, BACKUP)
    with open(CAMPAIGN, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["oncelik", "firma", "sehir", "email", "site", "dil_notu"])
        writer.writerows(satirlar)

    kontrol = subprocess.run(
        ["python3", "-c",
         "from send_mails import load_rows, preflight, render_for;"
         "rows=load_rows(); preflight(rows, require_password=False);"
         "[render_for(r) for r in rows];"
         "print('OK', len(rows))"],
        cwd="/root/projects/nl-job-outreach", capture_output=True, text=True)

    if kontrol.returncode != 0:
        shutil.copy2(BACKUP, CAMPAIGN)
        print("DOGRULAMA BASARISIZ - kampanya listesi geri alindi")
        print(kontrol.stderr.strip()[-400:])
        return

    print("kampanya dogrulandi:", kontrol.stdout.strip(), flush=True)
    testler = subprocess.run(["python3", "test_automation.py"],
                             cwd="/root/projects/nl-job-outreach",
                             capture_output=True, text=True)
    print("test paketi:", "GECTI" if testler.returncode == 0 else "KIRILDI")
    if testler.returncode != 0:
        shutil.copy2(BACKUP, CAMPAIGN)
        print("testler kirildi - kampanya listesi geri alindi")
        print(testler.stderr.strip()[-400:])


if __name__ == "__main__":
    main()
