#!/usr/bin/env python3
"""Research active-campaign targets in fair, bounded, resumable batches.

Only newly accepted queue entries count, not raw leads or old deliveries.
The publisher retains the existing evidence and deduplication gates.
"""
from __future__ import annotations

import csv
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTREACH = BASE.parent / "nl-job-outreach"
sys.path.insert(0, str(OUTREACH))
from country_campaign import (CAMPAIGN, COUNTRIES, MIN_FIT_SCORE,
                              RESEARCH_COUNTRIES, TARGET_PER_COUNTRY,
                              accepted_counts, target_for)
from project_paths import RUNTIME_DIR
from send_mails import CSV_PATH, AlreadyRunningError, queue_lock

DB = BASE / "leads.sqlite3"
BROWSER_PYTHON = BASE / ".venv-camoufox" / "bin" / "python"
BROWSER_MIN_AVAILABLE_MB = int(os.environ.get("BROWSER_MIN_AVAILABLE_MB", "900"))
LOCK = BASE / "qualified-contact-targets.lock"
PROGRESS = RUNTIME_DIR / f"{CAMPAIGN}-research.json"
CRAWL_WORKERS = max(1, min(8, int(os.environ.get("QUALIFIED_CRAWL_WORKERS", "6"))))
# 6 Eyl 2026: ulke kumesi ve uygunluk esigi degisti. Surum artisi kaynak
# asamasi durumunu sifirlar; boylece yeni ulkeler Wikidata/OSM kesfini bastan
# yapar ve IE/MT/LU'da 12/12'ye dayanmis retry sayaclari serbest kalir.
# Kabul sayilari kuyruk etiketinde durur, bu sifirlamadan etkilenmez.
PIPELINE_VERSION = 5
ATS_MARKER = RUNTIME_DIR / f"{CAMPAIGN}-ats-direct-postings-v1.done"
MAX_RETRY_ROUNDS = 12
SOURCE_RECHECK_SECONDS = int(os.environ.get("SOURCE_RECHECK_SECONDS", str(6 * 60 * 60)))
ATS_REFRESH_SECONDS = int(os.environ.get("ATS_REFRESH_SECONDS", str(12 * 60 * 60)))
IDLE_SLEEP_SECONDS = 60
# Overture's local data is useful for these two countries. The others have no
# relevant Overture rows, so they are not needlessly scanned.
OVERTURE_COUNTRIES = {"IE", "NL"}
# Arastirma sirasi country_campaign.RESEARCH_COUNTRIES'ten gelir (agirliga
# gore azalan). Burada ikinci bir liste tutulmuyor: 6-7 Eyl gecesi ayri
# tutulan RESEARCH_ORDER kumeyle uyusmadi ve `.index()` ValueError'u servisi
# 398 kez cokertip butun geceyi bosa harcadi.


def accepted(attempts: int = 10, wait: float = 6.0):
    """Kuyruk sayimlarini oku; kilit meshgulse bekleyip yeniden dene.

    7 Eyl 2026: yayinci 120 saniyede bir kuyruk kilidini aliyor. Kilit o anda
    meshgulse `queue_lock()` AlreadyRunningError firlatiyordu ve bu istisna
    butun arastirma servisini oldururuyordu -- gecici bir kilit cakismasi
    icin fazlasiyla sert bir sonuc. Kilit gercekten birakilmiyorsa hata yine
    yukselir, sessizce yanlis sayiyla devam edilmez.
    """
    for remaining in range(attempts - 1, -1, -1):
        try:
            with queue_lock(), CSV_PATH.open(encoding="utf-8-sig",
                                             newline="") as handle:
                return accepted_counts(csv.DictReader(handle))
        except AlreadyRunningError:
            if not remaining:
                raise
            time.sleep(wait)


def pending(country):
    with sqlite3.connect(DB, timeout=30) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM leads WHERE country=? AND profile_status='unscored'",
            (country,),
        ).fetchone()[0]


def has_wikidata_seed(country):
    """A restart must not repeat a completed public-source import."""
    with sqlite3.connect(DB, timeout=30) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM leads WHERE country=? AND source LIKE ? LIMIT 1",
            (country, f"{CAMPAIGN}:wikidata-{country.casefold()}%"),
        ).fetchone())


