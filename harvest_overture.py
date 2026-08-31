#!/usr/bin/env python3
"""Overture Maps yerel veri setinden aday firma kesfi.

/root/projects/overture-data/places.sqlite3 dosyasini SALT OKUNUR acar,
finans/muhasebe/yazilim/danismanlik agirlikli kaliteli adaylari confidence
sirasina gore leads havuzuna ekler. Overture'daki email alani SADECE
anahtar uretiminde (freemail kurali) kullanilir; status pending kalir,
e-posta kanitini site tarayicisi (scrape.py emails) toplar.

Kullanim:
  # once havuzu gor (DB'ye yazmaz)
  python3 harvest_overture.py --countries DE,NL,IE --new-per-country 800 --dry-run

  # gercek ekleme (kampanya etiketi sart)
  python3 harvest_overture.py --countries DE,NL,IE --new-per-country 800 \
      --source-label visa-priority-20260831
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Iterator

from scrape import FREEMAIL, add_lead, db, lead_key

PLACES_DB = "/root/projects/overture-data/places.sqlite3"

# Sektor adlari DB'deki TAM yazimla (aksanli); veri degeridir, degistirme.
GOOD_SECTORS: tuple[str, ...] = (
    "Muhasebe & Vergi",
    "Finansal Teknoloji",
    "Danışmanlık",
    "Yazılım & SaaS",
    "ERP & Raporlama",
)

# Gercek kategori degerleri salt-okunur GROUP BY sorgusuyla dogrulandi.
# Kalite oncelikli secim: finans/muhasebe/yazilim/danismanlik/IT/
# muhendislik-hizmet. restoran/kuafor/magaza gibi alakasizlar YOK.
# bank_credit_union bilerek dahil: sube kayitlari cikabilir, deep_enrich eler.
GOOD_CATEGORIES: tuple[str, ...] = (
    # finans + muhasebe
    "accountant", "tax_services", "tax_law", "tax_office", "bookkeeper",
    "payroll_services", "financial_advising", "financial_service", "banks",
    "bank_credit_union", "credit_union", "business_banking_service",
    "business_financing", "investing",
    # yazilim + IT + veri
    "software_development", "information_technology_company", "it_consultant",
    "it_service_and_computer_repair", "web_designer", "e_commerce_service",
    "automation_services",
    # danismanlik + is hizmetleri
    "business_consulting", "business_management_services",
    "human_resource_services", "employment_agencies",
    "executive_search_consultants",
    # muhendislik hizmeti
    "engineering_services",
)

# Firma sitesi yerine platform/sosyal medya sayfasi verilmis kayitlar:
# bu hostlar lead domaini OLAMAZ (tam host veya alt-domain esleşmesi).
PLATFORM_HOSTS: frozenset[str] = frozenset({
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "youtu.be", "google.com", "wikipedia.org", "yelp.com",
    "tiktok.com", "pinterest.com",
})


def is_platform_host(key: str) -> bool:
    """Anahtar bir platform hostu mu? (e-posta anahtarlari muaf)"""
    if "@" in key:
        return False
    return any(key == host or key.endswith("." + host)
               for host in PLATFORM_HOSTS)


def places_ro(path: str = PLACES_DB) -> sqlite3.Connection:
    """Overture DB'sine SALT OKUNUR baglanti; yazma/PRAGMA degisikligi yasak."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def iter_candidates(places: sqlite3.Connection, country: str,
                    min_confidence: float) -> Iterator[sqlite3.Row]:
    """Kalite filtresinden gecen adaylari confidence DESC sirayla verir.

    Kural: kategori onayli listedeyse al; kategorisi BOS olan satiri ancak
    sektoru ilgiliyse kurtar. Boylece ilgili sektore dusmus ama kategorisi
    restoran/kuafor gibi alakasiz olan satirlar asla gecmez.
    """
    ph_cat = ",".join("?" * len(GOOD_CATEGORIES))
    ph_sec = ",".join("?" * len(GOOD_SECTORS))
    sql = (
        "SELECT name, email, website, domain, city, confidence, sector, category "
        "FROM places "
        "WHERE country = ? "
        "AND (COALESCE(domain, '') != '' OR COALESCE(website, '') != '') "
        "AND confidence >= ? "
        f"AND (category IN ({ph_cat}) "
        f"     OR (sector IN ({ph_sec}) AND category IS NULL)) "
        "ORDER BY confidence DESC"
    )
    yield from places.execute(
        sql, (country, min_confidence, *GOOD_CATEGORIES, *GOOD_SECTORS))


