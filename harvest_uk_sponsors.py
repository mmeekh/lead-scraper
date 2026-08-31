#!/usr/bin/env python3
"""UK lisansli sponsor sicilinden GB lead kesfi.

GB icin OSM verimsiz; bunun yerine UKVI sicilindeki (zaten Skilled Worker
vizesi verebilen) A-rating firmalarin adindan alan adi adaylari uretilir,
DNS + site icerigi (title ve ilk 5KB'de firma adi tokenlari) ile dogrulanir.
Dogrulanan firma leads tablosuna status=pending olarak eklenir; e-postayi
mevcut tarayici (scrape.py emails) toplar.

Kullanim:
  python3 harvest_uk_sponsors.py --dry-run --limit 3
  python3 harvest_uk_sponsors.py --source-label visa-priority-20260831 --limit 50
  # restart: onceki calismanin "restart icin" satirindaki offseti ver
  python3 harvest_uk_sponsors.py --source-label X --offset 120 --limit 50
"""
from __future__ import annotations

import argparse
import csv
import re
import socket
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Iterator, TextIO

import requests

import scrape
from sponsor_registry import normalize_name

BASE = Path(__file__).parent
CATALOG = BASE / "catalog"

NOTE_TEXT = "uk sponsor sicili (A rating, Skilled Worker)"
REQUEST_DELAY = 0.5   # worker basina istekler arasi nazik bekleme (sn)
HTTP_TIMEOUT = 8
DNS_TIMEOUT = 5
PROBE_BYTES = 5120    # icerik eslesmesi icin okunan govde boyutu
MAX_CANDIDATES = 5    # firma basina en fazla denenen alan adi adayi
MAX_WORKERS = 3       # VPS 1 vCPU: daha fazlasina izin yok

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Anahtar kelime eslesmesi: normalize edilmis isim iki yandan bosluklanir,
# boylece " fish " gibi girisler kelime sinirinda, "financ" gibi girisler
# serbest parca olarak eslesir.
INCLUDE_KEYWORDS: tuple[str, ...] = (
    "accountan", "accounting", "audit", "tax", "payroll", "bookkeep",
    "ledger", "financ", "fiscal", "capital", "wealth", "invest", "fund",
    "asset", "equity", "pension", "actuari", "insur", "underwrit", "broker",
    "bank", "fintech", "payment", "treasury", "credit", "advisory",
    "advisor", "consult", "analytic", "analysis", "data", "intelligence",
    "software", "saas", "tech", "digital", "cyber", "cloud", "systems",
    "solutions", "platform", "automation", "robotic", "compliance",
    "blockchain", "crypto",
)
# Bariz alakasiz / emek arzi sektorler; eslesirse firma hic denenmez.
# DIKKAT: "taxi" burada oldugu icin "tax" iceren taksi firmalari elenir.
EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "taxi", "care", "nursing", "nurse", "nursery", "medic", "clinic",
    "dental", "pharmac", "hospital", "health", "veterinar", "restaurant",
    "takeaway", "catering", "kitchen", "halal", "kebab", "pizza", "grill",
    "cafe", " coffee ", "bakery", "butcher", " food", "farm", " fish",
    "poultry", "grocery", "supermarket", "retail", "transport", "logistic",
    "haulage", "courier", "freight", "cleaning", "cleaner", "recruit",
    "staffing", "manpower", "workforce", "construction", "builder",
    "scaffold", "roofing", "plumb", "joinery", "carpentry", "salon",
    "barber", "beauty", "hotel", "hostel", "school", "academy",
    "university", "church", "mosque", "garage", "motor", "vehicle",
    "driving", "textile", "garment", "fashion", "warehouse",
)
# Park/placeholder sayfa sinyalleri (probe metni kucuk harfli aranir).
PARK_SIGNALS: tuple[str, ...] = (
    "domain for sale", "domain is for sale", "buy this domain",
    "domain parking", "parked", "godaddy", "go daddy", "sedo",
    "hugedomains", "dan.com", "domain may be for sale", "coming soon",
    "under construction", "website builder", "starfield technologies",
)

