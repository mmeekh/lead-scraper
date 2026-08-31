#!/usr/bin/env python3
"""Luksemburg kuratorlu kalite kanali: uzman ve ulasilabilir firmalar.

Kapsam: fon yonetimi / fund administration, regtech, fintech, finans
yazilimi ve uzman danismanlik firmalari. Big Four ile cok buyuk global
markalar bilerek disarida tutulur; soguk basvuruyu okuma ihtimali daha yuksek
bagimsiz ve orta olcekli ekipler tercih edilir. Liste elle secildi; e-postalar
deep_enrich tarafindan sitelerinden CIKARILIR, tahmin edilmez.

Akis:
  1) SEEDS listesi kategorilere gore donusumlu siralanir (cesitlilik).
  2) Her aday icin DNS on-dogrulamasi yapilir (HTTP istegi YOK).
  3) Cozunen adaylar add_lead ile havuza yazilir, limit dolunca durur.

Kullanim:
  python3 seed_luxembourg.py --dry-run                 # DB'ye yazmadan on izleme
  python3 seed_luxembourg.py --self-check              # liste tutarlilik denetimi
  python3 seed_luxembourg.py --source-label X --limit 50
"""
from __future__ import annotations

import argparse
import concurrent.futures
import socket
import sys

from scrape import add_lead, db, norm_domain

# gecerli kategoriler (self-check bunlara karsi denetler)
VALID_CATEGORIES = {"fund-admin", "bank", "fintech", "regtech", "consulting"}

DNS_TIMEOUT = 5.0    # saniye, aday basina
DNS_WORKERS = 6      # VPS tek cekirdek: hafif tutuldu

# (domain, firma adi, kategori) - hepsi LU merkezli ya da guclu LU ofisli
SEEDS: list[tuple[str, str, str]] = [
    # --- fon yonetimi / fund administration ---
    ("alterdomus.com", "Alter Domus", "fund-admin"),
    ("iqeq.com", "IQ-EQ", "fund-admin"),
    ("apexgroup.com", "Apex Group", "fund-admin"),
    ("citco.com", "Citco", "fund-admin"),
    ("tmf-group.com", "TMF Group", "fund-admin"),
    ("vistra.com", "Vistra", "fund-admin"),
    ("ocorian.com", "Ocorian", "fund-admin"),
    ("waystone.com", "Waystone", "fund-admin"),
    ("universal-investment.com", "Universal Investment", "fund-admin"),
    ("caceis.com", "CACEIS", "fund-admin"),
    ("clearstream.com", "Clearstream", "fund-admin"),
    ("aztecgroup.eu", "Aztec Group", "fund-admin"),
    ("jtcgroup.com", "JTC Group", "fund-admin"),
    ("efa.eu", "European Fund Administration", "fund-admin"),
    ("zedra.com", "Zedra", "fund-admin"),
    ("ipconcept.com", "IPConcept", "fund-admin"),
    ("finexis.lu", "Finexis", "fund-admin"),
    ("ias-fid.lu", "IAS Fiduciaire", "fund-admin"),
    ("orientis.lu", "Orientis Partners", "fund-admin"),
    ("paddock.lu", "Paddock Fund Solutions", "fund-admin"),
    ("aifmservices.com", "AIF Management Services", "fund-admin"),
    ("ufundservices.com", "U Fund Services", "fund-admin"),
    ("standishmanagement.lu", "Standish Management Luxembourg", "fund-admin"),
    # --- bankalar ---
    ("spuerkeess.lu", "Spuerkeess (BCEE)", "bank"),
    ("bil.com", "Banque Internationale a Luxembourg", "bank"),
    ("raiffeisen.lu", "Banque Raiffeisen", "bank"),
    ("quintet.com", "Quintet Private Bank", "bank"),
    ("swissquote.lu", "Swissquote Bank Europe", "bank"),
    ("advanzia.com", "Advanzia Bank", "bank"),
    ("bankingcircle.com", "Banking Circle", "bank"),
    ("banquedeluxembourg.com", "Banque de Luxembourg", "bank"),
    ("pictet.com", "Pictet Luxembourg", "bank"),
    ("dz-privatbank.com", "DZ Privatbank", "bank"),
    ("vpbank.com", "VP Bank Luxembourg", "bank"),
    # --- fintech (LU lisansli / LU merkezli odeme ve varlik teknolojisi) ---
    ("mangopay.com", "Mangopay", "fintech"),
    ("satispay.com", "Satispay Europe", "fintech"),
    ("finologee.com", "Finologee", "fintech"),
    ("tokeny.com", "Tokeny", "fintech"),
    ("payconiq.com", "Payconiq International", "fintech"),
    ("bitstamp.net", "Bitstamp Europe", "fintech"),
    ("bitflyer.com", "bitFlyer Europe", "fintech"),
    # --- regtech ---
    ("luxtrust.com", "LuxTrust", "regtech"),
    ("kneip.com", "KNEIP", "regtech"),
    ("scorechain.com", "Scorechain", "regtech"),
    ("governance.com", "Governance.com", "regtech"),
    ("fundsquare.net", "Fundsquare", "regtech"),
    ("in-edit.lu", "InEdit", "regtech"),
    # --- uzman danismanlik / finans yazilimi ---
    ("bdo.lu", "BDO Luxembourg", "consulting"),
    ("grantthornton.lu", "Grant Thornton Luxembourg", "consulting"),
    ("reply.com", "Avantage Reply", "consulting"),
    ("ssctech.com", "SS&C Technologies", "consulting"),
    ("linedata.com", "Linedata", "consulting"),
    ("broadridge.com", "Broadridge", "consulting"),
    ("arendt.com", "Arendt & Medernach", "consulting"),
    ("mazars.lu", "Forvis Mazars Luxembourg", "consulting"),
    ("wavestone.com", "Wavestone Luxembourg", "consulting"),
    ("sia-partners.com", "Sia Partners", "consulting"),
    ("telindus.lu", "Telindus Luxembourg", "consulting"),
]


