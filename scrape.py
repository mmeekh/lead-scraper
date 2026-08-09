#!/usr/bin/env python3
"""Lead scraper: sirket sitesi -> dogrulanmis e-posta.

Kaynaklardan aday domain toplar, her sitenin iletisim sayfalarini tarar,
YAYINLANMIS e-postayi cikarir. Tahmin YOK - sadece sitede gercekten yazan adres.

Kullanim:
  # 1) Aday topla (istedigin kadar tekrarla, birikir)
  python3 scrape.py osm --area Rotterdam --country NL
  python3 scrape.py osm --area Hamburg --country DE --types accountant,tax_advisor
  python3 scrape.py links --url https://bir-rehber-sayfasi/liste --country DE
  python3 scrape.py seed --domains a.nl,b.de --country NL

  # 2) E-postalari cikar (kaldigi yerden devam eder, kesilirse sorun yok)
  python3 scrape.py emails --limit 200

  # 3) Kampanya listesine hazir CSV uret (mukerrerler otomatik elenir)
  python3 scrape.py export --out yeni-firmalar.csv --tag NL-EN

  # durum
  python3 scrape.py stats
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

BASE = Path(__file__).parent
DB_PATH = BASE / "leads.sqlite3"
# Export sirasinda mukerrer elemek icin okunan mevcut kampanya dosyalari.
# Ortam degiskeniyle degistirilebilir; yoksa export yine calisir.
CAMPAIGN_CSV = Path(os.environ.get(
    "CAMPAIGN_CSV", "/root/projects/nl-job-outreach/firmalar.csv"))
CAMPAIGN_LOG = Path(os.environ.get(
    "CAMPAIGN_LOG", "/root/projects/nl-job-outreach/sent-log.csv"))

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en,nl;q=0.8,de;q=0.7"}

# iletisim sayfasi adaylari (sirayla denenir, mail bulununca durur)
CONTACT_PATHS = [
    "", "/contact", "/contact.html", "/contactformulier", "/contact-us",
    "/kontakt", "/kontakt.html", "/impressum", "/imprint", "/iletisim",
    "/over-ons", "/about", "/about-us", "/ueber-uns", "/team", "/vacatures",
    "/careers", "/karriere", "/jobs",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')

# e-posta gibi gorunen ama olmayan seyler
JUNK_SUBSTR = (
    "sentry.io", "wixpress", "example.", "domain.com", "yourdomain",
    "@2x.", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
    "u003e", "email@", "your@", "name@", "test@", "abc@", "xxx@",
)
JUNK_LOCAL = {"noreply", "no-reply", "donotreply", "postmaster", "abuse",
              "webmaster", "hostmaster", "privacy", "dpo", "avg", "gdpr"}
# platform/ajans adresleri - firmanin kendisi degil
JUNK_DOMAIN = {
    "sentry.io", "wordpress.com", "wix.com", "jouwweb.nl", "squarespace.com",
    "godaddy.com", "cloudflare.com", "google.com", "gmail.example",
    "shopify.com", "webflow.com", "strato.de", "ionos.de", "hostnet.nl",
}
# genel mail saglayicilarina izin var (kucuk ofisler kullaniyor)
FREEMAIL = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "gmx.de",
            "gmx.net", "web.de", "t-online.de", "hetnet.nl", "ziggo.nl",
            "live.nl", "kpnmail.nl", "planet.nl", "icloud.com"}

OSM_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]
# Overpass tarayici UA'sini 406 ile reddediyor; aciklayici kimlik istiyor.
# Overpass kullanim kosullari iletisim bilgisi ister: SCRAPER_CONTACT ile ver.
OSM_CONTACT = os.environ.get("SCRAPER_CONTACT", "set SCRAPER_CONTACT env var")
OSM_UA = f"lead-scraper/1.0 (research use; {OSM_CONTACT})"
OSM_DEFAULT_TYPES = "accountant,tax_advisor,financial,employment_agency"


# ---------------------------------------------------------------- veritabani

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            domain   TEXT PRIMARY KEY,
            name     TEXT,
            city     TEXT,
            country  TEXT,
            source   TEXT,
            status   TEXT DEFAULT 'pending',   -- pending|done|noemail|error
            email    TEXT,
            all_mails TEXT,
            note     TEXT,
            checked_at TEXT
        )
    """)
    return conn


