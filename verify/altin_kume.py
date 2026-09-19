"""Altin kume: insan etiketi toplama ve isabet olcumu (docs §6).

Iki CSV bicimi:
  1) `verify gold`      -> altin-kume.csv      : hattin karari + kanitlar gorunur (inceleme icin)
  2) `verify gold --kor`-> altin-kume-kor.csv  : KOR kopya; hakem kararlari ve alintilar YOK,
     yalniz domain, legal_name, meslek, site linki, hakkinda linki, bos `insan_karari`, `not`.
     Etiketleyen kisi hattin kararindan etkilenmesin diye budur.

Olcum: `verify score --etiket <csv>` etiketli dosyayi domain+meslek uzerinden veritabanindaki
kararlarla eslestirir; isabet, kapsama, karisiklik matrisi ve meslek kirilimini verir.
Isabet  = insan 'evet' / hattin listeledigi (evet)
Kapsama = hattin listeledigi (evet) / insanin 'evet' dedigi
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import MODEL_A, MODEL_B, PROMPT_VERSION
from .db import db

BASLIKLAR = ["domain", "meslek", "sehir", "sektor", "hat_karari", "agreement",
             "legal_name", "activity_de", "model_a", "model_b", "alinti_1", "alinti_2",
             "kanit_url", "etiket", "not"]

# Kor kopya: hattin kararini ele veren hicbir sutun yok.
KOR_BASLIKLAR = ["domain", "legal_name", "meslek", "site_linki", "hakkinda_linki",
                 "insan_karari", "not"]

MESLEK_SORUSU = {
    "berufskraftfahrer": "Bu sirket KENDI bunyesinde Berufskraftfahrer (LKW/C-CE soforu) calistirir mi?",
    "elektroingenieur": "Bu sirket KENDI bunyesinde Ingenieur Elektrotechnik calistirir mi?",
    "marketing_manager": "Bu sirket KENDI bunyesinde Marketing Manager calistirir mi?",
}


def _satirlar(conn):
    return conn.execute(
        "SELECT v.*, d.city, d.sector, d.meslek, d.pool_name FROM verified v "
        "JOIN domains d ON d.domain = v.domain ORDER BY d.meslek, v.domain").fetchall()


def disa(yol: Path, limit: int = 0) -> dict:
    """Inceleme kopyasi: hattin karari ve kanitlar gorunur."""
    conn = db()
    satirlar = _satirlar(conn)
    conn.close()
    n = 0
    with open(yol, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BASLIKLAR)
        w.writeheader()
        for s in satirlar:
            prof = (json.loads(s["professions_json"] or "[]") or [{}])[0]
            modeller = prof.get("modeller", {})
            alintilar = [k.get("alinti", "") for k in prof.get("kanit", [])][:2]
            sayfalar = json.loads(s["pages_json"] or "[]")
            ana = next((p["url"] for p in sayfalar if p["tur"] == "home"), "")
            w.writerow({
                "domain": s["domain"], "meslek": s["meslek"], "sehir": s["city"], "sektor": s["sector"],
                "hat_karari": prof.get("karar", ""), "agreement": s["agreement"],
                "legal_name": s["legal_name"], "activity_de": (s["activity_de"] or "")[:300],
                "model_a": modeller.get(MODEL_A, {}).get("decision", ""),
                "model_b": modeller.get(MODEL_B, {}).get("decision", ""),
                "alinti_1": (alintilar[0] if alintilar else "")[:300],
                "alinti_2": (alintilar[1] if len(alintilar) > 1 else "")[:300],
                "kanit_url": ana, "etiket": "", "not": "",
            })
            n += 1
            if limit and n >= limit:
                break
    return {"satir": n, "dosya": str(yol), "tur": "inceleme (hat karari gorunur)",
            "aciklama": "etiket sutununu doldur: evet / hayir / belirsiz"}


def kor_disa(yol: Path, limit: int = 0) -> dict:
    """Kor kopya: yalniz sirket bilgisi ve linkler; karar/alinti sutunu yok."""
    conn = db()
    satirlar = _satirlar(conn)
    conn.close()
    n = 0
    with open(yol, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KOR_BASLIKLAR)
        w.writeheader()
        for s in satirlar:
            sayfalar = json.loads(s["pages_json"] or "[]")
            tur_url = {p["tur"]: p["url"] for p in sayfalar}
            hakkinda = tur_url.get("ueber") or tur_url.get("leistungen") or tur_url.get("karriere") or tur_url.get("impressum", "")
            w.writerow({
                "domain": s["domain"],
                "legal_name": s["legal_name"] or s["pool_name"],
                "meslek": s["meslek"],
                "site_linki": tur_url.get("home", f"https://{s['domain']}/"),
                "hakkinda_linki": hakkinda,
                "insan_karari": "", "not": "",
            })
            n += 1
            if limit and n >= limit:
                break
    return {"satir": n, "dosya": str(yol), "tur": "KOR (hat karari gizli)",
            "soru": MESLEK_SORUSU,
            "aciklama": "insan_karari sutununu doldur: evet / hayir / belirsiz"}


def _oku_etiketler(yol: Path) -> dict[tuple[str, str], dict]:
    """CSV'den (domain, meslek) -> {etiket, not}. Iki bicimi de kabul eder."""
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = list(csv.DictReader(f))
    etiketler: dict[tuple[str, str], dict] = {}
    for r in satirlar:
        domain = (r.get("domain") or "").strip().lower()
        meslek = (r.get("meslek") or "").strip()
        etiket = (r.get("insan_karari") or r.get("etiket") or "").strip().lower()
        if not domain or etiket not in ("evet", "hayir", "hayır", "belirsiz"):
            continue
        etiketler[(domain, meslek)] = {"etiket": "hayir" if etiket == "hayır" else etiket,
                                       "not": (r.get("not") or "").strip()}
    return etiketler


