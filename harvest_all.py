#!/usr/bin/env python3
"""Tum hedef ulkelerdeki sehirlerden aday firma topla (OSM Overpass).

OSM alan adlari yerel dilde; her sehir icin birden fazla isim varyanti denenir.
Overpass'a nazik olmak icin sorgular arasi bekleme var. Kesilirse tekrar
calistir - eklenen adaylar veritabaninda birikir, mukerrer olmaz.

Kullanim:
  python3 harvest_all.py                 # tum sehirler
  python3 harvest_all.py --only PL,CZ,HU # sadece bu ulkeler
"""
from __future__ import annotations

import argparse
import time

import requests

from scrape import OSM_ENDPOINTS, OSM_UA, add_lead, clean_emails, db, lead_key, EMAIL_RE

# (ulke kodu, [OSM alan adi varyantlari])  - yerel isim once
CITIES: list[tuple[str, list[str]]] = [
    # --- Orta / Dogu Avrupa: SSC-BPO merkezleri, Ingilizce calisilan finans ---
    ("BG", ["София", "Sofia"]), ("BG", ["Пловдив", "Plovdiv"]), ("BG", ["Варна", "Varna"]),
    ("RO", ["București", "Bucharest"]), ("RO", ["Cluj-Napoca"]), ("RO", ["Timișoara"]),
    ("RO", ["Iași"]), ("RO", ["Brașov"]),
    ("PL", ["Warszawa", "Warsaw"]), ("PL", ["Kraków"]), ("PL", ["Wrocław"]),
    ("PL", ["Poznań"]), ("PL", ["Gdańsk"]), ("PL", ["Katowice"]), ("PL", ["Łódź"]),
    ("CZ", ["Praha", "Prague"]), ("CZ", ["Brno"]), ("CZ", ["Ostrava"]),
    ("SK", ["Bratislava"]), ("SK", ["Košice"]),
    ("HU", ["Budapest"]), ("HU", ["Debrecen"]), ("HU", ["Szeged"]),
    ("HR", ["Zagreb"]), ("HR", ["Split"]),
    ("SI", ["Ljubljana"]), ("SI", ["Maribor"]),
    ("EE", ["Tallinn"]), ("EE", ["Tartu"]),
    ("LV", ["Rīga", "Riga"]),
    ("LT", ["Vilnius"]), ("LT", ["Kaunas"]),
    # --- Guney Avrupa ---
    ("PT", ["Lisboa", "Lisbon"]), ("PT", ["Porto"]), ("PT", ["Braga"]),
    ("ES", ["Madrid"]), ("ES", ["Barcelona"]), ("ES", ["València", "Valencia"]),
    ("ES", ["Málaga"]), ("ES", ["Sevilla"]),
    ("IT", ["Milano", "Milan"]), ("IT", ["Roma", "Rome"]), ("IT", ["Torino"]),
    ("IT", ["Bologna"]),
    ("GR", ["Αθήνα", "Athens"]), ("GR", ["Θεσσαλονίκη", "Thessaloniki"]),
    ("MT", ["Valletta"]), ("MT", ["Sliema"]), ("MT", ["Birkirkara"]), ("MT", ["San Ġiljan"]),
    # --- Bati / Kuzey Avrupa ---
    ("IE", ["Dublin"]), ("IE", ["Cork"]), ("IE", ["Galway"]), ("IE", ["Limerick"]),
    ("SE", ["Stockholm"]), ("SE", ["Göteborg", "Gothenburg"]), ("SE", ["Malmö"]),
    ("DK", ["København", "Copenhagen"]), ("DK", ["Aarhus"]),
    ("NO", ["Oslo"]), ("NO", ["Bergen"]),
    ("FI", ["Helsinki"]),
    ("LU", ["Luxembourg"]), ("LU", ["Esch-sur-Alzette"]),
    ("AT", ["Wien", "Vienna"]), ("AT", ["Graz"]),
    ("BE", ["Brussel", "Bruxelles", "Brussels"]), ("BE", ["Antwerpen"]),
    ("CH", ["Zürich"]), ("CH", ["Genève", "Geneva"]),
    # --- İngilizce ana çalışma dili olan, büyük şehirli hedef pazarlar ---
    # İngiltere için ISO kodu GB'dir. Küçük yerleşimler bilinçli olarak yok.
    ("GB", ["London"]), ("GB", ["Manchester"]), ("GB", ["Birmingham"]),
    ("GB", ["Edinburgh"]), ("GB", ["Glasgow"]), ("GB", ["Bristol"]),
    ("CA", ["Toronto"]), ("CA", ["Vancouver"]), ("CA", ["Calgary"]),
    ("CA", ["Ottawa"]),
    ("NZ", ["Auckland"]), ("NZ", ["Wellington"]), ("NZ", ["Christchurch"]),
    # --- Almanya (Turk nufusu yogun sehirler dahil) ---
    ("DE", ["Berlin"]), ("DE", ["Frankfurt am Main"]), ("DE", ["München"]),
    ("DE", ["Hamburg"]), ("DE", ["Düsseldorf"]), ("DE", ["Köln"]),
    ("DE", ["Stuttgart"]), ("DE", ["Essen"]), ("DE", ["Dortmund"]),
    ("DE", ["Duisburg"]), ("DE", ["Nürnberg"]), ("DE", ["Mannheim"]),
    # --- Hollanda (kampanyanin ana ulkesi, yeni sehirler) ---
    ("NL", ["Amsterdam"]), ("NL", ["Den Haag"]), ("NL", ["Utrecht"]),
    ("NL", ["Eindhoven"]), ("NL", ["Groningen"]), ("NL", ["Tilburg"]),
    ("NL", ["Almere"]), ("NL", ["Breda"]), ("NL", ["Nijmegen"]), ("NL", ["Arnhem"]),
    # --- Asya ---
    ("TH", ["กรุงเทพมหานคร", "Bangkok"]), ("TH", ["เชียงใหม่", "Chiang Mai"]),
    ("VN", ["Thành phố Hồ Chí Minh", "Ho Chi Minh City"]), ("VN", ["Hà Nội", "Hanoi"]),
]

OFFICE_TYPES = "accountant|tax_advisor|financial|financial_advisor|employment_agency|consulting"


def fetch_area(area: str, country: str = "", wide: bool = False) -> list[dict] | None:
    if wide:
        selectors = """
  nwr["office"]["website"](area.a);
  nwr["office"]["contact:website"](area.a);
  nwr["office"]["email"](area.a);
  nwr["office"]["contact:email"](area.a);
  nwr["craft"~"^(software|electronics|financial_advice)$"]["website"](area.a);
"""
    else:
        selectors = f"""
  nwr["office"~"^({OFFICE_TYPES})$"](area.a);
"""
    country_scope = (
        f'area["ISO3166-1"="{country}"]["admin_level"="2"]->.country;\n'
        f'area["name"="{area}"](area.country)->.a;'
        if country else f'area["name"="{area}"]->.a;'
    )
    query = f"""
[out:json][timeout:45];
{country_scope}
(
{selectors}
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
    parser.add_argument("--wide", action="store_true",
                        help="web sitesi/e-postasi olan tum ofisleri topla; derin CV filtresi sonra uygulanir")
    args = parser.parse_args()

    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    targets = [(c, names) for c, names in CITIES if not wanted or c in wanted]
    print(f"{len(targets)} sehir taranacak")

    conn = db()
    grand_new = grand_mail = 0
    for index, (country, names) in enumerate(targets, 1):
        elements = None
        used = ""
        for name in names:
            elements = fetch_area(name, country, args.wide)
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
