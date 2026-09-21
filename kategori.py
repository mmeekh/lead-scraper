#!/usr/bin/env python3
"""Kategori taramasi: OSM etiketiyle Almanya genelinde siteli sirket -> e-posta -> havuz (21 Eyl 2026, 2. surum).

Kaynak: Geofabrik germany-latest.osm.pbf (tek dosya, bir kez indirilir) -> pyosmium ile tek gecis:
website / contact:website etiketi olan tum nesneler zayif/osm-kat/siteli.jsonl'e cikarilir (Overpass yok, karo yok).
Site yoksa kayit atlanir (e-posta tahmini yok).

Akis (meslek basina, kullanici sirasiyla; biten hemen yuklenir):
  a) siteli.jsonl -> meslegin etiket kurali -> alan adi, tekil
  b) /api/havuz/bilinen -> 'epostali' atlanir; 'epostasiz' ve 'bilinmeyen' taranir
  c) site: ana sayfa + Impressum + Kontakt (zayif.site_tara: robots.txt, alan adi basina ardisik istek >= 1 sn,
     20 sn zaman asimi); yalniz kendi alan adindaki rol adresleri; freemail/kisisel yok
  d) verify/havuz-gonder.py <csv> --etiket <etiket>   (company, city, sector, website, email, source_url)
  e) rapor: OSM kayit / siteli / taranan / e-postali / yuklenen (+eklenen / ~guncel / red)
Saatlik ozet: zayif/DURUM-kategori.md (+ verify/DURUM.md basina ayni blok). Hata: meslek atlanir, sonda bildirilir.

  python kategori.py cikar                 # PBF -> siteli.jsonl (bir kez, ~10 dk)
  python kategori.py calis [--isci 128]    # tum meslekler sirayla
  python kategori.py rapor
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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
PBF = ZAYIF / "pbf" / "germany-latest.osm.pbf"
SITELI = ZAYIF / "osm-kat" / "siteli.jsonl"
OUT = ZAYIF / "out"
LOG = ZAYIF / "kategori.log"
DURUM_MD = ZAYIF / "DURUM-kategori.md"
VERIFY_DURUM = BASE / "verify" / "DURUM.md"
ISCI = 128
ANAHTARLAR = ("name", "addr:city", "addr:postcode", "website", "contact:website", "office", "shop", "craft", "amenity",
              "healthcare", "industrial", "man_made", "social_facility", "emergency", "landuse", "building")

YAZILIM, HUKUK, OTOMOTIV, SOSYAL, SANAYI, SAGLIK, INSAAT, GUZELLIK = (
    "Yazılım & Teknoloji", "Hukuk, Muhasebe & Vergi", "Otomotiv & Lojistik", "Sosyal Hizmet & STK",
    "Sanayi & Mühendislik", "Sağlık & Bakım", "İnşaat, Zanaat & Tesis", "Güzellik & Kişisel Bakım")
IT_AD = re.compile(r"software|systemhaus|digital|daten|\bdata\b|analytics|\bsap\b|\berp\b|\bit\b|\bit-", re.I)
LOG_AD = re.compile(r"spedition|logistik|transport|kurier|umzug", re.I)
SOZ_AD = re.compile(r"jugend|familie|sozial", re.I)


def _it(t):    return t.get("office") == "it" or t.get("shop") == "computer" or (t.get("office") == "company" and bool(IT_AD.search(t.get("name", ""))))
def _steuer(t): return t.get("office") in ("tax_advisor", "accountant", "financial")
def _log(t):   return t.get("office") in ("logistics", "courier") or t.get("industrial") == "warehouse" or bool(LOG_AD.search(t.get("name", "")))
def _sozial(t): return t.get("amenity") in ("social_facility", "social_centre") or t.get("office") == "charity" or (t.get("office") == "association" and bool(SOZ_AD.search(t.get("name", ""))))
def _ind(t):   return t.get("man_made") == "works" or t.get("craft") in ("metal_construction", "welder") or bool(t.get("industrial"))
def _pflege(t): return t.get("amenity") == "nursing_home" or t.get("social_facility") in ("nursing_home", "assisted_living") or t.get("healthcare") == "nurse"
def _kfz(t):   return t.get("shop") in ("car_repair", "car") or t.get("craft") == "car_painter"
def _hand(t):  return t.get("craft") in ("electrician", "plumber", "hvac", "painter", "carpenter", "joiner")
def _zahn(t):  return t.get("amenity") == "dentist" or t.get("healthcare") == "dentist"
def _physio(t): return t.get("healthcare") in ("physiotherapist", "occupational_therapist") or t.get("shop") == "massage"
def _friseur(t): return t.get("shop") in ("hairdresser", "beauty")


# sira = kullanici talebi (21 Eyl 12:00); kinder (pc-kita) daha once yuklendi
MESLEKLER: dict[str, dict] = {
    "it": dict(etiket="pc-it", sektor=YAZILIM, kural=_it),
    "steuer": dict(etiket="pc-steuer", sektor=HUKUK, kural=_steuer),
    "logistik": dict(etiket="pc-logistik", sektor=OTOMOTIV, kural=_log),
    "sozial": dict(etiket="pc-sozial", sektor=SOSYAL, kural=_sozial),
    "industrie": dict(etiket="pc-industrie", sektor=SANAYI, kural=_ind),
    "pflege": dict(etiket="pc-pflege", sektor=SAGLIK, kural=_pflege),
    "kfz": dict(etiket="pc-kfz", sektor=OTOMOTIV, kural=_kfz),
    "handwerk": dict(etiket="pc-handwerk", sektor=INSAAT, kural=_hand),
    "zahn": dict(etiket="pc-zahn", sektor=SAGLIK, kural=_zahn),
    "physio": dict(etiket="pc-physio", sektor=SAGLIK, kural=_physio),
    "friseur": dict(etiket="pc-friseur", sektor=GUZELLIK, kural=_friseur),
}

SEMA = """
CREATE TABLE IF NOT EXISTS kayitlar (
    domain TEXT NOT NULL, meslek TEXT NOT NULL, osm_ad TEXT DEFAULT '', osm_sehir TEXT DEFAULT '', osm_site TEXT DEFAULT '',
    kaynak TEXT DEFAULT '', havuz TEXT, yuklendi TEXT, created_at TEXT, PRIMARY KEY (domain, meslek)
);
CREATE INDEX IF NOT EXISTS ix_kayit_meslek ON kayitlar(meslek, havuz);
CREATE TABLE IF NOT EXISTS siteler (
    domain TEXT PRIMARY KEY, durum TEXT DEFAULT 'yeni', tries INTEGER DEFAULT 0, company TEXT DEFAULT '', city TEXT DEFAULT '',
    website TEXT DEFAULT '', email TEXT DEFAULT '', source_url TEXT DEFAULT '', note TEXT DEFAULT '', sayfa INTEGER DEFAULT 0, scanned_at TEXT,
    yuklendi_etiket TEXT
);
CREATE TABLE IF NOT EXISTS meslek_ozet (
    meslek TEXT PRIMARY KEY, osm_kayit INTEGER, siteli INTEGER, havuz_epostali INTEGER, taranacak INTEGER, taranan INTEGER,
    epostali INTEGER, onceki_etiket INTEGER, yuklenen INTEGER, eklenen INTEGER, guncellenen INTEGER, reddedilen INTEGER,
    durum TEXT, hata TEXT, basladi TEXT, bitti TEXT
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
    for sutun in ("yuklendi_etiket",):
        try:
            conn.execute(f"ALTER TABLE siteler ADD COLUMN {sutun} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE meslek_ozet ADD COLUMN onceki_etiket INTEGER")
    except sqlite3.OperationalError:
        pass
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


# ------------------------------------------------------------------ PBF -> siteli.jsonl (tek gecis)
def cikar() -> int:
    """germany-latest.osm.pbf icindeki website/contact:website etiketli tum nesneleri (secili etiketlerle) yazar."""
    import osmium
    from osmium.filter import KeyFilter
    if not PBF.exists():
        raise FileNotFoundError(PBF)
    SITELI.parent.mkdir(parents=True, exist_ok=True)
    gecici = SITELI.with_suffix(".tmp")
    n = 0; t0 = time.time()
    with gecici.open("w", encoding="utf-8") as f:
        fp = osmium.FileProcessor(str(PBF)).with_filter(KeyFilter("website", "contact:website"))
        for o in fp:
            tags = o.tags
            if "name" not in tags:
                continue
            kayit = {k: tags[k] for k in ANAHTARLAR if k in tags}
            kayit["_id"] = f"{o.type_str()}{o.id}" if hasattr(o, "type_str") else str(o.id)
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n"); n += 1
            if n % 100000 == 0:
                log(f"cikar: {n} siteli nesne, {time.time() - t0:.0f} sn")
    gecici.replace(SITELI)
    log(f"cikar: bitti, {n} adli+siteli nesne -> {SITELI.name} ({time.time() - t0:.0f} sn)")
    return n


def siteli_oku():
    with SITELI.open(encoding="utf-8") as f:
        for satir in f:
            yield json.loads(satir)


# ------------------------------------------------------------------ a) meslek kayitlari
def osm_topla(meslek: str) -> dict:
    kural = MESLEKLER[meslek]["kural"]
    nesne = 0; adaylar: dict[str, dict] = {}
    for t in siteli_oku():
        if not kural(t):
            continue
        nesne += 1
        site = t.get("website") or t.get("contact:website") or ""
        d = host_of(site)
        if not d or any(p in d for p in PLATFORM) or d.count(".") > 2:
            continue
        adaylar.setdefault(d, {"ad": t.get("name", ""), "sehir": t.get("addr:city", ""), "site": site})
    log(f"{meslek}: OSM {nesne} kayit -> {len(adaylar)} siteli alan adi")
    yaz(lambda c: c.executemany(
        "INSERT OR IGNORE INTO kayitlar(domain, meslek, osm_ad, osm_sehir, osm_site, kaynak, created_at) VALUES (?,?,?,?,?,?,?)",
        [(d, meslek, a["ad"], a["sehir"], a["site"], "pbf", now()) for d, a in adaylar.items()]))
    return {"osm_kayit": nesne, "siteli": len(adaylar)}


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
        time.sleep(0.5)
    conn = db()
    r = conn.execute("SELECT SUM(havuz='epostali'), SUM(havuz='epostasiz'), SUM(havuz='bilinmeyen') FROM kayitlar WHERE meslek=?", (meslek,)).fetchone()
    conn.close()
    log(f"{meslek}: havuz -> epostali {r[0] or 0} (atlanir), epostasiz {r[1] or 0}, bilinmeyen {r[2] or 0}")
    return {"havuz_epostali": r[0] or 0, "taranacak": (r[1] or 0) + (r[2] or 0)}


# ------------------------------------------------------------------ c) site
SURECLER = max(2, (os.cpu_count() or 4) - 1)     # GIL: HTML/regex isi tek cekirdekte kaliyordu; surec basina ayri is parcaciklari


def parca_tara(domainler: list[str], iplik: int) -> list[dict]:
    """Alt surecte calisir: kendi is parcacigi havuzuyla bir grup alan adini tarar."""
    sonuclar = []
    with ThreadPoolExecutor(max_workers=max(1, min(iplik, len(domainler)))) as pool:
        isler = {pool.submit(site_tara, d, []): d for d in domainler}
        for f in as_completed(isler):
            d = isler[f]
            try:
                sonuclar.append(f.result())
            except Exception as e:
                sonuclar.append({"domain": d, "durum": "hata", "note": f"{type(e).__name__}: {e}"[:200], "company": "", "city": "",
                                 "website": "", "email": "", "source_url": "", "sayfa": 0})
    return sonuclar


def tara(meslek: str, isci: int) -> dict:
    conn = db()
    satirlar = [r[0] for r in conn.execute(
        """SELECT k.domain FROM kayitlar k LEFT JOIN siteler s ON s.domain=k.domain
           WHERE k.meslek=? AND k.havuz IN ('epostasiz','bilinmeyen') AND (s.domain IS NULL OR (s.durum IN ('yeni','hata') AND s.tries<2))""", (meslek,))]
    conn.close()
    log(f"{meslek}: {len(satirlar)} site taranacak ({SURECLER} surec x {max(8, isci // SURECLER)} is parcacigi)")
    t0 = time.time(); n = ok = 0
    from concurrent.futures import ProcessPoolExecutor
    iplik = max(8, isci // SURECLER)               # surec basina is parcacigi; toplam ~isci
    for i in range(0, len(satirlar), 4000):
        parti = satirlar[i:i + 4000]
        sonuclar = []
        # her surece esit buyuk dilim: kucuk gruplarda en yavas sitenin kuyrugu sureci bosta birakiyordu (5 site/sn)
        boy = max(1, -(-len(parti) // SURECLER))
        gruplar = [parti[j:j + boy] for j in range(0, len(parti), boy)]
        with ProcessPoolExecutor(max_workers=SURECLER) as havuz:
            for grup in havuz.map(parca_tara, gruplar, [iplik] * len(gruplar)):
                for r in grup:
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
        """SELECT k.domain, k.osm_ad, k.osm_sehir, s.company, s.city, s.website, s.email, s.source_url, s.yuklendi_etiket
           FROM kayitlar k JOIN siteler s ON s.domain=k.domain
           WHERE k.meslek=? AND k.yuklendi IS NULL AND k.havuz IN ('epostasiz','bilinmeyen') AND s.durum='tarandi' AND s.email<>''""", (meslek,)).fetchall()
    conn.close()
    onceki = [r for r in satirlar if r["yuklendi_etiket"]]          # bu kosuda baska etiketle zaten yuklendi: tekrar gonderilmez
    satirlar = [r for r in satirlar if not r["yuklendi_etiket"]]
    if onceki:
        yaz(lambda c: c.executemany("UPDATE kayitlar SET yuklendi=? WHERE domain=? AND meslek=?", [("onceki:" + r["yuklendi_etiket"], r["domain"], meslek) for r in onceki]))
    if not satirlar:
        log(f"{meslek}: yuklenecek kayit yok ({len(onceki)} onceki etikette)")
        return {"onceki_etiket": len(onceki), "yuklenen": 0, "eklenen": 0, "guncellenen": 0, "reddedilen": 0}
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
    for s in (p.stdout + p.stderr).splitlines()[-3:]:
        log(f"  {meslek}: {s}")
    mt = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not mt:
        raise RuntimeError(f"yukleme basarisiz (kod {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    t = json.loads(mt.group(1))
    yaz(lambda c: (c.executemany("UPDATE kayitlar SET yuklendi=? WHERE domain=? AND meslek=?", [(now(), r["domain"], meslek) for r in satirlar]),
                   c.executemany("UPDATE siteler SET yuklendi_etiket=? WHERE domain=? AND yuklendi_etiket IS NULL", [(m["etiket"], r["domain"]) for r in satirlar])))
    log(f"{meslek}: {len(satirlar)} satir -> TOPLAM {json.dumps(t, ensure_ascii=False)} ({dosya.name}; {len(onceki)} onceki etikette atlandi)")
    return {"onceki_etiket": len(onceki), "yuklenen": len(satirlar), "eklenen": t.get("eklenen", 0), "guncellenen": t.get("guncellenen", 0), "reddedilen": t.get("reddedilen", 0)}


# ------------------------------------------------------------------ e) rapor / durum
def ozet_guncelle(meslek: str, **alanlar) -> None:
    def f(c):
        c.execute("INSERT OR IGNORE INTO meslek_ozet(meslek) VALUES (?)", (meslek,))
        for k, v in alanlar.items():
            c.execute(f"UPDATE meslek_ozet SET {k}=? WHERE meslek=?", (v, meslek))
    yaz(f)


def rapor(yazdir: bool = True) -> str:
    conn = db()
    satirlar = ["# DURUM — kategori taraması (OSM etiketi → site → havuz)", f"Güncelleme: {now()}", "",
                "| # | meslek | etiket | OSM kayıt | siteli | havuzda e-postalı | taranacak | taranan | e-postalı | önceki etikette | yüklenen | +eklenen | ~güncel | red | durum |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    sira = [("kinder", "pc-kita")] + [(m, t["etiket"]) for m, t in MESLEKLER.items()]
    for i, (m, et) in enumerate(sira, 0):
        r = conn.execute("SELECT * FROM meslek_ozet WHERE meslek=?", (m,)).fetchone()
        if not r:
            satirlar.append(f"| {i} | {m} | {et} | | | | | | | | | | | | bekliyor |"); continue
        c = conn.execute("""SELECT COUNT(1), SUM(s.email<>'') FROM kayitlar k JOIN siteler s ON s.domain=k.domain
                            WHERE k.meslek=? AND k.havuz IN ('epostasiz','bilinmeyen') AND s.durum<>'yeni'""", (m,)).fetchone()
        d = r["durum"] or "sürüyor"
        if r["hata"]:
            d += f" — hata: {r['hata'][:80]}"
        satirlar.append(f"| {i} | {m} | {et} | {r['osm_kayit'] or ''} | {r['siteli'] or ''} | {r['havuz_epostali'] or ''} | {r['taranacak'] or ''} | "
                        f"{c[0] or 0} | {c[1] or 0} | {r['onceki_etiket'] or 0} | {r['yuklenen'] or 0} | {r['eklenen'] or 0} | {r['guncellenen'] or 0} | {r['reddedilen'] or 0} | {d} |")
    s = conn.execute("SELECT durum, COUNT(1) FROM siteler GROUP BY durum").fetchall()
    conn.close()
    satirlar += ["", "Site durumları: " + ", ".join(f"{a} {b}" for a, b in s), "",
                 "Kaynak: Geofabrik germany-latest.osm.pbf (pyosmium tek geçiş). Kurallar: robots.txt, alan adı başına ardışık istek ≥1 sn, 20 sn zaman aşımı, "
                 "≤5 sayfa/site; yalnız sitede yazılı rol e-postası (kişisel/freemail yok); site yoksa kayıt atlanır; ücretli API yok. Verify/ilan görevleri kapalı."]
    metin = "\n".join(satirlar) + "\n"
    DURUM_MD.write_text(metin, encoding="utf-8")
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
    c = tara(meslek, isci); ozet_guncelle(meslek, **c)
    d = yukle(meslek); ozet_guncelle(meslek, **d, durum="bitti", bitti=now())
    log(f"RAPOR {meslek} ({MESLEKLER[meslek]['etiket']}): OSM kayıt {a['osm_kayit']} / siteli {a['siteli']} / havuzda e-postalı {b['havuz_epostali']} / "
        f"taranan {c['taranan']} / e-postalı {c['epostali']} / yüklenen {d['yuklenen']} (+{d['eklenen']} / ~{d['guncellenen']} / red {d['reddedilen']})"
        + (f" / önceki etikette {d['onceki_etiket']}" if d["onceki_etiket"] else ""))


def calis(isci: int) -> None:
    log(f"calis basladi: {len(MESLEKLER)} meslek, {isci} isci")
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; cikiliyor"); return
    if not SITELI.exists():
        cikar()
    hatali = []
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
    p.add_argument("komut", choices=["cikar", "calis", "rapor", "meslek"])
    p.add_argument("--meslek"); p.add_argument("--isci", type=int, default=ISCI)
    a = p.parse_args()
    if a.komut == "cikar":
        cikar()
    elif a.komut == "calis":
        calis(a.isci)
    elif a.komut == "meslek":
        meslek_isle(a.meslek, a.isci); rapor()
    else:
        rapor()


if __name__ == "__main__":
    main()