def olc(yol: Path) -> dict:
    """Etiketli CSV'yi veritabanindaki kararlarla domain+meslek uzerinden eslestirip olcer."""
    etiketler = _oku_etiketler(yol)
    if not etiketler:
        return {"hata": "etiketli satir yok (insan_karari/etiket sutunu bos)", "dosya": str(yol)}

    conn = db()
    kayitlar = {}
    for s in _satirlar(conn):
        prof = (json.loads(s["professions_json"] or "[]") or [{}])[0]
        modeller = prof.get("modeller", {})
        kayitlar[(s["domain"], s["meslek"])] = {
            "karar": prof.get("karar", ""), "agreement": s["agreement"],
            "a": modeller.get(MODEL_A, {}).get("decision", ""),
            "b": modeller.get(MODEL_B, {}).get("decision", ""),
        }
    conn.close()

    eslesen, eslesmeyen = [], []
    for (domain, meslek), e in etiketler.items():
        k = kayitlar.get((domain, meslek))
        if not k:
            # meslek bos birakilmissa yalniz domain ile dene
            aday = [v for (d, m), v in kayitlar.items() if d == domain]
            if len(aday) == 1:
                k = aday[0]
        if k:
            eslesen.append({**k, **e, "domain": domain, "meslek": meslek})
        else:
            eslesmeyen.append(domain)

    listelenen = [r for r in eslesen if r["karar"] == "evet"]
    dogru = [r for r in listelenen if r["etiket"] == "evet"]
    insan_evet = [r for r in eslesen if r["etiket"] == "evet"]

    matris: dict[str, int] = {}
    for r in eslesen:
        anahtar = f"hat={r['karar']}|insan={r['etiket']}"
        matris[anahtar] = matris.get(anahtar, 0) + 1

    kirilim: dict[str, dict] = {}
    for r in eslesen:
        d = kirilim.setdefault(r["meslek"] or "?", {"etiketli": 0, "listelenen": 0, "dogru": 0})
        d["etiketli"] += 1
        if r["karar"] == "evet":
            d["listelenen"] += 1
            d["dogru"] += 1 if r["etiket"] == "evet" else 0
    for d in kirilim.values():
        d["isabet"] = round(100 * d["dogru"] / d["listelenen"], 1) if d["listelenen"] else None

    # modellerin tek basina isabeti (karsilastirma icin)
    tek_model = {}
    for ad, alan in (("model_a", "a"), ("model_b", "b")):
        evet = [r for r in eslesen if r[alan] == "yes"]
        tek_model[ad] = {
            "yes_dedigi": len(evet),
            "dogru": sum(1 for r in evet if r["etiket"] == "evet"),
            "isabet": round(100 * sum(1 for r in evet if r["etiket"] == "evet") / len(evet), 1) if evet else None,
        }

    return {
        "dosya": str(yol), "model_a": MODEL_A, "model_b": MODEL_B, "istem_surumu": PROMPT_VERSION,
        "etiketli_satir": len(etiketler), "eslesen": len(eslesen),
        "eslesmeyen": eslesmeyen[:10],
        "listelenen": len(listelenen), "listelenen_dogru": len(dogru),
        "isabet_yuzde": round(100 * len(dogru) / len(listelenen), 1) if listelenen else None,
        "hedef": 95.0,
        "insan_evet": len(insan_evet),
        "kapsama_yuzde": round(100 * len(listelenen) / len(insan_evet), 1) if insan_evet else None,
        "karisiklik": matris,
        "meslek_kirilim": kirilim,
        "tek_model_isabeti": tek_model,
        "yanlis_listelenenler": [{"domain": r["domain"], "meslek": r["meslek"],
                                  "insan": r["etiket"], "not": r["not"]}
                                 for r in listelenen if r["etiket"] != "evet"][:20],
        "kacirilanlar": [{"domain": r["domain"], "meslek": r["meslek"], "hat": r["karar"],
                          "A": r["a"], "B": r["b"]}
                         for r in insan_evet if r["karar"] != "evet"][:20],
    }