def browser_pending(country):
    """Rows worth one bounded public-page JavaScript rendering attempt."""
    with sqlite3.connect(DB, timeout=30) as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM leads
                WHERE country=?
                  AND browser_checked_at IS NULL
                  AND COALESCE(job_urls,'') <> ''
                  AND (profile_status='error'
                       OR (profile_status='qualified' AND COALESCE(email,'')='')
                       OR (profile_status='rejected' AND fit_score>=?))""",
            (country, MIN_FIT_SCORE),
        ).fetchone()[0]


def browser_enrich(country):
    """Optional Camoufox fallback; standard crawling remains the primary path."""
    if not BROWSER_PYTHON.is_file():
        return False
    available_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                available_kb = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        # If memory cannot be measured, prefer the conservative option.
        return False
    if available_kb < BROWSER_MIN_AVAILABLE_MB * 1024:
        print("Camoufox ertelendi: yeterli bos RAM yok", flush=True)
        return False
    run(BROWSER_PYTHON, "browser_enrich.py", "--countries", country,
        # 9 Eyl 2026: ilanli firmalar en sicak kaynak (%2,0 vs %0,6) ama cogu
        # e-postasini yalniz JavaScript ile yukluyor; 2'lik limit bu havuzu
        # asla eritmiyordu. Camoufox hala RAM bekcisinin (900 MB) arkasinda.
        "--limit", "10", "--max-pages", "3", "--min-score", str(MIN_FIT_SCORE))
    return True


def default_progress():
    return {c: {"discovery_rounds": 0, "empty_rounds": 0,
                "retry_rounds": 0, "overture_done": False, "wikidata_done": False,
                "next_probe_at": 0.0, "sponsor_offset": 0,
                "status": "running"} for c in RESEARCH_COUNTRIES}


def load_progress():
    if not PROGRESS.exists():
        return default_progress()
    snapshot = json.loads(PROGRESS.read_text())
    if snapshot.get("pipeline_version") != PIPELINE_VERSION:
        # Counts live in the queue marker and remain intact. Only source-stage
        # state is reset when genuinely new discovery logic is deployed.
        return default_progress()
    progress = default_progress()
    for country in RESEARCH_COUNTRIES:
        progress[country].update(snapshot.get("countries", {}).get(country, {}))
        # A depleted source is a cooldown, never a terminal state. Earlier
        # bounded versions persisted source_exhausted; recover safely from it.
        progress[country]["status"] = "running"
    return progress


def save(progress):
    counts = accepted()
    snapshot = {"campaign": CAMPAIGN, "pipeline_version": PIPELINE_VERSION,
                "target_per_country": TARGET_PER_COUNTRY,
                "accepted": {c: counts[c] for c in COUNTRIES}, "countries": progress}
    temp = PROGRESS.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(PROGRESS)


def run(*args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=BASE, check=True)


def run_capture(*args) -> str:
    """Ciktisi okunmasi gereken adimlar icin; log'a da aynen basar."""
    print("+", " ".join(map(str, args)), flush=True)
    result = subprocess.run(list(map(str, args)), cwd=BASE, check=True,
                            text=True, capture_output=True)
    print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)
    return result.stdout


def sponsor_harvest(country, progress):
    """UKVI sponsor sicilinden yeni aday cikar (yalnizca GB).

    6 Eyl 2026: sicilde sektore uygun 12.696 firma var ve bunlarin ancak
    1.532'si lead'e donusmus. Sponsor lisansi, AB disi bir adayi ise alma
    yetkisi demek; Turk vatandasi icin en yuksek donusum ihtimali olan havuz
    burasi. OSM taramasi bu firmalari isim/adres uzerinden bulamiyor.
    """
    if country != "GB":
        return False
    offset = int(progress.get("sponsor_offset", 0))
    output = run_capture(sys.executable, "harvest_uk_sponsors.py",
                         "--limit", "300", "--workers", "2",
                         "--offset", str(offset),
                         "--source-label", CAMPAIGN)
    match = re.search(r"restart icin: --offset (\d+)", output)
    if match:
        progress["sponsor_offset"] = int(match.group(1))
    added = re.search(r"eklendi=(\d+)", output)
    return bool(added and int(added.group(1)))


