#!/usr/bin/env python3
"""Dogrulanmis listeyi kampanya CSV formatina cevirir (mukerrersiz).

Cikti dosyasi elle incelenip firmalar.csv'ye eklenir. Bu script kampanya
dosyasina KENDISI yazmaz - once dogrulama yapilsin diye ayri tutuldu.
"""
from __future__ import annotations

import csv
from pathlib import Path

from scrape import CAMPAIGN_CSV, CAMPAIGN_LOG, FREEMAIL, campaign_country, norm_domain

BASE = Path("/root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper")
IN_CSV = BASE / "dogrulanan-liste.csv"
OUT_CSV = BASE / "eklenecek-firmalar.csv"

COUNTRY_CODE = {
    "Estonia": "EE", "Latvia": "LV", "Lithuania": "LT", "Bulgaria": "BG",
    "Romania": "RO", "Czech Republic": "CZ", "Slovakia": "SK", "Hungary": "HU",
    "Croatia": "HR", "Slovenia": "SI", "Portugal": "PT", "Spain": "ES",
    "Italy": "IT", "Greece": "GR", "Malta": "MT", "Sweden": "SE",
    "Finland": "FI", "Luxembourg": "LU", "Belgium": "BE", "Thailand": "TH",
    "Vietnam": "VN", "Global": "EU",
}

# sehir bilgisi yok; notlardan cikarmak yerine ulke adi kullanilir (sablon
# bos sehri ulke adiyla dolduruyor)


def main() -> None:
    known_mails: set[str] = set()
    known_orgs: set[str] = set()

    for path in (CAMPAIGN_LOG, CAMPAIGN_CSV):
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                mail = (row.get("email") or "").strip().lower()
                if mail:
                    known_mails.add(mail)
                if path is CAMPAIGN_CSV:
                    site = (row.get("site") or "").strip().casefold()
                    if site in {"", "-", "n/a", "none"}:
                        known_orgs.add(f"{campaign_country(row)}:email:{mail}")
                    else:
                        known_orgs.add(f"{campaign_country(row)}:{norm_domain(site)}")

    rows = list(csv.DictReader(open(IN_CSV, encoding="utf-8")))
    fresh: list[list[str]] = []
    skipped = {"mail": 0, "kurum": 0, "ulke-kodu-yok": 0}

    for row in rows:
        code = COUNTRY_CODE.get(row["country"].strip())
        if not code:
            skipped["ulke-kodu-yok"] += 1
            continue
        mail = row["email"].strip().lower()
        domain = norm_domain(row["website"])
        site_value = "" if (not domain or domain in FREEMAIL) else domain
        org = f"{code}:{site_value}" if site_value else f"{code}:email:{mail}"

        if mail in known_mails:
            skipped["mail"] += 1
            continue
        if org in known_orgs:
            skipped["kurum"] += 1
            continue
        known_mails.add(mail)
        known_orgs.add(org)

        note_parts = [row.get("contact_type", ""), row.get("notes", "")]
        note = " - ".join(part for part in note_parts if part)[:110]
        fresh.append([f"{code}-EN", row["company"].strip(), row["country"].strip(),
                      mail, site_value, note])

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["oncelik", "firma", "sehir", "email", "site", "dil_notu"])
        writer.writerows(fresh)

    print(f"{len(fresh)} satir hazir -> {OUT_CSV}")
    print("  elenen:", skipped)


if __name__ == "__main__":
    main()
