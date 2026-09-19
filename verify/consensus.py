"""Uzlasma: 'evet' yalniz iki model de evet derse VE ikisinin de alintilari dogrulanirsa.

Biri 'hayir' -> hayir. Diger her durum -> belirsiz (listelenmez).
Ad: Impressum regex'i once, model onerisi sonra; havuz adiyla celisirse havuz adi atilir.
"""
from __future__ import annotations

import json
import re

from .config import MODEL_A, MODEL_B, PROMPT_VERSION
from .db import db, judgments_of, pages_of, read_page_text, save_verified, set_status
from .impressum import ad_celisiyor_mu, display_name, legal_name_bul

ILAN_RE = re.compile(r"^.{6,120}$")
ILAN_ISARET = re.compile(r"\(\s*[mwdx]\s*[/|]\s*[mwdx]\s*([/|]\s*[mwdx]\s*)?\)|\bm/w/d\b|"
                         r"\bgesucht\b|\bin (voll|teil)zeit\b|\bausbildung (zum|zur|als)\b|\bstellenangebot\b", re.I)
ILAN_GURULTU = re.compile(r"^(jobs?|karriere|stellenangebote|offene stellen|bewerbung|initiativbewerbung|"
                          r"jetzt bewerben|mehr erfahren|kontakt|impressum|datenschutz|startseite)$", re.I)


def job_ads_cikar(karriere_metin: str, url: str) -> list[dict]:
    ilanlar: list[dict] = []
    gorulen: set[str] = set()
    for satir in (karriere_metin or "").splitlines():
        s = satir.strip()
        if not s or not ILAN_RE.match(s) or ILAN_GURULTU.match(s) or not ILAN_ISARET.search(s):
            continue
        k = s.lower()
        if k in gorulen:
            continue
        gorulen.add(k)
        ilanlar.append({"baslik": s, "url": url})
        if len(ilanlar) >= 20:
            break
    return ilanlar


def uzlas(a: dict | None, b: dict | None) -> tuple[str, str]:
    """(karar, agreement) dondurur."""
    if not a or not b:
        return "belirsiz", "none"
    ka, kb = a["decision"], b["decision"]
    if ka == "no" or kb == "no":
        return "hayir", "both" if ka == kb else "one"
    if ka == "yes" and kb == "yes":
        if a["quotes_verified"] and b["quotes_verified"]:
            return "evet", "both"
        return "belirsiz", "one"          # alinti dogrulanmadi -> listelenmez
    if ka == kb == "unclear":
        return "belirsiz", "both"
    return "belirsiz", "one"


def isle(sadece_yeni: bool = True) -> dict:
    conn = db()
    durumlar = ("yargilandi",) if sadece_yeni else ("yargilandi", "tamam")
    yer = ",".join("?" * len(durumlar))
    satirlar = conn.execute(
        f"SELECT * FROM domains WHERE status IN ({yer}) ORDER BY domain", durumlar).fetchall()
    ozet = {"islenen": 0, "evet": 0, "hayir": 0, "belirsiz": 0,
            "agreement": {"both": 0, "one": 0, "none": 0}, "ad_celiskisi": 0, "legal_name_var": 0}
    for s in satirlar:
        domain, meslek = s["domain"], s["meslek"]
        yargilar = {j["model"]: dict(j) for j in judgments_of(conn, domain)}
        a = yargilar.get(MODEL_A)
        b = yargilar.get(MODEL_B)
        if a:
            a["quotes"] = json.loads(a["quotes_json"] or "[]")
        if b:
            b["quotes"] = json.loads(b["quotes_json"] or "[]")
        karar, agreement = uzlas(a, b)

        sayfalar = pages_of(conn, domain)
        tur_url = {p["tur"]: p["url"] for p in sayfalar}
        imp_metin = read_page_text(domain, "impressum")
        legal = legal_name_bul(imp_metin)
        model_ad = (a or {}).get("legal_name", "") or (b or {}).get("legal_name", "")
        if not legal and model_ad and len(model_ad) <= 100:
            legal = model_ad.strip()
        if legal:
            ozet["legal_name_var"] += 1
        celisik = ad_celisiyor_mu(legal, s["pool_name"])
        if celisik:
            ozet["ad_celiskisi"] += 1

        kanitlar = []
        for j, etiket in ((a, MODEL_A), (b, MODEL_B)):
            if not j:
                continue
            for q in j["quotes"]:
                kanitlar.append({"model": etiket, "url": tur_url.get("home", ""), "alinti": q})

        rec = {
            "domain": domain,
            "legal_name": legal,
            "display_name": display_name(legal) or (s["pool_name"] if not celisik else ""),
            "activity_de": (a or {}).get("activity_de", "") or (b or {}).get("activity_de", ""),
            "size_hint": (a or {}).get("size_hint", "") if (a or {}).get("size_hint", "") != "unbekannt" else "",
            "agreement": agreement,
            "professions": [{
                "meslek_kaydi": meslek,
                "karar": karar,
                "guven": "yuksek" if agreement == "both" and karar == "evet" else
                         ("orta" if agreement == "both" else "dusuk"),
                "kanit": kanitlar,
                "modeller": {
                    MODEL_A: {"decision": (a or {}).get("decision", ""), "evidence_type": (a or {}).get("evidence_type", ""),
                              "quotes_verified": bool((a or {}).get("quotes_verified")), "reason": (a or {}).get("reason", "")},
                    MODEL_B: {"decision": (b or {}).get("decision", ""), "evidence_type": (b or {}).get("evidence_type", ""),
                              "quotes_verified": bool((b or {}).get("quotes_verified")), "reason": (b or {}).get("reason", "")},
                },
            }],
            "job_ads": job_ads_cikar(read_page_text(domain, "karriere"), tur_url.get("karriere", "")),
            "models": [MODEL_A, MODEL_B],
            "pages": [{"tur": p["tur"], "url": p["url"], "chars": p["chars"], "rendered": bool(p["rendered"])}
                      for p in sayfalar],
            "prompt_version": PROMPT_VERSION,
            "havuz_adi_atildi": celisik,
            "city": s["city"], "sector": s["sector"], "email": s["email"],
        }
        save_verified(conn, rec)
        set_status(conn, domain, "tamam", f"{karar}/{agreement}")
        ozet["islenen"] += 1
        ozet[karar] = ozet.get(karar, 0) + 1
        ozet["agreement"][agreement] += 1
    conn.commit()
    conn.close()
    return ozet


def disa_aktar(yol) -> int:
    """Doğrulanmis kayitlari JSONL'e yazar (VPS'e gidecek bicim)."""
    conn = db()
    satirlar = conn.execute("SELECT * FROM verified ORDER BY domain").fetchall()
    n = 0
    with open(yol, "w", encoding="utf-8") as f:
        for s in satirlar:
            rec = {
                "domain": s["domain"], "legal_name": s["legal_name"], "display_name": s["display_name"],
                "activity_de": s["activity_de"], "size_hint": s["size_hint"], "agreement": s["agreement"],
                "professions": json.loads(s["professions_json"] or "[]"),
                "job_ads": json.loads(s["job_ads_json"] or "[]"),
                "models": json.loads(s["models_json"] or "[]"),
                "pages": json.loads(s["pages_json"] or "[]"),
                "prompt_version": s["prompt_version"], "verified_at": s["verified_at"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    conn.close()
    return n