# Tek basina kanit sayilmayan jenerik isim tokenlari: isimde ayirt edici
# token varsa, eslesenlerden en az biri jenerik olmamali.
GENERIC_TOKENS: frozenset[str] = frozenset({
    "consultancy", "consulting", "services", "solutions", "technologies",
    "technology", "systems", "software", "digital", "group", "global",
    "international", "management", "partners", "associates", "advisory",
    "holdings", "enterprises", "company", "limited",
})


# ------------------------------------------------------------- satir filtresi

def row_ok(row: dict[str, str]) -> bool:
    """Route == Skilled Worker VE rating 'Worker (A rating' ile baslar."""
    route = (row.get("Route") or "").strip()
    rating = (row.get("Type & Rating") or "").strip()
    return route == "Skilled Worker" and rating.startswith("Worker (A rating")


def name_relevant(name: str) -> bool:
    """Finance/automation/data profiline uyan isim mi? Dislama once gelir."""
    norm = normalize_name(name)
    if not norm:
        return False
    hay = f" {norm} "
    if any(k in hay for k in EXCLUDE_KEYWORDS):
        return False
    return any(k in hay for k in INCLUDE_KEYWORDS)


def verify_tokens(name: str) -> list[str]:
    """Icerik dogrulamasinda aranacak >=4 harfli isim tokenlari."""
    return [t for t in normalize_name(name).split() if len(t) >= 4]


def iter_eligible(handle: TextIO) -> Iterator[tuple[str, str]]:
    """CSV'den uygun (isim, sehir) akisi; satir satir, deterministik sirada.

    Ayni firma birden cok satirda gecebilir; normalize isimle tekillenir.
    Dogrulanamayacak (token'siz / aday'siz) firmalar hic uretilmez ki
    --offset sayaci restartlar arasinda tutarli kalsin.
    """
    seen: set[str] = set()
    for row in csv.DictReader(handle):
        if not row_ok(row):
            continue
        name = (row.get("Organisation Name") or "").strip()
        if not name or not name_relevant(name):
            continue
        norm = normalize_name(name)
        if norm in seen:
            continue
        seen.add(norm)
        if not verify_tokens(name) or not domain_candidates(name):
            continue
        yield name, (row.get("Town/City") or "").strip()


# --------------------------------------------------------- alan adi adaylari

def domain_candidates(name: str) -> list[str]:
    """Normalize isimden en fazla MAX_CANDIDATES alan adi adayi uretir."""
    tokens = normalize_name(name).split()
    if not tokens:
        return []
    out: list[str] = []

    def push(slug: str, tlds: tuple[str, ...]) -> None:
        if len(slug) < 3 or len(slug) > 40 or slug.isdigit():
            return
        for tld in tlds:
            candidate = slug + tld
            if candidate not in out:
                out.append(candidate)

    push("".join(tokens), (".co.uk", ".com", ".uk"))
    if len(tokens) > 2:  # kisaltilmis ilk-iki-kelime varyanti
        push("".join(tokens[:2]), (".co.uk", ".com"))
    return out[:MAX_CANDIDATES]


# ------------------------------------------------------------- dogrulama

def extract_title(html: str) -> str:
    match = TITLE_RE.search(html)
    if not match:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())


def probe_text(title: str, body: str) -> str:
    """Title + govde ornegini tek kucuk-harfli arama metnine indirger."""
    return " ".join((title + " " + body).casefold().split())


def looks_parked(probe: str) -> bool:
    return any(signal in probe for signal in PARK_SIGNALS)


def strip_domain_echo(probe: str, host: str, name: str) -> str:
    """Aday alan adinin kendisi kanit sayilmaz; probe'dan cikarilir.

    Park/placeholder sayfalar cogunlukla kendi alan adini basar; tokenlar
    bitisik slug icinde trivyal eslesir. Tam host her zaman silinir; bitisik
    slug ise yalnizca cok kelimeli isimlerde silinir ki tek kelimeli firmanin
    gercek metindeki adi kanit olmaya devam etsin.
    """
    probe = probe.replace(host.casefold(), " ")
    bare = host.split(".", 1)[0]
    if len(normalize_name(name).split()) >= 2:
        probe = probe.replace(bare, " ")
    return probe


