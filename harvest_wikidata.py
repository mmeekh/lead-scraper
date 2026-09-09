#!/usr/bin/env python3
"""Discover public company websites from Wikidata, then leave verification to the crawler.

This is a discovery-only source: a company is added only when Wikidata
publishes both a head-office country and an HTTPS/HTTP corporate website. It
never treats Wikidata as evidence for an email address; ``deep_enrich.py``
must still find that address on the company's own HTTPS pages.
"""
from __future__ import annotations

import argparse
import re
import time
from urllib.parse import urlparse

import requests

from scrape import add_lead, db, lead_key

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "EminJobResearch/1.0 (public-company-contact-research)"
COUNTRY_QIDS = {"IE": "Q27", "NL": "Q55", "PL": "Q36", "LU": "Q32", "MT": "Q233",
                # 6 Eyl 2026 kampanya genislemesi
                "GB": "Q145", "PT": "Q45", "FI": "Q33", "SE": "Q34", "NO": "Q20"}
BLOCKED_HOSTS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "wikidata.org", "google.com", "apple.com",
)


def allowed_website(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and bool(host) and not any(
        host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS
    )


def query(country_qid: str, limit: int) -> str:
    return f'''SELECT DISTINCT ?itemLabel ?website WHERE {{
      ?item wdt:P159 ?headOffice ; wdt:P856 ?website .
      ?headOffice wdt:P17 wd:{country_qid} .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} LIMIT {limit}'''


def harvest(code: str, limit: int, source_label: str) -> tuple[int, int]:
    response = requests.get(
        ENDPOINT, params={"query": query(COUNTRY_QIDS[code], limit), "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=120,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    conn = db()
    added = skipped = 0
    try:
        for item in bindings:
            website = item.get("website", {}).get("value", "")
            name = item.get("itemLabel", {}).get("value", "").strip()
            # Etiketi olmayan ogeler icin Wikidata etiket yerine kimligi (Q123456)
            # dondurur. 9 Eyl 2026: 30 mail "Dear Q9375345" diye gitti.
            if re.fullmatch(r"Q\d{4,}", name):
                continue
            if not name or not allowed_website(website):
                skipped += 1
                continue
            domain = lead_key(website, "")
            if not domain:
                skipped += 1
                continue
            if add_lead(conn, domain, name, code, code, f"{source_label}:wikidata-{code.lower()}"):
                added += 1
        conn.commit()
    finally:
        conn.close()
    return added, len(bindings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", required=True)
    parser.add_argument("--new-per-country", type=int, default=4000)
    parser.add_argument("--source-label", required=True)
    args = parser.parse_args()
    countries = [code.strip().upper() for code in args.countries.split(",") if code.strip()]
    unsupported = sorted(set(countries) - set(COUNTRY_QIDS))
    if unsupported:
        raise ValueError(f"unsupported country codes: {', '.join(unsupported)}")
    for index, code in enumerate(countries):
        try:
            added, considered = harvest(code, max(1, args.new_per_country), args.source_label)
            print(f"{code}: Wikidata raw={considered} new={added}", flush=True)
        except requests.RequestException as exc:
            print(f"{code}: Wikidata unavailable ({type(exc).__name__})", flush=True)
        if index + 1 < len(countries):
            # Be a courteous public-data client; one request per country.
            time.sleep(2)


if __name__ == "__main__":
    main()
