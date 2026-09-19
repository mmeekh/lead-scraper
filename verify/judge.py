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

SISTEM_A = (
    "Du bist ein Recruiting-Analyst, der deutsche Firmen-Websites liest. "
    "Stuetze dich AUSSCHLIESSLICH auf den gegebenen Text; rate nicht und nutze kein Weltwissen ueber die Firma. "
    "Frage: Ist es plausibel, dass diese Firma Menschen in diesem Beruf IM EIGENEN HAUS beschaeftigt? "
    "Eine Agentur oder ein Dienstleister, der diese Leistung selbst erbringt, beschaeftigt den Beruf "
    "(Werbeagentur -> Marketing). Eine Firma, die das Produkt nur verkauft oder vermittelt, ist weder "
    "Hersteller noch Anwender. Ein Branchenwort allein ist KEIN Beweis. "
    "Zitate kopierst du WORTWOERTLICH aus dem Text. Wenn der Text nicht reicht: decision = unclear. "
    "Antworte nur mit JSON nach dem Schema."
)

SISTEM_B = (
    "Du pruefst Belege. Arbeitsweise, in dieser Reihenfolge: "
    "(1) Fasse in einem Satz zusammen, was die Firma laut Text TUT. "
    "(2) Pruefe, ob der Text zeigt, dass diese Taetigkeit eigenes Personal in dem genannten Beruf erfordert "
    "oder eine Stellenanzeige dafuer existiert. "
    "(3) Entscheide. Nur der Text zaehlt, kein Vorwissen, keine Vermutung. "
    "Handel/Vermittlung/Verkauf eines Produkts beweist NICHT, dass die Firma den Beruf ausuebt; "
    "ein Dienstleister, der die Leistung selbst erbringt, beschaeftigt den Beruf sehr wohl. "
    "Im Zweifel: unclear. Belege sind woertliche Zitate aus dem Text. Ausgabe: nur JSON nach Schema."
)


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


def kullanici_istemi_a(meslek: dict, ad_adaylari: list[str], kanit: str) -> str:
    return (
        f"BERUF: {meslek['name_de']}\n"
        f"DEFINITION: {meslek['definition_de']}\n"
        f"SYNONYME: {', '.join(meslek['synonyms_de'])}\n"
        f"BESCHAEFTIGT DIESEN BERUF typischerweise: {meslek['employs_yes']}\n"
        f"BESCHAEFTIGT IHN NICHT: {meslek['employs_no']}\n\n"
        f"FIRMENNAME (Kandidaten, koennen falsch sein): {' | '.join(a for a in ad_adaylari if a) or 'unbekannt'}\n\n"
        f"SEITEN DER FIRMA:\n{kanit}\n\n"
        "Aufgabe: Entscheide (yes/no/unclear), ob diese Firma den genannten Beruf im eigenen Haus beschaeftigt. "
        "evidence_type: job_ad = konkrete Stellenanzeige, own_function = Text zeigt eigene Funktion/Abteilung/"
        "Ausstattung, activity = Taetigkeit erfordert den Beruf zwingend, none = kein Beleg. "
        "legal_name: die vollstaendige Firmierung aus dem Impressum (mit GmbH/AG/e.K. usw.), sonst \"\". "
        "activity_summary: 1-2 Saetze, was die Firma macht. size_hint: Mitarbeiterzahl-Bereich oder unbekannt. "
        "quotes: bis zu 3 woertliche Belegstellen aus dem obigen Text."
    )


def kullanici_istemi_b(meslek: dict, ad_adaylari: list[str], kanit: str) -> str:
    return (
        f"MATERIAL (Auszuege der Firmenwebsite):\n{kanit}\n\n"
        f"--- Ende Material ---\n"
        f"Firmenname laut Verzeichnis (unsicher): {' | '.join(a for a in ad_adaylari if a) or 'unbekannt'}\n"
        f"ZU PRUEFENDER BERUF: {meslek['name_de']} — {meslek['definition_de']}\n"
        f"Andere Bezeichnungen: {', '.join(meslek['synonyms_de'])}\n"
        f"Solche Betriebe haben dieses Personal: {meslek['employs_yes']}\n"
        f"Solche Betriebe haben es nicht: {meslek['employs_no']}\n\n"
        "Beantworte anhand des Materials: Beschaeftigt dieser Betrieb selbst Personal in diesem Beruf? "
        "decision: yes nur bei klarem Beleg im Material, no wenn das Material dagegen spricht, sonst unclear. "
        "evidence_type: job_ad / own_function / activity / none. "
        "legal_name: Firmierung aus dem Impressum inkl. Rechtsform, sonst \"\". "
        "activity_summary: was der Betrieb laut Material tut (1-2 Saetze). "
        "size_hint: 1-9 / 10-49 / 50-249 / 250+ / unbekannt. "
        "quotes: hoechstens 3 exakte Textstellen aus dem Material, die deine Entscheidung tragen."
    )


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
    kullanici = (kullanici_istemi_a if rol == "A" else kullanici_istemi_b)(meslek, ad_adaylari, kanit)
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