def harvest_country(places: sqlite3.Connection,
                    leads: sqlite3.Connection | None,
                    country: str, target: int, source_label: str,
                    min_confidence: float, dry_run: bool,
                    preview_limit: int = 10) -> dict:
    """Tek ulke icin kesif. dry_run ise leads baglantisina hic dokunmaz.

    Donen sozluk: eligible (tekil uygun aday), added (gercek insert),
    preview (ilk N aday: domain, ad, sektor, kategori, confidence).
    """
    seen: set[str] = set()
    preview: list[tuple[str, str, str, str, float]] = []
    eligible = added = 0
    if not dry_run and target <= 0:
        # hedef 0/negatif: tek insert bile yapilmaz (sinir denetimi insert'ten
        # once olmali; 31 Agu inceleme bulgusu)
        return {"eligible": 0, "added": 0, "preview": preview}
    for row in iter_candidates(places, country, min_confidence):
        site = (row["website"] or "").strip() or (row["domain"] or "").strip()
        # freemail kurali lead_key icinde: firma alan adi yoksa e-posta anahtar
        key = lead_key(site, (row["email"] or "").strip())
        if not key or key in FREEMAIL or is_platform_host(key):
            # bos/bozuk domain, e-postasiz ciplak freemail hostu
            # veya platform/sosyal medya hostu: atla
            continue
        if key in seen:
            continue
        seen.add(key)
        eligible += 1
        if len(preview) < preview_limit:
            preview.append((key, row["name"] or "", row["sector"] or "",
                            row["category"] or "", row["confidence"] or 0.0))
        if dry_run:
            continue
        assert leads is not None
        source = f"{source_label}:ovt-{country.lower()}"
        if add_lead(leads, key, row["name"] or "", row["city"] or "",
                    country, source):
            # SADECE gercekten insert olanlar sayilir (dedupe: rowcount==0 sayilmaz)
            added += 1
            if added >= target:
                break
    if not dry_run and leads is not None:
        leads.commit()
    return {"eligible": eligible, "added": added, "preview": preview}


def parse_countries(raw: str) -> list[str]:
    """Virgullu ulke listesini defansif ayristir (bosluk/kucuk harf tolere)."""
    return [code.strip().upper() for code in str(raw).split(",") if code.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--countries", default="DE,NL,IE",
                        help="virgullu ulke kodlari (ornek: DE,NL,IE)")
    parser.add_argument("--new-per-country", type=int, default=200,
                        help="ulke basina eklenecek YENI aday hedefi")
    parser.add_argument("--source-label", default="",
                        help="kaynak etiketi; DB'ye <etiket>:ovt-<ulke> yazilir")
    parser.add_argument("--min-confidence", type=float, default=0.5,
                        help="Overture confidence alt siniri")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB'ye yazmadan ulke basina ilk 10 adayi ve havuzu goster")
    args = parser.parse_args()

    countries = parse_countries(args.countries)
    if not countries:
        parser.error("ulke listesi bos")
    target = max(0, int(args.new_per_country))
    min_confidence = float(args.min_confidence)
    if not args.dry_run and not args.source_label.strip():
        parser.error("--source-label gerekli (dry-run disinda)")

    places = places_ro()
    leads = None if args.dry_run else db()
    total_added = 0
    try:
        for country in countries:
            stats = harvest_country(
                places, leads, country, target, args.source_label.strip(),
                min_confidence, args.dry_run)
            if args.dry_run:
                verdict = "YETERLI" if stats["eligible"] >= target else "YETERSIZ"
                print(f"{country}: uygun aday havuzu {stats['eligible']} | "
                      f"hedef {target} -> {verdict}")
                for i, (dom, name, sector, category, conf) in enumerate(
                        stats["preview"], start=1):
                    label = f"{sector}/{category}" if category else sector
                    print(f"  {i:2d}. {dom} | {name} | {label} | conf {conf:.2f}")
            else:
                print(f"{country}: {stats['added']} yeni aday eklendi "
                      f"(hedef {target}, kaynak "
                      f"{args.source_label.strip()}:ovt-{country.lower()})")
                total_added += stats["added"]
        if not args.dry_run:
            print(f"toplam: {total_added} yeni aday eklendi")
    finally:
        places.close()
        if leads is not None:
            leads.close()


if __name__ == "__main__":
    main()