def match_evidence(name: str, probe: str, domain: str = "",
                   town: str = "") -> str | None:
    """Firma adi tokenlarinin en az yarisi gecmeli; kanit metni dondurur.

    Ek sertlestirme (31 Agu incelemesi: 'Renn Consultancy Ltd t/a Emulous
    Water' Filipinler'deki adasi RENN Consultancy Inc. ile eslesmisti):
    - >=2 ayirt edici tokeni olan isimde tek ayirt edici token kanit sayilmaz.
    - .uk disi TLD'de sayfada Birlesik Krallik sinyali (sicildeki sehir veya
      'united kingdom') aranir; yoksa ayni isimli yabanci firma reddedilir.
    """
    tokens = verify_tokens(name)
    if not tokens:
        return None
    hits = [t for t in tokens if t in probe]
    if not hits or len(hits) * 2 < len(tokens):
        return None
    distinctive = [t for t in tokens if t not in GENERIC_TOKENS]
    distinctive_hits = [t for t in distinctive if t in probe]
    if distinctive and not distinctive_hits:
        return None  # yalnizca jenerik kelimeler eslesti; kanit sayilmaz
    if len(distinctive) >= 2 and len(distinctive_hits) < 2:
        return None
    if domain and not domain.casefold().endswith(".uk"):
        town_norm = " ".join((town or "").casefold().split())
        uk_signal = ("united kingdom" in probe
                     or (bool(town_norm) and town_norm in probe))
        if not uk_signal:
            return None
    return f"token {len(hits)}/{len(tokens)} ({','.join(hits[:4])})"


def dns_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def fetch_homepage(session: requests.Session, host: str) -> requests.Response | None:
    for scheme in ("https://", "http://"):
        time.sleep(REQUEST_DELAY)  # ayni worker'dan istekler arasi nazik ol
        try:
            resp = session.get(scheme + host, timeout=HTTP_TIMEOUT,
                               allow_redirects=True)
        except requests.RequestException:
            continue
        if resp.status_code < 400:
            return resp
    return None


def check_company(name: str, town: str) -> tuple[str, str, str | None, str]:
    """Worker islevi: (isim, sehir, dogrulanan-domain|None, kanit/aciklama)."""
    session = requests.Session()
    session.headers.update({"User-Agent": scrape.OSM_UA, "Accept-Language": "en"})
    try:
        for candidate in domain_candidates(name):
            if not dns_resolves(candidate):
                continue
            resp = fetch_homepage(session, candidate)
            if resp is None:
                continue
            title = extract_title(resp.text[:16384])
            probe = probe_text(title, resp.text[:PROBE_BYTES])
            if looks_parked(probe):
                continue
            evidence = match_evidence(
                name, strip_domain_echo(probe, candidate, name), candidate, town)
            if evidence is None:
                continue
            final_domain = scrape.norm_domain(resp.url) or candidate
            return (name, town, final_domain,
                    f"{candidate} | title='{title[:60]}' | {evidence}")
        return name, town, None, "aday dogrulanamadi"
    except Exception as exc:  # worker asla patlamasin
        return name, town, None, f"hata: {type(exc).__name__}"
    finally:
        session.close()


# ------------------------------------------------------------- veritabani

def record_lead(conn: sqlite3.Connection, domain: str, name: str, town: str,
                source_label: str) -> bool:
    """Dogrulanan firmayi ekler; yalnizca YENI insert olduysa note yazilir."""
    if not scrape.add_lead(conn, domain, name, town, "GB",
                           f"{source_label}:uksr"):
        return False
    conn.execute("UPDATE leads SET note=? WHERE domain=?", (NOTE_TEXT, domain))
    conn.commit()
    return True


def already_known(conn: sqlite3.Connection, candidates: list[str]) -> bool:
    """Adaylardan biri DB'de varsa ag masrafina hic girme."""
    if not candidates:
        return True  # aday uretilemeyen firma zaten denenemez
    marks = ",".join("?" for _ in candidates)
    row = conn.execute(
        f"SELECT 1 FROM leads WHERE domain IN ({marks}) LIMIT 1",
        candidates).fetchone()
    return row is not None


