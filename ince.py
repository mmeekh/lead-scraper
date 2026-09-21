#!/usr/bin/env python3
"""12 ince meslek: Common Crawl alan adi dizininden GENIS anahtar sozcuklerle yeni sirket (21 Eyl 2026, B hatti).

Kaynak: zayif/cc-domain-vertices.txt.gz (Common Crawl, ucretsiz). .de disinda .com/.net/.eu/.org da taranir;
.de olmayanlarda Impressum'da Almanya adresi (PLZ + sehir) sarti. Alan adi etiketi meslek sozcuklerinden birini
tasiyorsa aday olur; bir alan adi yalniz en oncelikli meslegine yazilir.

Meslek basina: aday -> /api/havuz/bilinen (havuzdakiler elenir) -> site (ana + Impressum + Kontakt; robots.txt, alan adi
basina ardisik istek >= 1 sn, 20 sn; yalniz kendi alan adindaki rol adresleri, kisisel ad yok) -> company = Impressum'daki
resmi ad, sehir = Impressum PLZ -> havuz-gonder.py --etiket pc-<meslek> -> rapor.
Sira: bus -> sap -> data -> kinderkrankenpflege -> rettung -> fleischer -> backer -> fahrzeug_ing -> kalte -> karosserie -> kurier -> fliesen.
A hatti (eposta.py 'ince' grubu) bitmeden site taramasi baslamaz (aday toplama ve havuz elemesi once yapilir).

  python ince.py calis [--isci 440] [--bekleme]     # --bekleme: A hattini bekleme
  python ince.py rapor
Durdurma: zayif/DUR-ince.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

for _akim in (sys.stdout, sys.stderr):
    try:
        _akim.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import zayif  # noqa: E402
from zayif import CC_GZ, GONDER, PLATFORM, site_kisalt, site_tara  # noqa: E402

ZAYIF = BASE / "zayif"
DB_PATH = ZAYIF / "ince.sqlite3"
OUT = ZAYIF / "out"
LOG = ZAYIF / "ince.log"
DURUM_MD = ZAYIF / "DURUM-ince.md"
EPOSTA_DB = ZAYIF / "eposta" / "eposta.sqlite3"
TLDLER = ("de", "com", "net", "eu", "org")
ISCI = 440
SURECLER = max(2, (os.cpu_count() or 4) - 1)

OTOMOTIV, YAZILIM, SAGLIK, GASTRO, INSAAT = ("Otomotiv & Lojistik", "Yazılım & Teknoloji", "Sağlık & Bakım",
                                             "Gastronomi & Turizm", "İnşaat, Zanaat & Tesis")


def _kural(sozcukler: list) -> "re.Pattern":
    """Sozcuk listesi -> alan adi etiketi deseni. Kisa (<=3) sozcukler ve '-' ile bitenler parca sinirli;
    tuple = hepsi birlikte gecmeli (or grubu icin '|' ile ayrilmis alt secenekler)."""
    parcalar = []
    for w in sozcukler:
        if isinstance(w, tuple):
            alt = []
            for g in w:
                alt.append("(?=.*(?:" + "|".join(re.escape(x) for x in g.split("|")) + "))")
            parcalar.append("".join(alt) + ".*")
        elif w.endswith("-"):
            parcalar.append(r"(^|-)" + re.escape(w[:-1]) + r"-")
        elif len(w) <= 4:
            parcalar.append(r"(^|-)" + re.escape(w) + r"(-|$|\d)")
        elif w == "daten":
            parcalar.append(r"daten(?!schutz)")
        else:
            parcalar.append(re.escape(w))
    return re.compile("|".join(parcalar), re.I)


MESLEKLER: dict[str, dict] = {
    "bus": dict(sektor=OTOMOTIV, sozcuk=["busreisen", "omnibus", "reisebus", "busbetrieb", "busunternehmen", "bustouristik", "reisedienst",
                                          "verkehrsbetrieb", "verkehrsgesellschaft", "linienverkehr", ("stadtwerke", "verkehr")],
                metin=r"omnibus|busreise|reisebus|busbetrieb|busunternehm|linienverkehr|busverkehr|busfahrer|bustour|fernbus|schulbus|nahverkehr|verkehrsbetrieb|verkehrsgesellschaft|linienbus"),
    "sap": dict(sektor=YAZILIM, sozcuk=["sap", "erp", "abap", "s4hana", "navision", "dynamics365", "proalpha", "abas", "odoo", "business-central",
                                        "it-consulting", "it-beratung", ("consulting", "software|digital|solutions|systems")],
                metin=r"\bsap\b|\berp\b|abap|s/?4 ?hana|navision|dynamics ?365|proalpha|\babas\b|odoo|business central|it-beratung|it-consulting|erp-system|softwareberatung|implementierung"),
    # "bi-" (Bürgerinitiative) ve genel "daten"/"ki" metin eslesmeleri dernek/blog gurultusu uretti (21 Eyl): bi- cikti,
    # metin kurali yalniz veri/analitik is baglamini sayar
    "data": dict(sektor=YAZILIM, sozcuk=["data", "daten", "analytics", "datascience", "machine-learning", "ki-", "ai-", "business-intelligence"],
                 metin=r"analytics|data science|datascience|machine learning|künstliche intelligenz|business intelligence|datenanalyse|big data|"
                       r"data engineering|data warehouse|datenplattform|predictive|deep learning|ki-lösung|ki-anwendung|datengetrieben|data-driven|dashboards?"),
    "kinderkrankenpflege": dict(sektor=SAGLIK, sozcuk=["kinderklinik", "kinderarzt", "kinderaerzte", "kinderzentrum", "spz", "kinderhospiz",
                                                         "kinderintensivpflege", "kinderkrankenpflege", "kinderpflegedienst"],
                                metin=r"kinderklinik|kinderarzt|kinderärzt|kinderzentrum|sozialpädiatri|\bspz\b|kinderhospiz|kinderintensiv|kinderkrankenpfl|kinderpflege|pädiatr|kinder- ?und ?jugendmedizin"),
    "rettung": dict(sektor=SAGLIK, sozcuk=["rettungsdienst", "krankentransport", "rettungswache", "drk-", "asb-", "malteser", "johanniter", "feuerwehr", "notfallmedizin"],
                    # gonullu itfaiye (Freiwillige Feuerwehr e.V.) isveren degil: yalniz rettungsdienst/berufsfeuerwehr baglami sayilir
                    metin=r"rettungsdienst|krankentransport|rettungswache|berufsfeuerwehr|rettungssanit|notfallsanit|notarzt|rettungswagen|hauptamtlich|rettungsassistent|krankenfahrt"),
    "fleischer": dict(sektor=GASTRO, sozcuk=["metzgerei", "fleischerei", "schlachterei", "fleischwaren", "wurst", "landmetzgerei", "partyservice"],
                      metin=r"metzgerei|fleischerei|schlachterei|fleischwaren|wurst|metzger|fleischer|partyservice|schinken"),
    "backer": dict(sektor=GASTRO, sozcuk=["baeckerei", "backhaus", "backstube", "brot", "konditorei", "cafe-baeckerei", "landbaeckerei"],
                   metin=r"bäckerei|baeckerei|backhaus|backstube|brot|konditorei|bäcker|backwaren|brötchen|kuchen"),
    "fahrzeug_ing": dict(sektor=OTOMOTIV, sozcuk=["fahrzeugbau", "nutzfahrzeuge", "anhaenger", "aufbauten", "sonderfahrzeug", "fahrzeugtechnik", "automotive", "zulieferer", "kfz-technik"],
                         metin=r"fahrzeugbau|nutzfahrzeug|anhänger|aufbauten|sonderfahrzeug|fahrzeugtechnik|automotive|zulieferer|kfz-technik|fahrzeugentwicklung"),
    "kalte": dict(sektor=INSAAT, sozcuk=["kaeltetechnik", "klimatechnik", "kaelte-klima", "kuehltechnik", "kaelteanlagen", "waermepumpe"],
                  metin=r"kälte|kaelte|klimatechnik|klimaanlage|kühltechnik|kälteanlage|wärmepumpe|klima"),
    "karosserie": dict(sektor=OTOMOTIV, sozcuk=["karosserie", "autolackiererei", "lackiererei", "unfallinstandsetzung", "karosseriebau"],
                       metin=r"karosserie|lackiererei|unfallinstandsetzung|autolackier|lackierung|karosseriebau|unfallschaden"),
    "kurier": dict(sektor=OTOMOTIV, sozcuk=["kurier", "kurierdienst", "express", "botendienst", "paketdienst", "lieferdienst", "fahrradkurier"],
                   metin=r"kurier|express|botendienst|paketdienst|lieferdienst|fahrradkurier|zustellung|transport|logistik|versand|lieferung"),
    "fliesen": dict(sektor=INSAAT, sozcuk=["fliesen", "fliesenleger", "naturstein", "bodenbelag", "mosaik", "fliesenstudio"],
                    metin=r"fliesen|naturstein|bodenbelag|mosaik|fliesenleger|verlegung|badsanierung"),
}
# Uluslararasi (Ingilizce/urun adi) sozcukler .de disinda dunya capinda gurultu uretiyor (expressbrisbane.com ...):
# bunlar yalniz .de alan adlarinda sayilir; Almanca sozcukler tum TLD'lerde.
ULUSLARARASI = {"data", "analytics", "datascience", "machine-learning", "ai-", "bi-", "business-intelligence", "express",
                "automotive", "odoo", "navision", "dynamics365", "proalpha", "abas", "s4hana", "business-central", "malteser"}


def _alman(sozcukler: list) -> list:
    return [w for w in sozcukler if isinstance(w, tuple) and w[0] not in ULUSLARARASI or (not isinstance(w, tuple) and w not in ULUSLARARASI)]


for _id, _m in MESLEKLER.items():
    _m["alan_re"] = _kural(_m["sozcuk"])
    _alm = _alman(_m["sozcuk"])
    _m["alan_re_intl"] = _kural(_alm) if _alm else re.compile(r"(?!)")
    _m["metin_re"] = re.compile(_m["metin"], re.I)
    zayif.MESLEKLER.setdefault(_id, {})["metin_re"] = _m["metin_re"]     # site_tara uyum kontrolu icin
ONCELIK = {m: i for i, m in enumerate(MESLEKLER)}

SEMA = """
CREATE TABLE IF NOT EXISTS adaylar (
    domain TEXT PRIMARY KEY, meslek TEXT NOT NULL, tld TEXT, havuz TEXT, durum TEXT DEFAULT 'yeni', tries INTEGER DEFAULT 0,
    company TEXT DEFAULT '', city TEXT DEFAULT '', website TEXT DEFAULT '', email TEXT DEFAULT '', source_url TEXT DEFAULT '',
    uyum INTEGER DEFAULT 0, sayfa INTEGER DEFAULT 0, note TEXT DEFAULT '', scanned_at TEXT, yuklendi TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ince_meslek ON adaylar(meslek, havuz, durum);
CREATE TABLE IF NOT EXISTS meslek_ozet (
    meslek TEXT PRIMARY KEY, aday INTEGER, onceden INTEGER, havuzda INTEGER, taranacak INTEGER, taranan INTEGER, epostali INTEGER,
    yuklenen INTEGER, eklenen INTEGER, guncellenen INTEGER, reddedilen INTEGER, durum TEXT, hata TEXT, basladi TEXT, bitti TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(m: str) -> None:
    s = f"[{now()}] ince: {m}"
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


def ozet_guncelle(meslek: str, **alanlar) -> None:
    def f(c):
        c.execute("INSERT OR IGNORE INTO meslek_ozet(meslek) VALUES (?)", (meslek,))
        for k, v in alanlar.items():
            c.execute(f"UPDATE meslek_ozet SET {k}=? WHERE meslek=?", (v, meslek))
    yaz(f)


# ------------------------------------------------------------------ 1) aday (Common Crawl, tum meslekler tek gecis)
def onceden_taranmis() -> set[str]:
    """Bu makinede baska hatlarda zaten taranmis alan adlari (tekrar taranmaz)."""
    s: set[str] = set()
    for yol, sorgu in ((ZAYIF / "zayif.sqlite3", "SELECT domain FROM adaylar WHERE durum<>'yeni'"),
                       (ZAYIF / "kategori.sqlite3", "SELECT domain FROM siteler WHERE durum<>'yeni'"),
                       (EPOSTA_DB, "SELECT domain FROM kayitlar WHERE durum<>'yeni'")):
        if yol.exists():
            try:
                c = sqlite3.connect(yol, timeout=60); s.update(r[0] for r in c.execute(sorgu)); c.close()
            except Exception as e:
                log(f"onceden: {yol.name} okunamadi: {e}")
    return s


def meslek_bul(etiket: str, tld: str = "de") -> str | None:
    for m, t in MESLEKLER.items():             # tanim sirasi = oncelik
        if (t["alan_re"] if tld == "de" else t["alan_re_intl"]).search(etiket):
            return m
    return None


def aday_topla() -> None:
    conn = db(); var = {r[0] for r in conn.execute("SELECT domain FROM adaylar")}; conn.close()
    if var:
        log(f"aday: tablo dolu ({len(var)}), toplama atlandi"); return
    eski = onceden_taranmis()
    log(f"aday: onceden taranmis {len(eski)} alan adi elenecek")
    t0 = time.time(); n = 0; yeni = []; sayac = {m: 0 for m in MESLEKLER}; onceden = {m: 0 for m in MESLEKLER}
    with gzip.open(CC_GZ, "rt", encoding="utf-8", errors="replace") as f:
        for satir in f:
            p = satir.rstrip("\n").split("\t")
            if len(p) < 2:
                continue
            parcalar = p[1].split(".")
            if len(parcalar) != 2 or parcalar[0] not in TLDLER:
                continue
            etiket = parcalar[1]
            if len(etiket) < 5 or "xn--" in etiket:
                continue
            n += 1
            m = meslek_bul(etiket, parcalar[0])
            if not m:
                continue
            domain = f"{etiket}.{parcalar[0]}"
            if any(x in domain for x in PLATFORM):
                continue
            if domain in eski:
                onceden[m] += 1; continue
            yeni.append((domain, m, parcalar[0], now())); sayac[m] += 1
    yaz(lambda c: c.executemany("INSERT OR IGNORE INTO adaylar(domain, meslek, tld, created_at) VALUES (?,?,?,?)", yeni))
    for m in MESLEKLER:
        ozet_guncelle(m, aday=sayac[m], onceden=onceden[m])
    log(f"aday: {n} alan adi ({'/'.join(TLDLER)}) tarandi, {len(yeni)} aday ({time.time() - t0:.0f} sn): "
        + ", ".join(f"{m} {sayac[m]} (+{onceden[m]} önceden)" for m in MESLEKLER))


# ------------------------------------------------------------------ 2) havuz elemesi
def havuz_ele(meslek: str) -> dict:
    hg = zayif.havuz_modul()
    conn = db(); domainler = [r[0] for r in conn.execute("SELECT domain FROM adaylar WHERE meslek=? AND havuz IS NULL", (meslek,))]; conn.close()
    for i in range(0, len(domainler), 2000):
        parca = domainler[i:i + 2000]
        r = hg.istek("/api/havuz/bilinen", {"domainler": parca}); bilinen = r.get("bilinen", {})
        yaz(lambda c, g=[(bilinen.get(d, "bilinmeyen"), d) for d in parca]: c.executemany("UPDATE adaylar SET havuz=? WHERE domain=?", g))
        time.sleep(0.5)
    conn = db()
    r = conn.execute("SELECT SUM(havuz<>'bilinmeyen'), SUM(havuz='bilinmeyen') FROM adaylar WHERE meslek=?", (meslek,)).fetchone(); conn.close()
    log(f"{meslek}: havuzda {r[0] or 0}, bilinmeyen {r[1] or 0}")
    return {"havuzda": r[0] or 0, "taranacak": r[1] or 0}


# ------------------------------------------------------------------ 3) site (cok surecli)
def parca_tara(satirlar: list[tuple[str, str]], iplik: int) -> list[dict]:
    sonuclar = []
    with ThreadPoolExecutor(max_workers=max(1, min(iplik, len(satirlar)))) as pool:
        isler = {pool.submit(site_tara, d, [m]): d for d, m in satirlar}
        for f in as_completed(isler):
            d = isler[f]
            try:
                sonuclar.append(f.result())
            except Exception as e:
                sonuclar.append({"domain": d, "durum": "hata", "note": f"{type(e).__name__}: {e}"[:200], "company": "", "city": "",
                                 "website": "", "email": "", "source_url": "", "uyum": 0, "sayfa": 0})
    return sonuclar


def tara(meslek: str, isci: int) -> dict:
    conn = db()
    satirlar = [(r[0], meslek) for r in conn.execute(
        "SELECT domain FROM adaylar WHERE meslek=? AND havuz='bilinmeyen' AND durum IN ('yeni','hata') AND tries<2", (meslek,))]
    conn.close()
    iplik = max(8, isci // SURECLER)
    log(f"{meslek}: {len(satirlar)} site taranacak ({SURECLER} surec x {iplik})")
    t0 = time.time(); n = ok = 0
    for i in range(0, len(satirlar), 4000):
        parti = satirlar[i:i + 4000]
        boy = max(1, -(-len(parti) // SURECLER))
        gruplar = [parti[j:j + boy] for j in range(0, len(parti), boy)]
        sonuclar = []
        with ProcessPoolExecutor(max_workers=SURECLER) as havuz:
            for grup in havuz.map(parca_tara, gruplar, [iplik] * len(gruplar)):
                for r in grup:
                    sonuclar.append(r); n += 1; ok += bool(r["email"] and r["uyum"])
        yaz(lambda c, ss=sonuclar: c.executemany(
            "UPDATE adaylar SET durum=?, tries=tries+1, company=?, city=?, website=?, email=?, source_url=?, uyum=?, sayfa=?, note=?, scanned_at=? WHERE domain=?",
            [(r["durum"], r["company"], r["city"], r["website"], r["email"], r["source_url"], r["uyum"], r["sayfa"], r["note"], now(), r["domain"]) for r in ss]))
        hiz = n / max(1, time.time() - t0)
        log(f"{meslek}: {n}/{len(satirlar)} site, {ok} uyumlu e-postali, {hiz:.1f} site/sn, kalan ~{(len(satirlar) - n) / max(hiz, 0.1) / 60:.0f} dk")
        if (ZAYIF / "DUR-ince").exists():
            log("DUR-ince: tarama kesildi"); break
    return {"taranan": n, "epostali": ok}


# ------------------------------------------------------------------ 4) yukle
def yukle(meslek: str) -> dict:
    conn = db()
    # .de disi TLD: Impressum'da Almanya adresi (PLZ->sehir) sart
    satirlar = conn.execute(
        """SELECT * FROM adaylar WHERE meslek=? AND durum='tarandi' AND email<>'' AND uyum=1 AND yuklendi IS NULL
           AND (tld='de' OR city<>'')""", (meslek,)).fetchall()
    conn.close()
    if not satirlar:
        log(f"{meslek}: yuklenecek kayit yok"); return {"yuklenen": 0, "eklenen": 0, "guncellenen": 0, "reddedilen": 0}
    OUT.mkdir(parents=True, exist_ok=True)
    dosya = OUT / f"ince-{meslek}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with dosya.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "city", "sector", "website", "email", "source_url"])
        w.writeheader()
        for r in satirlar:
            w.writerow({"company": (r["company"] or "").strip()[:200], "city": (r["city"] or "").strip()[:100], "sector": MESLEKLER[meslek]["sektor"],
                        "website": site_kisalt(r["website"]), "email": r["email"], "source_url": site_kisalt(r["source_url"])[:500]})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(GONDER), str(dosya), "--etiket", f"pc-{meslek}"], capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE)
    mt = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not mt:
        raise RuntimeError(f"yukleme basarisiz (kod {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    t = json.loads(mt.group(1))
    yaz(lambda c: c.executemany("UPDATE adaylar SET yuklendi=? WHERE domain=?", [(now(), r["domain"]) for r in satirlar]))
    log(f"{meslek}: {len(satirlar)} satir -> TOPLAM {json.dumps(t, ensure_ascii=False)} ({dosya.name})")
    return {"yuklenen": len(satirlar), "eklenen": t.get("eklenen", 0), "guncellenen": t.get("guncellenen", 0), "reddedilen": t.get("reddedilen", 0)}


# ------------------------------------------------------------------ 5) rapor
def rapor(yazdir: bool = True) -> str:
    conn = db()
    satirlar = ["# DURUM — 12 ince meslek, Common Crawl geniş sözcük taraması", f"Güncelleme: {now()}", "",
                "| # | meslek | etiket | aday | önceden taranmış | havuzda vardı | taranacak | taranan | e-postalı | yüklenen | +eklenen | ~güncel | red | durum |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, m in enumerate(MESLEKLER, 1):
        r = conn.execute("SELECT * FROM meslek_ozet WHERE meslek=?", (m,)).fetchone()
        c = conn.execute("SELECT SUM(durum<>'yeni'), SUM(durum='tarandi' AND uyum=1 AND email<>'') FROM adaylar WHERE meslek=? AND havuz='bilinmeyen'", (m,)).fetchone()
        if not r:
            satirlar.append(f"| {i} | {m} | pc-{m} | | | | | | | | | | | bekliyor |"); continue
        d = r["durum"] or "bekliyor"
        if r["hata"]:
            d += f" — hata: {r['hata'][:80]}"
        satirlar.append(f"| {i} | {m} | pc-{m} | {r['aday'] or 0} | {r['onceden'] or 0} | {r['havuzda'] or ''} | {r['taranacak'] or ''} | {c[0] or 0} | {c[1] or 0} | "
                        f"{r['yuklenen'] or 0} | {r['eklenen'] or 0} | {r['guncellenen'] or 0} | {r['reddedilen'] or 0} | {d} |")
    s = conn.execute("SELECT durum, COUNT(1) FROM adaylar WHERE havuz='bilinmeyen' GROUP BY durum").fetchall()
    conn.close()
    satirlar += ["", "Site durumları (havuzda olmayanlar): " + ", ".join(f"{a} {b}" for a, b in s), "",
                 "Kaynak: Common Crawl domain vertices (.de/.com/.net/.eu/.org). Kurallar: robots.txt, alan adı başına ardışık istek ≥1 sn, 20 sn, ≤5 sayfa; "
                 "yalnız kendi alan adındaki rol e-postası (kişisel/freemail yok); .de dışı için Impressum'da Almanya adresi şartı; captcha yok; ücretli API/dizin yok."]
    metin = "\n".join(satirlar) + "\n"
    DURUM_MD.write_text(metin, encoding="utf-8")
    if yazdir:
        print(metin)
    return metin


# ------------------------------------------------------------------ calis
def a_hatti_bitti() -> bool:
    if not EPOSTA_DB.exists():
        return True
    try:
        c = sqlite3.connect(EPOSTA_DB, timeout=60)
        r = c.execute("SELECT durum FROM grup_ozet WHERE grup='ince'").fetchone(); c.close()
        return bool(r and r[0] in ("bitti", "hata"))
    except Exception:
        return False


def meslek_isle(meslek: str, isci: int) -> None:
    ozet_guncelle(meslek, durum="sürüyor", basladi=now(), hata="")
    b = havuz_ele(meslek); ozet_guncelle(meslek, **b)
    c = tara(meslek, isci); ozet_guncelle(meslek, **c)
    d = yukle(meslek); ozet_guncelle(meslek, **d, durum="bitti", bitti=now())
    conn = db(); r = conn.execute("SELECT aday, onceden FROM meslek_ozet WHERE meslek=?", (meslek,)).fetchone(); conn.close()
    log(f"RAPOR {meslek} (pc-{meslek}): aday {r['aday']} (+{r['onceden']} önceden taranmış) / havuzda vardı {b['havuzda']} / taranan {c['taranan']} / "
        f"e-postalı {c['epostali']} / yüklenen {d['yuklenen']} (+{d['eklenen']} / ~{d['guncellenen']} / red {d['reddedilen']})")


def calis(isci: int, bekleme: bool = True) -> None:
    log(f"calis basladi: {len(MESLEKLER)} meslek, {isci} es zamanli")
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; cikiliyor"); return
    aday_topla()
    for m in MESLEKLER:                                  # havuz elemesi A hattini beklemeden
        conn = db(); r = conn.execute("SELECT havuzda FROM meslek_ozet WHERE meslek=?", (m,)).fetchone(); conn.close()
        if not r or r["havuzda"] is None:
            ozet_guncelle(m, **havuz_ele(m))
    rapor(yazdir=False)
    while bekleme and not a_hatti_bitti():
        if (ZAYIF / "DUR-ince").exists():
            log("DUR-ince: durduruldu"); return
        log("A hatti (eposta.py ince) bitmedi; 5 dk bekleniyor"); time.sleep(300)
    hatali = []
    for m in MESLEKLER:
        if (ZAYIF / "DUR-ince").exists():
            log("DUR-ince: durduruldu"); break
        conn = db(); r = conn.execute("SELECT durum FROM meslek_ozet WHERE meslek=?", (m,)).fetchone(); conn.close()
        if r and r["durum"] == "bitti":
            log(f"{m}: zaten bitti, atlandi"); continue
        try:
            meslek_isle(m, isci)
        except Exception as e:
            hatali.append((m, f"{type(e).__name__}: {e}"[:200])); ozet_guncelle(m, durum="hata", hata=f"{type(e).__name__}: {e}"[:200])
            log(f"!! {m} atlandi: {type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
        rapor(yazdir=False)
    rapor(yazdir=False)
    log("calis bitti" + (f"; hatali: {hatali}" if hatali else "; hata yok"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["calis", "rapor", "aday"])
    p.add_argument("--isci", type=int, default=ISCI); p.add_argument("--bekleme", action="store_true", help="A hattini bekleme")
    a = p.parse_args()
    if a.komut == "calis":
        calis(a.isci, bekleme=not a.bekleme)
    elif a.komut == "aday":
        aday_topla(); rapor()
    else:
        rapor()


if __name__ == "__main__":
    main()
