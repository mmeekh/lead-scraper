"""Ilan cikarimi - modelsiz, deterministik, hizli. Kosunun birincil urunu (20 Eyl 2026).

Her cekilmis alan adi icin: kariyer sayfalarindan (karriere, karriere2, karriere3; yoksa ana sayfa)
ilan basliklari cikarilir:
  - "(m/w/d)", "(w/m/d)", "(d/m/w)", "(m/w)", "(m/w/x)" iceren satirlar
  - "Wir suchen ..." cumleleri
  menu/altbilgi/cerez metni haric, 6-120 karakter.
Her baslik jobfind meslek kayitlarina eslenir: crawler/src/pipelines/meslek-sinyalleri.js
`rol` regex'i (katlanmis metin: kucuk harf, umlaut'suz, ss). Tutan kayitlar; yoksa ["diger"].
Cikti: verify/out/ilanlar-<n>.jsonl, satir {domain, legal_name, ad_title, ad_url, meslek_kayitlari, fetched_at};
her 5.000 alan adinda yeni dosya.

  python verify/ilan.py tara            # surekli isci: cekilmis, taranmamis alan adlari (oncelik sirasiyla)
  python verify/ilan.py tara --tek      # tek parti
  python verify/ilan.py dene <domain>   # bir alan adinda dokum
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import regex

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify.db import db, now, pages_of, read_page_text, yaz   # noqa: E402
from verify.impressum import legal_name_bul                     # noqa: E402

BASE = Path(__file__).resolve().parent
OUT = BASE / "out"
ROL_JSON = BASE / "rol-regex.json"
LOG = BASE / "kalinlastir.log"
PARCA = 5000                 # alan adi / dosya
PARTI = 500                  # isci partisi
KARIYER_TURLERI = ("karriere", "karriere2", "karriere3")

ILAN_ISARET = re.compile(r"\(\s*[mwdxf]\s*[/|]\s*[mwdxf](?:\s*[/|]\s*[mwdxf])?\s*\)|\b[mwdf]/[mwdf]/[mwdf]\b|\b[mwf]/[mwf]\b(?!/)", re.I)
WIR_SUCHEN = re.compile(r"^\s*wir suchen\b", re.I)
GURULTU = re.compile(
    r"cookie|datenschutz|impressum|akzeptieren|einstellungen|zustimm|newsletter|©|copyright|alle rechte|"
    r"^(jobs?|karriere|stellenangebote|offene stellen|bewerbung|jetzt bewerben|initiativbewerbung|mehr erfahren|"
    r"weiterlesen|zur stellenanzeige|alle stellen|kontakt|startseite|menü|menu|suche|zurück|login|anmelden)$|"
    r"^wir suchen (dich|sie|euch)[!.]?$|^wir suchen[!.]?$|gesucht und gefunden|"
    r"^wir suchen (verstärkung|unterstützung|neue kolleg|mitarbeiter|dich als|sie als)[^a-z]*$|"
    r"^wir suchen (verstärkung|unterstützung)( für unser team)?[!.]?$", re.I)
SEMA = """
CREATE TABLE IF NOT EXISTS ilan_tarama (
    domain TEXT PRIMARY KEY, kariyer_sayfa INTEGER DEFAULT 0, ilan INTEGER DEFAULT 0,
    eslesen INTEGER DEFAULT 0, parca INTEGER, scanned_at TEXT
);
"""

_ROL: list[tuple[str, "regex.Pattern"]] | None = None


def log(m: str) -> None:
    s = f"[{now()}] ilan: {m}"
    print(s, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def fold(s: str) -> str:
    """jobfind fit-score.js fold: kucuk harf, umlaut yok, ss=ss, i=i."""
    s = s.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("ı", "i"), ("é", "e"), ("è", "e"), ("à", "a")):
        s = s.replace(a, b)
    return s


def rol_regexleri() -> list[tuple[str, "regex.Pattern"]]:
    global _ROL
    if _ROL is None:
        kayitlar = json.loads(ROL_JSON.read_text(encoding="utf-8"))
        _ROL = [(k["ad"], regex.compile(k["rol"], regex.I)) for k in kayitlar]
    return _ROL


def basliklar(metin: str) -> list[str]:
    """Sayfa metninden ilan basliklari (tekil, sira korunur)."""
    gorulen, sonuc = set(), []
    for satir in metin.splitlines():
        s = re.sub(r"\s+", " ", satir).strip(" -–—•·|*>")
        if not (6 <= len(s) <= 120):
            continue
        if not (ILAN_ISARET.search(s) or WIR_SUCHEN.match(s)):
            continue
        if GURULTU.search(s):
            continue
        if len(re.findall(r"[A-Za-zÄÖÜäöüß]", s)) < 5:
            continue
        k = fold(s)
        if k in gorulen:
            continue
        gorulen.add(k)
        sonuc.append(s)
    return sonuc


def meslekleri_esle(baslik: str) -> list[str]:
    katli = fold(baslik)
    tutan = [ad for ad, r in rol_regexleri() if r.search(katli)]
    return tutan or ["diger"]


def alan_adi_tara(conn, domain: str) -> tuple[list[dict], int]:
    """Bir alan adinin ilan kayitlarini uretir; (kayitlar, kariyer_sayfa_sayisi) doner."""
    sayfalar = {p["tur"]: p["url"] for p in pages_of(conn, domain)}
    kariyer = [t for t in KARIYER_TURLERI if t in sayfalar]
    kaynaklar = kariyer if kariyer else (["home"] if "home" in sayfalar else [])
    legal = legal_name_bul(read_page_text(domain, "impressum")) if "impressum" in sayfalar else ""
    kayitlar, gorulen = [], set()
    for tur in kaynaklar:
        for b in basliklar(read_page_text(domain, tur)):
            k = fold(b)
            if k in gorulen:
                continue
            gorulen.add(k)
            kayitlar.append({"domain": domain, "legal_name": legal, "ad_title": b, "ad_url": sayfalar[tur],
                             "meslek_kayitlari": meslekleri_esle(b), "fetched_at": now(),
                             "kaynak": tur})
    return kayitlar, len(kariyer)


def parca_no(conn) -> int:
    n = conn.execute("SELECT COUNT(1) FROM ilan_tarama").fetchone()[0]
    return n // PARCA + 1


def tara(tek: bool = False) -> None:
    OUT.mkdir(exist_ok=True)
    conn = db(); conn.executescript(SEMA); conn.commit(); conn.close()
    log(f"isci basladi (parti {PARTI}, {PARCA} alan adi/dosya, {len(rol_regexleri())} rol regex)")
    bosta = 0
    while True:
        if (BASE / "DUR").exists():
            log("durdu (DUR)"); return
        conn = db()
        satirlar = conn.execute(
            """SELECT s.domain FROM sirketler s LEFT JOIN ilan_tarama t ON t.domain = s.domain
               WHERE s.fetch_status = 'cekildi' AND t.domain IS NULL ORDER BY s.oncelik, s.domain LIMIT ?""", (PARTI,)).fetchall()
        if not satirlar:
            conn.close()
            if tek:
                return
            bosta += 1
            if bosta % 10 == 1:
                log("bekleyen alan adi yok, 3 dk")
            time.sleep(180); continue
        bosta = 0; t0 = time.time()
        sayac = {"alan": 0, "kariyerli": 0, "ilanli": 0, "ilan": 0, "eslesen": 0}
        for (domain,) in satirlar:
            kayitlar, kariyer_n = alan_adi_tara(conn, domain)
            parca = parca_no(conn)
            if kayitlar:
                with (OUT / f"ilanlar-{parca:03d}.jsonl").open("a", encoding="utf-8") as f:
                    for k in kayitlar:
                        f.write(json.dumps(k, ensure_ascii=False) + "\n")
            eslesen = sum(1 for k in kayitlar if k["meslek_kayitlari"] != ["diger"])
            yaz(lambda c, d=domain, kn=kariyer_n, i=len(kayitlar), e=eslesen, p=parca: c.execute(
                "INSERT OR REPLACE INTO ilan_tarama(domain, kariyer_sayfa, ilan, eslesen, parca, scanned_at) VALUES (?,?,?,?,?,?)",
                (d, kn, i, e, p, now())))
            sayac["alan"] += 1; sayac["kariyerli"] += kariyer_n > 0; sayac["ilanli"] += bool(kayitlar)
            sayac["ilan"] += len(kayitlar); sayac["eslesen"] += eslesen
        conn.close()
        log(f"parti: {sayac['alan']} alan adi | kariyer sayfali {sayac['kariyerli']} | ilanli {sayac['ilanli']} | "
            f"ilan {sayac['ilan']} (meslek eslesen {sayac['eslesen']}) | {time.time() - t0:.0f} sn")
        if tek:
            return


def ozet(conn) -> dict:
    r = conn.execute("SELECT COUNT(1), SUM(kariyer_sayfa>0), SUM(ilan>0), SUM(ilan), SUM(eslesen), MAX(parca) FROM ilan_tarama").fetchone()
    return {"taranan": r[0] or 0, "kariyer_sayfali": r[1] or 0, "ilanli": r[2] or 0, "ilan": r[3] or 0,
            "meslek_eslesen": r[4] or 0, "dosya": r[5] or 0}


if __name__ == "__main__":
    komut = sys.argv[1] if len(sys.argv) > 1 else "tara"
    if komut == "tara":
        tara(tek="--tek" in sys.argv)
    elif komut == "dene":
        conn = db(); conn.executescript(SEMA)
        kayitlar, kn = alan_adi_tara(conn, sys.argv[2])
        print(f"kariyer sayfasi: {kn}")
        for k in kayitlar:
            print(f"  [{','.join(k['meslek_kayitlari'])}] {k['ad_title']}  <- {k['kaynak']}")
    else:
        print(__doc__)
