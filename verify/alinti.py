"""Cumle duzeyi alinti dogrulama + tek seferlik alinti tekrari (20 Eyl 2026).

Neden: iki model de "yes" dedigi 50 ciftin 36'si, alinti birebir tutmadigi icin dusuyordu.
Dusen sey karar degil, kopyalama becerisi. Cozum: o ciftlerde modelden ayni karari sayfadan
ZEICHEN FUER ZEICHEN kopyalanmis cumleyle bir kez daha iste (tek ek cagri).
"""
from __future__ import annotations

import json
import re
import time

import requests

from .config import MODEL_TIMEOUT_S, NUM_CTX, OLLAMA_URL
from .judge import _normalize, alintilari_dogrula

# Cumle sinirlari: noktalama + bosluk + buyuk harf/rakam baslangici, ya da bos satir (paragraf).
# Tek satir sonu sinir DEGIL (innerText bir cumleyi <br>/blok sinirinda kirar).
# Kisaltmalar (bzw., z. B., Dipl.-Ing., Nr., e.V. ...) sinir DEGIL - bunlar alintiyi yanlis boluyordu.
SINIR = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9„\"(])|\n{2,}")
KISALTMA = re.compile(
    r"(?:\b(?:bzw|z\. ?b|ca|inkl|exkl|u\. ?a|str|nr|dr|prof|dipl|ing|e\. ?v|co|kg|ggf|evtl|mio|mrd|tel|fax|vgl|usw|etc|"
    r"o\. ?ä|d\. ?h|bspw|max|min|sog|geb|jh|abs|art|hrsg|st|fr|mo|di|mi|do|sa|so|jan|feb|mär|apr|jun|jul|aug|sep|okt|nov|dez)"
    r"|\b[A-Za-zÄÖÜäöü]|\d)\.$", re.I)


def cumlelere_ayir(metin: str) -> list[str]:
    parcalar, sonuc = SINIR.split(metin), []
    for parca in parcalar:
        if not parca or len(parca.strip()) <= 3:
            continue
        if sonuc and KISALTMA.search(sonuc[-1].rstrip()):
            sonuc[-1] = sonuc[-1].rstrip() + " " + parca.lstrip()
        else:
            sonuc.append(parca)
    return sonuc


def cumle_duzeyi_dogrula(quotes: list[str], ham_sayfalar: dict[str, str]) -> tuple[bool, list[bool]]:
    """Alinti sayfa metninde birebir gecmeli VE tek bir cumlenin icinde kalmali
    (cumle sinirini asan, iki parcayi birlestiren 'alinti'lar reddedilir)."""
    tam, tekil = alintilari_dogrula(quotes, ham_sayfalar)
    if not tam:
        return tam, tekil
    metin = " \n ".join(ham_sayfalar.values())
    cumleler = [_normalize(c) for c in cumlelere_ayir(metin)]
    sonuc = []
    for q in quotes:
        qn = _normalize(q)
        sonuc.append(len(qn) < 400 and any(qn in c for c in cumleler))
    return all(sonuc), sonuc


SEMA_ALINTI = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "quotes": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["decision", "quotes"],
}

SISTEM_TEKRAR = (
    "Du hast diese Firma bereits beurteilt. Deine Zitate liessen sich NICHT wortwoertlich im Text finden. "
    "Gib jetzt dieselbe Entscheidung erneut ab und liefere 1-3 Belegstellen, die du ZEICHEN FUER ZEICHEN "
    "aus dem Material kopierst - jeweils ein vollstaendiger Satz oder ein zusammenhaengender Satzteil, "
    "ohne Auslassungen, ohne Umformulierung, ohne Navigationsleisten. Wenn es keine solche Stelle gibt, "
    "die deine Entscheidung traegt: decision = unclear. Nur JSON nach Schema."
)


def alinti_tekrar(model: str, meslek: dict, kanit: str, ham_sayfalar: dict[str, str], onceki: dict) -> dict:
    """Ayni karari, birebir alintiyla bir kez daha iste. Karar degisirse yeni karar gecerli."""
    kullanici = (
        f"BERUF: {meslek['name_de']} - {meslek['definition_de']}\n"
        f"DEINE VORHERIGE ENTSCHEIDUNG: {onceki.get('decision')} ({onceki.get('evidence_type', '')}); "
        f"Begruendung: {(onceki.get('reason') or '')[:200]}\n\n"
        f"MATERIAL:\n{kanit}\n\n"
        "Kopiere die Belegstellen exakt aus dem MATERIAL."
    )
    t0 = time.time()
    govde = {
        "model": model,
        "messages": [{"role": "system", "content": SISTEM_TEKRAR}, {"role": "user", "content": kullanici}],
        "stream": False, "format": SEMA_ALINTI, "think": False,
        "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": 400},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=govde, timeout=MODEL_TIMEOUT_S)
    if r.status_code == 400 and "think" in r.text.lower():
        govde.pop("think")
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=govde, timeout=MODEL_TIMEOUT_S)
    r.raise_for_status()
    try:
        j = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    except json.JSONDecodeError:
        j = {}
    yeni = dict(onceki)
    yeni["decision"] = j.get("decision") if j.get("decision") in ("yes", "no", "unclear") else "unclear"
    yeni["quotes"] = [q for q in (j.get("quotes") or []) if isinstance(q, str)][:3]
    yeni["quotes_verified"], yeni["quote_flags"] = cumle_duzeyi_dogrula(yeni["quotes"], ham_sayfalar)
    yeni["alinti_tekrar"] = True
    yeni["latency_ms"] = int((time.time() - t0) * 1000)
    return yeni
