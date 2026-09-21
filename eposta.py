#!/usr/bin/env python3
"""Ikinci hat: havuzdaki "sitesi var, e-postasi bulunamadi" kayitlarini TARAYICI katmaniyla yeniden dene (21 Eyl 2026).

Sunucu bunlari duz HTTP ile taradi; JS ile cizilen siteler, gizlenmis adresler (Cloudflare e-posta korumasi,
JS/CSS ters yazim), zaman asimi ve TLS hatalari orada kacti. Burada Playwright + headless Chromium:
ana sayfa + Impressum + Kontakt; yalniz kendi alan adindaki ROL adresleri; kisisel ad iceren adres (vorname.nachname@) ALINMAZ.

Grup basina akis:
  1) python verify/havuz-gonder.py --epostasiz zayif/eposta/<grup>.jsonl --kategori ...   (indir)
  2) tarayiciyla tara (robots.txt, alan adi basina tek sekme/ardisik istek >= 1 sn, 20 sn zaman asimi, captcha yok)
  3) python verify/havuz-gonder.py zayif/eposta/<grup>-<zaman>.csv --etiket pc-eposta-<grup>
     (company/city JSONL'den aynen; sector JSONL'de yok -> bos, sunucu mevcut kaydi korur, e-posta yalniz bossa dolar)
  4) rapor: indirilen / taranan / e-posta bulunan / yuklenen (+eklenen / ~guncel / red)
Sira: it -> steuer -> logistik -> sozial -> industrie -> pflege -> kfz -> kalan (suzgecsiz).

  python eposta.py calis [--sekme 32]
  python eposta.py rapor
  python eposta.py dene --domain x.de
Durdurma: zayif/DUR-eposta dosyasi.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin, urlsplit

for _akim in (sys.stdout, sys.stderr):
    try:
        _akim.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from verify.fetch import izinli, robots_icin  # noqa: E402
from zayif import (EMAIL_RE, GONDER, IMPRESSUM_LINK, KONTAKT_LINK, PARK, UA, epostalari_cikar,  # noqa: E402
                   eposta_sec, host_of, site_kisalt)

ZAYIF = BASE / "zayif"
EPOSTA = ZAYIF / "eposta"
DB_PATH = EPOSTA / "eposta.sqlite3"
LOG = EPOSTA / "eposta.log"
DURUM_MD = EPOSTA / "DURUM-eposta.md"
SEKME = 32
TIMEOUT_MS = 20_000
DELAY_S = 1.0
MAX_SAYFA = 3
GRUPLAR: dict[str, list[str]] = {
    "it": ["information_technology_company", "software_development", "it_service_and_computer_repair", "web_designer"],
    "steuer": ["tax_services", "accountant", "financial_service", "financial_advising", "business_consulting", "business_management_services"],
    "logistik": ["freight_and_cargo_service", "transportation", "wholesale_store", "building_supply_store", "garbage_collection_service"],
    "sozial": ["preschool", "day_care_preschool", "youth_organizations", "retirement_home", "skilled_nursing", "assisted_living_facility"],
    "industrie": ["business_manufacturing_and_supply", "industrial_equipment", "industrial_company", "metal_fabricator", "metal_supplier",
                  "electronics", "commercial_industrial"],
    "pflege": ["hospital", "medical_center"],
    "kfz": ["automotive_repair", "automotive_services_and_repair", "car_dealer", "auto_parts_and_supply_store"],
    "kalan": [],
}
# CSS/JS ters yazim: "ed.amrif@ofni" -> tersine cevrilince e-posta
TERS_RE = re.compile(r"[a-z]{2,}\.[a-z0-9.-]+@[a-z0-9._%+-]+", re.I)
ENGEL = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|mp4|webm|mp3|avi|css)(\?|$)", re.I)

SEMA = """
CREATE TABLE IF NOT EXISTS kayitlar (
    domain TEXT PRIMARY KEY, grup TEXT, company TEXT DEFAULT '', city TEXT DEFAULT '', category TEXT DEFAULT '', website TEXT DEFAULT '',
    classification TEXT DEFAULT '', durum TEXT DEFAULT 'yeni', email TEXT DEFAULT '', source_url TEXT DEFAULT '', note TEXT DEFAULT '',
    sayfa INTEGER DEFAULT 0, scanned_at TEXT, yuklendi TEXT
);
CREATE INDEX IF NOT EXISTS ix_eposta_grup ON kayitlar(grup, durum);
CREATE TABLE IF NOT EXISTS grup_ozet (
    grup TEXT PRIMARY KEY, indirilen INTEGER, taranan INTEGER, bulunan INTEGER, yuklenen INTEGER, eklenen INTEGER, guncellenen INTEGER,
    reddedilen INTEGER, durum TEXT, hata TEXT, basladi TEXT, bitti TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(m: str) -> None:
    s = f"[{now()}] eposta: {m}"
    print(s, flush=True)
    EPOSTA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def db() -> sqlite3.Connection:
    EPOSTA.mkdir(parents=True, exist_ok=True)
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


def ozet_guncelle(grup: str, **alanlar) -> None:
    def f(c):
        c.execute("INSERT OR IGNORE INTO grup_ozet(grup) VALUES (?)", (grup,))
        for k, v in alanlar.items():
            c.execute(f"UPDATE grup_ozet SET {k}=? WHERE grup=?", (v, grup))
    yaz(f)


# ------------------------------------------------------------------ 1) indir
def indir(grup: str) -> int:
    dosya = EPOSTA / f"{grup}.jsonl"
    if not dosya.exists():
        args = [sys.executable, str(GONDER), "--epostasiz", str(dosya)]
        for k in GRUPLAR[grup]:
            args += ["--kategori", k]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE)
        if p.returncode != 0:
            raise RuntimeError(f"indirme basarisiz: {(p.stderr or p.stdout)[-300:]}")
        log(f"{grup}: {p.stdout.strip().splitlines()[-1]}")
    n = 0; toplu = []
    with dosya.open(encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            k = json.loads(satir); n += 1
            d = (k.get("domain") or "").strip().lower()
            if not d:
                continue
            toplu.append((d, grup, k.get("company") or "", k.get("city") or "", k.get("category") or "", k.get("website") or f"https://{d}/",
                          k.get("classification") or ""))
    yaz(lambda c: c.executemany("INSERT OR IGNORE INTO kayitlar(domain, grup, company, city, category, website, classification) VALUES (?,?,?,?,?,?,?)", toplu))
    log(f"{grup}: {n} kayit indirildi/okundu")
    return n


# ------------------------------------------------------------------ 2) tarayiciyla tara
def ters_cozumle(metin: str) -> set[str]:
    bul = set()
    for m in TERS_RE.finditer(metin):
        aday = m.group(0)[::-1].lower()
        if EMAIL_RE.fullmatch(aday):
            bul.add(aday)
    return bul


async def sayfa_ac(page, url: str) -> tuple[bool, str, str, str]:
    """(ok, son_url, html, innerText)"""
    try:
        yanit = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    except Exception:
        return False, url, "", ""
    if yanit is None or yanit.status >= 400:
        return False, page.url, "", ""
    tur = (yanit.headers or {}).get("content-type", "")
    if tur and "html" not in tur.lower():
        return False, page.url, "", ""
    try:
        await page.wait_for_timeout(1500)                    # JS ile cizilen/cozulen adresler icin pay
        html_ = await asyncio.wait_for(page.content(), 15)   # content()/evaluate() zaman asimi tanimaz; askida kalan sayfa partiyi kilitliyordu
        metin = await asyncio.wait_for(page.evaluate("() => document.body ? document.body.innerText : ''"), 15)
    except Exception:
        return False, page.url, "", ""
    return True, page.url, html_ or "", metin or ""


ALAN_ADI_SURE_S = 150      # alan adi basina toplam ust sinir (3 sayfa x 20 sn + beklemeler)


async def alan_adi_tara(browser, kayit: dict, sem: asyncio.Semaphore) -> dict:
    sonuc = {"domain": kayit["domain"], "durum": "hata", "email": "", "source_url": "", "note": "", "sayfa": 0}
    try:
        return await asyncio.wait_for(_alan_adi_tara(browser, kayit, sem, sonuc), ALAN_ADI_SURE_S)
    except asyncio.TimeoutError:
        sonuc["durum"] = "hata"; sonuc["note"] = "alan adi sure asimi"
        return sonuc


async def _alan_adi_tara(browser, kayit: dict, sem: asyncio.Semaphore, sonuc: dict) -> dict:
    domain = kayit["domain"]
    try:
        await asyncio.to_thread(socket.getaddrinfo, domain, 443)
    except Exception:
        try:
            await asyncio.to_thread(socket.getaddrinfo, "www." + domain, 443)
        except Exception:
            sonuc["durum"] = "dns-yok"; return sonuc
    rp = await asyncio.to_thread(robots_icin, domain)
    async with sem:
        ctx = await browser.new_context(user_agent=UA, locale="de-DE", viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
        ctx.set_default_timeout(TIMEOUT_MS)

        async def _engelle(route):
            try:
                await route.abort()
            except Exception:
                pass
        await ctx.route(ENGEL, _engelle)
        page = await ctx.new_page()
        son = 0.0

        async def bekle():
            nonlocal son
            gecen = time.monotonic() - son
            if gecen < DELAY_S:
                await asyncio.sleep(DELAY_S - gecen)
            son = time.monotonic()
        try:
            baslangic = kayit.get("website") or f"https://{domain}/"
            adaylar = [baslangic] + [u for u in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/") if u != baslangic]
            ok = False; ana_url = html_ = metin = ""
            for u in adaylar:
                if not izinli(rp, u):
                    sonuc["durum"] = "robots"; sonuc["note"] = "robots.txt"; return sonuc
                await bekle(); sonuc["sayfa"] += 1
                ok, ana_url, html_, metin = await sayfa_ac(page, u)
                if ok and len(metin) > 100:
                    break
                ok = False
            if not ok:
                sonuc["durum"] = "ulasilamadi"; return sonuc
            if PARK.search(metin[:3000]) or PARK.search(ana_url):
                sonuc["durum"] = "park"; return sonuc
            hostlar = [domain, host_of(ana_url)]
            kaynak: dict[str, str] = {}

            def topla(h, m, u):
                # cizilmis HTML + gorunur metin (JS'in cozdugu adres innerText'te duz gorunur) + ters yazim
                for e in epostalari_cikar(h) | {x.lower() for x in EMAIL_RE.findall(m)} | ters_cozumle(m) | ters_cozumle(h):
                    kaynak.setdefault(e, u)
            topla(html_, metin, ana_url)
            if eposta_sec(set(kaynak), hostlar):
                sonuc.update(durum="tarandi", email=eposta_sec(set(kaynak), hostlar)); sonuc["source_url"] = kaynak[sonuc["email"]]; return sonuc
            # Impressum / Kontakt baglantilari (JS menuler dahil, cizilmis DOM'dan)
            try:
                ham = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).slice(0, 600)"
                                          ".map(a => [a.getAttribute('href'), (a.innerText || '').trim().slice(0, 60)])")
            except Exception:
                ham = []
            linkler: dict[str, str] = {}
            for href, txt in ham:
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    if href and href.startswith("mailto:"):
                        for e in EMAIL_RE.findall(href):
                            kaynak.setdefault(e.lower(), ana_url)
                    continue
                try:
                    mutlak = urljoin(ana_url, href)
                except ValueError:
                    continue
                if host_of(mutlak) not in hostlar:
                    continue
                imza = f"{urlsplit(mutlak).path} {txt}"
                if "impressum" not in linkler and IMPRESSUM_LINK.search(imza):
                    linkler["impressum"] = mutlak
                elif "kontakt" not in linkler and KONTAKT_LINK.search(imza):
                    linkler["kontakt"] = mutlak
            taban = f"{urlsplit(ana_url).scheme}://{urlsplit(ana_url).netloc}"
            for tur in ("impressum", "kontakt"):
                if sonuc["sayfa"] >= MAX_SAYFA:
                    break
                u = linkler.get(tur) or f"{taban}/{tur}"
                if not izinli(rp, u):
                    continue
                await bekle(); sonuc["sayfa"] += 1
                ok2, u2, h2, m2 = await sayfa_ac(page, u)
                if ok2:
                    topla(h2, m2, u2)
                    if eposta_sec(set(kaynak), hostlar):
                        break
            e = eposta_sec(set(kaynak), hostlar)
            sonuc.update(durum="tarandi", email=e, source_url=kaynak.get(e, "") if e else "")
            return sonuc
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            sonuc["note"] = f"{type(ex).__name__}: {ex}"[:150]; return sonuc
        finally:
            try:
                await asyncio.wait_for(asyncio.shield(ctx.close()), 15)
            except Exception:
                pass


async def tara(grup: str, sekme: int) -> dict:
    from playwright.async_api import async_playwright
    conn = db()
    satirlar = [dict(r) for r in conn.execute("SELECT domain, website FROM kayitlar WHERE grup=? AND durum='yeni'", (grup,))]
    conn.close()
    log(f"{grup}: {len(satirlar)} site taranacak ({sekme} sekme)")
    n = ok = 0; t0 = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"])
        try:
            sem = asyncio.Semaphore(sekme)
            for i in range(0, len(satirlar), 500):
                parti = satirlar[i:i + 500]
                sonuclar = await asyncio.gather(*[alan_adi_tara(browser, k, sem) for k in parti], return_exceptions=True)
                temiz = []
                for k, r in zip(parti, sonuclar):
                    if isinstance(r, Exception):
                        r = {"domain": k["domain"], "durum": "hata", "email": "", "source_url": "", "note": f"{type(r).__name__}: {r}"[:150], "sayfa": 0}
                    temiz.append(r); n += 1; ok += bool(r["email"])
                yaz(lambda c, t=temiz: c.executemany(
                    "UPDATE kayitlar SET durum=?, email=?, source_url=?, note=?, sayfa=?, scanned_at=? WHERE domain=?",
                    [(r["durum"], r["email"], r["source_url"], r["note"], r["sayfa"], now(), r["domain"]) for r in t]))
                hiz = n / max(1, time.time() - t0)
                log(f"{grup}: {n}/{len(satirlar)} site, {ok} e-posta bulundu, {hiz:.2f} site/sn, kalan ~{(len(satirlar) - n) / max(hiz, 0.05) / 3600:.1f} sa")
                if n % 5000 < 500:
                    try:
                        yukle(grup)
                    except Exception as e:
                        log(f"{grup}: ara yukleme hatasi: {e}")
                if (ZAYIF / "DUR-eposta").exists():
                    log("DUR-eposta: tarama kesildi"); break
                # tarayiciyi ara sira tazele (bellek)
                if n % 5000 < 500 and n:
                    await browser.close()
                    browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"])
        finally:
            await browser.close()
    return {"taranan": n, "bulunan": ok}


# ------------------------------------------------------------------ 3) yukle
def yukle(grup: str) -> dict:
    conn = db()
    satirlar = conn.execute("SELECT * FROM kayitlar WHERE grup=? AND durum='tarandi' AND email<>'' AND yuklendi IS NULL", (grup,)).fetchall()
    conn.close()
    if not satirlar:
        return {"yuklenen": 0, "eklenen": 0, "guncellenen": 0, "reddedilen": 0}
    dosya = EPOSTA / f"{grup}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with dosya.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "city", "sector", "website", "email", "source_url"])
        w.writeheader()
        for r in satirlar:
            w.writerow({"company": r["company"], "city": r["city"], "sector": "", "website": site_kisalt(r["website"]),
                        "email": r["email"], "source_url": site_kisalt(r["source_url"])[:500]})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(GONDER), str(dosya), "--etiket", f"pc-eposta-{grup}"], capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE)
    mt = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not mt:
        raise RuntimeError(f"yukleme basarisiz (kod {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    t = json.loads(mt.group(1))
    yaz(lambda c: c.executemany("UPDATE kayitlar SET yuklendi=? WHERE domain=?", [(now(), r["domain"]) for r in satirlar]))
    log(f"{grup}: {len(satirlar)} satir -> TOPLAM {json.dumps(t, ensure_ascii=False)} ({dosya.name})")
    conn = db()
    o = conn.execute("SELECT * FROM grup_ozet WHERE grup=?", (grup,)).fetchone(); conn.close()
    top = {k: (o[k] or 0 if o else 0) + t.get(k, 0) for k in ("eklenen", "guncellenen", "reddedilen")}
    top["yuklenen"] = ((o["yuklenen"] or 0) if o else 0) + len(satirlar)
    ozet_guncelle(grup, **top)
    return top


# ------------------------------------------------------------------ 4) rapor
def rapor(yazdir: bool = True) -> str:
    conn = db()
    satirlar = ["# DURUM — e-postasız kayıtların tarayıcıyla yeniden taranması", f"Güncelleme: {now()}", "",
                "| # | grup | etiket | indirilen | taranan | e-posta bulunan | yüklenen | +eklenen | ~güncel | red | durum |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, g in enumerate(GRUPLAR, 1):
        o = conn.execute("SELECT * FROM grup_ozet WHERE grup=?", (g,)).fetchone()
        c = conn.execute("SELECT COUNT(1), SUM(durum<>'yeni'), SUM(email<>'') FROM kayitlar WHERE grup=?", (g,)).fetchone()
        if not o and not c[0]:
            satirlar.append(f"| {i} | {g} | pc-eposta-{g} | | | | | | | | bekliyor |"); continue
        d = (o["durum"] if o else None) or "sürüyor"
        if o and o["hata"]:
            d += f" — hata: {o['hata'][:80]}"
        satirlar.append(f"| {i} | {g} | pc-eposta-{g} | {c[0]} | {c[1] or 0} | {c[2] or 0} | {(o['yuklenen'] if o else 0) or 0} | "
                        f"{(o['eklenen'] if o else 0) or 0} | {(o['guncellenen'] if o else 0) or 0} | {(o['reddedilen'] if o else 0) or 0} | {d} |")
    s = conn.execute("SELECT durum, COUNT(1) FROM kayitlar GROUP BY durum").fetchall()
    conn.close()
    satirlar += ["", "Site durumları: " + ", ".join(f"{a} {b}" for a, b in s), "",
                 "Playwright + headless Chromium; robots.txt, alan adı başına tek sekme ve ≥1 sn, 20 sn zaman aşımı, ≤3 sayfa; yalnız kendi alan adındaki rol adresleri, "
                 "kişisel ad içeren adres alınmaz; captcha çözme yok; ücretli API yok."]
    metin = "\n".join(satirlar) + "\n"
    DURUM_MD.write_text(metin, encoding="utf-8")
    if yazdir:
        print(metin)
    return metin


# ------------------------------------------------------------------ calis
def grup_isle(grup: str, sekme: int) -> None:
    ozet_guncelle(grup, durum="sürüyor", basladi=now(), hata="")
    n = indir(grup); ozet_guncelle(grup, indirilen=n)
    t = asyncio.run(tara(grup, sekme)); ozet_guncelle(grup, taranan=t["taranan"], bulunan=t["bulunan"])
    y = yukle(grup)
    conn = db(); o = conn.execute("SELECT * FROM grup_ozet WHERE grup=?", (grup,)).fetchone(); conn.close()
    ozet_guncelle(grup, durum="bitti", bitti=now())
    log(f"RAPOR {grup} (pc-eposta-{grup}): indirilen {n} / taranan {t['taranan']} / e-posta bulunan {t['bulunan']} / "
        f"yüklenen {o['yuklenen'] or 0} (+{o['eklenen'] or 0} / ~{o['guncellenen'] or 0} / red {o['reddedilen'] or 0})")


def calis(sekme: int) -> None:
    log(f"calis basladi: {len(GRUPLAR)} grup, {sekme} sekme")
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; cikiliyor"); return
    hatali = []
    for g in GRUPLAR:
        if (ZAYIF / "DUR-eposta").exists():
            log("DUR-eposta: durduruldu"); break
        conn = db(); r = conn.execute("SELECT durum FROM grup_ozet WHERE grup=?", (g,)).fetchone(); conn.close()
        if r and r["durum"] == "bitti":
            log(f"{g}: zaten bitti, atlandi"); continue
        try:
            grup_isle(g, sekme)
        except Exception as e:
            hatali.append((g, f"{type(e).__name__}: {e}"[:200]))
            ozet_guncelle(g, durum="hata", hata=f"{type(e).__name__}: {e}"[:200])
            log(f"!! {g} atlandi: {type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
        rapor(yazdir=False)
    rapor(yazdir=False)
    log("calis bitti" + (f"; hatali gruplar: {hatali}" if hatali else "; hata yok"))


async def dene(domain: str) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            print(json.dumps(await alan_adi_tara(browser, {"domain": domain, "website": f"https://{domain}/"}, asyncio.Semaphore(1)), ensure_ascii=False, indent=1))
        finally:
            await browser.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["calis", "rapor", "dene", "yukle"])
    p.add_argument("--sekme", type=int, default=SEKME); p.add_argument("--domain"); p.add_argument("--grup")
    a = p.parse_args()
    if a.komut == "calis":
        calis(a.sekme)
    elif a.komut == "dene":
        asyncio.run(dene(a.domain))
    elif a.komut == "yukle":
        print(yukle(a.grup))
    else:
        rapor()


if __name__ == "__main__":
    main()