def norm_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value or value in {"-", "n/a", "none"}:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = (urlparse(value).hostname or "").lower()
    return host.removeprefix("www.")


def lead_key(site: str, mail: str) -> str:
    """Kurum anahtari. Firmanin kendi alan adi varsa o; yoksa (gmail gibi
    genel saglayici) e-postanin kendisi anahtar olur - boylece ayni saglayiciyi
    kullanan farkli firmalar birbirini EZMEZ."""
    domain = norm_domain(site)
    if domain and domain not in FREEMAIL:
        return domain
    mail = (mail or "").strip().lower()
    if mail and EMAIL_RE.fullmatch(mail):
        return mail
    return domain


def add_lead(conn, domain, name="", city="", country="", source="") -> bool:
    domain = domain.strip().lower() if "@" in (domain or "") else norm_domain(domain)
    if not domain or domain in JUNK_DOMAIN:
        return False
    try:
        conn.execute(
            "INSERT OR IGNORE INTO leads(domain,name,city,country,source) VALUES(?,?,?,?,?)",
            (domain, name, city, country, source))
        return conn.total_changes > 0
    except sqlite3.Error:
        return False


# ------------------------------------------------------------ kaynak: OSM

def cmd_osm(args) -> None:
    types = "|".join(t.strip() for t in args.types.split(",") if t.strip())
    query = f"""
[out:json][timeout:90];
area["name"="{args.area}"]->.a;
(
  node["office"~"^({types})$"](area.a);
  way["office"~"^({types})$"](area.a);
);
out center tags;
"""
    data = None
    for endpoint in OSM_ENDPOINTS:
        try:
            resp = requests.post(endpoint, data={"data": query},
                                 headers={"User-Agent": OSM_UA}, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                break
            print(f"  {endpoint} -> HTTP {resp.status_code}")
        except Exception as exc:
            print(f"  {endpoint} -> {type(exc).__name__}")
    if data is None:
        print("Overpass'a ulasilamadi, sonra tekrar dene")
        return

    conn = db()
    added = direct = 0
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        site = tags.get("website") or tags.get("contact:website") or ""
        mail = tags.get("email") or tags.get("contact:email") or ""
        name = tags.get("name", "")
        city = tags.get("addr:city") or args.area
        domain = lead_key(site, mail)
        if not domain:
            continue
        if add_lead(conn, domain, name, city, args.country, f"osm:{args.area}"):
            added += 1
        if mail and EMAIL_RE.fullmatch(mail.strip()):
            conn.execute(
                "UPDATE leads SET status='done', email=?, note='OSM etiketinden', "
                "checked_at=datetime('now') WHERE domain=? AND status='pending'",
                (mail.strip(), domain))
            direct += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"{args.area}: {added} yeni aday eklendi ({direct} tanesinin maili OSM'de hazir) | havuz: {total}")


# ------------------------------------------------- kaynak: liste sayfasindan

def cmd_links(args) -> None:
    """Bir rehber/liste sayfasindaki dis baglantilardan domain toplar."""
    try:
        resp = requests.get(args.url, headers=HEADERS, timeout=25)
        html = resp.text
    except Exception as exc:
        print(f"sayfa alinamadi: {type(exc).__name__}")
        return
    host = norm_domain(args.url)
    conn = db()
    added = 0
    for raw in re.findall(r'href=["\'](https?://[^"\'>\s]+)', html):
        domain = norm_domain(raw)
        if not domain or domain == host:
            continue
        if any(s in domain for s in ("facebook", "twitter", "linkedin", "instagram",
                                     "youtube", "google", "wikipedia", "x.com")):
            continue
        if add_lead(conn, domain, "", args.city or "", args.country, f"links:{host}"):
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"{added} yeni domain eklendi | havuz: {total}")


def cmd_seed(args) -> None:
    conn = db()
    added = sum(add_lead(conn, d, "", args.city or "", args.country, "seed")
                for d in args.domains.split(","))
    conn.commit()
    conn.close()
    print(f"{added} yeni domain eklendi")


# ------------------------------------------------------- e-posta cikarici

def cf_decode(hexstr: str) -> str:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key)
                       for i in range(2, len(hexstr), 2))
    except Exception:
        return ""