# ----------------------------------------------------------------------- CLI

def find_default_csv() -> Path | None:
    paths = sorted(CATALOG.glob("uk-sponsors-*.csv"))
    return paths[-1] if paths else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default="",
                        help="UKVI sicil CSV'si (varsayilan: catalog icindeki en yeni)")
    parser.add_argument("--limit", type=int, default=20,
                        help="eklenecek yeni lead hedefi")
    parser.add_argument("--workers", type=int, default=2,
                        help=f"es zamanli worker (en fazla {MAX_WORKERS})")
    parser.add_argument("--source-label", default="uk-sponsors",
                        help="source kolonu on eki (source=LABEL:uksr)")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB'ye yazma; dogrulama sonuclarini yazdir")
    parser.add_argument("--offset", type=int, default=0,
                        help="restart icin atlanacak uygun firma sayisi")
    parser.add_argument("--max-attempts", type=int, default=0,
                        help="en fazla bu kadar firma dene (0=sinirsiz)")
    args = parser.parse_args()

    csv_path = Path(args.csv) if args.csv else find_default_csv()
    if csv_path is None or not csv_path.exists():
        print("uk-sponsors CSV bulunamadi (catalog/uk-sponsors-*.csv)")
        sys.exit(2)

    workers = max(1, min(MAX_WORKERS, int(args.workers)))
    limit = max(1, int(args.limit))
    offset = max(0, int(args.offset))
    max_attempts = max(0, int(args.max_attempts))
    socket.setdefaulttimeout(DNS_TIMEOUT)

    # dry-run canli DB'ye hic dokunmaz (baglanti bile acilmaz)
    conn = None if args.dry_run else scrape.db()
    mode = "DRY-RUN" if args.dry_run else "canli"
    print(f"kaynak: {csv_path.name} | mod: {mode} | hedef: {limit} | "
          f"worker: {workers} | offset: {offset}")

    attempted = verified = added = dup = known = submitted = 0
    stop = False

    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        firms = iter_eligible(handle)
        skipped_offset = 0
        for _ in range(offset):
            if next(firms, None) is None:
                break
            skipped_offset += 1

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future, str] = {}

            def submit_next() -> bool:
                nonlocal known, submitted
                if max_attempts and submitted >= max_attempts:
                    return False
                for firm_name, firm_town in firms:
                    if conn is not None and already_known(
                            conn, domain_candidates(firm_name)):
                        known += 1
                        continue
                    futures[pool.submit(check_company, firm_name, firm_town)] = firm_name
                    submitted += 1
                    return True
                return False

            for _ in range(workers):
                if not submit_next():
                    break

            while futures:
                done_set, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for future in done_set:
                    futures.pop(future, None)
                    firm_name, firm_town, domain, evidence = future.result()
                    attempted += 1
                    if domain and stop:
                        verified += 1  # limit doldu: ucustaki sonuc yazilmaz
                    elif domain:
                        verified += 1
                        if conn is None:
                            added += 1
                            print(f"DRY OK {firm_name} -> {domain} | {evidence}",
                                  flush=True)
                        elif record_lead(conn, domain, firm_name, firm_town,
                                         args.source_label):
                            added += 1
                            print(f"OK {firm_name} -> {domain}", flush=True)
                        else:
                            dup += 1
                            print(f"VAR {domain} (zaten kayitli)", flush=True)
                    if attempted % 25 == 0:
                        print(f"denendi={attempted} dogrulandi={verified}",
                              flush=True)
                    if added >= limit:
                        stop = True
                if not stop:
                    while len(futures) < workers and submit_next():
                        pass

    if conn is not None:
        conn.close()
    print(f"bitti: denendi={attempted} dogrulandi={verified} eklendi={added} "
          f"mukerrer={dup} db-mevcut-atlandi={known}")
    # offset uygun-satir sirasina baglidir; yalnizca AYNI CSV ile gecerli
    print(f"restart icin: --offset {skipped_offset + known + attempted} "
          f"--csv {csv_path}")


if __name__ == "__main__":
    main()
