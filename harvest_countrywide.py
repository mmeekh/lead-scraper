#!/usr/bin/env python3
"""Country-wide OSM discovery for under-researched job markets.

One generic implementation replaces country-specific copy/paste. It discovers
at most ``--new-per-country`` unseen domains per country; later deep enrichment
must prove CV fit and a public HTTPS email source before outreach can see them.
"""
from __future__ import annotations

import argparse
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from scrape import EMAIL_RE, OSM_ENDPOINTS, OSM_UA, add_lead, clean_emails, db, lead_key

COUNTRY_NAMES = {
    "IE": "Ireland", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "SG": "Singapore", "AE": "United Arab Emirates", "NZ": "New Zealand",
    "QA": "Qatar", "MT": "Malta", "NL": "Netherlands", "DE": "Germany",
    "CH": "Switzerland", "SE": "Sweden", "DK": "Denmark", "LU": "Luxembourg",
    "NO": "Norway", "FI": "Finland", "PT": "Portugal", "PL": "Poland",
}

SELECTORS = '''
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["website"](area.country);
  nwr["office"~"^(accountant|tax_advisor|financial|financial_advisor|consulting|employment_agency)$"]["contact:website"](area.country);
  nwr["office"~"^(it|company|research)$"]["website"](area.country);
  nwr["office"~"^(it|company|research)$"]["contact:website"](area.country);
  nwr["craft"~"^(software|electronics|financial_advice)$"]["website"](area.country);
'''

# Big-city fallback keeps the requested city preference while avoiding
# expensive whole-country Overpass queries. These are discovery candidates;
# deep_enrich remains the mandatory CV/evidence gate.
MAJOR_CITIES = {
    "IE": ("Dublin", "Cork", "Galway", "Limerick", "Waterford"),
    "PL": ("Warszawa", "Kraków", "Wrocław", "Poznań", "Gdańsk", "Katowice", "Łódź"),
    "NL": ("Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven", "Groningen",
           "Tilburg", "Breda", "Arnhem", "Nijmegen"),
    "MT": ("Valletta", "Sliema", "Birkirkara", "San Ġiljan"),
    "LU": ("Luxembourg", "Esch-sur-Alzette"),
    # 6 Eyl 2026: yeni agirlikli ulkeler. Sehir listesi olmayan ulke butun
    # ulkeyi tek Overpass sorgusuyla taramak zorunda kaliyordu; bu hem yavas
    # hem de sik sik zaman asimina ugruyor.
    "GB": ("London", "Manchester", "Birmingham", "Leeds", "Glasgow",
           "Edinburgh", "Bristol", "Reading", "Nottingham"),
    "PT": ("Lisboa", "Porto", "Braga", "Coimbra", "Aveiro"),
    "FI": ("Helsinki", "Espoo", "Tampere", "Turku", "Vantaa", "Oulu"),
    "SE": ("Stockholm", "Göteborg", "Malmö", "Uppsala", "Linköping"),
    "NO": ("Oslo", "Bergen", "Trondheim", "Stavanger"),
}

BROAD_CITY_SELECTORS = '''
  nwr["office"]["website"](area.city);
  nwr["office"]["contact:website"](area.city);
  nwr["craft"~"^(software|electronics|financial_advice)$"]["website"](area.city);
'''


def fetch_query(query: str, timeout: int) -> list[dict] | None:
    for endpoint in OSM_ENDPOINTS:
        try:
            response = requests.post(endpoint, data={"data": query},
                                     headers={"User-Agent": OSM_UA}, timeout=timeout + 20)
            if response.status_code == 200:
                return response.json().get("elements", [])
            if response.status_code in {429, 504}:
                time.sleep(6)
        except requests.RequestException:
            continue
    return None


def fetch_country(code: str) -> tuple[str, list[dict] | None]:
    query = f'''[out:json][timeout:150];
area["ISO3166-1"="{code}"]["admin_level"="2"]->.country;
(
{SELECTORS}
);
out center tags;'''
    return code, fetch_query(query, 150)


def fetch_city(code: str, city: str) -> list[dict] | None:
    query = f'''[out:json][timeout:55];
area["ISO3166-1"="{code}"]["admin_level"="2"]->.country;
area["name"="{city}"](area.country)->.city;
(
{BROAD_CITY_SELECTORS}
);
out center tags;'''
    return fetch_query(query, 55)


def store_country(code: str, elements: list[dict], limit: int,
                  source_label: str) -> tuple[int, int]:
    conn = db()
    new = direct = 0
    try:
        for item in elements:
            tags = item.get("tags", {})
            website = tags.get("website") or tags.get("contact:website") or ""
            email = (tags.get("email") or tags.get("contact:email") or "").strip()
            domain = lead_key(website, email)
            if not domain:
                continue
            city = tags.get("addr:city") or COUNTRY_NAMES[code]
            inserted = add_lead(
                conn, domain, tags.get("name", ""), city, code,
                f"{source_label}:{code.lower()}"
            )
            if inserted:
                new += 1
            direct_emails = clean_emails({email}, domain) if EMAIL_RE.fullmatch(email) else []
            if direct_emails:
                conn.execute(
                    "UPDATE leads SET status='done', email=?, note='country-wide OSM tag', "
                    "checked_at=datetime('now') WHERE domain=? AND status='pending'",
                    (direct_emails[0], domain),
                )
                direct += 1
            if new >= limit:
                break
        conn.commit()
        return new, direct
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", required=True)
    parser.add_argument("--new-per-country", type=int, default=200)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--source-label", default="osm-countrywide",
                        help="bu turu izlemek icin kaynak etiketi")
    parser.add_argument("--broad-cities", action="store_true",
                        help="buyuk sehirlerde web sitesi olan tum ofisleri kesfet")
    args = parser.parse_args()
    codes = [code.strip().upper() for code in args.countries.split(",") if code.strip()]
    unknown = sorted(set(codes) - set(COUNTRY_NAMES))
    if unknown:
        raise ValueError(f"unsupported country codes: {','.join(unknown)}")
    print(f"country-wide discovery: {','.join(codes)}; target={args.new_per_country} new each", flush=True)
    # Poland's full-country query repeatedly times out. City queries are faster
    # and match the requested urban focus.
    country_codes = [code for code in codes if not (args.broad_cities and code == "PL")]
    added = Counter()
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = {pool.submit(fetch_country, code): code for code in country_codes}
        for future in as_completed(futures):
            code, elements = future.result()
            if elements is None:
                print(f"{code}: no OSM response", flush=True)
                continue
            new, direct = store_country(code, elements, args.new_per_country, args.source_label)
            added[code] += new
            print(f"{code}: raw={len(elements)} new={new} direct_tag={direct}", flush=True)

    if args.broad_cities:
        for code in codes:
            for city in MAJOR_CITIES.get(code, ()):
                if added[code] >= args.new_per_country:
                    break
                elements = fetch_city(code, city)
                if elements is None:
                    print(f"{code} {city}: no OSM response", flush=True)
                    continue
                new, direct = store_country(
                    code, elements, args.new_per_country - added[code], args.source_label)
                added[code] += new
                print(f"{code} {city}: raw={len(elements)} new={new} direct_tag={direct}", flush=True)


if __name__ == "__main__":
    main()
