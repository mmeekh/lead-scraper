#!/usr/bin/env python3
"""Discover Germany-based companies without a city allow-list.

This mirrors the Netherlands nationwide discovery pass, but stays entirely
within Germany's administrative boundary. Discovery is deliberately broad;
only deep HTTPS evidence and CV-fit checks can hand a contact to outreach.
"""
from __future__ import annotations

import time

import requests

from scrape import EMAIL_RE, OSM_ENDPOINTS, OSM_UA, add_lead, clean_emails, db, lead_key

QUERIES = (
    ("finance", '''
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["website"](area.de);
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["contact:website"](area.de);
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["email"](area.de);
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["contact:email"](area.de);
'''),
    ("technology", '''
  nwr["craft"~"^(software|electronics|financial_advice)$"]["website"](area.de);
  nwr["craft"~"^(software|electronics|financial_advice)$"]["contact:website"](area.de);
  nwr["office"~"^(it|company|research)$"]["website"](area.de);
  nwr["office"~"^(it|company|research)$"]["contact:website"](area.de);
'''),
)


def fetch(selector: str) -> list[dict] | None:
    query = f'''[out:json][timeout:150];
area["ISO3166-1"="DE"]["admin_level"="2"]->.de;
(
{selector}
);
out center tags;'''
    for endpoint in OSM_ENDPOINTS:
        try:
            response = requests.post(endpoint, data={"data": query},
                                     headers={"User-Agent": OSM_UA}, timeout=170)
            if response.status_code == 200:
                return response.json().get("elements", [])
            if response.status_code in {429, 504}:
                time.sleep(15)
        except requests.RequestException:
            continue
    return None


def main() -> None:
    conn = db()
    total_new = total_direct = 0
    for label, selector in QUERIES:
        elements = fetch(selector)
        if elements is None:
            print(f"{label}: no response", flush=True)
            continue
        new = direct = 0
        for item in elements:
            tags = item.get("tags", {})
            website = tags.get("website") or tags.get("contact:website") or ""
            email = (tags.get("email") or tags.get("contact:email") or "").strip()
            domain = lead_key(website, email)
            if not domain:
                continue
            city = tags.get("addr:city") or "Germany"
            if add_lead(conn, domain, tags.get("name", ""), city, "DE", f"osm-de-nationwide:{label}"):
                new += 1
            direct_emails = clean_emails({email}, domain) if EMAIL_RE.fullmatch(email) else []
            if direct_emails:
                conn.execute(
                    "UPDATE leads SET status='done', email=?, note='DE country-wide OSM tag', "
                    "checked_at=datetime('now') WHERE domain=? AND status='pending'",
                    (direct_emails[0], domain),
                )
                direct += 1
        conn.commit()
        total_new += new
        total_direct += direct
        print(f"{label}: {len(elements)} raw, {new} new, {direct} tagged addresses", flush=True)
        time.sleep(10)
    conn.close()
    print(f"DONE: {total_new} new candidates, {total_direct} direct addresses", flush=True)


if __name__ == "__main__":
    main()
