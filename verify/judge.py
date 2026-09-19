"""Hakem katmani: iki bagimsiz yerel model, ayri istemler, ayni JSON semasi.

Ucretli/bulut API yok - yalniz yerel Ollama. Modeller SIRAYLA yuklenir
(OLLAMA_MAX_LOADED_MODELS=1), GPU payi %80 (OLLAMA_GPU_OVERHEAD).
"""
from __future__ import annotations

import json
import re
import time

import requests

from .config import (EVIDENCE_CHARS, EVIDENCE_PER_PAGE, MODEL_TIMEOUT_S, NUM_CTX,
                     OLLAMA_URL, PROMPT_VERSION)
from .db import read_page_text
from .istemler import SISTEM_A, SISTEM_B, kullanici_a, kullanici_b

SEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "evidence_type": {"type": "string", "enum": ["job_ad", "own_function", "activity", "none"]},
        "quotes": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "reason": {"type": "string"},
        "legal_name": {"type": "string"},
        "activity_summary": {"type": "string"},
        "size_hint": {"type": "string", "enum": ["1-9", "10-49", "50-249", "250+", "unbekannt"]},
        "red_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "evidence_type", "quotes", "reason", "legal_name", "activity_summary"],
}

def kanit_metni(conn, domain: str, sayfalar) -> tuple[str, dict[str, str]]:
    """Sayfa turlerine gore paylastirilmis, toplamda EVIDENCE_CHARS'i asmayan kanit."""
    parcalar: list[str] = []
    ham: dict[str, str] = {}
    toplam = 0
    sirali = sorted(sayfalar, key=lambda p: ("home", "karriere", "leistungen", "ueber", "impressum",
                                             "kontakt").index(p["tur"]) if p["tur"] in
                    ("home", "karriere", "leistungen", "ueber", "impressum", "kontakt") else 9)
    for p in sirali:
        metin = read_page_text(domain, p["tur"])
        if not metin:
            continue
        ham[p["tur"]] = metin
        pay = min(EVIDENCE_PER_PAGE.get(p["tur"], 2500), max(0, EVIDENCE_CHARS - toplam))
        if pay < 200:
            continue
        kirpik = metin[:pay]
        toplam += len(kirpik)
        parcalar.append(f"### SEITE [{p['tur']}] {p['url']}\n{kirpik}")
    return "\n\n".join(parcalar), ham


def _normalize(s: str) -> str:
    s = s.lower().replace("­", "")
    s = s.replace("„", '"').replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip(" .,:;!?-\"'()")


def alintilari_dogrula(quotes: list[str], ham_sayfalar: dict[str, str]) -> tuple[bool, list[bool]]:
    """Alintilar sayfa metninde birebir (bosluk/noktalama normalize) geciyor mu?"""
    havuz = _normalize(" \n ".join(ham_sayfalar.values()))
    tekil: list[bool] = []
    for q in quotes:
        qn = _normalize(q or "")
        tekil.append(bool(qn) and len(qn) >= 12 and qn in havuz)
    return (bool(tekil) and all(tekil)), tekil


def sor(model: str, sistem: str, kullanici: str) -> tuple[dict, int]:
    """Ollama /api/chat, sema zorunlu JSON.

    Gemma 4 gibi 'dusunen' modeller cikti butcesini thinking'e harcayip content'i bos
    birakiyor; bu yuzden think=False gonderilir. Modeli desteklemeyen surumler icin
    tek seferlik geri donus var.
    """
    t0 = time.time()
    govde = {
        "model": model,
        "messages": [{"role": "system", "content": sistem}, {"role": "user", "content": kullanici}],
        "stream": False,
        "format": SEMA,
        "think": False,
        "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": 600, "top_p": 0.9},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=govde, timeout=MODEL_TIMEOUT_S)
    if r.status_code == 400 and "think" in r.text.lower():
        govde.pop("think")
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=govde, timeout=MODEL_TIMEOUT_S)
    r.raise_for_status()
    icerik = r.json().get("message", {}).get("content", "")
    ms = int((time.time() - t0) * 1000)
    try:
        return json.loads(icerik), ms
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", icerik, re.S)
        if m:
            try:
                return json.loads(m.group(0)), ms
            except json.JSONDecodeError:
                pass
        return {"decision": "unclear", "evidence_type": "none", "quotes": [],
                "reason": "model JSON uretemedi", "legal_name": "", "activity_summary": ""}, ms


def yargila(model: str, rol: str, meslek: dict, ad_adaylari: list[str], kanit: str,
            ham_sayfalar: dict[str, str]) -> dict:
    sistem = SISTEM_A if rol == "A" else SISTEM_B
    kullanici = (kullanici_a if rol == "A" else kullanici_b)(meslek, ad_adaylari, kanit)
    j, ms = sor(model, sistem, kullanici)
    quotes = [q for q in (j.get("quotes") or []) if isinstance(q, str)][:3]
    dogru, tekil = alintilari_dogrula(quotes, ham_sayfalar)
    j["quotes"] = quotes
    j["quotes_verified"] = dogru
    j["quote_flags"] = tekil
    j["latency_ms"] = ms
    j["prompt_version"] = f"{PROMPT_VERSION}-{rol}"
    if j.get("decision") not in ("yes", "no", "unclear"):
        j["decision"] = "unclear"
    return j


def model_hazir(model: str) -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=20)
        adlar = [m["name"] for m in r.json().get("models", [])]
        return any(a == model or a.startswith(model.split(":")[0] + ":") and a == model for a in adlar)
    except requests.RequestException:
        return False


def bosalt(model: str) -> None:
    """Modeli GPU'dan indir (sirayla yukleme icin)."""
    try:
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except requests.RequestException:
        pass
