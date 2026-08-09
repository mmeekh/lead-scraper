#!/usr/bin/env python3
"""Verify a company list that came from an untrusted source.

For every row it looks for the claimed email on the company's own website.
Rows whose address cannot be found are dropped, which keeps pattern-guessed
addresses (info@brand.tld) out of the dataset. Big4 and ATS-only employers
are filtered out as well.

Usage: python3 verify_list.py [incoming.csv] [verified.csv]
Input columns: country, company, email, website, source_url
"""
from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

from scrape import CFEMAIL_RE, CONTACT_PATHS, EMAIL_RE, HEADERS, cf_decode

IN_CSV = sys.argv[1] if len(sys.argv) > 1 else "incoming-list.csv"
OUT_CSV = sys.argv[2] if len(sys.argv) > 2 else "verified-list.csv"

# Big4 ve sadece kendi ATS portalindan basvuru alan devler - kullanici kurali
BANNED = ("pwc", "deloitte", "kpmg", "ernst", "@ey.com", "adecco", "randstad",
          "manpower", "hays", "robert half", "roberthalf", "michael page",
          "michaelpage", "accenture", "capgemini", "genpact", "tmf group",
          "alter domus", "alterdomus", "apex group", "concentrix",
          "teleperformance", "infosys", "wipro")


def is_banned(row: dict) -> bool:
    blob = f"{row['company']} {row['email']} {row['website']}".lower()
    return any(token in blob for token in BANNED)


def verify(row: dict) -> tuple[dict, str]:
    """E-posta firmanin sitesinde geciyor mu?"""
    target = row["email"].strip().lower()
    base = (row["website"] or "").strip()
    if not base:
        return row, "site-yok"
    if "://" not in base:
        base = "https://" + base

    urls = [row["source_url"]] if row.get("source_url") else []
    urls += [urljoin(base, path) for path in CONTACT_PATHS[:10]]

    session = requests.Session()
    session.headers.update(HEADERS)
    reachable = False
    try:
        for url in urls:
            if not url:
                continue
            try:
                resp = session.get(url, timeout=10, allow_redirects=True)
            except Exception:
                continue
            if resp.status_code >= 400:
                continue
            reachable = True
            html = resp.text
            found = {m.lower() for m in EMAIL_RE.findall(html)}
            for hexstr in CFEMAIL_RE.findall(html):
                decoded = cf_decode(hexstr)
                if "@" in decoded:
                    found.add(decoded.lower())
            if target in found:
                return row, "DOGRULANDI"
    finally:
        session.close()
    return row, ("sitede-yok" if reachable else "erisilemedi")


def main() -> None:
    rows = list(csv.DictReader(open(IN_CSV, encoding="utf-8")))
    banned = [r for r in rows if is_banned(r)]
    candidates = [r for r in rows if not is_banned(r)]
    print(f"toplam {len(rows)} | Big4/ATS elenen {len(banned)} | dogrulanacak {len(candidates)}",
          flush=True)

    verified, stats = [], {"DOGRULANDI": 0, "sitede-yok": 0, "erisilemedi": 0, "site-yok": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(verify, r) for r in candidates]
        for future in as_completed(futures):
            row, status = future.result()
            stats[status] = stats.get(status, 0) + 1
            done += 1
            if status == "DOGRULANDI":
                verified.append(row)
                print(f"  [{done}/{len(candidates)}] OK {row['email']}", flush=True)
            elif done % 20 == 0:
                print(f"  [{done}/{len(candidates)}] ...", flush=True)

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(verified)

    print(f"\nSONUC: {len(verified)} dogrulandi -> {OUT_CSV}")
    print("  dagilim:", stats)


if __name__ == "__main__":
    main()