def clean_emails(raw: set[str], domain: str) -> list[str]:
    out = []
    for mail in raw:
        mail = mail.strip().strip(".,;:'\"()<>").lower()
        if not EMAIL_RE.fullmatch(mail) or len(mail) > 90:
            continue
        if any(j in mail for j in JUNK_SUBSTR):
            continue
        local, _, host = mail.partition("@")
        if local in JUNK_LOCAL or host in JUNK_DOMAIN:
            continue
        # sadece firmanin kendi alan adi veya bilinen freemail kabul
        root = ".".join(domain.split(".")[-2:])
        if not (host == domain or host.endswith("." + domain)
                or root in host or host in FREEMAIL):
            continue
        out.append(mail)
    # tercih sirasi: info/contact gibi genel adresler once
    priority = ("info", "contact", "kontakt", "office", "mail", "kantoor",
                "administratie", "bewerbung", "career", "jobs", "hr")
    out = sorted(set(out), key=lambda m: (
        0 if m.split("@")[0] in priority else 1, len(m)))
    return out


def scrape_site(row: sqlite3.Row) -> tuple[str, str, list[str], str]:
    """(domain, status, emails, note)"""
    domain = row["domain"]
    if "@" in domain:  # site yok, anahtar zaten e-posta: taranacak bir sey yok
        return domain, "done", [domain], "kaynak etiketinden"
    found: set[str] = set()
    last_note = ""
    session = requests.Session()
    session.headers.update(HEADERS)
    for scheme in ("https://", "http://"):
        for path in CONTACT_PATHS:
            url = urljoin(f"{scheme}{domain}", path)
            try:
                resp = session.get(url, timeout=12, allow_redirects=True)
            except Exception as exc:
                last_note = type(exc).__name__
                continue
            if resp.status_code >= 400:
                last_note = f"HTTP {resp.status_code}"
                continue
            html = resp.text
            for hexstr in CFEMAIL_RE.findall(html):
                decoded = cf_decode(hexstr)
                if "@" in decoded:
                    found.add(decoded)
            found.update(EMAIL_RE.findall(html))
            cleaned = clean_emails(found, domain)
            if cleaned:
                session.close()
                return domain, "done", cleaned, f"bulundu: {path or '/'}"
            time.sleep(0.4)  # ayni siteye nazik ol
        if found or last_note.startswith("HTTP"):
            break
    session.close()
    cleaned = clean_emails(found, domain)
    if cleaned:
        return domain, "done", cleaned, "bulundu"
    return domain, ("error" if last_note and not last_note.startswith("HTTP 4") else "noemail"), [], last_note


def cmd_emails(args) -> None:
    conn = db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM leads WHERE status='pending' ORDER BY rowid LIMIT ?",
        (args.limit,)).fetchall()
    if not rows:
        print("islenecek bekleyen aday yok")
        return
    print(f"{len(rows)} site taranacak (es zamanli {args.workers})")

    done = ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_site, r): r["domain"] for r in rows}
        for future in as_completed(futures):
            domain, status, mails, note = future.result()
            conn.execute(
                "UPDATE leads SET status=?, email=?, all_mails=?, note=?, "
                "checked_at=datetime('now') WHERE domain=?",
                (status, mails[0] if mails else None, ",".join(mails[:6]),
                 note, domain))
            conn.commit()
            done += 1
            if mails:
                ok += 1
                print(f"  [{done}/{len(rows)}] OK  {domain} -> {mails[0]}", flush=True)
            elif done % 10 == 0:
                print(f"  [{done}/{len(rows)}] ...", flush=True)
    conn.close()
    print(f"bitti: {ok}/{len(rows)} sitede yayinlanmis e-posta bulundu")


# ------------------------------------------------------------------ export

# kampanyada eski etiketlerin hangi ulkeye dustugu (organizasyon anahtari icin)
LEGACY_TAG_COUNTRY = {
    "1-TURK": "NL", "1-TURK-RISKLI": "NL", "2-EN": "NL", "3-NL": "NL",
    "DE-EN": "DE", "DE-TURK": "DE", "GULF-EN": "AE", "GULF-TURK": "AE",
}


