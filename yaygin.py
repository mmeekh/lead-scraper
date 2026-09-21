#!/usr/bin/env python3
"""Son tarama: 5 yaygin meslek, iki kaynak (21 Eyl 2026, 20:00).

  A) sunucudaki e-postasiz Overture kayitlari -> tarayici katmani (eposta.py, grup y-<meslek>, ayni meslek etiketi)
  B) Common Crawl alan adi dizini, genis sozcukler (.de/.com/.net/.eu/.org; .de disinda Impressum'da Almanya adresi sart)
     -> ince.py altyapisi (bu modul ince'nin meslek tablosunu/veritabanini yaygin setiyle degistirir)
Her meslek icin A ve B ayni anda calisir (tam guc): A ayri surec (Playwright, 64 sekme), B bu surecte (11 surec x 40).
Sira: pflege -> lager -> buchhaltung -> dev -> kinder. dev'de ek sart: baslik/Impressum'da Software|IT|Digital|Technolog;
saglamayanlar yuklenmez, zayif/out/yaygin-dev-supheli.csv'ye yazilir.

  python yaygin.py calis [--isci 440] [--sekme 64]
  python yaygin.py rapor
Durdurma: zayif/DUR-yaygin (B) + zayif/DUR-eposta (A).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import subprocess
import sys
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
import ince  # noqa: E402
import zayif  # noqa: E402
from ince import _kural  # noqa: E402
from zayif import site_tara  # noqa: E402

ZAYIF = BASE / "zayif"
SAGLIK, OTOMOTIV, HUKUK, YAZILIM, EGITIM = ("Sağlık & Bakım", "Otomotiv & Lojistik", "Hukuk, Muhasebe & Vergi", "Yazılım & Teknoloji", "Eğitim")
DEV_SART = re.compile(r"software|softwareentwicklung|\bit\b|it-|digital|technolog", re.I)

MESLEKLER: dict[str, dict] = {
    "pflege": dict(etiket="pc-pflege", grup="y-pflege", sektor=SAGLIK,
                   sozcuk=["pflegedienst", "ambulante-pflege", "pflegeheim", "altenheim", "seniorenheim", "seniorenresidenz", "seniorenzentrum",
                           "tagespflege", "intensivpflege", "kurzzeitpflege", "betreutes-wohnen", "hospiz", "diakonie", "caritas", "awo-", ("drk", "pflege"), "sozialstation"],
                   metin=r"pflegedienst|ambulante pflege|pflegeheim|altenheim|seniorenheim|seniorenresidenz|seniorenzentrum|tagespflege|intensivpflege|kurzzeitpflege|"
                         r"betreutes wohnen|hospiz|pflegekräfte|pflegefachkraft|altenpflege|sozialstation|pflege"),
    "lager": dict(etiket="pc-lager", grup="y-lager", sektor=OTOMOTIV,
                  sozcuk=["lager", "logistikzentrum", "grosshandel", "fulfillment", "kontraktlogistik", "lagerlogistik", "versandzentrum", "baustoffhandel",
                          "getraenkegrosshandel", "lebensmittelgrosshandel", "distribution"],
                  metin=r"lager|logistik|großhandel|grosshandel|fulfillment|versand|kommissionier|distribution|baustoffhandel|lieferung|warenwirtschaft"),
    "buchhaltung": dict(etiket="pc-steuer", grup="y-steuer", sektor=HUKUK,
                        sozcuk=["steuerberater", "steuerberatung", "steuerkanzlei", "stb-", "wirtschaftspruefer", "wirtschaftspruefung", "buchhaltung",
                                "lohnbuchhaltung", "buchhaltungsservice", "buchfuehrung", "bilanzbuchhalter", "treuhand"],
                        metin=r"steuerberat|steuerkanzlei|wirtschaftsprüf|buchhaltung|lohnbuchhaltung|buchführung|bilanzbuchhalter|treuhand|jahresabschluss|steuererklärung|finanzbuchhaltung"),
    "dev": dict(etiket="pc-software", grup="y-dev", sektor=YAZILIM,
                sozcuk=["software", "softwarehaus", "softwareentwicklung", "app-", "apps", "cloud", "digital", "solutions", "systems", "tech",
                        "technologies", "it-", "entwicklung", "plattform", "saas", "labs"],
                metin=r"software|softwareentwicklung|\bit\b|it-|digital|technolog|entwicklung|programmier|webentwicklung|app-entwicklung|cloud|saas|plattform"),
    "kinder": dict(etiket="pc-kita", grup="y-kita", sektor=EGITIM,
                   sozcuk=["kita", "kita-", "kindergarten", "kindertagesstaette", "kinderkrippe", "kinderhaus", "kinderladen", "hort", "familienzentrum",
                           "kinderbetreuung", "waldkindergarten", "montessori", "waldorf", ("traeger", "kita"), "elterninitiative"],
                   metin=r"kita|kindergarten|kindertagesstätte|kinderkrippe|kinderhaus|kinderladen|\bhort\b|familienzentrum|kinderbetreuung|waldkindergarten|"
                         r"montessori|waldorf|erzieher|elterninitiative|krippe"),
}
# Ingilizce/uluslararasi sozcukler yalniz .de'de sayilir (ince.py'deki kural); "lager" parca sinirli (schlager, verlagerung ...)
ince.ULUSLARARASI |= {"software", "apps", "cloud", "digital", "solutions", "systems", "tech", "technologies", "saas", "labs", "distribution",
                      "fulfillment", "montessori", "waldorf", "caritas", "hospiz", "it-", "app-"}
for _id, _m in MESLEKLER.items():
    sozcuk = [w for w in _m["sozcuk"] if w != "lager"]
    _m["alan_re"] = re.compile(_kural(sozcuk).pattern + ("|(^|-)lager(-|$)" if "lager" in _m["sozcuk"] else ""), re.I)
    _alm = ince._alman(sozcuk)
    _m["alan_re_intl"] = re.compile((_kural(_alm).pattern if _alm else r"(?!)") + ("|(^|-)lager(-|$)" if "lager" in _m["sozcuk"] else ""), re.I)
    _m["metin_re"] = re.compile(_m["metin"], re.I)
    zayif.MESLEKLER.setdefault(_id, {})["metin_re"] = _m["metin_re"]

# ince altyapisini yaygin setine cevir (ayni surecte ve alt sureclerde: bu modul ice aktarildiginda uygulanir)
ince.MESLEKLER = MESLEKLER
ince.ONCELIK = {m: i for i, m in enumerate(MESLEKLER)}
ince.DB_PATH = ZAYIF / "yaygin.sqlite3"
ince.LOG = ZAYIF / "yaygin.log"
ince.DURUM_MD = ZAYIF / "DURUM-yaygin.md"


def parca_tara(satirlar: list[tuple[str, str]], iplik: int) -> list[dict]:
    """Alt surecte: bu modulun ice aktarilmasi ince'yi yaygin setine cevirir; dev icin ek baslik/Impressum sarti burada."""
    sonuclar = []
    with ThreadPoolExecutor(max_workers=max(1, min(iplik, len(satirlar)))) as pool:
        isler = {pool.submit(site_tara, d, [m]): (d, m) for d, m in satirlar}
        for f in as_completed(isler):
            d, m = isler[f]
            try:
                r = f.result()
            except Exception as e:
                r = {"domain": d, "durum": "hata", "note": f"{type(e).__name__}: {e}"[:200], "company": "", "city": "", "website": "",
                     "email": "", "source_url": "", "uyum": 0, "sayfa": 0}
            if m == "dev" and r.get("uyum") and not DEV_SART.search((r.get("baslik") or "") + " " + (r.get("impressum") or "") + " " + (r.get("company") or "")):
                r["uyum"] = 0; r["note"] = (r.get("note") or "") + "; dev-sart-yok"      # ad esleşti ama baslik/Impressum yazilim demiyor
            sonuclar.append(r)
    return sonuclar


