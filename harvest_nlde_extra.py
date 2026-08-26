#!/usr/bin/env python3
"""harvest_all.py'nin ana sehir listesi NL ve DE icin zaten tarandi
(bkz. harvest.log). Bu, o listede olmayan ikincil sehirleri ekler -
Hollanda agirlikli (kullanici karari, 11 Agu 2026): erkend referent
sarti kucuk NL burolarinin cogunu elese de, sponsorluk statusu OSM'den
gorunmuyor - hacmi burada artirip filtrelemeyi indirme/gonderim
asamasinda yapiyoruz.

Ayni fetch_area/main mantigi harvest_all.py'den; sadece sehir listesi farkli.
"""
from __future__ import annotations

import argparse
import time

import requests

from scrape import OSM_ENDPOINTS, OSM_UA, add_lead, clean_emails, db, lead_key, EMAIL_RE

CITIES: list[tuple[str, list[str]]] = [
    # --- Hollanda: ana listede olmayan ikincil sehirler (agirlikli) ---
    ("NL", ["Rotterdam"]), ("NL", ["Haarlem"]), ("NL", ["Amstelveen"]),
    ("NL", ["Zwolle"]), ("NL", ["Leiden"]), ("NL", ["Amersfoort"]),
    ("NL", ["Maastricht"]), ("NL", ["Dordrecht"]), ("NL", ["Zaanstad"]),
    ("NL", ["Apeldoorn"]), ("NL", ["Enschede"]), ("NL", ["'s-Hertogenbosch", "Den Bosch"]),
    ("NL", ["Delft"]), ("NL", ["Alkmaar"]), ("NL", ["Hilversum"]),
    ("NL", ["Rijswijk"]), ("NL", ["Leeuwarden"]), ("NL", ["Zoetermeer"]),
    ("NL", ["Hoofddorp"]),
    # --- Almanya: ana listede olmayan ikincil sehirler (daha az agirlik) ---
    ("DE", ["Bremen"]), ("DE", ["Hannover"]), ("DE", ["Leipzig"]),
    ("DE", ["Dresden"]), ("DE", ["Wiesbaden"]), ("DE", ["Bonn"]),
    ("DE", ["Karlsruhe"]), ("DE", ["Münster"]), ("DE", ["Augsburg"]),
    ("DE", ["Mainz"]),
]

OFFICE_TYPES = "accountant|tax_advisor|financial|financial_advisor|employment_agency|consulting"


def fetch_area(area: str) -> list[dict] | None:
    query = f"""
[out:json][timeout:45];
area["name"="{area}"]->.a;
(
  node["office"~"^({OFFICE_TYPES})$"](area.a);
  way["office"~"^({OFFICE_TYPES})$"](area.a);
);
out center tags;
"""
    for endpoint in OSM_ENDPOINTS:
        try:
            resp = requests.post(endpoint, data={"data": query},
                                 headers={"User-Agent": OSM_UA}, timeout=50)
            if resp.status_code == 200:
                return resp.json().get("elements", [])
            if resp.status_code in (429, 504):
                time.sleep(10)
        except Exception:
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="virgullu ulke kodlari")
    parser.add_argument("--sleep", type=int, default=8, help="sorgular arasi saniye")
    args = parser.parse_args()

    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    targets = [(c, names) for c, names in CITIES if not wanted or c in wanted]
    print(f"{len(targets)} sehir taranacak (NL+DE ek liste)", flush=True)

    conn = db()
    grand_new = grand_mail = 0
    for index, (country, names) in enumerate(targets, 1):
        elements = None
        used = ""
        for name in names:
            elements = fetch_area(name)
            if elements:
                used = name
                break
            time.sleep(2)
        if not elements:
            print(f"[{index}/{len(targets)}] {country} {names[0]}: sonuc yok", flush=True)
            time.sleep(args.sleep)
            continue

        new = mails = 0
        for element in elements:
            tags = element.get("tags", {})
            site = tags.get("website") or tags.get("contact:website") or ""
            mail = (tags.get("email") or tags.get("contact:email") or "").strip()
            domain = lead_key(site, mail)
            if not domain:
                continue
            city = tags.get("addr:city") or used
            if add_lead(conn, domain, tags.get("name", ""), city, country, f"osm:{used}"):
                new += 1
            direct = clean_emails({mail}, domain) if mail and EMAIL_RE.fullmatch(mail) else []
            if direct:
                conn.execute(
                    "UPDATE leads SET status='done', email=?, note='OSM etiketinden', "
                    "checked_at=datetime('now') WHERE domain=? AND status='pending'",
                    (direct[0], domain))
                mails += 1
        conn.commit()
        grand_new += new
        grand_mail += mails
        print(f"[{index}/{len(targets)}] {country} {used}: {len(elements)} kayit, "
              f"{new} yeni aday, {mails} hazir mail", flush=True)
        time.sleep(args.sleep)

    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"\nBITTI: {grand_new} yeni aday, {grand_mail} hazir mail | havuz toplam: {total}")


if __name__ == "__main__":
    main()
