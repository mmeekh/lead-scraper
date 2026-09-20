"""Kalinlastirma kosusu: 539.659 sirket x 54 meslek (1,13 M cift), oncelik sirasiyla, kaldigi yerden surer.

  python verify/kalinlastir.py ice-aktar            # csv.gz -> sirketler + ciftler tablolari (idempotent)
  python verify/kalinlastir.py cek                   # cekici isci (Playwright), surekli
  python verify/kalinlastir.py yargila               # hakem iscisi (A tum ciftler -> B yalniz A=yes), surekli
  python verify/kalinlastir.py durum                 # DURUM.md'yi simdi yaz
  python verify/kalinlastir.py denetim <meslek>      # %3 kor denetim ornegi (evet) + 20 listelenmeyen

Kurallar: ucretli API yok; robots.txt; alan adi basina tek sekme ve >=1 sn; <=8 sekme; alan adi basina
en fazla 4 sayfa; makinenin <=%80'i (Ollama GPU payi ortamdan). Uzlasma: iki hakem yes + cumle duzeyi
alinti dogrulama + kanit gecidi -> evet; A=no -> hayir; A=unclear -> belirsiz.
Kayipsiz on eleme: sayfa metninde meslegin hicbir kanit anahtari gecmiyorsa cift modele gitmeden
'belirsiz/on-eleme' olur (gecit zaten anahtar ister; sonuc degismez, GPU tasarrufu).
Her 5.000 ciftte verify/out/verified-<meslek>-<n>.jsonl (evet) ve redd-<meslek>-<n>.jsonl (hayir/belirsiz).
Saatte bir DURUM.md; meslek basina listeleme orani %2 alti / %40 ustuyse (>=300 model karari sonra)
meslek duraklatilir ve BLOKAJ.md'ye yazilir. Dosya verify/DUR varsa isciler nazikce durur.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify import fetch as cekici                      # noqa: E402
from verify import judge as hakem                       # noqa: E402
from verify.candidates import meslekler                 # noqa: E402
from verify.config import MODEL_A, MODEL_B, PROMPT_VERSION  # noqa: E402
from verify.consensus import job_ads_cikar, kanit_gecidi, uzlas  # noqa: E402
from verify.db import (db, now, pages_of, read_page_text, save_judgment,  # noqa: E402
                       upsert_domain, yaz)
from verify.impressum import display_name, legal_name_bul  # noqa: E402

BASE = Path(__file__).resolve().parent
ADAY = BASE / "jobfind-adaylar-53meslek-2026-09-20.csv.gz"
OUT = BASE / "out"
DURUM = BASE / "DURUM.md"
BLOKAJ = BASE / "BLOKAJ.md"
DUR = BASE / "DUR"
KOSU_LOG = BASE / "kalinlastir.log"

PARCA = 5000                       # cikti parcasi (cift)
TUR1_TAVAN = {"verkauf": 20000, "service": 20000, "industriekauf": 20000, "koch": 20000}
DURAKLAT_MIN = 300                 # oran degerlendirmesi icin en az model karari
DURAKLAT_ALT, DURAKLAT_UST = 0.02, 0.40
A_PARTI = 150                      # A icin parti; sonra B (model degisimi seyrek olsun)
CEKIM_PARTI = 64
MAX_SAYFA = 4                      # alan adi basina (VPS kurali)
YENIDEN_DENEME_GUN = 7
DISK_ASGARI_GB = 5
TEK_PARTI = "--tek" in sys.argv          # duman testi: tek parti kos ve cik
ATLA = ("facebook.", "instagram.", "linkedin.", "google.", "wixsite.", "jimdo", "business.site",
        "sites.google", "myshopify.", "ebay.", "amazon.", "xing.", "youtube.")

SEMA = """
CREATE TABLE IF NOT EXISTS sirketler (
    domain TEXT PRIMARY KEY, company TEXT, city TEXT, category TEXT, website TEXT,
    oncelik INTEGER, fetch_status TEXT DEFAULT 'yeni', tries INTEGER DEFAULT 0,
    next_try TEXT, fetched_at TEXT, sayfa INTEGER DEFAULT 0, note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sirket_fetch ON sirketler(fetch_status, oncelik);
CREATE TABLE IF NOT EXISTS ciftler (
    domain TEXT NOT NULL, meslek TEXT NOT NULL, oncelik INTEGER, tur INTEGER DEFAULT 1,
    on_eleme INTEGER DEFAULT 0, a_decision TEXT, b_decision TEXT, karar TEXT, agreement TEXT,
    sebep TEXT DEFAULT '', parca INTEGER, updated_at TEXT,
    PRIMARY KEY (domain, meslek)
);
CREATE INDEX IF NOT EXISTS ix_cift_kuyruk ON ciftler(karar, oncelik, meslek);
CREATE INDEX IF NOT EXISTS ix_cift_meslek ON ciftler(meslek, karar);
CREATE TABLE IF NOT EXISTS meslek_durum (
    meslek TEXT PRIMARY KEY, durum TEXT DEFAULT 'aktif', not_ TEXT DEFAULT '', updated_at TEXT
);
"""


def log(m: str) -> None:
    s = f"[{now()}] {m}"
    print(s, flush=True)
    with KOSU_LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def baglan() -> sqlite3.Connection:
    conn = db()
    conn.executescript(SEMA)
    conn.commit()
    return conn


def katalog() -> tuple[dict, dict[str, int]]:
    kat = meslekler()
    raw = json.loads((BASE / "meslekler.json").read_text(encoding="utf-8"))
    sira = {m: i for i, m in enumerate(raw["oncelik"])}
    return kat, sira


def host_of(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        h = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


# --------------------------------------------------------------------------- ice aktarma
def ice_aktar() -> None:
    kat, sira = katalog()
    conn = baglan()
    var = conn.execute("SELECT COUNT(1) FROM ciftler").fetchone()[0]
    if var:
        log(f"ice-aktar: ciftler zaten dolu ({var}); yalniz eksikler eklenecek")
    n_s = n_c = atlanan = bilinmeyen = 0
    with gzip.open(ADAY, "rt", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        toplu_s, toplu_c = [], []
        for row in r:
            d = (row.get("domain") or "").strip().lower()
            if d.startswith("www."):
                d = d[4:]
            if not d or any(a in d for a in ATLA):
                atlanan += 1
                continue
            mes = [m.strip() for m in re.split(r"[|;,]", row.get("meslek_kayitlari") or "") if m.strip()]
            mes = [m for m in mes if m in kat and m in sira]
            if not mes:
                bilinmeyen += 1
                continue
            oncelik = min(sira[m] for m in mes)
            site = (row.get("website") or "").strip() or f"https://{d}/"
            toplu_s.append((d, row.get("company", ""), row.get("city", ""), row.get("category", ""), site, oncelik))
            for m in mes:
                toplu_c.append((d, m, sira[m]))
            if len(toplu_s) >= 5000:
                conn.executemany("INSERT OR IGNORE INTO sirketler(domain,company,city,category,website,oncelik) VALUES (?,?,?,?,?,?)", toplu_s)
                conn.executemany("INSERT OR IGNORE INTO ciftler(domain,meslek,oncelik) VALUES (?,?,?)", toplu_c)
                n_s += len(toplu_s); n_c += len(toplu_c)
                toplu_s, toplu_c = [], []
                conn.commit()
        if toplu_s:
            conn.executemany("INSERT OR IGNORE INTO sirketler(domain,company,city,category,website,oncelik) VALUES (?,?,?,?,?,?)", toplu_s)
            conn.executemany("INSERT OR IGNORE INTO ciftler(domain,meslek,oncelik) VALUES (?,?,?)", toplu_c)
            n_s += len(toplu_s); n_c += len(toplu_c)
            conn.commit()
    for m in sira:
        conn.execute("INSERT OR IGNORE INTO meslek_durum(meslek,durum,updated_at) VALUES (?,?,?)", (m, "aktif", now()))
    conn.commit()
    s = conn.execute("SELECT COUNT(1) FROM sirketler").fetchone()[0]
    c = conn.execute("SELECT COUNT(1) FROM ciftler").fetchone()[0]
    conn.close()
    log(f"ice-aktar: {n_s} sirket / {n_c} cift islendi; tabloda {s} sirket, {c} cift; atlanan platform {atlanan}, mesleksiz {bilinmeyen}")


# --------------------------------------------------------------------------- yardimcilar
def disk_bos_gb() -> float:
    return shutil.disk_usage(BASE).free / 1e9


def dur_istendi() -> bool:
    return DUR.exists()


def tur1_kota_doldu(conn, meslek: str) -> bool:
    tavan = TUR1_TAVAN.get(meslek)
    if not tavan:
        return False
    n = conn.execute("SELECT COUNT(1) FROM ciftler WHERE meslek=? AND (karar IS NOT NULL OR a_decision IS NOT NULL)", (meslek,)).fetchone()[0]
    return n >= tavan


def aktif_meslekler(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT meslek FROM meslek_durum WHERE durum='aktif'")}


def blokaj_yaz(baslik: str, metin: str) -> None:
    with BLOKAJ.open("a", encoding="utf-8") as f:
        f.write(f"\n## {baslik} — {now()}\n\n{metin}\n")


# --------------------------------------------------------------------------- cekici
async def cek_dongusu() -> None:
    from playwright.async_api import async_playwright
    cekici.MAX_PAGES = MAX_SAYFA                       # VPS kurali: alan adi basina en fazla 4 sayfa
    log(f"cekici basladi: parti {CEKIM_PARTI}, sekme {cekici.MAX_TABS}, sayfa/alan adi {MAX_SAYFA}")
    bosta = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        try:
            while not dur_istendi():
                if disk_bos_gb() < DISK_ASGARI_GB:
                    log(f"!! disk {disk_bos_gb():.1f} GB — cekici bekliyor"); await asyncio.sleep(600); continue
                conn = baglan()
                aktif = aktif_meslekler(conn)
                yer = ",".join("?" * len(aktif)) or "''"
                # yalniz aktif meslegi olan ve hakemin isleyecegi sirketler; oncelik sirasiyla
                satirlar = conn.execute(
                    f"""SELECT s.* FROM sirketler s WHERE s.fetch_status IN ('yeni','tekrar')
                        AND (s.next_try IS NULL OR s.next_try <= ?) AND s.tries < 2
                        AND EXISTS (SELECT 1 FROM ciftler c WHERE c.domain=s.domain AND c.karar IS NULL AND c.meslek IN ({yer}))
                        ORDER BY s.oncelik, s.domain LIMIT ?""", (now(), *aktif, CEKIM_PARTI)).fetchall()
                conn.close()
                if not satirlar:
                    bosta += 1
                    if bosta % 10 == 1:
                        log("cekici: kuyruk bos, 5 dk bekleniyor")
                    await asyncio.sleep(300); continue
                bosta = 0
                t0 = time.time()
                sem = asyncio.Semaphore(cekici.MAX_TABS)
                # bir_domain domains tablosuna durum yazar; satir yoksa no-op. pages tablosu ortak.
                sonuclar = await asyncio.gather(*[cekici.bir_domain(browser, dict(s), sem) for s in satirlar], return_exceptions=True)
                ok = kotu = 0
                for s, r in zip(satirlar, sonuclar):
                    d = s["domain"]
                    if isinstance(r, Exception) or r.get("hata"):
                        kotu += 1
                        hata = str(r)[:120] if isinstance(r, Exception) else r["hata"]
                        yaz(lambda c, d=d, h=hata: c.execute(
                            "UPDATE sirketler SET fetch_status=CASE WHEN tries+1>=2 THEN 'cekilemedi' ELSE 'tekrar' END, tries=tries+1, "
                            "next_try=datetime('now', ?), note=? WHERE domain=?", (f"+{YENIDEN_DENEME_GUN} days", h, d)))
                    else:
                        ok += 1
                        yaz(lambda c, d=d, n=r["sayfa"]: c.execute(
                            "UPDATE sirketler SET fetch_status='cekildi', fetched_at=?, sayfa=? WHERE domain=?", (now(), n, d)))
                # ana sayfasi alinamayanlarin (sayfa=0) ciftleri hakeme gitmesin
                yaz(lambda c: c.execute(
                    "UPDATE ciftler SET karar='belirsiz', agreement='none', sebep='cekilemedi', updated_at=? "
                    "WHERE karar IS NULL AND domain IN (SELECT domain FROM sirketler WHERE fetch_status='cekilemedi')", (now(),)))
                log(f"cekici: {ok} cekildi, {kotu} sorunlu, {time.time() - t0:.0f} sn")
                if TEK_PARTI:
                    break
        finally:
            await browser.close()
    log("cekici durdu (DUR)")


# --------------------------------------------------------------------------- hakem
def cumle_duzeyi_dogrula(quotes: list[str], ham: dict[str, str]) -> tuple[bool, list[bool]]:
    """Cumle duzeyi: alinti sayfa metninde birebir gecmeli VE tek bir cumlenin icinde kalmali
    (cumle sinirini asan, iki parcayi birlestiren 'alinti'lar reddedilir)."""
    tam, tekil = hakem.alintilari_dogrula(quotes, ham)
    if not tam:
        return tam, tekil
    metin = " \n ".join(ham.values())
    cumleler = [hakem._normalize(c) for c in re.split(r"(?<=[.!?])\s+|\n+", metin) if len(c.strip()) > 3]
    sonuc = []
    for q in quotes:
        qn = hakem._normalize(q)
        sonuc.append(any(qn in c for c in cumleler) if len(qn) < 400 else False)
    return all(sonuc), sonuc


def on_eleme_gecer(ham: dict[str, str], anahtarlar: list[str]) -> bool:
    """Kayipsiz on eleme: gecit kanit anahtari ister; sayfa metninde hicbiri yoksa evet imkansiz."""
    if not anahtarlar:
        return True
    metin = " ".join(ham.values()).lower()
    return any(a.lower() in metin for a in anahtarlar)


def cift_kaydi(conn, domain: str, meslek: str, a: dict | None, b: dict | None, karar: str, agreement: str, sebep: str) -> dict:
    sayfalar = pages_of(conn, domain)
    tur_url = {p["tur"]: p["url"] for p in sayfalar}
    s = conn.execute("SELECT * FROM sirketler WHERE domain=?", (domain,)).fetchone()
    legal = legal_name_bul(read_page_text(domain, "impressum")) or ((a or {}).get("legal_name") or "")[:100]
    kanit = []
    for j, et in ((a, MODEL_A), (b, MODEL_B)):
        for q in (j or {}).get("quotes", []):
            kanit.append({"model": et, "url": tur_url.get("home", ""), "alinti": q})
    return {
        "domain": domain, "meslek_kaydi": meslek, "karar": karar, "agreement": agreement, "sebep": sebep,
        "legal_name": legal, "display_name": display_name(legal) or (s["company"] if s else ""),
        "havuz_adi": s["company"] if s else "", "city": s["city"] if s else "", "category": s["category"] if s else "",
        "activity_de": (a or {}).get("activity_summary", "") or (b or {}).get("activity_summary", ""),
        "size_hint": (a or {}).get("size_hint", "") if (a or {}).get("size_hint", "") != "unbekannt" else "",
        "kanit": kanit,
        "modeller": {MODEL_A: {k: (a or {}).get(k) for k in ("decision", "evidence_type", "quotes_verified", "reason")},
                     MODEL_B: {k: (b or {}).get(k) for k in ("decision", "evidence_type", "quotes_verified", "reason")}},
        "job_ads": job_ads_cikar(read_page_text(domain, "karriere"), tur_url.get("karriere", "")) if karar == "evet" else [],
        "pages": [{"tur": p["tur"], "url": p["url"], "chars": p["chars"]} for p in sayfalar],
        "models": [MODEL_A, MODEL_B], "prompt_version": PROMPT_VERSION, "verified_at": now(),
    }


def parcaya_yaz(meslek: str, karar: str, kayit: dict) -> int:
    """5.000'lik parca: meslek basina sonuclanan cift sayisina gore parca no; evet ayri, hayir/belirsiz ayri."""
    OUT.mkdir(exist_ok=True)
    conn = db(sema=False)
    n = conn.execute("SELECT COUNT(1) FROM ciftler WHERE meslek=? AND karar IS NOT NULL", (meslek,)).fetchone()[0]
    conn.close()
    parca = n // PARCA + 1
    ad = ("verified" if karar == "evet" else "redd") + f"-{meslek}-{parca:03d}.jsonl"
    with (OUT / ad).open("a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    return parca


def sonuclandir(conn, domain: str, meslek: str, a, b, karar: str, agreement: str, sebep: str, on_eleme: int = 0) -> None:
    kayit = cift_kaydi(conn, domain, meslek, a, b, karar, agreement, sebep)
    parca = parcaya_yaz(meslek, karar, kayit)
    yaz(lambda c: c.execute(
        "UPDATE ciftler SET a_decision=?, b_decision=?, karar=?, agreement=?, sebep=?, parca=?, on_eleme=?, updated_at=? WHERE domain=? AND meslek=?",
        ((a or {}).get("decision"), (b or {}).get("decision"), karar, agreement, sebep, parca, on_eleme, now(), domain, meslek)))


def oran_kontrol(conn, meslek: str) -> None:
    r = conn.execute("SELECT SUM(karar='evet'), COUNT(1) FROM ciftler WHERE meslek=? AND karar IS NOT NULL AND on_eleme=0 AND sebep NOT IN ('cekilemedi')", (meslek,)).fetchone()
    evet, n = (r[0] or 0), (r[1] or 0)
    if n < DURAKLAT_MIN:
        return
    oran = evet / n
    if oran < DURAKLAT_ALT or oran > DURAKLAT_UST:
        yaz(lambda c: c.execute("UPDATE meslek_durum SET durum='duraklatildi', not_=?, updated_at=? WHERE meslek=?",
                                (f"listeleme orani %{100 * oran:.1f} ({evet}/{n} model karari)", now(), meslek)))
        blokaj_yaz(f"meslek duraklatildi: {meslek}",
                   f"Model kararli {n} ciftte listeleme orani **%{100 * oran:.1f}** ({evet} evet) — sinir %2–%40 disinda. "
                   f"Meslek duraklatildi; kuyruk sonraki meslege gecti. Inceleme: `python verify/kalinlastir.py denetim {meslek}` "
                   f"ve `verify/out/verified-{meslek}-*.jsonl`. Yeniden acmak icin: "
                   f"`UPDATE meslek_durum SET durum='aktif' WHERE meslek='{meslek}'`.")
        log(f"!! {meslek} duraklatildi: oran %{100 * oran:.1f} ({evet}/{n})")


def ollama_bekle() -> None:
    """Ollama ayakta degilse partiyi hatayla tuketme; 30 sn araliklarla bekle (en cok sonsuz, log seyrek)."""
    import requests
    bekleme = 0
    while not dur_istendi():
        try:
            requests.get(f"{hakem.OLLAMA_URL}/api/tags", timeout=10).raise_for_status()
            if bekleme:
                log(f"hakem: Ollama geri geldi ({bekleme} sn sonra)")
            return
        except Exception:
            if bekleme % 300 == 0:
                log("!! hakem: Ollama yanit vermiyor (127.0.0.1:11434) — bekleniyor")
            time.sleep(30); bekleme += 30


def yargi_dongusu() -> None:
    kat, sira = katalog()
    log(f"hakem basladi: A parti {A_PARTI}, A={MODEL_A}, B={MODEL_B}")
    son_durum = 0.0
    bosta = 0
    while not dur_istendi():
        if time.time() - son_durum > 3600:
            durum_yaz(); son_durum = time.time()
        ollama_bekle()
        conn = baglan()
        aktif = aktif_meslekler(conn)
        aktif = {m for m in aktif if not tur1_kota_doldu(conn, m)}
        yer = ",".join("?" * len(aktif)) or "''"
        # A bekleyen ciftler: sirketi cekilmis, karar yok, meslek aktif — oncelik sirasiyla
        satirlar = conn.execute(
            f"""SELECT c.domain, c.meslek FROM ciftler c JOIN sirketler s ON s.domain=c.domain
                WHERE c.karar IS NULL AND c.a_decision IS NULL AND s.fetch_status='cekildi' AND c.meslek IN ({yer})
                ORDER BY c.oncelik, c.domain LIMIT ?""", (*aktif, A_PARTI)).fetchall()
        conn.close()
        if not satirlar:
            bosta += 1
            if bosta % 10 == 1:
                log("hakem: bekleyen cift yok (cekici onde degil ya da kuyruk bitti), 2 dk bekleniyor")
            time.sleep(120); continue
        bosta = 0
        t0 = time.time()
        # ---- A: tum ciftler (on eleme ile)
        b_kuyrugu: list[tuple[str, str, dict, str, dict]] = []
        sayac = {"on_eleme": 0, "a_yes": 0, "a_no": 0, "a_unclear": 0, "hata": 0}
        conn = baglan()
        for domain, meslek in satirlar:
            m = kat[meslek]
            sayfalar = pages_of(conn, domain)
            kanit, ham = hakem.kanit_metni(conn, domain, sayfalar)
            if not kanit:
                sonuclandir(conn, domain, meslek, None, None, "belirsiz", "none", "metin-yok"); continue
            if not on_eleme_gecer(ham, m.get("beleg_anahtarlar", [])):
                sayac["on_eleme"] += 1
                sonuclandir(conn, domain, meslek, None, None, "belirsiz", "none", "on-eleme", on_eleme=1); continue
            s = conn.execute("SELECT company FROM sirketler WHERE domain=?", (domain,)).fetchone()
            try:
                a = hakem.yargila(MODEL_A, "A", m, [s["company"] if s else ""], kanit, ham)
            except Exception as e:
                sayac["hata"] += 1; log(f"  !! A {domain}/{meslek}: {type(e).__name__}: {e}"[:160]); ollama_bekle(); continue
            save_judgment(conn, domain, meslek, MODEL_A, a); conn.commit()
            if a["decision"] == "yes":
                sayac["a_yes"] += 1
                b_kuyrugu.append((domain, meslek, a, kanit, ham))
                yaz(lambda c, d=domain, mk=meslek: c.execute("UPDATE ciftler SET a_decision='yes', updated_at=? WHERE domain=? AND meslek=?", (now(), d, mk)))
            elif a["decision"] == "no":
                sayac["a_no"] += 1
                sonuclandir(conn, domain, meslek, a, None, "hayir", "one", "A=no")
            else:
                sayac["a_unclear"] += 1
                sonuclandir(conn, domain, meslek, a, None, "belirsiz", "one", "A=unclear")
        conn.close()
        ta = time.time() - t0
        # ---- B: yalniz A=yes
        tb0 = time.time(); evet = 0
        if b_kuyrugu:
            hakem.bosalt(MODEL_A)
            conn = baglan()
            for domain, meslek, a, kanit, ham in b_kuyrugu:
                m = kat[meslek]
                s = conn.execute("SELECT company FROM sirketler WHERE domain=?", (domain,)).fetchone()
                try:
                    b = hakem.yargila(MODEL_B, "B", m, [s["company"] if s else ""], kanit, ham)
                except Exception as e:
                    sayac["hata"] += 1; log(f"  !! B {domain}/{meslek}: {type(e).__name__}: {e}"[:160]); ollama_bekle(); continue
                save_judgment(conn, domain, meslek, MODEL_B, b); conn.commit()
                # cumle duzeyi alinti dogrulama (her iki taraf)
                a["quotes_verified"], _ = cumle_duzeyi_dogrula(a.get("quotes", []), ham)
                b["quotes_verified"], _ = cumle_duzeyi_dogrula(b.get("quotes", []), ham)
                karar, agreement = uzlas(a, b)
                sebep = "uzlasma"
                if karar == "evet":
                    alintilar = [q for j in (a, b) for q in j.get("quotes", [])]
                    if not kanit_gecidi(alintilar, m.get("beleg_anahtarlar", [])):
                        karar, agreement, sebep = "belirsiz", "one", "kanit-gecidi"
                elif karar == "belirsiz" and b["decision"] == "yes" and not (a["quotes_verified"] and b["quotes_verified"]):
                    sebep = "alinti-dogrulanmadi"
                elif karar == "hayir":
                    sebep = "B=no"
                else:
                    sebep = f"B={b['decision']}"
                if karar == "evet":
                    evet += 1
                sonuclandir(conn, domain, meslek, a, b, karar, agreement, sebep)
            conn.close()
            hakem.bosalt(MODEL_B)
        # ---- oran kontrolu (partide gecen meslekler)
        conn = baglan()
        for meslek in {mk for _, mk in satirlar}:
            oran_kontrol(conn, meslek)
        conn.close()
        log(f"hakem parti: {len(satirlar)} cift | on-eleme {sayac['on_eleme']} | A yes/no/unclear {sayac['a_yes']}/{sayac['a_no']}/{sayac['a_unclear']} "
            f"| B {len(b_kuyrugu)} -> evet {evet} | hata {sayac['hata']} | A {ta:.0f} sn, B {time.time() - tb0:.0f} sn")
        if TEK_PARTI:
            break
    durum_yaz()
    log("hakem durdu (DUR)")


# --------------------------------------------------------------------------- durum / denetim
def durum_yaz() -> None:
    conn = baglan()
    q = lambda sql, *p: conn.execute(sql, p).fetchone()
    s_top = q("SELECT COUNT(1) FROM sirketler")[0]
    s_dur = dict(conn.execute("SELECT fetch_status, COUNT(1) FROM sirketler GROUP BY 1").fetchall())
    c_top = q("SELECT COUNT(1) FROM ciftler")[0]
    c_kar = dict(conn.execute("SELECT COALESCE(karar,'bekliyor'), COUNT(1) FROM ciftler GROUP BY 1").fetchall())
    on_el = q("SELECT COUNT(1) FROM ciftler WHERE on_eleme=1")[0]
    a_yes_bek = q("SELECT COUNT(1) FROM ciftler WHERE a_decision='yes' AND karar IS NULL")[0]
    j = dict(conn.execute("SELECT model, COUNT(1) FROM judgments GROUP BY 1").fetchall())
    hiz = dict(conn.execute("SELECT model, ROUND(AVG(latency_ms)/1000.0,1) FROM judgments WHERE created_at > datetime('now','-1 day','localtime') GROUP BY 1").fetchall())
    son_saat = q("SELECT COUNT(1) FROM ciftler WHERE karar IS NOT NULL AND updated_at > datetime('now','-1 hour','localtime')")[0]
    son_saat_evet = q("SELECT COUNT(1) FROM ciftler WHERE karar='evet' AND updated_at > datetime('now','-1 hour','localtime')")[0]
    mes = conn.execute("""SELECT c.meslek, md.durum, COUNT(1) top, SUM(c.karar IS NOT NULL) biten, SUM(c.karar='evet') evet,
                          SUM(c.on_eleme=1) onel, SUM(c.karar IS NOT NULL AND c.on_eleme=0 AND c.sebep<>'cekilemedi') modelli
                          FROM ciftler c JOIN meslek_durum md ON md.meslek=c.meslek GROUP BY c.meslek ORDER BY c.oncelik""").fetchall()
    dur = [r for r in conn.execute("SELECT meslek, not_ FROM meslek_durum WHERE durum<>'aktif'")]
    conn.close()
    satirlar = ["# DURUM — kalınlaştırma koşusu", f"Güncelleme: {now()} · istem {PROMPT_VERSION} · A `{MODEL_A}` · B `{MODEL_B}`", "",
                "## Genel", "",
                f"- Şirket: {s_top} — çekildi {s_dur.get('cekildi', 0)}, bekliyor {s_dur.get('yeni', 0) + s_dur.get('tekrar', 0)}, çekilemedi {s_dur.get('cekilemedi', 0)}",
                f"- Çift: {c_top} — evet **{c_kar.get('evet', 0)}**, hayır {c_kar.get('hayir', 0)}, belirsiz {c_kar.get('belirsiz', 0)} (ön eleme {on_el}), bekleyen {c_kar.get('bekliyor', 0)}",
                f"- Son 1 saat: {son_saat} çift sonuçlandı, {son_saat_evet} evet",
                f"- Model çağrısı: A {j.get(MODEL_A, 0)}, B {j.get(MODEL_B, 0)} · A=yes olup B bekleyen: {a_yes_bek}",
                f"- Hız (son 24 s): A {hiz.get(MODEL_A, '-')} sn/kayıt, B {hiz.get(MODEL_B, '-')} sn/kayıt · disk boş {disk_bos_gb():.0f} GB",
                f"- Duraklatılan meslek: {', '.join(f'{m} ({n})' for m, n in dur) or 'yok'}", "",
                "## Meslek başına (öncelik sırasıyla)", "",
                "| meslek | durum | çift | biten | ön eleme | model kararı | evet | listeleme % (modelli) |", "|---|---|---|---|---|---|---|---|"]
    for m, d, top, biten, evet, onel, modelli in mes:
        oran = f"{100 * (evet or 0) / modelli:.1f}" if modelli else "-"
        satirlar.append(f"| {m} | {d} | {top} | {biten or 0} | {onel or 0} | {modelli or 0} | {evet or 0} | {oran} |")
    satirlar += ["", "Çıktı: `verify/out/verified-<meslek>-<n>.jsonl` (evet) · `redd-<meslek>-<n>.jsonl` (hayır/belirsiz) · 5.000 çift/parça.",
                 "Durdurmak: `verify/DUR` dosyası oluştur. Günlük: `verify/kalinlastir.log`. Blokajlar: `verify/BLOKAJ.md`."]
    DURUM.write_text("\n".join(satirlar) + "\n", encoding="utf-8")


def denetim(meslek: str) -> None:
    """Kor denetim: evetlerin rastgele %3'u + listelenmeyen 20 satir; hakem karari/alinti yok."""
    OUT.mkdir(exist_ok=True)
    conn = baglan()
    evet = [r[0] for r in conn.execute("SELECT domain FROM ciftler WHERE meslek=? AND karar='evet'", (meslek,))]
    diger = [r[0] for r in conn.execute("SELECT domain FROM ciftler WHERE meslek=? AND karar IN ('hayir','belirsiz') AND on_eleme=0 AND a_decision IS NOT NULL", (meslek,))]
    rnd = random.Random(20260920)
    secim = rnd.sample(evet, max(1, round(len(evet) * 0.03))) if evet else []
    secim += rnd.sample(diger, min(20, len(diger)))
    rnd.shuffle(secim)
    yol = OUT / f"denetim-{meslek}.csv"
    with yol.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(["domain", "legal_name", "meslek", "site_linki", "insan_karari", "not"])
        for d in secim:
            s = conn.execute("SELECT company, website FROM sirketler WHERE domain=?", (d,)).fetchone()
            legal = legal_name_bul(read_page_text(d, "impressum")) or (s["company"] if s else "")
            w.writerow([d, legal, meslek, s["website"] if s else f"https://{d}/", "", ""])
    conn.close()
    log(f"denetim {meslek}: {len(secim)} satir -> {yol} (evet %3 = {len(secim) - min(20, len(diger))}, listelenmeyen {min(20, len(diger))})")


if __name__ == "__main__":
    komut = sys.argv[1] if len(sys.argv) > 1 else "durum"
    if komut == "ice-aktar":
        ice_aktar()
    elif komut == "cek":
        asyncio.run(cek_dongusu())
    elif komut == "yargila":
        yargi_dongusu()
    elif komut == "durum":
        durum_yaz(); print(DURUM.read_text(encoding="utf-8"))
    elif komut == "denetim":
        denetim(sys.argv[2])
    else:
        print(__doc__)
