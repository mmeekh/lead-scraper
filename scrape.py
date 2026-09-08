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
    "CAMPAIGN_CSV", "/root/projects/otomasyon-paneli/apps/personal-job-outreach/nl-job-outreach/firmalar.csv"))
CAMPAIGN_LOG = Path(os.environ.get(
    "CAMPAIGN_LOG", "/root/projects/otomasyon-paneli/apps/personal-job-outreach/nl-job-outreach/sent-log.csv"))

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
              "webmaster", "globalwebmaster", "hostmaster", "privacy", "dpo",
              "avg", "gdpr", "support", "help", "sales", "customerservice",
              "klantenservice"}
# platform/ajans adresleri - firmanin kendisi degil
JUNK_DOMAIN = {
    "sentry.io", "wordpress.com", "wix.com", "jouwweb.nl", "squarespace.com",
    "godaddy.com", "cloudflare.com", "google.com", "gmail.example",
    "shopify.com", "webflow.com", "strato.de", "ionos.de", "hostnet.nl",
}
# genel mail saglayicilarina izin var (kucuk ofisler kullaniyor)
FREEMAIL = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "gmx.de",
            "gmx.net", "web.de", "t-online.de", "hetnet.nl", "ziggo.nl",
            "live.nl", "kpnmail.nl", "planet.nl", "icloud.com", "aol.com",
            "aol.de", "freenet.de", "online.de", "magenta.de", "outlook.de",
            "hotmail.de", "yahoo.de", "protonmail.com", "proton.me",
            "mailbox.org"}

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

PROFILE_COLUMNS = {
    "fit_score": "INTEGER NOT NULL DEFAULT 0",
    "fit_tracks": "TEXT",
    "fit_reasons": "TEXT",
    "fit_keywords": "TEXT",
    "career_url": "TEXT",
    "job_urls": "TEXT",
    "ats_priority": "INTEGER NOT NULL DEFAULT 0",
    "email_source_url": "TEXT",
    "job_titles": "TEXT",
    "english_signal": "INTEGER NOT NULL DEFAULT 0",
    "profile_status": "TEXT NOT NULL DEFAULT 'unscored'",
    "profile_checked_at": "TEXT",
    "browser_checked_at": "TEXT",
}


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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(leads)")}
    for name, definition in PROFILE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_profile_queue "
        "ON leads(country, profile_status, fit_score)"
    )
    # 6 Eyl 2026: export_fit_audit.py her aday icin lower(email) uzerinden
    # arama yapiyor. Indekssiz halde 573 bin satirlik tam tarama demekti
    # (sorgu basina ~311 ms); 3.000 adayli bir yayin turu 15 dakikayi asip
    # yayincinin 180 sn'lik adim zaman asimina takiliyordu.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_email_lower ON leads(lower(email))"
    )
    # Panel "son taranan 25" listesi checked_at'e gore siralar; indekssiz halde
    # 573 bin satirlik siralama demekti.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_checked_at ON leads(checked_at)")
    conn.commit()
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
        cursor = conn.execute(
            "INSERT OR IGNORE INTO leads(domain,name,city,country,source) VALUES(?,?,?,?,?)",
            (domain, name, city, country, source))
        return cursor.rowcount == 1
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
        direct_mails = clean_emails({mail}, domain) if mail and EMAIL_RE.fullmatch(mail.strip()) else []
        if direct_mails:
            conn.execute(
                "UPDATE leads SET status='done', email=?, note='OSM etiketinden', "
                "checked_at=datetime('now') WHERE domain=? AND status='pending'",
                (direct_mails[0], domain))
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


def _base_label(host: str) -> str:
    """Public-suffix kutuphanesi gerektirmeyen temkinli kurum etiketi."""
    labels = [part for part in host.lower().split(".") if part]
    if len(labels) < 2:
        return labels[0] if labels else ""
    # example.com.tr / example.co.uk gibi yaygin iki parcali uzantilar.
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {
        "co", "com", "org", "net",
    }:
        return labels[-3]
    return labels[-2]


def email_matches_domain(mail: str, domain: str) -> bool:
    local, _, host = mail.partition("@")
    if not local or not host:
        return False
    # Sitesi olmayan dogrudan e-posta kayitlarinda anahtar adresin kendisidir.
    if "@" in domain:
        return mail == domain.lower()
    if host in FREEMAIL:
        return True
    if (host == domain or host.endswith("." + domain)
            or domain.endswith("." + host)):
        return True
    # Ayni kurumun farkli ulke uzantilari (firma.de / firma.com) kabul edilir.
    return _base_label(host) == _base_label(domain)


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
        if not email_matches_domain(mail, domain):
            continue
        out.append(mail)
    # Is basvurusu adresleri once, genel kurum adresleri sonra, kisisel adresler en son.
    priority = (
        "career", "careers", "jobs", "job", "hr", "recruiting", "recruitment",
        "bewerbung", "bewerbungen", "karriere", "werkenbij", "talent", "personal",
        "info", "contact", "kontakt", "office", "mail", "kantoor", "administratie",
    )
    rank = {local: index for index, local in enumerate(priority)}
    out = sorted(set(out), key=lambda m: (
        rank.get(m.split("@")[0], len(priority)), len(m)))
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
    countries = [code.strip().upper() for code in args.countries.split(",") if code.strip()]
    clauses = ["status='pending'"]
    values: list[str | int] = []
    if countries:
        clauses.append("country IN (" + ",".join("?" for _ in countries) + ")")
        values.extend(countries)
    if args.source_prefix:
        # substr: etiketteki '_'/'%' LIKE jokeri olarak islemesin
        clauses.append("substr(source,1,?)=?")
        values.extend([len(args.source_prefix), args.source_prefix])
    values.append(args.limit)
    rows = conn.execute(
        "SELECT * FROM leads WHERE " + " AND ".join(clauses) + " ORDER BY rowid LIMIT ?",
        values,
    ).fetchall()
    if not rows:
        print("islenecek bekleyen aday yok")
        conn.close()
        return
    print(f"{len(rows)} site taranacak (es zamanli {args.workers})")

    done = ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_site, r): r["domain"] for r in rows}
        for future in as_completed(futures):
            try:
                domain, status, mails, note = future.result()
            except Exception as exc:
                domain = futures[future]
                status, mails = "error", []
                note = f"worker:{type(exc).__name__}"
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