def campaign_country(row: dict[str, str]) -> str:
    """send_mails.route_for ile ayni ulke esleme mantigi."""
    tag = (row.get("oncelik") or "").strip().upper()
    if tag in LEGACY_TAG_COUNTRY:
        country = LEGACY_TAG_COUNTRY[tag]
        if country == "AE" and "qatar" in (row.get("sehir") or "").casefold():
            return "QA"
        return country
    return tag[:-3] if tag.endswith("-EN") else tag


def cmd_export(args) -> None:
    known_mails: set[str] = set()
    known_orgs: set[str] = set()

    if CAMPAIGN_LOG.exists():
        with open(CAMPAIGN_LOG, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("email"):
                    known_mails.add(row["email"].strip().lower())
    if CAMPAIGN_CSV.exists():
        with open(CAMPAIGN_CSV, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("email"):
                    known_mails.add(row["email"].strip().lower())
                # send_mails._site_host ile ayni mantik: site yoksa anahtar e-posta
                site = (row.get("site") or "").strip().casefold()
                mail_lower = (row.get("email") or "").strip().lower()
                if site in {"", "-", "n/a", "none"}:
                    known_orgs.add(f"{campaign_country(row)}:email:{mail_lower}")
                else:
                    known_orgs.add(f"{campaign_country(row)}:{norm_domain(site)}")

    conn = db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM leads WHERE status='done' AND email IS NOT NULL "
        "ORDER BY country, domain").fetchall()
    conn.close()

    fresh: list[list[str]] = []
    skipped = {"mail": 0, "kurum": 0, "ulkesiz": 0}
    for row in rows:
        mail = (row["email"] or "").strip().lower()
        domain = row["domain"]
        country = (row["country"] or "").strip().upper()
        if not country:
            skipped["ulkesiz"] += 1
            continue
        tag = f"{country}-EN"
        # freemail/site-siz kayitlarda site sutunu bos birakilir; kampanya
        # kodu o zaman kurum anahtarini e-postadan uretir (cakisma olmaz)
        site_value = "" if ("@" in domain or domain in FREEMAIL) else domain
        org = f"{country}:{site_value}" if site_value else f"{country}:email:{mail}"
        if mail in known_mails:
            skipped["mail"] += 1
            continue
        if org in known_orgs:
            skipped["kurum"] += 1
            continue
        known_mails.add(mail)
        known_orgs.add(org)
        fresh.append([tag, row["name"] or mail.split("@")[0], row["city"] or "",
                      mail, site_value, row["note"] or ""])

    out = Path(args.out)
    with open(out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["oncelik", "firma", "sehir", "email", "site", "dil_notu"])
        writer.writerows(fresh)
    print(f"{len(fresh)} yeni satir -> {out}")
    print(f"  elenen: ayni e-posta {skipped['mail']}, ayni kurum {skipped['kurum']}, "
          f"ulkesiz {skipped['ulkesiz']}")


def cmd_stats(args) -> None:
    conn = db()
    rows = conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) FROM leads GROUP BY source ORDER BY 2 DESC LIMIT 8").fetchall()
    conn.close()
    print(f"havuz: {total}")
    for status, count in rows:
        print(f"  {status}: {count}")
    print("kaynaklar:")
    for source, count in by_source:
        print(f"  {source}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("osm", help="OpenStreetMap'ten sehir bazli aday topla")
    p.add_argument("--area", required=True)
    p.add_argument("--country", default="")
    p.add_argument("--types", default=OSM_DEFAULT_TYPES)
    p.set_defaults(func=cmd_osm)

    p = sub.add_parser("links", help="liste/rehber sayfasindan domain topla")
    p.add_argument("--url", required=True)
    p.add_argument("--country", default="")
    p.add_argument("--city", default="")
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("seed", help="elle domain ekle")
    p.add_argument("--domains", required=True)
    p.add_argument("--country", default="")
    p.add_argument("--city", default="")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("emails", help="bekleyen sitelerden e-posta cikar")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--workers", type=int, default=6)
    p.set_defaults(func=cmd_emails)

    p = sub.add_parser("export", help="kampanya formatinda CSV uret")
    p.add_argument("--out", default="yeni-firmalar.csv")
    p.add_argument("--tag", default="NL-EN", help="oncelik etiketi (NL-EN, DE-EN...)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="durum ozeti")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