def discover(country, progress):
    before = pending(country)
    # GB icin once sponsor sicili: OSM'den cok daha yuksek donusumlu kaynak.
    if country == "GB" and sponsor_harvest(country, progress):
        progress["discovery_rounds"] += 1
        progress["empty_rounds"] = 0
        progress["retry_rounds"] = 0
        progress["next_probe_at"] = time.time() + SOURCE_RECHECK_SECONDS
        return True
    # Public company websites from Wikidata add genuine new domains rather
    # than repeatedly probing the same maps source. Website/contact evidence
    # still has to come from the company's own pages before publication.
    if not progress.get("wikidata_done", False) and not has_wikidata_seed(country):
        run(sys.executable, "harvest_wikidata.py", "--countries", country,
            "--new-per-country", "4000", "--source-label", CAMPAIGN)
    progress["wikidata_done"] = True
    # A fresh structured-source batch already gives the bounded enricher work.
    # Do not retain a large OSM response in RAM merely to rediscover the same
    # websites; it will be retried on a later source probe if needed.
    if pending(country) > before:
        progress["discovery_rounds"] += 1
        progress["empty_rounds"] = 0
        progress["retry_rounds"] = 0
        progress["next_probe_at"] = time.time() + SOURCE_RECHECK_SECONDS
        return True
    if country in OVERTURE_COUNTRIES and not progress.get("overture_done", False):
        run(sys.executable, "harvest_overture.py", "--countries", country,
            "--new-per-country", "900", "--source-label", CAMPAIGN)
        progress["overture_done"] = True
    run(sys.executable, "harvest_countrywide.py", "--countries", country,
        "--new-per-country", "900", "--workers", "1", "--broad-cities",
        "--source-label", CAMPAIGN)
    progress["discovery_rounds"] += 1
    after = pending(country)
    progress["next_probe_at"] = time.time() + SOURCE_RECHECK_SECONDS
    if after <= before:
        progress["empty_rounds"] += 1
    else:
        progress["empty_rounds"] = 0
        # Fresh discovery gets a new controlled error-retry allowance.
        progress["retry_rounds"] = 0
    return after > before


def refresh_ats_if_due():
    if (ATS_MARKER.exists()
            and time.time() - ATS_MARKER.stat().st_mtime < ATS_REFRESH_SECONDS):
        return False
    run(sys.executable, "ats_discovery.py", "sync")
    run(sys.executable, "ats_discovery.py", "scan", "--rescan",
        "--countrywide", ",".join(RESEARCH_COUNTRIES), "--limit", "4000",
        "--workers", str(CRAWL_WORKERS))
    ATS_MARKER.touch()
    return True


def selfcheck() -> list[str]:
    """Tarama baslamadan yapilandirmayi dogrula; sorun listesi dondur.

    7 Eyl 2026: bir liste uyusmazligi servisi 398 kez cokertti ve gece bosa
    gitti. Buradaki kontrollerin her biri o gece yasanan ya da yasanabilecek
    bir arizayi tarama baslamadan yakalar. Ayni fonksiyon test_orchestrator
    tarafindan da cagrilir; ikisi ayrismasin diye tek yerde durur.
    """
    from harvest_countrywide import COUNTRY_NAMES, MAJOR_CITIES
    from harvest_wikidata import COUNTRY_QIDS
    from send_mails import route_for

    problems: list[str] = []
    for country in RESEARCH_COUNTRIES:
        if country not in COUNTRIES:
            problems.append(f"{country}: RESEARCH_COUNTRIES icinde ama COUNTRIES'te yok")
        if country not in COUNTRY_NAMES:
            problems.append(f"{country}: harvest_countrywide.COUNTRY_NAMES eksik")
        if not MAJOR_CITIES.get(country):
            problems.append(f"{country}: harvest_countrywide.MAJOR_CITIES bos")
        if country not in COUNTRY_QIDS:
            problems.append(f"{country}: harvest_wikidata.COUNTRY_QIDS eksik")
        try:
            routed, language = route_for({"oncelik": f"{country}-EN"})
        except ValueError as exc:
            problems.append(f"{country}: route_for hata verdi ({exc})")
        else:
            if (routed, language) != (country, "en"):
                problems.append(f"{country}: route_for {routed}/{language} dondurdu")
        if target_for(country) <= 0:
            problems.append(f"{country}: arastirma hedefi 0, hic aday uretilmeyecek")
    for country in RESEARCH_COUNTRIES:
        # Aktif ulke siralamasi tam olarak 6-7 Eyl gecesi coken ifade.
        try:
            RESEARCH_COUNTRIES.index(country)
        except ValueError as exc:  # pragma: no cover - savunma amacli
            problems.append(f"{country}: siralama hatasi ({exc})")
    if not BASE.joinpath("leads.sqlite3").is_file():
        problems.append("leads.sqlite3 bulunamadi")
    if not CSV_PATH.is_file():
        problems.append(f"kuyruk dosyasi yok: {CSV_PATH}")
    try:
        accepted(attempts=3, wait=2.0)
    except Exception as exc:
        problems.append(f"accepted() okunamadi: {type(exc).__name__}: {exc}")
    try:
        load_progress()
    except Exception as exc:
        problems.append(f"progress dosyasi yuklenemedi: {type(exc).__name__}: {exc}")
    return problems


