#!/usr/bin/env python3
"""Kategori taramasi: 11 meslekte havuzu ulke tavanina cikarma (21 Eyl 2026).

Kaynak bu kez ad degil OSM KATEGORISI: Overpass (Almanya bbox karolari) + yerel OSM onbellegi (../scraper/data/osm).
Her kayit icin website, yoksa contact:website; site yoksa atlanir (e-posta tahmini yok).

Akis (meslek basina, 1'den 11'e sirayla):
  a) OSM kayitlari -> alan adi, tekil                     (zayif/osm-kat/<meslek>-<karo>.json onbellek)
  b) /api/havuz/bilinen -> 'epostali' atlanir; 'epostasiz' ve 'bilinmeyen' taranir
  c) site: ana sayfa + Impressum + Kontakt (zayif.site_tara: robots.txt, alan adi basina ardisik istek >= 1 sn,
     20 sn zaman asimi); yalniz kendi alan adindaki rol adresleri, freemail/kisisel yok
  d) verify/havuz-gonder.py <meslek>.csv --etiket pc-<etiket>   (company, city, sector, website, email, source_url)
  e) kisa rapor: OSM kayit / siteli / taranan / e-postali / yuklenen (+eklenen / ~guncel / red)
Saatlik ozet: zayif/DURUM-kategori.md (+ verify/DURUM.md basina ayni ozet). Hata: meslek atlanir, sonda bildirilir.

  python kategori.py calis [--isci 64]     # tum meslekler sirayla
  python kategori.py rapor
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

for _akim in (sys.stdout, sys.stderr):
    try:
        _akim.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import zayif  # noqa: E402
from zayif import GONDER, PLATFORM, host_of, site_kisalt, site_tara  # noqa: E402

ZAYIF = BASE / "zayif"
DB_PATH = ZAYIF / "kategori.sqlite3"
OSM_KAT = ZAYIF / "osm-kat"
OUT = ZAYIF / "out"
LOG = ZAYIF / "kategori.log"
DURUM_MD = ZAYIF / "DURUM-kategori.md"
VERIFY_DURUM = BASE / "verify" / "DURUM.md"
OSM_DIR = BASE.parent / "scraper" / "data" / "osm"
UA = zayif.UA
ISCI = 64

# Almanya bbox 47.27,5.87 - 55.06,15.04 -> 3x3 karo (Overpass tek seferde tum ulkeyi kaldiramiyor)
LAT = (47.27, 49.9, 52.5, 55.06)
LON = (5.87, 8.9, 12.0, 15.04)
KAROLAR = [f"{LAT[i]},{LON[j]},{LAT[i + 1]},{LON[j + 1]}" for i in range(3) for j in range(3)]
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
             "https://lz4.overpass-api.de/api/interpreter"]

EGITIM, GUZELLIK, SAGLIK, INSAAT, HUKUK, YAZILIM = ("Eğitim", "Güzellik & Kişisel Bakım", "Sağlık & Bakım",
                                                     "İnşaat, Zanaat & Tesis", "Hukuk, Muhasebe & Vergi", "Yazılım & Teknoloji")
IT_AD = re.compile(r"software|systemhaus|digital|daten|\bsap\b|\berp\b|\bit\b|it-|informatik|edv", re.I)
# meslek -> (etiket, sektor, Overpass secicileri, yerel onbellek eslesme fonksiyonu)
MESLEKLER: dict[str, dict] = {
    "kinder": dict(etiket="pc-kita", sektor=EGITIM, secici=['["amenity"="kindergarten"]', '["amenity"="childcare"]']),
    "friseur": dict(etiket="pc-friseur", sektor=GUZELLIK, secici=['["shop"="hairdresser"]', '["shop"="beauty"]']),
    "zahn": dict(etiket="pc-zahn", sektor=SAGLIK, secici=['["amenity"="dentist"]', '["healthcare"="dentist"]']),
    "physio": dict(etiket="pc-physio", sektor=SAGLIK, secici=['["healthcare"="physiotherapist"]', '["shop"="massage"]',
                                                             '["healthcare"="occupational_therapist"]']),
    "elektro": dict(etiket="pc-elektro", sektor=INSAAT, secici=['["craft"="electrician"]', '["shop"="electrical"]']),
    "shk": dict(etiket="pc-shk", sektor=INSAAT, secici=['["craft"="plumber"]', '["craft"="hvac"]']),
    "maler": dict(etiket="pc-maler", sektor=INSAAT, secici=['["craft"="painter"]']),
    "holz": dict(etiket="pc-holz", sektor=INSAAT, secici=['["craft"="carpenter"]', '["craft"="joiner"]', '["craft"="cabinet_maker"]']),
    "pflege": dict(etiket="pc-pflege", sektor=SAGLIK, secici=[
        '["amenity"="social_facility"]["social_facility"~"^(nursing_home|assisted_living|group_home|outreach)$"]',
        '["amenity"="nursing_home"]', '["healthcare"="nurse"]']),
    "buchhaltung": dict(etiket="pc-steuer", sektor=HUKUK, secici=['["office"="tax_advisor"]', '["office"="accountant"]']),
    "it": dict(etiket="pc-it", sektor=YAZILIM, secici=['["office"="it"]', '["shop"="computer"]',
                                                      '["office"="company"]["name"~"Software|Systemhaus|Digital|Daten|SAP|ERP|Informatik|EDV|(^| )IT( |-|$)",i]']),
}


def etiket_uyar(meslek: str, t: dict) -> bool:
    """Yerel onbellekteki nesne bu meslegin OSM etiketlerini tasiyor mu?"""
    g = t.get
    return {
        "kinder": g("amenity") in ("kindergarten", "childcare"),
        "friseur": g("shop") in ("hairdresser", "beauty"),
        "zahn": g("amenity") == "dentist" or g("healthcare") == "dentist",
        "physio": g("healthcare") in ("physiotherapist", "occupational_therapist") or g("shop") == "massage",
        "elektro": g("craft") == "electrician" or g("shop") == "electrical",
        "shk": g("craft") in ("plumber", "hvac"),
        "maler": g("craft") == "painter",
        "holz": g("craft") in ("carpenter", "joiner", "cabinet_maker"),
        "pflege": (g("amenity") == "social_facility" and g("social_facility") in ("nursing_home", "assisted_living", "group_home", "outreach"))
                  or g("amenity") == "nursing_home" or g("healthcare") == "nurse",
        "buchhaltung": g("office") in ("tax_advisor", "accountant"),
        "it": g("office") == "it" or g("shop") == "computer" or (g("office") == "company" and bool(IT_AD.search(g("name", "")))),
    }[meslek]


SEMA = """
CREATE TABLE IF NOT EXISTS kayitlar (
    domain TEXT NOT NULL, meslek TEXT NOT NULL, osm_ad TEXT DEFAULT '', osm_sehir TEXT DEFAULT '', osm_site TEXT DEFAULT '',
    kaynak TEXT DEFAULT '', havuz TEXT, yuklendi TEXT, created_at TEXT, PRIMARY KEY (domain, meslek)
);
CREATE INDEX IF NOT EXISTS ix_kayit_meslek ON kayitlar(meslek, havuz);
CREATE TABLE IF NOT EXISTS siteler (
    domain TEXT PRIMARY KEY, durum TEXT DEFAULT 'yeni', tries INTEGER DEFAULT 0, company TEXT DEFAULT '', city TEXT DEFAULT '',
    website TEXT DEFAULT '', email TEXT DEFAULT '', source_url TEXT DEFAULT '', note TEXT DEFAULT '', sayfa INTEGER DEFAULT 0, scanned_at TEXT
);
CREATE TABLE IF NOT EXISTS meslek_ozet (
    meslek TEXT PRIMARY KEY, osm_kayit INTEGER, siteli INTEGER, havuz_epostali INTEGER, taranacak INTEGER, taranan INTEGER,
    epostali INTEGER, yuklenen INTEGER, eklenen INTEGER, guncellenen INTEGER, reddedilen INTEGER, durum TEXT, hata TEXT, basladi TEXT, bitti TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(m: str) -> None:
    s = f"[{now()}] kategori: {m}"
    print(s, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def db() -> sqlite3.Connection:
    ZAYIF.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SEMA)
    return conn


def yaz(fn, deneme: int = 8):
    for i in range(deneme):
        conn = db()
        try:
            r = fn(conn); conn.commit(); return r
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == deneme - 1:
                raise
            time.sleep(0.5 + i)
        finally:
            conn.close()


# ------------------------------------------------------------------ a) OSM
def overpass(meslek: str, karo: str) -> list[dict]:
    OSM_KAT.mkdir(parents=True, exist_ok=True)
    dosya = OSM_KAT / f"{meslek}-{karo.replace(',', '_')}.json"
    if dosya.exists():
        return json.loads(dosya.read_text(encoding="utf-8"))
    parcalar = []
    for s in MESLEKLER[meslek]["secici"]:
        parcalar.append(f'  nwr{s}["website"];')
        parcalar.append(f'  nwr{s}["contact:website"];')
    sorgu = f"[out:json][timeout:300][bbox:{karo}];\n(\n" + "\n".join(parcalar) + "\n);\nout tags;"
    son = None
    for tur in range(3):
        for ep in ENDPOINTS:
            try:
                r = requests.post(ep, data={"data": sorgu}, headers={"User-Agent": UA}, timeout=400)
                if r.status_code in (429, 503, 504):
                    son = f"HTTP {r.status_code}"; time.sleep(30); continue
                r.raise_for_status()
                j = r.json()
                if "elements" not in j:
                    son = f"yanit: {j.get('remark', '')[:100]}"; continue
                els = [{"id": e["id"], "type": e["type"], "tags": e.get("tags", {})} for e in j["elements"]]
                dosya.write_text(json.dumps(els, ensure_ascii=False), encoding="utf-8")
                log(f"{meslek}: overpass karo {karo} <- {ep.split('/')[2]}: {len(els)} nesne")
                time.sleep(3)
                return els
            except Exception as e:
                son = f"{type(e).__name__}: {e}"[:120]; time.sleep(10)
    raise RuntimeError(f"overpass {meslek} {karo}: {son}")


def osm_topla(meslek: str) -> dict:
    """Overpass karolari + yerel onbellek -> kayitlar tablosu; (osm_kayit, siteli) doner."""
    nesne = 0; adaylar: dict[str, dict] = {}
    karo_hata = 0
    for karo in KAROLAR:
        try:
            els = overpass(meslek, karo)
        except Exception as e:
            log(f"!! {e}"); karo_hata += 1; continue
        for e in els:
            nesne += 1
            t = e["tags"]
            site = t.get("website") or t.get("contact:website") or ""
            d = host_of(site)
            if not d or any(p in d for p in PLATFORM) or d.count(".") > 2:
                continue
            adaylar.setdefault(d, {"ad": t.get("name", ""), "sehir": t.get("addr:city", ""), "site": site, "kaynak": "overpass"})
    yerel = 0
    for f in glob.glob(str(OSM_DIR / "*.json")):
        try:
            els = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for e in els:
            t = e.get("tags", {})
            if not etiket_uyar(meslek, t):
                continue
            yerel += 1
            site = t.get("website") or t.get("contact:website") or ""
            d = host_of(site)
            if not d or any(p in d for p in PLATFORM) or d.count(".") > 2:
                continue
            if d not in adaylar:
                adaylar[d] = {"ad": t.get("name", ""), "sehir": t.get("addr:city", ""), "site": site, "kaynak": "onbellek"}
    log(f"{meslek}: overpass {nesne} nesne ({karo_hata} karo hatali), yerel onbellek {yerel} nesne -> {len(adaylar)} siteli alan adi")
    yaz(lambda c: c.executemany(
        "INSERT OR IGNORE INTO kayitlar(domain, meslek, osm_ad, osm_sehir, osm_site, kaynak, created_at) VALUES (?,?,?,?,?,?,?)",
        [(d, meslek, a["ad"], a["sehir"], a["site"], a["kaynak"], now()) for d, a in adaylar.items()]))
    return {"osm_kayit": nesne + yerel, "siteli": len(adaylar)}


# ------------------------------------------------------------------ b) havuz
def havuz_ele(meslek: str) -> dict:
    hg = zayif.havuz_modul()
    conn = db()
    domainler = [r[0] for r in conn.execute("SELECT domain FROM kayitlar WHERE meslek=? AND havuz IS NULL", (meslek,))]
    conn.close()
    for i in range(0, len(domainler), 2000):
        parca = domainler[i:i + 2000]
        r = hg.istek("/api/havuz/bilinen", {"domainler": parca})
        bilinen = r.get("bilinen", {})
        yaz(lambda c, g=[(bilinen.get(d, "bilinmeyen"), d) for d in parca]: c.executemany(
            "UPDATE kayitlar SET havuz=? WHERE domain=? AND meslek=?", [(h, d, meslek) for h, d in g]))
        time.sleep(1.0)
    conn = db()
    r = conn.execute("SELECT SUM(havuz='epostali'), SUM(havuz='epostasiz'), SUM(havuz='bilinmeyen') FROM kayitlar WHERE meslek=?", (meslek,)).fetchone()
    conn.close()
    log(f"{meslek}: havuz -> epostali {r[0] or 0} (atlanir), epostasiz {r[1] or 0}, bilinmeyen {r[2] or 0}")
    return {"havuz_epostali": r[0] or 0, "taranacak": (r[1] or 0) + (r[2] or 0)}


# ------------------------------------------------------------------ c) site
def site_isle(domain: str) -> dict:
    r = site_tara(domain, [])
    return r


def tara(meslek: str, isci: int) -> dict:
    conn = db()
    satirlar = [r[0] for r in conn.execute(
        """SELECT k.domain FROM kayitlar k LEFT JOIN siteler s ON s.domain=k.domain
           WHERE k.meslek=? AND k.havuz IN ('epostasiz','bilinmeyen') AND (s.domain IS NULL OR (s.durum IN ('yeni','hata') AND s.tries<2))""", (meslek,))]
    conn.close()
    log(f"{meslek}: {len(satirlar)} site taranacak ({isci} isci)")
    t0 = time.time(); n = ok = 0
    for i in range(0, len(satirlar), 1000):
        parti = satirlar[i:i + 1000]
        with ThreadPoolExecutor(max_workers=isci) as pool:
            isler = {pool.submit(site_isle, d): d for d in parti}
            sonuclar = []
            for f in as_completed(isler):
                d = isler[f]
                try:
                    r = f.result()
                except Exception as e:
                    r = {"domain": d, "durum": "hata", "note": f"{type(e).__name__}: {e}"[:200], "company": "", "city": "",
                         "website": "", "email": "", "source_url": "", "sayfa": 0}
                sonuclar.append(r); n += 1; ok += bool(r["email"])
        yaz(lambda c, ss=sonuclar: c.executemany(
            """INSERT INTO siteler(domain, durum, tries, company, city, website, email, source_url, note, sayfa, scanned_at)
               VALUES (?,?,1,?,?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET durum=excluded.durum, tries=tries+1, company=excluded.company,
               city=excluded.city, website=excluded.website, email=excluded.email, source_url=excluded.source_url, note=excluded.note,
               sayfa=excluded.sayfa, scanned_at=excluded.scanned_at""",
            [(r["domain"], r["durum"], r["company"], r["city"], r["website"], r["email"], r["source_url"], r["note"], r["sayfa"], now()) for r in ss]))
        hiz = n / max(1, time.time() - t0)
        log(f"{meslek}: {n}/{len(satirlar)} site, {ok} e-postali, {hiz:.1f} site/sn, kalan ~{(len(satirlar) - n) / max(hiz, 0.1) / 60:.0f} dk")
        if (ZAYIF / "DUR-kategori").exists():
            log("DUR-kategori: tarama kesildi"); break
    return {"taranan": n, "epostali": ok}


# ------------------------------------------------------------------ d) yukle
def yukle(meslek: str) -> dict:
    m = MESLEKLER[meslek]
    conn = db()
    satirlar = conn.execute(
        """SELECT k.domain, k.osm_ad, k.osm_sehir, s.company, s.city, s.website, s.email, s.source_url FROM kayitlar k JOIN siteler s ON s.domain=k.domain
           WHERE k.meslek=? AND k.yuklendi IS NULL AND k.havuz IN ('epostasiz','bilinmeyen') AND s.durum='tarandi' AND s.email<>''""", (meslek,)).fetchall()
    conn.close()
    if not satirlar:
        log(f"{meslek}: yuklenecek kayit yok"); return {"yuklenen": 0, "eklenen": 0, "guncellenen": 0, "reddedilen": 0}
    OUT.mkdir(parents=True, exist_ok=True)
    dosya = OUT / f"kategori-{meslek}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with dosya.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "city", "sector", "website", "email", "source_url"])
        w.writeheader()
        for r in satirlar:
            w.writerow({"company": (r["osm_ad"] or r["company"] or "").strip()[:200], "city": (r["osm_sehir"] or r["city"] or "").strip()[:100],
                        "sector": m["sektor"], "website": site_kisalt(r["website"]), "email": r["email"], "source_url": site_kisalt(r["source_url"])[:500]})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(GONDER), str(dosya), "--etiket", m["etiket"]], capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE)
    for s in (p.stdout + p.stderr).splitlines()[-4:]:
        log(f"  {meslek}: {s}")
    mt = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not mt:
        raise RuntimeError(f"yukleme basarisiz (kod {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    t = json.loads(mt.group(1))
    yaz(lambda c: c.executemany("UPDATE kayitlar SET yuklendi=? WHERE domain=? AND meslek=?", [(now(), r["domain"], meslek) for r in satirlar]))
    log(f"{meslek}: {len(satirlar)} satir -> TOPLAM {json.dumps(t, ensure_ascii=False)} ({dosya.name})")
    return {"yuklenen": len(satirlar), "eklenen": t.get("eklenen", 0), "guncellenen": t.get("guncellenen", 0), "reddedilen": t.get("reddedilen", 0)}


# ------------------------------------------------------------------ e) rapor / durum
def ozet_guncelle(meslek: str, **alanlar) -> None:
    def f(c):
        c.execute("INSERT OR IGNORE INTO meslek_ozet(meslek) VALUES (?)", (meslek,))
        for k, v in alanlar.items():
            c.execute(f"UPDATE meslek_ozet SET {k}=? WHERE meslek=?", (v, meslek))
    yaz(f)


def rapor(yazdir: bool = True) -> str:
    conn = db()
    satirlar = ["# DURUM — kategori taraması (11 meslek, OSM etiketi → site → havuz)", f"Güncelleme: {now()}", "",
                "| # | meslek | etiket | OSM kayıt | siteli | havuzda e-postalı | taranacak | taranan | e-postalı | yüklenen | +eklenen | ~güncel | red | durum |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, (m, t) in enumerate(MESLEKLER.items(), 1):
        r = conn.execute("SELECT * FROM meslek_ozet WHERE meslek=?", (m,)).fetchone()
        if not r:
            satirlar.append(f"| {i} | {m} | {t['etiket']} | | | | | | | | | | | bekliyor |"); continue
        # canli sayac: taranan / e-postali (ozet yazilmamis olsa da)
        c = conn.execute("""SELECT COUNT(1), SUM(s.email<>'') FROM kayitlar k JOIN siteler s ON s.domain=k.domain
                            WHERE k.meslek=? AND k.havuz IN ('epostasiz','bilinmeyen') AND s.durum<>'yeni'""", (m,)).fetchone()
        d = r["durum"] or "sürüyor"
        if r["hata"]:
            d += f" — hata: {r['hata'][:80]}"
        satirlar.append(f"| {i} | {m} | {t['etiket']} | {r['osm_kayit'] or ''} | {r['siteli'] or ''} | {r['havuz_epostali'] or ''} | {r['taranacak'] or ''} | "
                        f"{c[0] or 0} | {c[1] or 0} | {r['yuklenen'] or 0} | {r['eklenen'] or 0} | {r['guncellenen'] or 0} | {r['reddedilen'] or 0} | {d} |")
    s = conn.execute("SELECT durum, COUNT(1) FROM siteler GROUP BY durum").fetchall()
    conn.close()
    satirlar += ["", "Site durumları: " + ", ".join(f"{a} {b}" for a, b in s), "",
                 "Kurallar: robots.txt, alan adı başına ardışık istek ≥1 sn, 20 sn zaman aşımı, ≤5 sayfa/site; yalnız sitede yazılı rol e-postası "
                 "(kişisel/freemail yok); site yoksa kayıt atlanır; ücretli API yok. Verify/ilan görevleri kapalı."]
    metin = "\n".join(satirlar) + "\n"
    DURUM_MD.write_text(metin, encoding="utf-8")
    # verify/DURUM.md basina ayni ozet (isaretli blok)
    try:
        eski = VERIFY_DURUM.read_text(encoding="utf-8") if VERIFY_DURUM.exists() else ""
        eski = re.sub(r"<!-- kategori-basi -->.*?<!-- kategori-sonu -->\n?", "", eski, flags=re.S)
        VERIFY_DURUM.write_text("<!-- kategori-basi -->\n" + metin + "<!-- kategori-sonu -->\n" + eski, encoding="utf-8")
    except Exception as e:
        log(f"verify/DURUM.md yazilamadi: {e}")
    if yazdir:
        print(metin)
    return metin


# ------------------------------------------------------------------ calis
def meslek_isle(meslek: str, isci: int) -> None:
    ozet_guncelle(meslek, durum="sürüyor", basladi=now(), hata="")
    a = osm_topla(meslek); ozet_guncelle(meslek, **a)
    b = havuz_ele(meslek); ozet_guncelle(meslek, **b)
    son_durum = 0.0
    c = tara(meslek, isci); ozet_guncelle(meslek, **c)
    d = yukle(meslek); ozet_guncelle(meslek, **d, durum="bitti", bitti=now())
    log(f"RAPOR {meslek} ({MESLEKLER[meslek]['etiket']}): OSM kayıt {a['osm_kayit']} / siteli {a['siteli']} / havuzda e-postalı {b['havuz_epostali']} / "
        f"taranan {c['taranan']} / e-postalı {c['epostali']} / yüklenen {d['yuklenen']} (+{d['eklenen']} / ~{d['guncellenen']} / red {d['reddedilen']})")


def calis(isci: int) -> None:
    log(f"calis basladi: {len(MESLEKLER)} meslek, {isci} isci")
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; cikiliyor"); return
    hatali = []
    import threading
    dur = threading.Event()

    def saatlik():
        while not dur.wait(3600):
            try:
                rapor(yazdir=False)
            except Exception as e:
                log(f"rapor hatasi: {e}")
    threading.Thread(target=saatlik, daemon=True).start()
    for m in MESLEKLER:
        if (ZAYIF / "DUR-kategori").exists():
            log("DUR-kategori: durduruldu"); break
        conn = db(); r = conn.execute("SELECT durum FROM meslek_ozet WHERE meslek=?", (m,)).fetchone(); conn.close()
        if r and r["durum"] == "bitti":
            log(f"{m}: zaten bitti, atlandi"); continue
        try:
            meslek_isle(m, isci)
        except Exception as e:
            hatali.append((m, f"{type(e).__name__}: {e}"[:200]))
            ozet_guncelle(m, durum="hata", hata=f"{type(e).__name__}: {e}"[:200])
            log(f"!! {m} atlandi: {type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
        rapor(yazdir=False)
    dur.set()
    rapor(yazdir=False)
    log("calis bitti" + (f"; hatali meslekler: {hatali}" if hatali else "; hata yok"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["calis", "rapor", "meslek"])
    p.add_argument("--meslek"); p.add_argument("--isci", type=int, default=ISCI)
    a = p.parse_args()
    if a.komut == "calis":
        calis(a.isci)
    elif a.komut == "meslek":
        meslek_isle(a.meslek, a.isci); rapor()
    else:
        rapor()


if __name__ == "__main__":
    main()
