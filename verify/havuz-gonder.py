#!/usr/bin/env python3
"""Kendi bilgisayarındaki scraper çıktısını atjobfind.com şirket havuzuna gönder.

Kullanım (PC'de, Python 3.10+, ek paket gerekmez):
    set JOBFIND_HAVUZ_ANAHTAR=hv_...            (Windows)   /  export JOBFIND_HAVUZ_ANAHTAR=hv_...
    python havuz-gonder.py sirketler.csv --etiket emin-pc
    python havuz-gonder.py sirketler.jsonl
    python havuz-gonder.py --bilinen domainler.txt        # hangileri zaten havuzda?
    python havuz-gonder.py --durum
    python havuz-gonder.py --ilanlar verify/out/ilanlar-001.jsonl   # PC ilan çıkarımı (20 Eyl 2026)

CSV başlıkları (sıra önemsiz, fazlası yok sayılır):
    company, city, sector, website, email, source_url
JSONL: her satır aynı alanlarla bir nesne.

Sunucu kuralları: e-posta biçimi ve MX doğrulanır, freemail (gmail vb.)
düşürülür, sektör Türkçe kataloğa çevrilir (bilinmeyen -> 'Diğer'), aynı
alan adı ikinci kez gelirse e-posta yalnız boşsa dolar. İstek başına 500
kayıt, 10 dakikada en fazla 120 istek (~60.000 kayıt / 10 dk).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("JOBFIND_HAVUZ_URL", "https://atjobfind.com")
ALANLAR = ("company", "city", "sector", "website", "email", "source_url")


def anahtar() -> str:
    k = os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip()
    if not k:
        sys.exit("JOBFIND_HAVUZ_ANAHTAR ortam değişkeni boş.")
    return k


def istek(yol: str, govde: dict | None = None) -> dict:
    veri = json.dumps(govde).encode("utf-8") if govde is not None else None
    req = urllib.request.Request(
        BASE + yol, data=veri, method="POST" if veri is not None else "GET",
        headers={"X-Havuz-Anahtar": anahtar(), "Content-Type": "application/json", "User-Agent": "havuz-gonder/1"},
    )
    for deneme in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and deneme < 3:
                time.sleep(30 * (deneme + 1))
                continue
            sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
        except urllib.error.URLError as e:
            if deneme < 3:
                time.sleep(5 * (deneme + 1))
                continue
            sys.exit(f"bağlantı hatası: {e}")
    return {}


def kayitlari_oku(yol: str):
    if yol.lower().endswith((".jsonl", ".ndjson")):
        with open(yol, encoding="utf-8") as f:
            for satir in f:
                satir = satir.strip()
                if satir:
                    yield json.loads(satir)
    else:
        with open(yol, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                yield row


def temizle(row: dict) -> dict:
    return {a: str(row.get(a) or "").strip() for a in ALANLAR}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dosya", nargs="?", help="CSV ya da JSONL")
    p.add_argument("--etiket", default="pc", help="kaynak etiketi (havuzda source=pc:<etiket>)")
    p.add_argument("--parti", type=int, default=500, help="istek başına kayıt (en fazla 500)")
    p.add_argument("--bilinen", metavar="DOMAINLER.txt", help="satır başına alan adı; havuzda olanları listeler")
    p.add_argument("--durum", action="store_true", help="havuz özeti")
    p.add_argument("--ilanlar", metavar="ILANLAR.jsonl", help="verify/out/ilanlar-*.jsonl: {domain, legal_name, ad_title, ad_url, meslek_kayitlari[], fetched_at}")
    a = p.parse_args()

    if a.ilanlar:
        # Şirketin KENDİ sitesindeki ilanlar; sunucu başka alan adındaki ad_url'yi reddeder.
        toplam = {"eklenen": 0, "guncellenen": 0, "reddedilen": 0}
        kutu = []

        def gonder_ilan():
            if not kutu:
                return
            r = istek("/api/havuz/ilanlar", {"ilanlar": kutu, "etiket": a.etiket})
            for k in toplam:
                toplam[k] += int(r.get(k, 0))
            ornek = r.get("red_ornek") or []
            print(f"  +{r.get('eklenen', 0)} yeni, ~{r.get('guncellenen', 0)} güncel, -{r.get('reddedilen', 0)} red"
                  + (f"  (ör. {ornek[0]['sebep']}: {ornek[0].get('domain')})" if ornek else ""))
            kutu.clear()

        for row in kayitlari_oku(a.ilanlar):
            kutu.append({
                "domain": str(row.get("domain") or "").strip(),
                "legal_name": str(row.get("legal_name") or "").strip(),
                "ad_title": str(row.get("ad_title") or "").strip(),
                "ad_url": str(row.get("ad_url") or "").strip(),
                "meslek_kayitlari": [str(k) for k in (row.get("meslek_kayitlari") or []) if k],
                "fetched_at": str(row.get("fetched_at") or "").strip(),
            })
            if len(kutu) >= max(1, min(500, a.parti)):
                gonder_ilan()
        gonder_ilan()
        print("TOPLAM", json.dumps(toplam, ensure_ascii=False))
        return

    if a.durum:
        print(json.dumps(istek("/api/havuz/durum"), ensure_ascii=False, indent=1))
        return
    if a.bilinen:
        with open(a.bilinen, encoding="utf-8") as f:
            domainler = [s.strip() for s in f if s.strip()]
        bilinen, bilinmeyen = {}, []
        for i in range(0, len(domainler), 2000):
            r = istek("/api/havuz/bilinen", {"domainler": domainler[i:i + 2000]})
            bilinen.update(r.get("bilinen", {}))
            bilinmeyen.extend(r.get("bilinmeyen", []))
        print(f"havuzda: {len(bilinen)} (e-postalı {sum(1 for v in bilinen.values() if v == 'epostali')}) | bilinmeyen: {len(bilinmeyen)}")
        for d in bilinmeyen:
            print(d)
        return
    if not a.dosya:
        p.error("dosya ver ya da --bilinen / --durum kullan")

    parti = max(1, min(500, a.parti))
    toplam = {"eklenen": 0, "guncellenen": 0, "reddedilen": 0}
    kutu: list[dict] = []

    def gonder():
        if not kutu:
            return
        r = istek("/api/havuz/ekle", {"kayitlar": kutu, "etiket": a.etiket})
        for k in toplam:
            toplam[k] += int(r.get(k, 0))
        ornek = r.get("red_ornek") or []
        print(f"  +{r.get('eklenen', 0)} yeni, ~{r.get('guncellenen', 0)} güncel, -{r.get('reddedilen', 0)} red"
              + (f"  (ör. {ornek[0]['sebep']}: {ornek[0].get('email') or ornek[0].get('website')})" if ornek else ""))
        kutu.clear()

    for row in kayitlari_oku(a.dosya):
        kutu.append(temizle(row))
        if len(kutu) >= parti:
            gonder()
    gonder()
    print("TOPLAM", json.dumps(toplam, ensure_ascii=False))


if __name__ == "__main__":
    main()
