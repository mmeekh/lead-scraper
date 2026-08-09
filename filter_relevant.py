#!/usr/bin/env python3
"""Export edilen adaylardan SADECE profile uygun olanlari secer.

OSM'nin office=financial/employment_agency etiketi genis: koclik, terapi,
muhendislik gibi alakasiz kayitlar da geliyor. Bu script firma adi, alan adi
ve e-postadan muhasebe/vergi/finans/ise alim sinyali arar; sinyal yoksa
disarida birakir. Amac: alakasiz firmaya basvuru gonderip ciddiyetsiz
gorunmemek.

Kullanim: python3 filter_relevant.py <girdi.csv> <cikti.csv>
"""
from __future__ import annotations

import csv
import sys

FINANS = (
    # muhasebe / vergi / denetim
    "account", "tax", "audit", "financ", "fiscaal", "fiscal", "bookkeep", "payroll",
    "steuer", "stb-", "buchhalt", "wirtschaftspr", "lohn", "kanzlei", "treuhand",
    "revision", "revisor", "administratie", "boekhoud", "belasting", "accountan",
    "rachunk", "ksieg", "księg", "podatk", "comptab", "contabil", "conta",
    "commercialista", "asesor", "gestor", "konyvel", "könyvel", "fokonyv", "főkönyv",
    "adó", "ucetn", "účetn", "danov", "daňov", "raamatupid", "gramatved", "buhalter",
    "kirjanpito", "regnskab", "bilan", "cpa", "acca",
    # finans / yatirim / sigorta
    "fin", "capital", "invest", "fonds", "fund", "asset", "wealth", "money", "valor",
    "banc", "bank", "credit", "kredit", "insur", "versicher", "assur", "pension",
    "treasur", "controll", "budget", "econom", "hypothe", "incasso", "debiteur",
    # danismanlik
    "consult", "advies", "adviseur", "advisory", "beratung", "berater", "expert",
)
ISE_ALIM = (
    "recruit", "personal", "staffing", "talent", "human resource", "career",
    "arbeit", "uitzend", "werving", "praca", "kadr", "employ", "hiring", "staffmill",
)
# bariz alakasiz alanlar (OSM etiket hatasi) - sinyal olsa bile ele
GURULTU = (
    "coaching", "hypnose", "therap", "ernährung", "wund", "lern-", "fitness",
    "yoga", "massage", "kosmetik", "friseur", "restaurant", "immobilien",
    "ingenieur", "architek", "bau ", "baufi", "reise", "travel", "auto",
)


def uygun(row: dict) -> bool:
    blob = f"{row.get('firma','')} {row.get('site','')} {row.get('email','')}".lower()
    if any(k in blob for k in GURULTU):
        return False
    return any(k in blob for k in FINANS) or any(k in blob for k in ISE_ALIM)


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    kept = [r for r in rows if uygun(r)]

    with open(dst, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(kept)

    print(f"{len(rows)} adaydan {len(kept)} tanesi profile uygun "
          f"({len(rows)-len(kept)} elendi) -> {dst}")


if __name__ == "__main__":
    main()
