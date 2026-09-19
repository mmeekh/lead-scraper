"""Altin kume: insan etiketi toplama ve isabet olcumu (docs §6).

Akis:
  1) `verify gold --out altin-kume.csv`  -> etiketlenecek satirlari (kanit + link) yazar
  2) insan `etiket` sutununu doldurur: evet / hayir / belirsiz
  3) `verify score --in altin-kume.csv`  -> listelenen (evet+both) satirlarda isabet, kapsama,
     karisiklik matrisi, meslek kirilimi. Model/istem surumu raporla birlikte kaydedilir.
Isabet = dogru(evet) / listelenen(evet). Kapsama = listelenen(evet) / insanin evet dedigi.
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


def disa(yol: Path, limit: int = 0) -> dict:
    conn = db()
    satirlar = conn.execute(
        "SELECT v.*, d.city, d.sector, d.meslek FROM verified v JOIN domains d ON d.domain = v.domain "
        "ORDER BY d.meslek, v.domain").fetchall()
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
    return {"satir": n, "dosya": str(yol),
            "aciklama": "etiket sutununu doldur: evet / hayir / belirsiz"}


def olc(yol: Path) -> dict:
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = [r for r in csv.DictReader(f)]
    etiketli = [r for r in satirlar if (r.get("etiket") or "").strip().lower() in ("evet", "hayir", "belirsiz")]
    if not etiketli:
        return {"hata": "etiketli satir yok", "toplam_satir": len(satirlar)}

    def norm(x): return (x or "").strip().lower()

    listelenen = [r for r in etiketli if norm(r["hat_karari"]) == "evet" and norm(r["agreement"]) == "both"]
    dogru = [r for r in listelenen if norm(r["etiket"]) == "evet"]
    insan_evet = [r for r in etiketli if norm(r["etiket"]) == "evet"]

    matris: dict[str, int] = {}
    for r in etiketli:
        k = f"hat={norm(r['hat_karari'])}|insan={norm(r['etiket'])}"
        matris[k] = matris.get(k, 0) + 1

    meslek_kirilim: dict[str, dict] = {}
    for r in etiketli:
        m = r["meslek"]
        d = meslek_kirilim.setdefault(m, {"etiketli": 0, "listelenen": 0, "dogru": 0})
        d["etiketli"] += 1
        if norm(r["hat_karari"]) == "evet" and norm(r["agreement"]) == "both":
            d["listelenen"] += 1
            if norm(r["etiket"]) == "evet":
                d["dogru"] += 1
    for d in meslek_kirilim.values():
        d["isabet"] = round(100 * d["dogru"] / d["listelenen"], 1) if d["listelenen"] else None

    return {
        "model_a": MODEL_A, "model_b": MODEL_B, "istem_surumu": PROMPT_VERSION,
        "etiketli_satir": len(etiketli),
        "listelenen": len(listelenen),
        "listelenen_dogru": len(dogru),
        "isabet_yuzde": round(100 * len(dogru) / len(listelenen), 1) if listelenen else None,
        "hedef": 95.0,
        "insan_evet": len(insan_evet),
        "kapsama_yuzde": round(100 * len(listelenen) / len(insan_evet), 1) if insan_evet else None,
        "karisiklik": matris,
        "meslek_kirilim": meslek_kirilim,
        "yanlis_listelenenler": [{"domain": r["domain"], "meslek": r["meslek"],
                                  "insan": norm(r["etiket"]), "not": r.get("not", "")}
                                 for r in listelenen if norm(r["etiket"]) != "evet"][:20],
    }
