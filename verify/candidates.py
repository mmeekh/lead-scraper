"""Aday alan adlari: scraper'in kendi verisinden (JSONL) meslek grubuna gore secer.

Kaynak varsayilani: ayni makinedeki OSM tabanli sirket verisi (company/city/sector/website/email).
Kisisel veri yok - yalniz sirket alan adi, adi, sehri, sektoru.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from urllib.parse import urlsplit

from .config import MESLEKLER_PATH
from .db import db, upsert_domain

VARSAYILAN_KAYNAK = Path(r"C:\Projects\Company finder\scraper\data\toplu-hazir.jsonl")

# meslek kaydi -> hangi sektorden aday alinir + bu turda kac tane
GRUPLAR = [
    ("berufskraftfahrer", "Otomotiv & Lojistik", 35),
    ("elektroingenieur", "Sanayi & Mühendislik", 35),
    ("marketing_manager", "Medya, Kültür & Etkinlik", 30),
]
SEHIRLER = ("Hamburg", "München", "Köln")

# platform/portal alan adlari - sirketin kendi sitesi degil
ATLA = ("facebook.", "instagram.", "linkedin.", "google.", "wixsite.", "jimdo",
        "business.site", "sites.google", "myshopify.", "ebay.", "amazon.")


def host_of(url: str) -> str:
    try:
        h = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def meslekler() -> dict[str, dict]:
    data = json.loads(MESLEKLER_PATH.read_text(encoding="utf-8"))
    return {m["id"]: m for m in data["meslekler"]}


def sec(kaynak: Path, limit_toplam: int = 100, seed: int = 20260919) -> list[dict]:
    """Sektor+sehir kotasina gore aday secer (her grup icin ayri kota)."""
    havuz: dict[str, list[dict]] = {g[1]: [] for g in GRUPLAR}
    with kaynak.open(encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            r = json.loads(satir)
            if r.get("sector") not in havuz or r.get("city") not in SEHIRLER:
                continue
            if not r.get("email") or not r.get("website"):
                continue          # e-postali kayitlar (VPS havuzunun hedef kitlesi)
            h = host_of(r["website"])
            if not h or any(a in h for a in ATLA):
                continue
            havuz[r["sector"]].append({
                "domain": h, "pool_name": r.get("company", ""), "city": r.get("city", ""),
                "sector": r.get("sector", ""), "email": r.get("email", ""),
            })

    rnd = random.Random(seed)
    secilen: list[dict] = []
    gorulen: set[str] = set()
    for meslek_id, sektor, kota in GRUPLAR:
        aday = havuz.get(sektor, [])
        rnd.shuffle(aday)
        # sehirler arasi dengeli dagitim
        per_city = {s: 0 for s in SEHIRLER}
        hedef_city = max(1, kota // len(SEHIRLER))
        alinan = 0
        for tur in (1, 2):   # 1: sehir kotasina uy, 2: kalani doldur
            for a in aday:
                if alinan >= kota or len(secilen) >= limit_toplam:
                    break
                if a["domain"] in gorulen:
                    continue
                if tur == 1 and per_city.get(a["city"], 0) >= hedef_city:
                    continue
                gorulen.add(a["domain"])
                per_city[a["city"]] = per_city.get(a["city"], 0) + 1
                secilen.append({**a, "meslek": meslek_id})
                alinan += 1
            if alinan >= kota:
                break
    return secilen[:limit_toplam]


def yukle(kaynak: Path | None = None, limit: int = 100) -> dict:
    kaynak = kaynak or VARSAYILAN_KAYNAK
    if not kaynak.exists():
        raise SystemExit(f"aday kaynagi yok: {kaynak}")
    secilen = sec(kaynak, limit_toplam=limit)
    conn = db()
    for a in secilen:
        upsert_domain(conn, a["domain"], pool_name=a["pool_name"], city=a["city"],
                      sector=a["sector"], meslek=a["meslek"], email=a["email"])
    conn.commit()
    ozet: dict = {"toplam": len(secilen), "meslek": {}, "sehir": {}}
    for a in secilen:
        ozet["meslek"][a["meslek"]] = ozet["meslek"].get(a["meslek"], 0) + 1
        ozet["sehir"][a["city"]] = ozet["sehir"].get(a["city"], 0) + 1
    conn.close()
    return ozet