def cmd_retry_errors(args) -> None:
    """Yalnizca gecici ag/sunucu hatalarini yeniden kuyruğa alir."""
    transient = (
        "ConnectionError", "SSLError", "ConnectTimeout", "ReadTimeout",
        "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503",
    )
    conn = db()
    placeholders = ",".join("?" for _ in transient)
    cursor = conn.execute(
        f"UPDATE leads SET status='pending', email=NULL, all_mails=NULL, "
        f"checked_at=NULL WHERE note IN ({placeholders})",
        transient,
    )
    conn.commit()
    conn.close()
    print(f"{cursor.rowcount} gecici hata yeniden kuyruga alindi")


def cmd_reclean(args) -> None:
    """Daha once bulunan adresleri guncel kalite kurallariyla yeniden temizler.

    Bir kaydin tum adresleri elenirse site silinmez; daha iyi bir adres
    bulunabilmesi icin yeniden tarama kuyruguna alinir.
    """
    conn = db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT domain,email,all_mails FROM leads "
        "WHERE status='done' AND email IS NOT NULL"
    ).fetchall()
    updated = pending = 0
    for row in rows:
        candidates = {
            mail.strip() for mail in (row["all_mails"] or "").split(",")
            if mail.strip()
        }
        candidates.add(row["email"].strip())
        cleaned = clean_emails(candidates, row["domain"])
        if cleaned:
            new_primary = cleaned[0]
            new_all = ",".join(cleaned[:6])
            if new_primary != row["email"] or new_all != (row["all_mails"] or ""):
                conn.execute(
                    "UPDATE leads SET email=?, all_mails=? WHERE domain=?",
                    (new_primary, new_all, row["domain"]),
                )
                updated += 1
        else:
            conn.execute(
                "UPDATE leads SET status='pending', "
                "note='recheck: dusuk kaliteli adres elendi', checked_at=NULL "
                "WHERE domain=?",
                (row["domain"],),
            )
            pending += 1
    conn.commit()
    conn.close()
    print(f"{updated} kaydin adres sirasi/icerigi temizlendi")
    print(f"{pending} kayit daha iyi adres icin yeniden kuyruga alindi")


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
        "ORDER BY CASE WHEN fit_reasons LIKE '%turkish_company:%' THEN 0 ELSE 1 END, "
        "fit_score DESC, country, domain").fetchall()
    conn.close()

    fresh: list[list[str]] = []
    skipped = {"mail": 0, "kurum": 0, "ulkesiz": 0, "kaynaksiz": 0}
    wanted_countries = {
        code.strip().upper() for code in args.countries.split(",") if code.strip()
    }
    for row in rows:
        mail = (row["email"] or "").strip().lower()
        domain = row["domain"]
        country = (row["country"] or "").strip().upper()
        if not country:
            skipped["ulkesiz"] += 1
            continue
        if wanted_countries and country not in wanted_countries:
            continue
        if row["fit_score"] < args.min_fit_score:
            continue
        if args.min_fit_score > 0 and row["profile_status"] != "qualified":
            continue
        if args.require_email_source and not (row["email_source_url"] or "").startswith("https://"):
            skipped["kaynaksiz"] += 1
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
        if args.limit and len(fresh) >= args.limit:
            break

    out = Path(args.out)
    with open(out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["oncelik", "firma", "sehir", "email", "site", "dil_notu"])
        writer.writerows(fresh)
    print(f"{len(fresh)} yeni satir -> {out}")
    print(f"  elenen: ayni e-posta {skipped['mail']}, ayni kurum {skipped['kurum']}, "
          f"ulkesiz {skipped['ulkesiz']}, kaynak-linki-yok {skipped['kaynaksiz']}")


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
    p.add_argument("--countries", default="", help="yalnizca bu ulke kodlari")
    p.add_argument("--source-prefix", default="", help="yalnizca bu kaynakla baslayan adaylar")
    p.set_defaults(func=cmd_emails)

    p = sub.add_parser("export", help="kampanya formatinda CSV uret")
    p.add_argument("--out", default="yeni-firmalar.csv")
    p.add_argument("--tag", default="NL-EN", help="oncelik etiketi (NL-EN, DE-EN...)")
    p.add_argument("--countries", default="",
                   help="virgullu ulke filtresi (ornegin IE,SE,DK,NO,MT)")
    p.add_argument("--min-fit-score", type=int, default=0,
                   help="yalnizca bu CV uyum puani ve uzerini aktar")
    p.add_argument("--limit", type=int, default=0,
                   help="en yuksek puanli en fazla N yeni kaydi aktar (0=tumu)")
    p.add_argument("--require-email-source", action="store_true",
                   help="yalnizca HTTPS kaynak sayfasinda yeniden dogrulanan adresleri aktar")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="durum ozeti")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("retry-errors", help="gecici ag/sunucu hatalarini yeniden dene")
    p.set_defaults(func=cmd_retry_errors)

    p = sub.add_parser("reclean", help="bulunan adresleri guncel kalite kurallariyla temizle")
    p.set_defaults(func=cmd_reclean)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
