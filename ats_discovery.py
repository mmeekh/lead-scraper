#!/usr/bin/env python3
"""Acik ATS kataloglarindan hedef buyuk sehirlerde isveren kesfeder.

Sirket/board katalogu Colophon Group Job Seek projesinin CC BY-NC 4.0 veri
setinden sabitlenmis bir Git commit'iyle alinir. Kod kopyalanmaz; Greenhouse,
Ashby, Lever ve Recruitee'nin herkese acik ilan API'leri bu projenin kendi
restart-safe SQLite kuyruguyla taranir.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from profile_fit import role_fit, score_profile, target_country_from_location
from scrape import HEADERS, add_lead, db, norm_domain

BASE = Path(__file__).parent
CATALOG = BASE / "catalog"
JOBSEEK_COMMIT = "143aa449c31d9d846ddad314953b3bfecbada739"
RAW_BASE = f"https://raw.githubusercontent.com/colophon-group/jobseek/{JOBSEEK_COMMIT}/apps/crawler/data"
FILES = ("companies.csv", "boards.csv")
SUPPORTED = {"greenhouse", "ashby", "lever", "recruitee"}
SHARED_ATS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "recruitee.com", "workable.com",
    "personio.", "teamtailor.com", "smartrecruiters.com", "myworkdayjobs.com",
)


@dataclass(frozen=True)
class Job:
    title: str
    locations: tuple[str, ...]
    description: str


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ats_boards (
            board_slug TEXT PRIMARY KEY,
            company_slug TEXT,
            company_name TEXT,
            website TEXT,
            board_url TEXT,
            monitor_type TEXT,
            monitor_config TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            target_jobs INTEGER NOT NULL DEFAULT 0,
            profile_jobs INTEGER NOT NULL DEFAULT 0,
            matched_country TEXT,
            matched_city TEXT,
            job_titles TEXT,
            note TEXT,
            checked_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ats_status ON ats_boards(status)")
    conn.commit()


def download_catalog(force: bool = False) -> None:
    CATALOG.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "text/csv,*/*;q=0.8"})
    for filename in FILES:
        destination = CATALOG / f"jobseek-{filename}"
        if destination.exists() and destination.stat().st_size > 1000 and not force:
            continue
        response = session.get(f"{RAW_BASE}/{filename}", timeout=60)
        response.raise_for_status()
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(response.content)
        temporary.replace(destination)
    session.close()


def sync_catalog(force: bool = False) -> None:
    download_catalog(force)
    companies_path = CATALOG / "jobseek-companies.csv"
    boards_path = CATALOG / "jobseek-boards.csv"
    with companies_path.open(encoding="utf-8") as handle:
        companies = {row["slug"]: row for row in csv.DictReader(handle)}

    conn = db()
    ensure_table(conn)
    inserted = eligible = 0
    with boards_path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["monitor_type"] not in SUPPORTED:
                continue
            company = companies.get(row["company_slug"])
            if not company or not company.get("website"):
                continue
            eligible += 1
            cursor = conn.execute(
                "INSERT OR IGNORE INTO ats_boards(" 
                "board_slug,company_slug,company_name,website,board_url,monitor_type,monitor_config" 
                ") VALUES(?,?,?,?,?,?,?)",
                (row["board_slug"], row["company_slug"], company["name"], company["website"],
                 row["board_url"], row["monitor_type"], row["monitor_config"]),
            )
            inserted += cursor.rowcount == 1
            conn.execute(
                "UPDATE ats_boards SET company_name=?,website=?,board_url=?,monitor_config=? "
                "WHERE board_slug=?",
                (company["name"], company["website"], row["board_url"],
                 row["monitor_config"], row["board_slug"]),
            )
    conn.commit()
    total = conn.execute("SELECT count(*) FROM ats_boards").fetchone()[0]
    conn.close()
    print(f"Job Seek katalogu: {eligible} desteklenen board, {inserted} yeni, toplam {total}")
    print(f"kaynak commit: {JOBSEEK_COMMIT}")


def _get_json(url: str, *, params: dict | None = None) -> object:
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "application/json"})
    last_error = "request_failed"
    try:
        for attempt in range(3):
            try:
                response = session.get(url, params=params, timeout=25, allow_redirects=True)
                if response.status_code == 404:
                    raise FileNotFoundError("HTTP 404")
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = f"HTTP {response.status_code}"
                    time.sleep((attempt + 1) * 1.5 + random.random())
                    continue
                response.raise_for_status()
                return response.json()
            except FileNotFoundError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = type(exc).__name__
                time.sleep((attempt + 1) + random.random())
        raise RuntimeError(last_error)
    finally:
        session.close()


def _config(row: dict) -> dict:
    try:
        value = json.loads(row.get("monitor_config") or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def fetch_greenhouse(row: dict) -> list[Job]:
    token = _config(row).get("token")
    if not token:
        token = urlparse(row["board_url"]).path.strip("/").split("/")[0]
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "true"},
    )
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    result: list[Job] = []
    for item in jobs:
        locations: list[str] = []
        location = item.get("location") or {}
        if isinstance(location, dict) and location.get("name"):
            locations.append(str(location["name"]))
        locations.extend(
            str(office["name"]) for office in item.get("offices", [])
            if isinstance(office, dict) and office.get("name")
        )
        result.append(Job(str(item.get("title") or ""), tuple(locations),
                          str(item.get("content") or "")))
    return result


def fetch_ashby(row: dict) -> list[Job]:
    token = _config(row).get("token") or urlparse(row["board_url"]).path.strip("/").split("/")[0]
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    result: list[Job] = []
    for item in jobs:
        locations: list[str] = []
        if item.get("location"):
            locations.append(str(item["location"]))
        for location in item.get("secondaryLocations", []):
            value = location if isinstance(location, str) else location.get("location", "")
            if value:
                locations.append(str(value))
        result.append(Job(str(item.get("title") or ""), tuple(locations),
                          str(item.get("descriptionPlain") or item.get("descriptionHtml") or "")))
    return result


def fetch_lever(row: dict) -> list[Job]:
    token = _config(row).get("token") or urlparse(row["board_url"]).path.strip("/").split("/")[0]
    eu = ".eu.lever.co" in row["board_url"]
    host = "https://api.eu.lever.co" if eu else "https://api.lever.co"
    result: list[Job] = []
    for skip in range(0, 5000, 100):
        data = _get_json(f"{host}/v0/postings/{token}", params={"mode": "json", "limit": 100, "skip": skip})
        if not isinstance(data, list):
            break
        for item in data:
            categories = item.get("categories") or {}
            locations = categories.get("allLocations") or []
            if not locations and categories.get("location"):
                locations = [categories["location"]]
            description = " ".join(str(item.get(key) or "") for key in ("description", "additional"))
            result.append(Job(str(item.get("text") or ""), tuple(map(str, locations)), description))
        if len(data) < 100:
            break
        time.sleep(0.55)
    return result


def fetch_recruitee(row: dict) -> list[Job]:
    config = _config(row)
    api_base = config.get("api_base")
    if not api_base:
        parsed = urlparse(row["board_url"])
        api_base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    data = _get_json(f"{str(api_base).rstrip('/')}/api/offers")
    offers = data.get("offers", []) if isinstance(data, dict) else []
    result: list[Job] = []
    for item in offers:
        locations: list[str] = []
        for location in item.get("locations", []):
            if not isinstance(location, dict):
                continue
            value = ", ".join(str(x) for x in (location.get("city"), location.get("country")) if x)
            if value:
                locations.append(value)
        if not locations and item.get("location"):
            locations.append(str(item["location"]))
        description = " ".join(str(item.get(key) or "") for key in ("description", "requirements"))
        result.append(Job(str(item.get("title") or ""), tuple(locations), description))
    return result


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "recruitee": fetch_recruitee,
}


def scan_board(row: dict, countrywide: set[str] | None = None) -> dict:
    try:
        jobs = FETCHERS[row["monitor_type"]](row)
    except FileNotFoundError:
        return {"status": "dead", "note": "HTTP 404", "target": [], "profile": []}
    except Exception as exc:
        return {"status": "error", "note": type(exc).__name__, "target": [], "profile": []}

    target: list[tuple[Job, str, str]] = []
    profile: list[Job] = []
    for job in jobs:
        matches = [target_country_from_location(location, countrywide) for location in job.locations]
        matches = [match for match in matches if match]
        if not matches:
            continue
        country, city = matches[0]
        target.append((job, country, city))
        fit = score_profile(job.description, name=row["company_name"],
                            domain=row["website"], job_titles=(job.title,))
        if role_fit(job.title) or fit.score >= 45:
            profile.append(job)
    return {"status": "matched" if target else "offtarget", "note": f"jobs={len(jobs)}",
            "target": target, "profile": profile}


def merge_titles(existing: str, incoming: list[str]) -> str:
    values = [value.strip() for value in (existing or "").split("|") if value.strip()]
    values.extend(title.strip() for title in incoming if title.strip())
    return " | ".join(dict.fromkeys(values))[:8000]


def scan_catalog(limit: int, workers: int, retry_errors: bool, rescan: bool = False,
                 countrywide: set[str] | None = None) -> None:
    conn = db()
    ensure_table(conn)
    conn.row_factory = sqlite3.Row
    if rescan:
        # Yeni ülke/şehir eklendiğinde önceki "offtarget" sonucu artık geçerli
        # değildir. Katalogu tekrar okumak güvenlidir: lead ekleme domain bazında
        # idempotent, gönderim kuyruğuna ise bu program hiç yazmaz.
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM ats_boards WHERE status != 'dead' ORDER BY rowid LIMIT ?", (limit,)
        ).fetchall()]
    else:
        statuses = ("pending", "error") if retry_errors else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM ats_boards WHERE status IN ({placeholders}) ORDER BY rowid LIMIT ?",
            (*statuses, limit),
        ).fetchall()]
    if not rows:
        print("taranacak ATS board yok")
        conn.close()
        return

    print(f"{len(rows)} ATS board taranacak; workers={workers}", flush=True)
    done = matched = profile_count = new_leads = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_board, row, countrywide): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            result = future.result()
            target = result["target"]
            profile = result["profile"]
            country = city = ""
            titles: list[str] = []
            if target:
                counts = Counter((code, place) for _, code, place in target)
                (country, city), _ = counts.most_common(1)[0]
                titles = [job.title for job in profile] or [job.title for job, _, _ in target]
                website_domain = norm_domain(row["website"])
                if website_domain and not any(part in website_domain for part in SHARED_ATS):
                    new_leads += add_lead(
                        conn, website_domain, row["company_name"], city, country,
                        f"jobseek-ats:{row['monitor_type']}",
                    )
                    existing = conn.execute(
                        "SELECT job_titles FROM leads WHERE domain=?", (website_domain,)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE leads SET name=COALESCE(NULLIF(name,''),?), city=?, country=?, "
                            "source=CASE WHEN COALESCE(source,'') LIKE 'jobseek-ats:%' THEN source "
                            "ELSE TRIM(COALESCE(source,'') || '+jobseek-ats','+') END, "
                            "career_url=?, job_titles=?, profile_status='unscored' WHERE domain=?",
                            (row["company_name"], city, country, row["board_url"],
                             merge_titles(existing[0] or "", titles), website_domain),
                        )
            conn.execute(
                "UPDATE ats_boards SET status=?,target_jobs=?,profile_jobs=?,matched_country=?,"
                "matched_city=?,job_titles=?,note=?,checked_at=datetime('now') WHERE board_slug=?",
                (result["status"], len(target), len(profile), country, city,
                 " | ".join(dict.fromkeys(titles))[:8000], result["note"], row["board_slug"]),
            )
            conn.commit()
            done += 1
            matched += bool(target)
            profile_count += bool(profile)
            if done % 20 == 0 or target:
                print(f"[{done}/{len(rows)}] {row['company_name']} {result['status']} "
                      f"target={len(target)} profile={len(profile)}", flush=True)
    conn.close()
    print(f"bitti: {matched} hedef-sehir isvereni, {profile_count} profil-ilanli, {new_leads} yeni domain")


def stats() -> None:
    conn = db()
    ensure_table(conn)
    print("ATS durumlari:")
    for row in conn.execute("SELECT status,count(*) FROM ats_boards GROUP BY status ORDER BY 2 DESC"):
        print(f"  {row[0]}: {row[1]}")
    print("Hedef ulke eslesmeleri:")
    for row in conn.execute(
        "SELECT matched_country,count(DISTINCT company_slug) FROM ats_boards "
        "WHERE status='matched' GROUP BY matched_country ORDER BY 2 DESC"
    ):
        print(f"  {row[0]}: {row[1]}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync")
    sync.add_argument("--force", action="store_true")
    scan = sub.add_parser("scan")
    scan.add_argument("--limit", type=int, default=4000)
    scan.add_argument("--workers", type=int, default=4)
    scan.add_argument("--retry-errors", action="store_true")
    scan.add_argument("--rescan", action="store_true",
                      help="yeni ülke/şehir eklendikten sonra ölü olmayan boardları yeniden tara")
    scan.add_argument("--countrywide", default="",
                      help="şehir filtresi olmadan ülke adıyla eşleşecek kodlar (AU,SG gibi)")
    sub.add_parser("stats")
    args = parser.parse_args()
    if args.command == "sync":
        sync_catalog(args.force)
    elif args.command == "scan":
        countrywide = {code.strip().upper() for code in args.countrywide.split(",") if code.strip()}
        scan_catalog(args.limit, args.workers, args.retry_errors, args.rescan, countrywide)
    else:
        stats()


if __name__ == "__main__":
    main()
