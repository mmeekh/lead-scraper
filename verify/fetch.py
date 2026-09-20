"""Zengin cekim: Playwright + headless Chromium ile sirket basina 5-8 sayfa.

Kurallar (docs/yerel-dogrulama-modu-2026-09-19.md §1, §2):
  - robots.txt'e uyulur (urllib.robotparser), captcha cozulmez, login yok
  - alan adi basina <= 2 es zamanli istek ve >= 1 sn aralik (burada: alan adi basina tek sekme, ardisik)
  - toplam <= 8 sekme (makinenin %80 payi), istek zaman asimi 20 sn
  - User-Agent: lead-scraper-verify/0.1 (+SCRAPER_CONTACT)
Sayfa turleri: home, ueber, leistungen, karriere, impressum, kontakt.
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from .config import (DOMAIN_DELAY_S, MAX_PAGES, MAX_TABS, PAGE_CHARS_CAP,
                     REQUEST_TIMEOUT_S, UA)
from .db import bump_try, db, pages_of, save_page, set_status, yaz

# --- sayfa turu tanima (yol + bagalanti metni)
TUR_KURAL = [
    ("impressum", re.compile(r"impressum|imprint|anbieterkennzeichnung|legal-?notice", re.I)),
    ("karriere", re.compile(r"karriere|jobs?|stellen|stellenangebot|offene-stellen|bewerb|career|"
                            r"arbeiten-bei|wir-suchen|mitarbeiter-gesucht|ausbildung", re.I)),
    ("ueber", re.compile(r"ueber-uns|uber-uns|über-uns|ueberuns|about|unternehmen|firma|wir-ueber-uns|"
                         r"philosophie|historie|team|profil", re.I)),
    ("leistungen", re.compile(r"leistungen|dienstleistung|services?|produkte|produkt|angebot|"
                              r"loesungen|lösungen|portfolio|branchen|kompetenzen|maschinen|fuhrpark|"
                              r"flotte|technik|anlagen", re.I)),
    ("kontakt", re.compile(r"kontakt|contact|anfahrt|standort|ansprechpartner", re.I)),
]
TUR_ONCELIK = ("impressum", "karriere", "karriere2", "karriere3", "leistungen", "ueber", "kontakt")
ATLA_UZANTI = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3|avi)(\?|$)", re.I)


def kok(host: str) -> str:
    parcalar = host.split(".")
    return ".".join(parcalar[-2:]) if len(parcalar) > 2 else host


def robots_icin(domain: str) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"https://{domain}/robots.txt")
    try:
        req = Request(f"https://{domain}/robots.txt", headers={"User-Agent": UA})
        with urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            govde = r.read(200_000).decode("utf-8", "replace")
        rp.parse(govde.splitlines())
    except Exception:
        rp.parse([])        # robots.txt yoksa/okunamazsa: kisitsiz say (RFC davranisi)
    return rp


def izinli(rp: urllib.robotparser.RobotFileParser, url: str) -> bool:
    try:
        return rp.can_fetch(UA, url) or rp.can_fetch("*", url)
    except Exception:
        return True


def metni_kisalt(metin: str) -> str:
    metin = re.sub(r"[ \t ]+", " ", metin)
    metin = re.sub(r"\n{3,}", "\n\n", metin)
    return metin.strip()[:PAGE_CHARS_CAP]


async def _ac(page, url: str) -> tuple[bool, str, str]:
    """Sayfayi acar; (basarili, son_url, metin) doner."""
    try:
        yanit = await page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_S * 1000)
    except Exception:
        return False, url, ""
    if yanit is None or not yanit.ok:
        return False, page.url, ""
    tur = (yanit.headers or {}).get("content-type", "")
    if tur and "html" not in tur.lower():
        return False, page.url, ""
    try:
        await page.wait_for_timeout(700)        # JS ile cizilen icerige kisa pay
        metin = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        metin = ""
    return True, page.url, metni_kisalt(metin or "")


async def _baglantilar(page, taban: str) -> dict[str, str]:
    """Ana sayfadaki ic baglantilardan sayfa turu -> URL haritasi."""
    try:
        ham = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).slice(0, 400)"
            ".map(a => [a.getAttribute('href'), (a.innerText || '').trim().slice(0, 80)])")
    except Exception:
        return {}
    taban_host = kok((urlsplit(taban).hostname or "").replace("www.", ""))
    bulunan: dict[str, str] = {}
    kariyer: list[str] = []          # ilan cikarimi icin en cok 3 kariyer sayfasi
    for href, metin in ham:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            mutlak = urljoin(taban, href)
        except ValueError:
            continue
        p = urlsplit(mutlak)
        if p.scheme not in ("http", "https") or not p.hostname:
            continue
        if kok(p.hostname.replace("www.", "")) != taban_host:
            continue
        if ATLA_UZANTI.search(p.path):
            continue
        aday = f"{p.scheme}://{p.netloc}{p.path}"
        yol_metin = f"{p.path} {metin}"
        for tur, kural in TUR_KURAL:
            if kural.search(yol_metin):
                if tur == "karriere":
                    if aday not in kariyer and len(kariyer) < 3:
                        kariyer.append(aday)
                elif tur not in bulunan:
                    bulunan[tur] = aday
                break
    for i, url in enumerate(kariyer):
        bulunan["karriere" if i == 0 else f"karriere{i + 1}"] = url
    return bulunan


async def bir_domain(browser, satir, sem: asyncio.Semaphore) -> dict:
    domain = satir["domain"]
    sonuc = {"domain": domain, "sayfa": 0, "hata": "", "rendered": True}
    rp = await asyncio.to_thread(robots_icin, domain)
    async with sem:
        ctx = await browser.new_context(
            user_agent=UA, locale="de-DE", viewport={"width": 1280, "height": 900},
            java_script_enabled=True, ignore_https_errors=True)
        ctx.set_default_timeout(REQUEST_TIMEOUT_S * 1000)
        # gorsel/medya/font engelle: hiz ve bant genisligi (handler async olmali,
        # aksi halde eslesen istekler hic sonuclanmaz ve sayfa yuklemesi asili kalir)
        async def _engelle(route):
            try:
                await route.abort()
            except Exception:
                pass

        await ctx.route(re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|mp4|webm|mp3|avi)(\?|$)", re.I),
                        _engelle)
        page = await ctx.new_page()
        try:
            son_istek = 0.0

            async def bekle():
                nonlocal son_istek
                gecen = time.monotonic() - son_istek
                if gecen < DOMAIN_DELAY_S:
                    await asyncio.sleep(DOMAIN_DELAY_S - gecen)
                son_istek = time.monotonic()

            # 1) ana sayfa: https -> www -> http
            ana_url = ana_metin = ""
            for aday in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
                if not izinli(rp, aday):
                    continue
                await bekle()
                ok, son_url, metin = await _ac(page, aday)
                if ok and len(metin) > 120:
                    ana_url, ana_metin = son_url, metin
                    break
            if not ana_metin:
                sonuc["hata"] = "ana sayfa alinamadi"
                yaz(lambda c: (bump_try(c, domain), set_status(c, domain, "cekilemedi", sonuc["hata"])))
                return sonuc
            yaz(lambda c: save_page(c, domain, "home", ana_url, ana_metin, True))
            sonuc["sayfa"] = 1

            # 2) alt sayfalar
            linkler = await _baglantilar(page, ana_url)
            taban = f"{urlsplit(ana_url).scheme}://{urlsplit(ana_url).netloc}"
            for tur in TUR_ONCELIK:
                if sonuc["sayfa"] >= MAX_PAGES:
                    break
                url = linkler.get(tur)
                if not url and tur in ("impressum", "kontakt", "karriere"):
                    url = f"{taban}/{tur}"          # yaygin yol: deneme ucuz
                if not url or not izinli(rp, url):
                    continue
                await bekle()
                ok, son_url, metin = await _ac(page, url)
                if ok and len(metin) > 80:
                    yaz(lambda c, _t=tur, _u=son_url, _m=metin: save_page(c, domain, _t, _u, _m, True))
                    sonuc["sayfa"] += 1

            durum = "cekildi" if sonuc["sayfa"] >= 2 else "eksik"
            yaz(lambda c: set_status(c, domain, durum, f"{sonuc['sayfa']} sayfa"))
            return sonuc
        except Exception as e:                      # tek site tum kosuyu dusurmesin
            sonuc["hata"] = f"{type(e).__name__}: {e}"[:200]
            try:
                yaz(lambda c: (bump_try(c, domain), set_status(c, domain, "hata", sonuc["hata"])))
            except Exception:
                pass
            return sonuc
        finally:
            await ctx.close()


async def cek(limit: int = 100, durumlar: tuple[str, ...] = ("yeni", "cekilemedi", "hata")) -> dict:
    from playwright.async_api import async_playwright

    conn = db()
    yer = ",".join("?" * len(durumlar))
    satirlar = conn.execute(
        f"SELECT * FROM domains WHERE status IN ({yer}) AND tries < 3 ORDER BY domain LIMIT ?",
        (*durumlar, limit)).fetchall()
    conn.close()
    if not satirlar:
        return {"islenen": 0, "not": "kuyruk bos"}

    ozet = {"islenen": 0, "sayfa": 0, "basarisiz": 0}
    t0 = time.time()
    sem = asyncio.Semaphore(MAX_TABS)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        try:
            isler = [bir_domain(browser, s, sem) for s in satirlar]
            for i in range(0, len(isler), MAX_TABS * 2):
                for r in await asyncio.gather(*isler[i:i + MAX_TABS * 2], return_exceptions=True):
                    if isinstance(r, Exception):
                        ozet["basarisiz"] += 1
                        continue
                    ozet["islenen"] += 1
                    ozet["sayfa"] += r["sayfa"]
                    if r["hata"]:
                        ozet["basarisiz"] += 1
                print(f"  cekim {ozet['islenen']}/{len(satirlar)} alan adi, {ozet['sayfa']} sayfa, "
                      f"{ozet['basarisiz']} sorunlu, {time.time() - t0:.0f} sn", flush=True)
        finally:
            await browser.close()
    ozet["sure_sn"] = round(time.time() - t0, 1)
    return ozet
