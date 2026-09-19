"""verify kuyrugu: ayri SQLite dosyasi (leads.sqlite3'e dokunulmaz).

Her asama kaldigi yerden surer: domains.status asamalari isaretler.
  yeni -> cekildi -> yargilandi -> tamam      (hata: cekilemedi / hata)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, PAGES_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain      TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'yeni',
    tries       INTEGER NOT NULL DEFAULT 0,
    pool_name   TEXT DEFAULT '',   -- havuzdaki ad (dogrulanacak, guvenilmez)
    city        TEXT DEFAULT '',
    sector      TEXT DEFAULT '',
    meslek      TEXT DEFAULT '',   -- bu tur icin sorulan meslek kaydi
    email       TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_domains_status ON domains(status);

CREATE TABLE IF NOT EXISTS pages (
    domain      TEXT NOT NULL,
    tur         TEXT NOT NULL,
    url         TEXT NOT NULL,
    chars       INTEGER NOT NULL DEFAULT 0,
    rendered    INTEGER NOT NULL DEFAULT 0,
    fetched_at  TEXT,
    PRIMARY KEY (domain, tur)
);

CREATE TABLE IF NOT EXISTS judgments (
    domain          TEXT NOT NULL,
    meslek          TEXT NOT NULL,
    model           TEXT NOT NULL,
    decision        TEXT NOT NULL,
    evidence_type   TEXT DEFAULT '',
    quotes_json     TEXT DEFAULT '[]',
    reason          TEXT DEFAULT '',
    legal_name      TEXT DEFAULT '',
    activity_de     TEXT DEFAULT '',
    size_hint       TEXT DEFAULT '',
    red_flags_json  TEXT DEFAULT '[]',
    quotes_verified INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    prompt_version  TEXT DEFAULT '',
    created_at      TEXT,
    PRIMARY KEY (domain, meslek, model)
);

CREATE TABLE IF NOT EXISTS verified (
    domain          TEXT PRIMARY KEY,
    legal_name      TEXT DEFAULT '',
    display_name    TEXT DEFAULT '',
    activity_de     TEXT DEFAULT '',
    size_hint       TEXT DEFAULT '',
    agreement       TEXT DEFAULT '',   -- both / one / none
    professions_json TEXT DEFAULT '[]',
    job_ads_json    TEXT DEFAULT '[]',
    models_json     TEXT DEFAULT '[]',
    pages_json      TEXT DEFAULT '[]',
    prompt_version  TEXT DEFAULT '',
    verified_at     TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_SEMA_KURULDU = False


def db(sema: bool = True) -> sqlite3.Connection:
    """Baglanti acar. sema=True yalniz ilk cagrida DDL calistirir (yazma kilidi kisa kalsin)."""
    global _SEMA_KURULDU
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if sema and not _SEMA_KURULDU:
        conn.executescript(SCHEMA)
        conn.commit()
        _SEMA_KURULDU = True
    return conn


def yaz(islem, deneme: int = 6):
    """Kisa omurlu yazma: baglanti acilir, islem(conn) kosar, commit edilip kapatilir.

    Es zamanli cekim islerinde uzun suren baglantilar 'database is locked' uretiyordu;
    yazmalar bu yardimciyla atomik ve kisa tutulur.
    """
    son = None
    for i in range(deneme):
        conn = db(sema=False)
        try:
            sonuc = islem(conn)
            conn.commit()
            return sonuc
        except sqlite3.OperationalError as e:
            son = e
            time.sleep(0.4 * (i + 1))
        finally:
            conn.close()
    raise son if son else RuntimeError("yazma basarisiz")


# --- sayfa metni: DB'de degil, dosyada (verify/pages/, git'e girmez)

def page_path(domain: str, tur: str) -> Path:
    safe = domain.replace("/", "_").replace("\\", "_")
    return PAGES_DIR / safe / f"{tur}.txt"


def write_page_text(domain: str, tur: str, text: str) -> int:
    p = page_path(domain, tur)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return len(text)


def read_page_text(domain: str, tur: str) -> str:
    p = page_path(domain, tur)
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- kuyruk yardimcilari

def upsert_domain(conn: sqlite3.Connection, domain: str, **fields: Any) -> None:
    cols = {"pool_name": "", "city": "", "sector": "", "meslek": "", "email": "", "note": ""}
    cols.update({k: v for k, v in fields.items() if k in cols})
    conn.execute(
        "INSERT INTO domains (domain, status, pool_name, city, sector, meslek, email, note, updated_at) "
        "VALUES (?, 'yeni', ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(domain) DO UPDATE SET pool_name=excluded.pool_name, city=excluded.city, "
        "sector=excluded.sector, meslek=excluded.meslek, email=excluded.email, updated_at=excluded.updated_at",
        (domain, cols["pool_name"], cols["city"], cols["sector"], cols["meslek"],
         cols["email"], cols["note"], now()),
    )


def set_status(conn: sqlite3.Connection, domain: str, status: str, note: str = "") -> None:
    conn.execute(
        "UPDATE domains SET status=?, note=?, updated_at=? WHERE domain=?",
        (status, note, now(), domain),
    )


def bump_try(conn: sqlite3.Connection, domain: str) -> None:
    conn.execute("UPDATE domains SET tries=tries+1, updated_at=? WHERE domain=?", (now(), domain))


def domains_by_status(conn: sqlite3.Connection, status: str, limit: int = 0) -> list[sqlite3.Row]:
    sql = "SELECT * FROM domains WHERE status=? ORDER BY domain"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (status,)).fetchall()


def save_page(conn: sqlite3.Connection, domain: str, tur: str, url: str, text: str, rendered: bool) -> None:
    chars = write_page_text(domain, tur, text)
    conn.execute(
        "INSERT INTO pages (domain, tur, url, chars, rendered, fetched_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(domain, tur) DO UPDATE SET url=excluded.url, chars=excluded.chars, "
        "rendered=excluded.rendered, fetched_at=excluded.fetched_at",
        (domain, tur, url, chars, 1 if rendered else 0, now()),
    )


def pages_of(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM pages WHERE domain=? ORDER BY tur", (domain,)).fetchall()


def save_judgment(conn: sqlite3.Connection, domain: str, meslek: str, model: str, j: dict) -> None:
    conn.execute(
        "INSERT INTO judgments (domain, meslek, model, decision, evidence_type, quotes_json, reason, "
        "legal_name, activity_de, size_hint, red_flags_json, quotes_verified, latency_ms, prompt_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(domain, meslek, model) DO UPDATE SET decision=excluded.decision, "
        "evidence_type=excluded.evidence_type, quotes_json=excluded.quotes_json, reason=excluded.reason, "
        "legal_name=excluded.legal_name, activity_de=excluded.activity_de, size_hint=excluded.size_hint, "
        "red_flags_json=excluded.red_flags_json, quotes_verified=excluded.quotes_verified, "
        "latency_ms=excluded.latency_ms, prompt_version=excluded.prompt_version, created_at=excluded.created_at",
        (domain, meslek, model, j.get("decision", "unclear"), j.get("evidence_type", ""),
         json.dumps(j.get("quotes", []), ensure_ascii=False), (j.get("reason") or "")[:200],
         j.get("legal_name", ""), j.get("activity_summary", ""), j.get("size_hint", ""),
         json.dumps(j.get("red_flags", []), ensure_ascii=False), 1 if j.get("quotes_verified") else 0,
         int(j.get("latency_ms", 0)), j.get("prompt_version", ""), now()),
    )


def judgments_of(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM judgments WHERE domain=?", (domain,)).fetchall()


def save_verified(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        "INSERT INTO verified (domain, legal_name, display_name, activity_de, size_hint, agreement, "
        "professions_json, job_ads_json, models_json, pages_json, prompt_version, verified_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(domain) DO UPDATE SET legal_name=excluded.legal_name, display_name=excluded.display_name, "
        "activity_de=excluded.activity_de, size_hint=excluded.size_hint, agreement=excluded.agreement, "
        "professions_json=excluded.professions_json, job_ads_json=excluded.job_ads_json, "
        "models_json=excluded.models_json, pages_json=excluded.pages_json, "
        "prompt_version=excluded.prompt_version, verified_at=excluded.verified_at",
        (rec["domain"], rec.get("legal_name", ""), rec.get("display_name", ""), rec.get("activity_de", ""),
         rec.get("size_hint", ""), rec.get("agreement", ""),
         json.dumps(rec.get("professions", []), ensure_ascii=False),
         json.dumps(rec.get("job_ads", []), ensure_ascii=False),
         json.dumps(rec.get("models", []), ensure_ascii=False),
         json.dumps(rec.get("pages", []), ensure_ascii=False),
         rec.get("prompt_version", ""), now()),
    )


def counts(conn: sqlite3.Connection, table: str, column: str) -> list[tuple[str, int]]:
    rows = conn.execute(f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column} ORDER BY 2 DESC").fetchall()
    return [(r[0], r[1]) for r in rows]