ince.parca_tara = parca_tara
log = ince.log


def a_hatti_baslat(meslek: str, sekme: int) -> subprocess.Popen:
    grup = MESLEKLER[meslek]["grup"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen([sys.executable, str(BASE / "eposta.py"), "calis", "--sekme", str(sekme), "--gruplar", grup],
                            cwd=BASE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def a_ozet(meslek: str) -> dict:
    grup = MESLEKLER[meslek]["grup"]
    c = sqlite3.connect(ZAYIF / "eposta" / "eposta.sqlite3", timeout=60)
    r = c.execute("SELECT COUNT(1), SUM(durum<>'yeni'), SUM(email<>'') FROM kayitlar WHERE grup=?", (grup,)).fetchone()
    o = c.execute("SELECT yuklenen, eklenen, guncellenen, reddedilen, durum FROM grup_ozet WHERE grup=?", (grup,)).fetchone()
    c.close()
    return {"indirilen": r[0] or 0, "taranan": r[1] or 0, "bulunan": r[2] or 0, "yuklenen": (o and o[0]) or 0, "eklenen": (o and o[1]) or 0,
            "guncellenen": (o and o[2]) or 0, "reddedilen": (o and o[3]) or 0, "durum": (o and o[4]) or "?"}


def dev_supheli() -> Path | None:
    c = ince.db()
    rows = c.execute("SELECT domain, company, city, email, note FROM adaylar WHERE meslek='dev' AND durum='tarandi' AND email<>'' AND uyum=0").fetchall(); c.close()
    if not rows:
        return None
    yol = ZAYIF / "out" / "yaygin-dev-supheli.csv"
    with yol.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["domain", "company", "city", "email", "not"]); w.writerows(rows)
    return yol


def calis(isci: int, sekme: int) -> None:
    log(f"yaygin calis basladi: {list(MESLEKLER)}, B {isci} es zamanli, A {sekme} sekme")
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; cikiliyor"); return
    ince.aday_topla()
    hatali = []
    for m in MESLEKLER:
        if (ZAYIF / "DUR-yaygin").exists():
            log("DUR-yaygin: durduruldu"); break
        c = ince.db(); r = c.execute("SELECT durum FROM meslek_ozet WHERE meslek=?", (m,)).fetchone(); c.close()
        if r and r["durum"] == "bitti":
            log(f"{m}: zaten bitti, atlandi"); continue
        a = a_hatti_baslat(m, sekme)
        log(f"{m}: A hatti (eposta.py {MESLEKLER[m]['grup']}) basladi, pid {a.pid}")
        try:
            ince.meslek_isle(m, isci)
        except Exception as e:
            hatali.append((m, f"{type(e).__name__}: {e}"[:200])); ince.ozet_guncelle(m, durum="hata", hata=f"{type(e).__name__}: {e}"[:200])
            log(f"!! {m} B atlandi: {type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
        while a.poll() is None:
            log(f"{m}: B bitti, A hatti bekleniyor"); time.sleep(120)
        ao = a_ozet(m)
        log(f"RAPOR-A {m} ({MESLEKLER[m]['etiket']}): indirilen {ao['indirilen']} / taranan {ao['taranan']} / e-posta bulunan {ao['bulunan']} / "
            f"yüklenen {ao['yuklenen']} (+{ao['eklenen']} / ~{ao['guncellenen']} / red {ao['reddedilen']}) [{ao['durum']}]")
        if m == "dev":
            yol = dev_supheli()
            log(f"dev: şüpheli (ad eşleşti, başlık/Impressum yazılım demiyor, yüklenmedi): {yol or 'yok'}")
        ince.rapor(yazdir=False)
    ince.rapor(yazdir=False)
    log("yaygin calis bitti" + (f"; hatali: {hatali}" if hatali else "; hata yok"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["calis", "rapor", "aday"])
    p.add_argument("--isci", type=int, default=440); p.add_argument("--sekme", type=int, default=64)
    a = p.parse_args()
    if a.komut == "calis":
        calis(a.isci, a.sekme)
    elif a.komut == "aday":
        ince.aday_topla(); ince.rapor()
    else:
        ince.rapor()


if __name__ == "__main__":
    main()
