"""96 insan etiketli kayitta iki ayarin olcumu (20 Eyl 2026):
  1) alinti kurali: mevcut (cumle duzeyi, tekrar yok) vs alinti tekrari (tek ek cagri)
  2) B modeli: qwen2.5:14b (mevcut) vs qwen3:8b vs qwen3:14b - tek basina ve A ile uzlasmada

  python verify/olc_gold.py [--modeller qwen2.5:14b-instruct-q4_K_M,qwen3:8b,qwen3:14b]
Cikti: verify/olcum-gold-2026-09-20.json + RAPOR'a yapistirilacak markdown tablo (stdout).
Yeni yargilar judgments tablosuna model adiyla yazilir (PK domain+meslek+model); eski yargilar bozulmaz.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify import judge as hakem                      # noqa: E402
from verify.alinti import alinti_tekrar, cumle_duzeyi_dogrula  # noqa: E402
from verify.candidates import meslekler                # noqa: E402
from verify.config import MODEL_A                      # noqa: E402
from verify.consensus import kanit_gecidi              # noqa: E402
from verify.db import db, now, pages_of, save_judgment  # noqa: E402

BASE = Path(__file__).resolve().parent
GOLD = BASE / "altin-kume-etiketli.csv"
CIKTI = BASE / "olcum-gold-2026-09-20.json"
TEKRAR_SEMA = """CREATE TABLE IF NOT EXISTS alinti_tekrarlari (
    domain TEXT, meslek TEXT, model TEXT, decision TEXT, quotes_json TEXT, verified INTEGER,
    latency_ms INTEGER, created_at TEXT, PRIMARY KEY (domain, meslek, model));"""


def gold() -> list[dict]:
    with GOLD.open(encoding="utf-8-sig", newline="") as f:
        return [{"domain": r["domain"].strip().lower(), "meslek": r["meslek"].strip(),
                 "etiket": (r.get("insan_karari") or r.get("etiket") or "").strip().lower()}
                for r in csv.DictReader(f) if (r.get("insan_karari") or r.get("etiket") or "").strip()]


def yargi_al(conn, domain, meslek, model) -> dict | None:
    r = conn.execute("SELECT * FROM judgments WHERE domain=? AND meslek=? AND model=?", (domain, meslek, model)).fetchone()
    if not r:
        return None
    j = dict(r); j["quotes"] = json.loads(j["quotes_json"] or "[]"); j["activity_summary"] = j.get("activity_de", "")
    return j


def b_yargila(model: str, satirlar: list[dict], kat: dict) -> dict:
    """Eksik olan B yargilarini uretir (96 kayit), sure olcer."""
    conn = db()
    n = 0; t0 = time.time(); sureler = []
    for s in satirlar:
        if yargi_al(conn, s["domain"], s["meslek"], model):
            continue
        sayfalar = pages_of(conn, s["domain"])
        kanit, ham = hakem.kanit_metni(conn, s["domain"], sayfalar)
        if not kanit:
            continue
        pool = conn.execute("SELECT pool_name FROM domains WHERE domain=?", (s["domain"],)).fetchone()
        j = hakem.yargila(model, "B", kat[s["meslek"]], [pool["pool_name"] if pool else ""], kanit, ham)
        save_judgment(conn, s["domain"], s["meslek"], model, j); conn.commit()
        n += 1; sureler.append(j["latency_ms"])
        if n % 20 == 0:
            print(f"  {model}: {n} yargi, {time.time() - t0:.0f} sn", flush=True)
    conn.close()
    hakem.bosalt(model)
    return {"yeni_yargi": n, "ort_sn": round(sum(sureler) / max(1, len(sureler)) / 1000, 1) if sureler else None}


def tekrar_al(conn, domain, meslek, model, kat, onceki) -> dict:
    """Alinti tekrari: onbellekli (alinti_tekrarlari tablosu)."""
    r = conn.execute("SELECT * FROM alinti_tekrarlari WHERE domain=? AND meslek=? AND model=?", (domain, meslek, model)).fetchone()
    if r:
        return {**onceki, "decision": r["decision"], "quotes": json.loads(r["quotes_json"]), "quotes_verified": bool(r["verified"]), "alinti_tekrar": True}
    sayfalar = pages_of(conn, domain)
    kanit, ham = hakem.kanit_metni(conn, domain, sayfalar)
    y = alinti_tekrar(model, kat[meslek], kanit, ham, onceki)
    conn.execute("INSERT OR REPLACE INTO alinti_tekrarlari VALUES (?,?,?,?,?,?,?,?)",
                 (domain, meslek, model, y["decision"], json.dumps(y["quotes"], ensure_ascii=False), int(y["quotes_verified"]), y["latency_ms"], now()))
    conn.commit()
    return y


def uzlasma_degerlendir(satirlar, model_b, kat, tekrar: bool, kural: str = "cumle") -> dict:
    """Mevcut kural (cumle duzeyi, tekrarsiz) ya da tekrar kuraliyla uzlasma; isabet/kapsama."""
    conn = db(); conn.executescript(TEKRAR_SEMA)
    listelenen = dogru = ek_cagri = alinti_dusen = gecit_dusen = 0
    insan_evet = sum(1 for s in satirlar if s["etiket"] == "evet")
    kayip = []
    for s in satirlar:
        a = yargi_al(conn, s["domain"], s["meslek"], MODEL_A); b = yargi_al(conn, s["domain"], s["meslek"], model_b)
        if not a or not b or a["decision"] != "yes" or b["decision"] != "yes":
            continue
        sayfalar = pages_of(conn, s["domain"]); _, ham = hakem.kanit_metni(conn, s["domain"], sayfalar)
        dogrula = cumle_duzeyi_dogrula if kural == "cumle" else hakem.alintilari_dogrula
        for j, model in ((a, MODEL_A), (b, model_b)):
            j["quotes_verified"], _ = dogrula(j["quotes"], ham)
        if tekrar:
            for j, model in ((a, MODEL_A), (b, model_b)):
                if not j["quotes_verified"]:
                    y = tekrar_al(conn, s["domain"], s["meslek"], model, kat, j); ek_cagri += 1
                    yv, _ = dogrula(y["quotes"], ham)
                    j.update({"decision": y["decision"], "quotes": y["quotes"], "quotes_verified": yv})
            if a["decision"] != "yes" or b["decision"] != "yes":
                continue
        if not (a["quotes_verified"] and b["quotes_verified"]):
            alinti_dusen += 1; continue
        if not kanit_gecidi(a["quotes"] + b["quotes"], kat[s["meslek"]].get("beleg_anahtarlar", [])):
            gecit_dusen += 1; continue
        listelenen += 1
        if s["etiket"] == "evet":
            dogru += 1
        else:
            kayip.append(s["domain"])
    conn.close()
    return {"listelenen": listelenen, "dogru": dogru, "isabet": round(100 * dogru / listelenen, 1) if listelenen else None,
            "kapsama": round(100 * dogru / insan_evet, 1) if insan_evet else None, "insan_evet": insan_evet,
            "alintida_dusen": alinti_dusen, "gecitte_dusen": gecit_dusen, "ek_cagri": ek_cagri, "yanlis_listelenen": kayip}


def tek_basina(satirlar, model) -> dict:
    conn = db(); evet = dogru = 0; sn = []
    for s in satirlar:
        j = yargi_al(conn, s["domain"], s["meslek"], model)
        if not j:
            continue
        sn.append(j["latency_ms"])
        if j["decision"] == "yes":
            evet += 1; dogru += s["etiket"] == "evet"
    conn.close()
    return {"yes": evet, "dogru": dogru, "isabet": round(100 * dogru / evet, 1) if evet else None,
            "ort_sn": round(sum(sn) / max(1, len(sn)) / 1000, 1) if sn else None}


def main():
    modeller = ["qwen2.5:14b-instruct-q4_K_M", "qwen3:8b", "qwen3:14b"]
    if "--modeller" in sys.argv:
        modeller = sys.argv[sys.argv.index("--modeller") + 1].split(",")
    kat = meslekler(); satirlar = gold()
    print(f"gold: {len(satirlar)} satir | A={MODEL_A}", flush=True)
    sonuc = {"tarih": now(), "model_a": MODEL_A, "gold": len(satirlar), "b_modelleri": {}}
    conn = db(); conn.executescript(TEKRAR_SEMA); conn.close()
    for m in modeller:
        print(f"== B = {m}", flush=True)
        try:
            uret = b_yargila(m, satirlar, kat)
        except Exception as e:
            print(f"  !! {m}: {e}"); sonuc["b_modelleri"][m] = {"hata": str(e)[:200]}; continue
        r = {"uretim": uret, "tek_basina": tek_basina(satirlar, m),
             "cumle": uzlasma_degerlendir(satirlar, m, kat, tekrar=False, kural="cumle"),
             "cumle_tekrar": uzlasma_degerlendir(satirlar, m, kat, tekrar=True, kural="cumle"),
             "birebir": uzlasma_degerlendir(satirlar, m, kat, tekrar=False, kural="birebir"),
             "birebir_tekrar": uzlasma_degerlendir(satirlar, m, kat, tekrar=True, kural="birebir")}
        hakem.bosalt(m); hakem.bosalt(MODEL_A)
        sonuc["b_modelleri"][m] = r
        print(json.dumps(r, ensure_ascii=False), flush=True)
    sonuc["a_tek_basina"] = tek_basina(satirlar, MODEL_A)
    CIKTI.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    # markdown tablo
    def h(u):
        return f"{u['listelenen']}/{u['dogru']} · %{u['isabet']} · kaps. %{u['kapsama']}"
    print("\n| B modeli | B tek başına (yes→doğru, isabet) | sn/kayıt | cümle kuralı | cümle + tekrar | birebir kuralı | birebir + tekrar | ek çağrı |")
    print("|---|---|---|---|---|---|---|---|")
    for m, r in sonuc["b_modelleri"].items():
        if "hata" in r:
            print(f"| {m} | hata | | | | | | |"); continue
        t = r["tek_basina"]
        print(f"| `{m}` | {t['dogru']}/{t['yes']}, %{t['isabet']} | {t['ort_sn']} | {h(r['cumle'])} | {h(r['cumle_tekrar'])} | "
              f"{h(r['birebir'])} | {h(r['birebir_tekrar'])} | {r['birebir_tekrar']['ek_cagri']} |")
    print(f"\nA tek başına: {sonuc['a_tek_basina']}")


if __name__ == "__main__":
    main()