def round_robin(seeds: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Kategori cesitliligi: once her kategoriden birer tane, sonra tur tur."""
    buckets: dict[str, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    for item in seeds:
        cat = item[2]
        if cat not in buckets:
            buckets[cat] = []
            order.append(cat)
        buckets[cat].append(item)
    mixed: list[tuple[str, str, str]] = []
    while any(buckets.values()):
        for cat in order:
            if buckets[cat]:
                mixed.append(buckets[cat].pop(0))
    return mixed


def dns_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, OSError):
        return False


def dns_filter(seeds: list[tuple[str, str, str]]) -> tuple[
        list[tuple[str, str, str]], list[str]]:
    """DNS on-dogrulamasi: cozunmeyen adaylar elenir ve raporlanir."""
    valid: list[tuple[str, str, str]] = []
    failed: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=DNS_WORKERS) as pool:
        futures = {pool.submit(dns_resolves, d): (d, n, c) for d, n, c in seeds}
        results: dict[str, bool] = {}
        for fut, (d, _n, _c) in futures.items():
            try:
                results[d] = fut.result(timeout=DNS_TIMEOUT * 3)
            except concurrent.futures.TimeoutError:
                results[d] = False
    for d, n, c in seeds:  # orijinal (donusumlu) sirayi koru
        if results.get(d):
            valid.append((d, n, c))
        else:
            failed.append(d)
    return valid, failed


def self_check() -> int:
    """Liste tutarlilik denetimi: DB'ye ve aga dokunmaz."""
    errors: list[str] = []
    domains = [d for d, _n, _c in SEEDS]
    dupes = {d for d in domains if domains.count(d) > 1}
    if dupes:
        errors.append(f"dupe domain var: {sorted(dupes)}")
    for d, n, c in SEEDS:
        if not (d and n and c):
            errors.append(f"eksik alan: {(d, n, c)}")
        if c not in VALID_CATEGORIES:
            errors.append(f"gecersiz kategori: {d} -> {c}")
        if norm_domain(d) != d:
            errors.append(f"norm_domain'den sag cikmiyor: {d!r} -> {norm_domain(d)!r}")
    if len(SEEDS) < 55:
        errors.append(f"liste cok kisa: {len(SEEDS)} < 55")
    cats = sorted({c for _d, _n, c in SEEDS})
    print(f"self-check: {len(SEEDS)} kayit, kategoriler: {', '.join(cats)}")
    for cat in cats:
        say = sum(1 for _d, _n, c in SEEDS if c == cat)
        print(f"  {cat:<11} {say}")
    if errors:
        for e in errors:
            print(f"HATA: {e}")
        print("self-check BASARISIZ")
        return 1
    print("self-check OK: dupe yok, alanlar dolu, kategoriler gecerli, "
          "domainler normalize")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Luksemburg kuratorlu kalite kanali seed scripti")
    ap.add_argument("--limit", type=int, default=50,
                    help="en fazla kac YENI kayit eklensin (varsayilan 50)")
    ap.add_argument("--source-label", default="",
                    help="kaynak etiketi; DB'ye <etiket>:lu-seed yazilir")
    ap.add_argument("--dry-run", action="store_true",
                    help="DB'ye yazmadan DNS kontrolu + on izleme")
    ap.add_argument("--self-check", action="store_true",
                    help="liste tutarlilik denetimi (ag ve DB yok)")
    args = ap.parse_args()
    if not (args.dry_run or args.self_check) and not args.source_label.strip():
        # etiketsiz gercek kosum kampanya kapsami disina lead yazar ve
        # need() sayimlari sismis kesfe yol acar (harvest_overture kalibi)
        ap.error("--source-label gerekli (dry-run/self-check disinda)")

    if args.self_check:
        sys.exit(self_check())

    mixed = round_robin(SEEDS)
    print(f"{len(mixed)} aday, DNS on-dogrulamasi basliyor ...")
    valid, failed = dns_filter(mixed)
    print(f"DNS: {len(valid)} cozundu, {len(failed)} elendi")
    for d in failed:
        print(f"  elendi (DNS cozunmedi): {d}")

    if args.dry_run:
        print(f"\ndry-run: DB'ye yazilmadi; ilk 15 gecerli aday "
              f"(limit={args.limit}):")
        for d, n, c in valid[:15]:
            print(f"  {d:<28} {n:<36} {c}")
        return

    conn = db()
    added = skipped = 0
    for d, n, c in valid:
        if added >= args.limit:
            break
        if add_lead(conn, d, n, "Luxembourg", "LU", f"{args.source_label}:lu-seed"):
            conn.execute("UPDATE leads SET note=? WHERE domain=?",
                         (f"LU kuratorlu kalite kanali: {c}", d))
            added += 1
        else:
            skipped += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"LU kuratorlu kanal: {added} yeni eklendi, {skipped} zaten havuzdaydi, "
          f"{len(failed)} DNS'te elendi | havuz: {total}")


if __name__ == "__main__":
    main()
