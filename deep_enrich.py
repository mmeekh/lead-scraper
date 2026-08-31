#!/usr/bin/env python3
"""Sirket sitesini derin tarar, CV uyumunu puanlar ve adresi kanitlar.

Ana sayfanin yaninda hakkimizda, urun/hizmet, kariyer ve iletisim sayfalarini
tarar. Yalnizca sayfada gercekten bulunan e-postalari kabul eder. Her uygunluk
puani aciklanabilir kanitlarla SQLite'a yazilir.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import sponsor_registry
from profile_fit import role_fit, score_profile
from scrape import (CFEMAIL_RE, EMAIL_RE, HEADERS, cf_decode, clean_emails, db,
                    norm_domain)
from sponsor_registry import SPONSOR_GATE_COUNTRIES

# SERT KAPI: GB/NL lead'i resmi sponsor sicilinde eslesmeden ASLA 'qualified'
# olamaz (degismez kural). Kapi --no-sponsor-gate ile kapatilabilir.
SPONSOR_REJECT_REASON = "sponsor kaydi yok (sicil: GB-UKVI / NL-IND)"

PAGE_HINTS = (
    "career", "careers", "jobs", "vacanc", "join-us", "join-our", "work-with",
    "about", "company", "who-we-are", "services", "solutions", "products",
    "expertise", "industries", "technology", "platform", "contact", "team",
    "open-application", "spontaneous-application",
)
CAREER_HINTS = (
    "career", "jobs", "vacanc", "join", "work-with", "open-position", "hiring",
)
ATS_HOST_PARTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "recruitee.com", "workable.com",
    "personio.", "teamtailor.com", "smartrecruiters.com", "myworkdayjobs.com",
    "join.com", "jobylon.com", "bamboohr.com",
)
PLATFORM_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "wikipedia.org", "google.com",
}
SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
    ".doc", ".docx", ".xls", ".xlsx", ".css", ".js",
)


@dataclass
class Page:
    url: str
    text: str
    html: str


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(("script", "style", "noscript", "svg", "template")):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:180_000]


def _jsonld_titles(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    titles: list[str] = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                kind = item.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    title = item.get("title")
                    if isinstance(title, str) and title.strip():
                        titles.append(" ".join(title.split()))
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return titles


def _page_links(page_url: str, html: str, official_domain: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(page_url, href).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if parsed.path.lower().endswith(SKIP_EXTENSIONS):
            continue
        host = norm_domain(url)
        label = " ".join(anchor.get_text(" ", strip=True).split()).casefold()
        blob = f"{parsed.path} {parsed.query} {label}".casefold()
        hints = sum(hint in blob for hint in PAGE_HINTS)
        same_site = host == official_domain or host.endswith("." + official_domain)
        external_ats = any(part in host for part in ATS_HOST_PARTS) and any(
            hint in blob for hint in CAREER_HINTS
        )
        if not same_site and not external_ats:
            continue
        if not hints and parsed.path not in {"", "/"}:
            continue
        priority = hints * 10 + (8 if any(h in blob for h in CAREER_HINTS) else 0)
        priority += 4 if "contact" in blob else 0
        found[url] = max(priority, found.get(url, 0))
    return sorted(((score, url) for url, score in found.items()), reverse=True)


def _fetch(session: requests.Session, url: str) -> requests.Response | None:
    try:
        response = session.get(url, timeout=15, allow_redirects=True)
        if response.status_code >= 400:
            return None
        content_type = response.headers.get("Content-Type", "").casefold()
        if content_type and "html" not in content_type and "text" not in content_type:
            return None
        return response
    except requests.RequestException:
        return None


def _root_page(session: requests.Session, domain: str) -> requests.Response | None:
    for scheme in ("https", "http"):
        response = _fetch(session, f"{scheme}://{domain}/")
        if response is not None:
            return response
    return None


def _emails_on_page(html: str) -> set[str]:
    found = set(EMAIL_RE.findall(html))
    for encoded in CFEMAIL_RE.findall(html):
        decoded = cf_decode(encoded)
        if decoded:
            found.add(decoded)
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.select('a[href^="mailto:"]'):
        address = anchor.get("href", "")[7:].split("?", 1)[0]
        if address:
            found.add(address)
    return found


def _link_job_titles(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    titles: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if 4 <= len(label) <= 120 and role_fit(label):
            titles.append(label)
    return titles


# baslik icindeki "Firma | Slogan" gibi parcalari ayirmak icin
_NAME_SEP_RE = re.compile(r"\s*\|\s*|\s+-\s+|\s*[–—·•]\s*|\s*::\s*")


def _site_name_candidates(pages: list[Page]) -> list[str]:
    """Ana sayfanin <title> ve og:site_name/og:title metalarindan isim adaylari."""
    if not pages:
        return []
    soup = BeautifulSoup(pages[0].html, "lxml")
    raw: list[str] = []
    if soup.title and soup.title.string:
        raw.append(soup.title.string)
    for selector in ('meta[property="og:site_name"]', 'meta[property="og:title"]',
                     'meta[name="application-name"]'):
        for node in soup.select(selector):
            content = (node.get("content") or "").strip()
            if content:
                raw.append(content)
    names: list[str] = []
    for value in raw:
        value = " ".join(value.split())
        if not value:
            continue
        names.append(value)
        names.extend(part.strip() for part in _NAME_SEP_RE.split(value) if part.strip())
    return list(dict.fromkeys(names))[:8]


def _sponsor_gate_pass(conn: sqlite3.Connection, code: str, row: dict) -> bool:
    """Crawl oncesi kapi: lead adi sicille eslesiyorsa gecer.

    ':uksr' kaynagina otomatik gecis BILEREK yok: o isimler zaten sicilden
    geldigi icin tam eslesme bedavaya gecer; otomatik gecis ise yanlis domain'e
    cozulmus bir kaydin kapiyi bypass etmesine izin verirdi (31 Agu incelemesi).
    """
    name = (row.get("name") or "").strip()
    return bool(name) and sponsor_registry.is_sponsor(conn, code, name)


def crawl_company(domain: str, max_pages: int) -> tuple[list[Page], list[str], str]:
    if "@" in domain or domain in PLATFORM_HOSTS:
        return [], [], "site_yok_veya_platform"
    session = requests.Session()
    session.headers.update(HEADERS)
    root = _root_page(session, domain)
    if root is None:
        session.close()
        return [], [], "ana_sayfa_alinamadi"

    official = norm_domain(root.url) or domain
    pages: list[Page] = []
    titles: list[str] = []
    visited: set[str] = set()
    queue: list[tuple[int, str]] = [(100, root.url)]
    queued = {root.url}

    while queue and len(pages) < max_pages:
        queue.sort(reverse=True)
        _, url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        response = root if not pages and url == root.url else _fetch(session, url)
        if response is None:
            continue
        html = response.text[:2_000_000]
        text = _visible_text(html)
        pages.append(Page(response.url, text, html))
        titles.extend(_jsonld_titles(html))
        titles.extend(_link_job_titles(html))
        for priority, link in _page_links(response.url, html, official):
            if link not in visited and link not in queued:
                queue.append((priority, link))
                queued.add(link)
        time.sleep(0.25)
    session.close()
    return pages, list(dict.fromkeys(titles))[:40], "ok"


def enrich_row(row: dict, max_pages: int, min_score: int) -> dict:
    domain = row["domain"]
    pages, discovered_titles, note = crawl_company(domain, max_pages)
    prior_titles = [x.strip() for x in (row.get("job_titles") or "").split("|") if x.strip()]
    titles = list(dict.fromkeys(prior_titles + discovered_titles))[:40]
    combined_text = " ".join(page.text for page in pages)
    fit = score_profile(
        combined_text,
        name=row.get("name") or "",
        domain=domain,
        job_titles=tuple(titles),
    )

    raw_emails: set[str] = set()
    source_by_email: dict[str, str] = {}
    for page in pages:
        for email in _emails_on_page(page.html):
            lowered = email.strip().lower()
            raw_emails.add(lowered)
            source_by_email.setdefault(lowered, page.url)
    if row.get("email"):
        raw_emails.add(row["email"].strip().lower())
    raw_emails.update(
        email.strip().lower() for email in (row.get("all_mails") or "").split(",")
        if email.strip()
    )
    cleaned = clean_emails(raw_emails, domain)
    primary = cleaned[0] if cleaned else None
    source_url = source_by_email.get(primary or "") or row.get("email_source_url")
    career_url = next(
        (page.url for page in pages if any(h in page.url.casefold() for h in CAREER_HINTS)),
        row.get("career_url"),
    )
    qualified = fit.score >= min_score and fit.qualified
    return {
        "domain": domain,
        "email": primary,
        "all_mails": ",".join(cleaned[:8]),
        "email_source_url": source_url,
        "career_url": career_url,
        "job_titles": " | ".join(titles),
        "fit_score": fit.score,
        "fit_tracks": ",".join(fit.tracks),
        "fit_reasons": " | ".join(fit.reasons),
        "fit_keywords": ",".join(fit.keywords[:40]),
        "english_signal": int(fit.english_signal),
        "profile_status": "qualified" if qualified else ("error" if not pages else "rejected"),
        "crawl_note": note,
        "pages": len(pages),
        "resolved": bool(pages),
        "site_names": _site_name_candidates(pages),
    }


def load_rows(countries: set[str], limit: int, rescore: bool,
              retry_errors: bool, source_prefix: str = "") -> list[dict]:
    conn = db()
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in countries)
    if rescore:
        status_clause = ""
    elif retry_errors:
        status_clause = "AND profile_status IN ('unscored','error')"
    else:
        status_clause = "AND profile_status='unscored'"
    # substr karsilastirmasi: etiketteki '_'/'%' LIKE jokeri olarak islemesin
    source_clause = "AND substr(source,1,?)=?" if source_prefix else ""
    values: list[object] = [*sorted(countries)]
    if source_prefix:
        values.extend([len(source_prefix), source_prefix])
    values.append(limit)
    rows = conn.execute(
        f"SELECT * FROM leads WHERE country IN ({placeholders}) {status_clause} {source_clause} "
        "ORDER BY CASE source WHEN 'jobseek-ats' THEN 0 ELSE 1 END, rowid LIMIT ?",
        values,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", default="IE,SE,DK,NO,MT")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--min-score", type=int, default=35)
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--source-prefix", default="", help="yalnizca bu kaynakla baslayan adaylar")
    parser.add_argument("--no-sponsor-gate", action="store_true",
                        help="GB/NL sponsor sicil kapisini devre disi birak")
    args = parser.parse_args()
    countries = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
    countries.discard("CY")  # campaign-level exclusion: Southern Cyprus removed
    if not countries:
        print("hedef ulke kampanyadan cikarilmis; islem yapilmadi")
        return

    conn = db()
    gate_countries: set[str] = set() if args.no_sponsor_gate else (
        countries & SPONSOR_GATE_COUNTRIES)
    if gate_countries:
        sponsor_registry.ensure_table(conn)
        empty_registry = {
            code for code in sorted(gate_countries)
            if sponsor_registry.sponsor_count(conn, code) == 0}
        if empty_registry:
            # sicil bos/eksikken GB/NL sessizce gecirilmez (fail-closed);
            # ama karma listede diger ulkelerin isi engellenmez
            print(f"sponsors tablosu bos ({','.join(sorted(empty_registry))}): once "
                  f"'python3 sponsor_registry.py refresh --countries GB,NL' calistir")
            countries -= empty_registry
            gate_countries -= empty_registry
            if not countries:
                conn.close()
                sys.exit(1)
            print(f"UYARI: {','.join(sorted(empty_registry))} bu turda ATLANDI; "
                  f"kalan ulkelerle devam ediliyor", flush=True)

    rows = load_rows(countries, args.limit, args.rescore, args.retry_errors, args.source_prefix)
    if not rows:
        print("puanlanacak sirket yok")
        conn.close()
        return

    # SERT KAPI on kontrolu: isim eslesmesi gecenler isaretlenir. Eslesmeyen
    # lead HEMEN reddedilmez - ticari ad ile tescilli ad sistematik ayrisir
    # ('Kayak' vs 'Kayak Software (UK) Limited'); karar crawl sonrasi site
    # basligi/metadan bir kez daha denendikten sonra verilir (31 Agu inceleme
    # bulgusu: on-crawl red kalici sahte negatif uretiyordu).
    row_country = {row["domain"]: (row.get("country") or "").strip().upper()
                   for row in rows}
    gate_passed: set[str] = set()
    for row in rows:
        code = row_country[row["domain"]]
        if code not in gate_countries or _sponsor_gate_pass(conn, code, row):
            gate_passed.add(row["domain"])

    print(f"{len(rows)} sirket derin taranacak; workers={args.workers}", flush=True)
    done = qualified = with_email = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(enrich_row, row, args.max_pages, args.min_score): row["domain"]
            for row in rows
        }
        for future in as_completed(futures):
            domain = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "domain": domain, "email": None, "all_mails": "",
                    "email_source_url": None, "career_url": None, "job_titles": "",
                    "fit_score": 0, "fit_tracks": "", "fit_reasons": "",
                    "fit_keywords": "", "english_signal": 0,
                    "profile_status": "error", "crawl_note": type(exc).__name__, "pages": 0,
                    "resolved": False,
                }
            code = row_country.get(domain, "")
            if code in gate_countries and domain not in gate_passed:
                # isim eslesmedi: site basligi/metadan bir kez daha dene.
                # Baslik parcalari GUVENILMEZ aday oldugu icin yalnizca TAM
                # eslesme sayilir (is_sponsor_strict); 'Home'/'London' gibi
                # jenerik parcalar kapiyi acamaz (31 Agu inceleme bulgusu).
                if result["resolved"] and any(
                        sponsor_registry.is_sponsor_strict(conn, code, candidate)
                        for candidate in result.get("site_names") or []):
                    gate_passed.add(domain)
                elif result["resolved"] or result["profile_status"] == "qualified":
                    # degismez kural: sicil eslesmesi olmadan GB/NL qualified olamaz
                    result["profile_status"] = "rejected"
                    reasons = result.get("fit_reasons") or ""
                    if SPONSOR_REJECT_REASON not in reasons:
                        result["fit_reasons"] = (
                            f"{reasons} | {SPONSOR_REJECT_REASON}" if reasons
                            else SPONSOR_REJECT_REASON)
            new_status = "done" if result["email"] else "noemail"
            conn.execute(
                "UPDATE leads SET email=CASE WHEN ? THEN ? ELSE email END, "
                "all_mails=CASE WHEN ? THEN ? ELSE all_mails END, "
                "status=CASE WHEN ? THEN ? ELSE status END, "
                "email_source_url=CASE WHEN ? THEN ? ELSE email_source_url END, "
                "career_url=COALESCE(?,career_url), "
                "job_titles=CASE WHEN ?<>'' THEN ? ELSE job_titles END, "
                "fit_score=?, fit_tracks=?, fit_reasons=?, fit_keywords=?, "
                "english_signal=?, profile_status=?, profile_checked_at=datetime('now'), "
                "note=CASE WHEN ?='ok' THEN note ELSE COALESCE(note,'') || ' deep:' || ? END "
                "WHERE domain=?",
                (result["resolved"], result["email"], result["resolved"], result["all_mails"],
                 result["resolved"], new_status,
                 result["resolved"], result["email_source_url"], result["career_url"],
                 result["job_titles"], result["job_titles"],
                 result["fit_score"], result["fit_tracks"], result["fit_reasons"],
                 result["fit_keywords"], result["english_signal"], result["profile_status"],
                 result["crawl_note"], result["crawl_note"], domain),
            )
            conn.commit()
            done += 1
            qualified += result["profile_status"] == "qualified"
            with_email += bool(result["email"])
            if done % 10 == 0 or result["profile_status"] == "qualified":
                print(
                    f"[{done}/{len(rows)}] {domain} score={result['fit_score']} "
                    f"email={'yes' if result['email'] else 'no'} pages={result['pages']}",
                    flush=True,
                )
    conn.close()
    print(f"bitti: {qualified} uygun, {with_email} adresli / {len(rows)}")


if __name__ == "__main__":
    main()