def source_probe_due(progress) -> bool:
    try:
        return time.time() >= float(progress.get("next_probe_at", 0))
    except (TypeError, ValueError):
        return True


def main():
    if "--selfcheck" in sys.argv[1:]:
        problems = selfcheck()
        for problem in problems:
            print("SORUN:", problem, flush=True)
        print("selfcheck:", "BASARISIZ" if problems else "OK", flush=True)
        raise SystemExit(1 if problems else 0)
    # Tarama her baslangicta once kendini denetler. Sorun varsa hemen ve
    # acik bir mesajla cikar; systemd StartLimitBurst ile birkac dakikada
    # 'failed' olur, sabah raporu bunu alarm olarak bildirir.
    problems = selfcheck()
    if problems:
        for problem in problems:
            print("SORUN:", problem, flush=True)
        raise SystemExit("selfcheck basarisiz; tarama baslatilmadi")
    LOCK.touch(exist_ok=True)
    with LOCK.open("r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("qualified target worker is already running")
        progress = load_progress()
        run(sys.executable, "sponsor_registry.py", "refresh", "--countries", "NL")
        # ATS ilanlari sabit bir katalog anlik goruntusu degil: ayni board'da
        # yeni rol acilabilir. Bu nedenle kontrollu aralikla tekrar okunur.
        refresh_ats_if_due()
        save(progress)
        while True:
            counts = accepted()
            active = sorted(
                (c for c in RESEARCH_COUNTRIES if counts[c] < target_for(c)),
                key=lambda country: (RESEARCH_COUNTRIES.index(country), counts[country]),
            )
            if not active:
                # Target doldugunda process kapanmaz; kontrollu ATS yenilemesi
                # ve yeni kampanya talebi icin saglikli bekler.
                refresh_ats_if_due()
                save(progress)
                time.sleep(IDLE_SLEEP_SECONDS)
                continue
            did_work = refresh_ats_if_due()
            # Small scoring batches give every country a turn each cycle.
            for country in active:
                country_worked = False
                if not pending(country) and source_probe_due(progress[country]):
                    country_worked = discover(country, progress[country])
                    save(progress)
                if pending(country):
                    run(sys.executable, "deep_enrich.py", "--countries", country,
                        "--limit", "80", "--workers", str(CRAWL_WORKERS),
                        "--max-pages", "8", "--min-score", str(MIN_FIT_SCORE))
                    country_worked = True
                elif progress[country]["retry_rounds"] < MAX_RETRY_ROUNDS:
                    run(sys.executable, "deep_enrich.py", "--countries", country,
                        "--limit", "80", "--workers", str(CRAWL_WORKERS),
                        "--max-pages", "8", "--min-score", str(MIN_FIT_SCORE),
                        "--retry-errors")
                    progress[country]["retry_rounds"] += 1
                    country_worked = True
                elif browser_pending(country):
                    # Rendering is deliberately last: it only revisits a tiny,
                    # high-signal ATS subset which normal HTTP extraction could
                    # not resolve. It never submits or interacts with pages.
                    country_worked = browser_enrich(country)
                # Existing publisher has its own lock; a concurrent instance
                # safely skips. It only needs waking after actual research.
                if country_worked:
                    run(sys.executable, OUTREACH / "incremental_publish_worker.py", "--once")
                print(f"STATUS {country}: accepted={accepted()[country]}/{target_for(country)} "
                      f"pending={pending(country)}", flush=True)
                save(progress)
                did_work = did_work or country_worked
            save(progress)
            if not did_work:
                time.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
