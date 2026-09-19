"""Kosu olcumleri tek yerden: cekim, hakem, uzlasma, ad dogrulama sayilari.

  python verify/olcum.py            # JSON ozet
Rapor bu ciktidan yazilir; her kosuda tekrar uretilebilir.
"""
from __future__ import annotations

import json
import re

from verify.config import MODEL_A, MODEL_B, NUM_CTX, PROMPT_VERSION
from verify.db import db, read_page_text
from verify.impressum import ad_celisiyor_mu, legal_name_bul

BICIM = re.compile(r"GmbH|AG|e\.?\s?K|OHG|KG|UG|mbH|SE|e\.?\s?V|GbR|PartG|eG", re.I)


def olc() -> dict:
    conn = db()
    q = lambda sql, *p: conn.execute(sql, p).fetchall()
    tek = lambda sql, *p: conn.execute(sql, p).fetchone()[0]

    o: dict = {"model_a": MODEL_A, "model_b": MODEL_B, "istem": PROMPT_VERSION, "num_ctx": NUM_CTX}

    o["cekim"] = {
        "alan_adi": tek("SELECT COUNT(*) FROM domains"),
        "durum": {r[0]: r[1] for r in q("SELECT status, COUNT(*) FROM domains GROUP BY status")},
        "sayfa": tek("SELECT COUNT(*) FROM pages"),
        "sayfa_turu": {r[0]: r[1] for r in q("SELECT tur, COUNT(*) FROM pages GROUP BY tur ORDER BY 2 DESC")},
        "ort_karakter": tek("SELECT COALESCE(ROUND(AVG(chars)),0) FROM pages"),
        "sayfa_basi_alan_adi": round(tek("SELECT COUNT(*) FROM pages") /
                                     max(1, tek("SELECT COUNT(DISTINCT domain) FROM pages")), 1),
        "impressum_orani": round(100 * tek("SELECT COUNT(*) FROM pages WHERE tur='impressum'") /
                                 max(1, tek("SELECT COUNT(DISTINCT domain) FROM pages")), 1),
    }

    o["hakem"] = {}
    for model in (MODEL_A, MODEL_B):
        toplam = tek("SELECT COUNT(*) FROM judgments WHERE model=?", model)
        if not toplam:
            continue
        o["hakem"][model] = {
            "yargi": toplam,
            "karar": {r[0]: r[1] for r in q("SELECT decision, COUNT(*) FROM judgments WHERE model=? "
                                            "GROUP BY decision", model)},
            "kanit_turu": {r[0]: r[1] for r in q("SELECT evidence_type, COUNT(*) FROM judgments WHERE model=? "
                                                 "GROUP BY evidence_type ORDER BY 2 DESC", model)},
            "alinti_dogrulanan": tek("SELECT COUNT(*) FROM judgments WHERE model=? AND quotes_verified=1", model),
            "alinti_dogrulanan_yuzde": round(100 * tek("SELECT COUNT(*) FROM judgments WHERE model=? AND "
                                                       "quotes_verified=1", model) / max(1, toplam), 1),
            "ort_gecikme_ms": tek("SELECT ROUND(AVG(latency_ms)) FROM judgments WHERE model=?", model),
            "toplam_sure_dk": round(tek("SELECT COALESCE(SUM(latency_ms),0) FROM judgments WHERE model=?",
                                        model) / 60000, 1),
        }

    # iki modelin ayni kayitta uyusmasi
    ciftler = q("SELECT a.domain, a.decision, b.decision FROM judgments a JOIN judgments b "
                "ON a.domain=b.domain AND a.meslek=b.meslek WHERE a.model=? AND b.model=?", MODEL_A, MODEL_B)
    if ciftler:
        ayni = sum(1 for _, x, y in ciftler if x == y)
        o["uyum"] = {
            "cift": len(ciftler),
            "ayni_karar": ayni,
            "ayni_karar_yuzde": round(100 * ayni / len(ciftler), 1),
            "dagilim": {f"A={x}|B={y}": sum(1 for _, i, j in ciftler if i == x and j == y)
                        for x in ("yes", "no", "unclear") for y in ("yes", "no", "unclear")
                        if any(i == x and j == y for _, i, j in ciftler)},
        }

    if tek("SELECT COUNT(*) FROM verified"):
        kararlar: dict[str, int] = {}
        for r in q("SELECT professions_json FROM verified"):
            prof = (json.loads(r[0] or "[]") or [{}])[0]
            k = prof.get("karar", "?")
            kararlar[k] = kararlar.get(k, 0) + 1
        o["uzlasma"] = {
            "kayit": tek("SELECT COUNT(*) FROM verified"),
            "karar": kararlar,
            "agreement": {r[0]: r[1] for r in q("SELECT agreement, COUNT(*) FROM verified GROUP BY agreement")},
            "ilan_bulunan": tek("SELECT COUNT(*) FROM verified WHERE job_ads_json NOT IN ('[]','')"),
            "boyut_ipucu": tek("SELECT COUNT(*) FROM verified WHERE size_hint <> ''"),
        }
        # meslek kirilimi
        kir: dict[str, dict] = {}
        for r in q("SELECT v.professions_json, d.meslek FROM verified v JOIN domains d ON d.domain=v.domain"):
            prof = (json.loads(r[0] or "[]") or [{}])[0]
            d = kir.setdefault(r[1], {})
            k = prof.get("karar", "?")
            d[k] = d.get(k, 0) + 1
        o["uzlasma"]["meslek_kirilim"] = kir

    # Impressum'dan ad
    satirlar = q("SELECT domain, pool_name FROM domains")
    imp = var = bicimli = celiski = 0
    for domain, pool in satirlar:
        metin = read_page_text(domain, "impressum")
        if not metin:
            continue
        imp += 1
        ad = legal_name_bul(metin)
        if ad:
            var += 1
            if BICIM.search(ad):
                bicimli += 1
            if ad_celisiyor_mu(ad, pool):
                celiski += 1
    o["ad_dogrulama"] = {
        "impressum_sayfasi": imp, "legal_name_cikarildi": var,
        "legal_name_yuzde": round(100 * var / max(1, imp), 1),
        "hukuki_bicim_iceren": bicimli,
        "havuz_adiyla_celiskili": celiski,
        "havuz_adi_atildi_yuzde": round(100 * celiski / max(1, var), 1),
    }
    conn.close()
    return o


if __name__ == "__main__":
    print(json.dumps(olc(), ensure_ascii=False, indent=2))
