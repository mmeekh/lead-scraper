"""Impressum'dan tuzel ad (legal_name) cikarimi - deterministik katman.

Havuzdaki ad (Overture/OSM) guvenilmez; Alman ticari sitelerinde Impressum zorunlu
oldugu icin tuzel ad oradan okunur. Model de ad onerir; karsilastirma consensus'ta.
"""
from __future__ import annotations

import re

HUKUKI_BICIM = (
    r"GmbH\s*&\s*Co\.?\s*KG(?:aA)?", r"GmbH\s*&\s*Co\.?\s*OHG", r"gGmbH", r"GmbH", r"mbH",
    r"UG\s*\(haftungsbeschr[aä]nkt\)(?:\s*&\s*Co\.?\s*KG)?", r"AG\s*&\s*Co\.?\s*KG", r"\bAG\b",
    r"\bSE\b", r"\be\.?\s?K(?:fr)?\.?", r"\bOHG\b", r"\bKG\b", r"\be\.?\s?V\.?", r"\bGbR\b",
    r"\bPartG(?:mbB)?\b", r"\bLtd\.?\b", r"\bKGaA\b", r"\beG\b",
)
BICIM_RE = re.compile(r"(?:" + "|".join(HUKUKI_BICIM) + r")")
# Impressum'da adin yakinindaki gurultu satirlari
GURULTU = re.compile(
    r"^(impressum|angaben gem|kontakt|telefon|tel\.?|fax|e-?mail|mail|web|internet|"
    r"umsatzsteuer|ust|steuernummer|registergericht|handelsregister|hrb|hra|"
    r"vertreten durch|geschäftsführer|geschaeftsfuehrer|inhaber|verantwortlich|"
    r"redaktionell|datenschutz|haftung|adresse|anschrift|sitz der|postfach)\b", re.I)
ADRES = re.compile(r"\b\d{5}\b|\b(str(aße|asse|\.)|weg|platz|allee|gasse|ring|chaussee)\b", re.I)


def temizle(ad: str) -> str:
    ad = re.sub(r"\s+", " ", ad).strip(" -–—|:;,.\t")
    ad = re.sub(r"^(Firma|Fa\.)\s+", "", ad, flags=re.I)
    return ad.strip()


def legal_name_bul(impressum_metin: str) -> str:
    """Impressum metninden tuzel adi dondurur ('' = bulunamadi)."""
    if not impressum_metin:
        return ""
    satirlar = [s.strip() for s in impressum_metin.splitlines()]
    # 1) hukuki bicim iceren ilk makul satir
    for satir in satirlar[:80]:
        if not satir or len(satir) > 120 or GURULTU.match(satir):
            continue
        if ADRES.search(satir) and not BICIM_RE.search(satir):
            continue
        m = BICIM_RE.search(satir)
        if not m:
            continue
        # ad hukuki bicimde biter: sonrasini (adres, telefon, kisi) kes
        aday = temizle(satir[:m.end()])
        # "Musterfirma GmbH Musterstr. 1" gibi artiklari at
        if 3 <= len(aday) <= 100:
            return aday
    # 2) hukuki bicim yoksa: "Angaben gemäß § 5 TMG" sonrasi ilk dolu satir
    for i, satir in enumerate(satirlar[:60]):
        if re.search(r"angaben gem|diensteanbieter|anbieter:", satir, re.I):
            for sonraki in satirlar[i + 1:i + 5]:
                sonraki = sonraki.strip()
                if sonraki and not GURULTU.match(sonraki) and not ADRES.search(sonraki):
                    aday = temizle(sonraki)
                    if 3 <= len(aday) <= 100:
                        return aday
    return ""


def display_name(legal: str) -> str:
    """Mail hitabi icin kisa ad: hukuki eki ve noktalamayi at."""
    if not legal:
        return ""
    kisa = BICIM_RE.sub("", legal)
    kisa = re.sub(r"\s*[&,\-–]\s*$", "", re.sub(r"\s+", " ", kisa)).strip(" .,-–&")
    return kisa or legal


def ad_celisiyor_mu(legal: str, havuz_adi: str) -> bool:
    """Havuz adi tuzel adla ortusmuyorsa True (havuz adi atilir)."""
    if not legal or not havuz_adi:
        return False
    norm = lambda s: re.sub(r"[^a-z0-9]", "", display_name(s).lower())
    a, b = norm(legal), norm(havuz_adi)
    if not a or not b:
        return False
    return not (a in b or b in a or a[:6] == b[:6])
