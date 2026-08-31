#!/usr/bin/env python3
"""Resmi sponsor sicilleri: GB (UKVI Skilled Worker) + NL (IND erkende referent).

Sicil verisini leads.sqlite3 icindeki "sponsors" tablosuna yukler. deep_enrich
bu tabloyu SERT KAPI olarak kullanir: GB/NL lead'i sicilde eslesmiyorsa
'qualified' olamaz.

Kullanim:
  python3 sponsor_registry.py refresh --countries GB,NL
  python3 sponsor_registry.py status
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator, TextIO

import requests

from scrape import OSM_UA, db

BASE = Path(__file__).parent
CATALOG = BASE / "catalog"
FRESH_DAYS = 7
UK_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
NL_PAGE = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"
NL_MIN_ROWS = 1000    # daha azi parse bozulmasina isaret eder
UK_MIN_ROWS = 50_000  # bugunku gercek deger ~121k; kolon/deger degisirse eski sicil korunur

# deep_enrich'in kapi uyguladigi ulkeler
SPONSOR_GATE_COUNTRIES = {"GB", "NL"}

# NFKD'nin ayristirmadigi harfler (casefold zaten ss yapiyor ama garanti olsun)
_CHAR_FOLD = str.maketrans({
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ð": "d", "þ": "th",
    "ł": "l",
})
# YALNIZCA isim sonundaki hukuki ekler atilir; kisa isimler bozulmaz.
_SINGLE_SUFFIXES = {
    "ltd", "limited", "plc", "llp", "lp", "uk", "bv", "nv", "holding",
    "holdings", "group", "gmbh", "co", "company", "inc", "corp", "se",
}
_PAIR_SUFFIXES = {("b", "v"), ("n", "v")}

# IND sayfasinda firma listesi: <th scope="row">AD</th> ... <td>KVK</td>
NL_ROW_RE = re.compile(
    r'<th scope="row">\s*(.*?)\s*</th>\s*<td>\s*([^<]*?)\s*</td>', re.S)

# Tek basina kanit sayilamayacak jenerik/sehir tokenlari: sicilde "London & Co"
# gibi kayitlarin normu tek jenerik kelimeye dusebiliyor ve site basliklarinda
# "Home", "Contact" gibi parcalar cikiyor; bunlar uzerinden eslesme sahte
# pozitif uretir (31 Agu incelemesinde canli sicille kanitlandi).
GENERIC_TOKENS = frozenset({
    "home", "welcome", "contact", "about", "news", "blog", "shop", "store",
    "online", "official", "website", "site", "page", "menu", "search", "login",
    "info", "data", "tech", "digital", "global", "international", "national",
    "group", "holding", "holdings", "solutions", "services", "service",
    "systems", "consulting", "consultancy", "partners", "company", "business",
    "agency", "studio", "office", "team", "world", "capital", "first", "united",
    "kingdom", "city", "centre", "center", "europe", "european", "britain",
    "british", "england", "english", "scotland", "wales", "ireland", "london",
    "manchester", "birmingham", "leeds", "liverpool", "glasgow", "edinburgh",
    "bristol", "sheffield", "cardiff", "belfast", "netherlands", "nederland",
    "holland", "dutch", "amsterdam", "rotterdam", "utrecht", "eindhoven",
    "hague", "haag", "groningen", "tilburg", "almere", "breda", "nijmegen",
})

# Cok kelimeli ama yine de jenerik olan obekler: sicilde gercek kayitlari var
# (or. IND'de belediye olarak 'Den Haag', kvk:27370927) ama site basligi ya da
# icerme kaniti olarak kullanilamazlar.
GENERIC_PHRASES = frozenset({
    "den haag", "the hague", "s gravenhage", "united kingdom", "great britain",
    "city of london", "new york",
})


# ---------------------------------------------------------------- veritabani

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sponsors (
            country    TEXT NOT NULL,
            name       TEXT NOT NULL,
            name_norm  TEXT NOT NULL,
            extra      TEXT,
            fetched_at TEXT,
            UNIQUE(country, name_norm)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sponsors_country ON sponsors(country)")
    conn.commit()


def sponsor_count(conn: sqlite3.Connection, country: str) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sponsors WHERE country=?",
            ((country or "").strip().upper(),)).fetchone()
    except sqlite3.OperationalError:  # tablo henuz yok
        return 0
    return int(row[0])


# --------------------------------------------------------------- normalizasyon

def normalize_name(value: str) -> str:
    """Kucuk harf + aksan katlama + noktalama->bosluk + sondaki hukuki ekleri at."""
    value = (value or "").casefold().translate(_CHAR_FOLD)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    tokens = value.split()
    while tokens:
        if len(tokens) >= 3 and (tokens[-2], tokens[-1]) in _PAIR_SUFFIXES:
            tokens = tokens[:-2]
        elif len(tokens) >= 2 and tokens[-1] in _SINGLE_SUFFIXES:
            tokens = tokens[:-1]
        else:
            break  # en az bir token kalir; kisa isim bozulmaz
    return " ".join(tokens)


def _single_token_ok(norm: str, min_len: int) -> bool:
    """Cok kelimeli norm her zaman kabul; tek kelime jenerik degil + yeterince uzunsa."""
    if " " in norm:
        return True
    return len(norm) >= min_len and norm not in GENERIC_TOKENS


def is_sponsor(conn: sqlite3.Connection, country: str, name: str) -> bool:
    """Tam eslesme; olmazsa TOKEN SINIRLI iki yonlu icerme.

    Icerme her iki yonde de ' norm ' kelime sinirlarina bakar ki 'kayak'
    'kayak software (uk) limited' kaydini yakalasin ama 'abcde',
    'abcdefgh' icinde eslesmesin. Icerilen taraf tek jenerik token ise
    ('london', 'home', 'amsterdam' gibi) kanit sayilmaz - sicilde normu tek
    jenerik kelimeye dusen gercek kayitlar var ve bunlar sahte pozitif uretir.
    """
    code = (country or "").strip().upper()
    norm = normalize_name(name)
    if not code or not norm:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sponsors WHERE country=? AND name_norm=? LIMIT 1",
            (code, norm)).fetchone()
        if row:
            return True
        # lead adi sponsor adinin icinde (ticari ad -> tescilli ad: 'kayak')
        if _single_token_ok(norm, 4):
            row = conn.execute(
                "SELECT 1 FROM sponsors WHERE country=? "
                "AND instr(' '||name_norm||' ', ?)>0 LIMIT 1",
                (code, f" {norm} ")).fetchone()
            if row:
                return True
        # sponsor adi lead adinin icinde. Yalnizca COK KELIMELI sicil normlari
        # kanit olabilir: 'Bridge UK, Limited' -> 'bridge' gibi tek yaygin
        # kelimeye cokusen kayitlar her lead'i yakalar (31 Agu canli deney:
        # 'London Bridge Consulting' sahte pozitifi). 'Den Haag' gibi jenerik
        # obekler de kanit sayilmaz.
        if len(norm) >= 6:
            phrases = tuple(sorted(GENERIC_PHRASES))
            placeholders = ",".join("?" for _ in phrases)
            row = conn.execute(
                "SELECT 1 FROM sponsors WHERE country=? "
                "AND instr(?, ' '||name_norm||' ')>0 "
                "AND name_norm LIKE '% %' "
                f"AND name_norm NOT IN ({placeholders}) LIMIT 1",
                (code, f" {norm} ", *phrases)).fetchone()
            return row is not None
    except sqlite3.OperationalError:
        return False
    return False


def is_sponsor_strict(conn: sqlite3.Connection, country: str, name: str) -> bool:
    """Yalnizca TAM eslesme + jenerik aday reddi.

    Site basligi/meta parcalari gibi GUVENILMEZ adaylar icin kullanilir:
    icerme yok; tek kelimelik aday ancak >=4 karakter ve jenerik degilse
    denenir ('Home', 'London', 'Amsterdam' parcalari kapiyi acamaz; 'ASML'
    gibi kisa markalar tam eslesmeyle gecebilir - 'holding' eki atilinca
    sicil normu da 'asml' olur).
    """
    code = (country or "").strip().upper()
    norm = normalize_name(name)
    if (not code or not norm or not _single_token_ok(norm, 4)
            or norm in GENERIC_PHRASES):
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sponsors WHERE country=? AND name_norm=? LIMIT 1",
            (code, norm)).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


# ------------------------------------------------------------- yerel katalog

def _fresh_local(pattern: str, days: int = FRESH_DAYS) -> Path | None:
    """catalog/ altinda 'days' gunden taze en yeni kopyayi dondurur."""
    best: tuple[float, Path] | None = None
    for path in CATALOG.glob(pattern):
        age_days = (dt.datetime.now().timestamp() - path.stat().st_mtime) / 86400
        match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if match:
            try:
                age_days = (dt.date.today() - dt.date.fromisoformat(match.group(1))).days
            except ValueError:
                pass
        if age_days <= days and (best is None or age_days < best[0]):
            best = (age_days, path)
    return best[1] if best else None


def _download(url: str, target: Path) -> None:
    CATALOG.mkdir(exist_ok=True)
    with requests.get(url, headers={"User-Agent": OSM_UA}, timeout=120,
                      stream=True) as resp:
        resp.raise_for_status()
        with open(target, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                handle.write(chunk)


def _store(conn: sqlite3.Connection, country: str,
           rows: Iterable[tuple[str, str]], min_rows: int = 0) -> int:
    """(name, extra) akisini sponsors tablosuna ATOMIK yazar.

    Satirlar once bellege alinip sayilir; min_rows altinda kalirsa ESKI SICIL
    SILINMEDEN hata verilir (kaynak formati degisti demektir). DELETE + tum
    INSERT'ler tek transaction'dadir: yarim kalan refresh (OOM/Ctrl-C) tabloyu
    bos ya da kismi birakamaz; WAL okuyuculari ya eski ya yeni TAM sicili
    gorur, aradaki bosluk penceresi yoktur. ~121k satir SQLite icin sorunsuz.
    """
    ensure_table(conn)
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    prepared: list[tuple[str, str, str, str, str]] = []
    for name, extra in rows:
        norm = normalize_name(name)
        if not norm:
            continue
        prepared.append((country, name, norm, extra, fetched_at))
    if len(prepared) < min_rows:
        raise RuntimeError(
            f"{country}: yalnizca {len(prepared)} satir parse edildi "
            f"(beklenen >= {min_rows}); eski sicil korunuyor")
    with conn:  # BEGIN..COMMIT; hata halinde ROLLBACK
        conn.execute("DELETE FROM sponsors WHERE country=?", (country,))
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO sponsors(country,name,name_norm,extra,fetched_at) "
            "VALUES(?,?,?,?,?)", prepared)
    return cursor.rowcount


# ------------------------------------------------------------------- GB: UKVI

def iter_uk_rows(handle: TextIO) -> Iterator[tuple[str, str]]:
    """UKVI CSV'sinden yalnizca Route='Skilled Worker' satirlarini uretir.

    Kolonlar: Organisation Name, Town/City, County, Type & Rating, Route.
    Alanlarda bas/son bosluklar var; ayni org birden cok Route satirinda
    gecebilir (dedupe UNIQUE(country,name_norm) ile saglanir).
    """
    reader = csv.DictReader(handle)
    for row in reader:
        route = (row.get("Route") or "").strip()
        if route != "Skilled Worker":
            continue
        name = (row.get("Organisation Name") or "").strip()
        if not name:
            continue
        town = (row.get("Town/City") or "").strip()
        rating = (row.get("Type & Rating") or "").strip()
        yield name, " | ".join(part for part in (town, rating) if part)


def fetch_uk(conn: sqlite3.Connection, path: Path | None = None) -> int:
    if path is None:
        path = _fresh_local("uk-sponsors-*.csv")
    if path is None:
        print(f"taze yerel kopya yok, indiriliyor: {UK_PAGE}")
        page = requests.get(UK_PAGE, headers={"User-Agent": OSM_UA}, timeout=60)
        page.raise_for_status()
        links = re.findall(r'href="(https?://[^"]+\.csv)"', page.text)
        link = next((l for l in links if "worker" in l.lower()),
                    links[0] if links else None)
        if not link:
            raise RuntimeError("gov.uk sayfasinda .csv linki bulunamadi")
        path = CATALOG / f"uk-sponsors-{dt.date.today().isoformat()}.csv"
        _download(link, path)
    print(f"GB kaynagi: {path}")
    with open(path, encoding="utf-8-sig", newline="") as handle:
        inserted = _store(conn, "GB", iter_uk_rows(handle), min_rows=UK_MIN_ROWS)
    return inserted


# -------------------------------------------------------------------- NL: IND

def parse_nl_html(html: str) -> list[tuple[str, str]]:
    """IND HTML'inden (firma adi, 'kvk:NO') listesi cikarir."""
    import html as html_mod
    out: list[tuple[str, str]] = []
    for raw_name, kvk in NL_ROW_RE.findall(html):
        name = " ".join(html_mod.unescape(raw_name).split())
        if name:
            out.append((name, f"kvk:{kvk.strip()}"))
    return out


def fetch_nl(conn: sqlite3.Connection, path: Path | None = None) -> int:
    if path is None:
        path = _fresh_local("ind-work-*.html")
    if path is None:
        print(f"taze yerel kopya yok, indiriliyor: {NL_PAGE}")
        path = CATALOG / f"ind-work-{dt.date.today().isoformat()}.html"
        _download(NL_PAGE, path)
    print(f"NL kaynagi: {path}")
    rows = parse_nl_html(path.read_text(encoding="utf-8", errors="replace"))
    if len(rows) < NL_MIN_ROWS:
        raise RuntimeError(
            f"IND parse bozuk gorunuyor: yalnizca {len(rows)} satir cikti "
            f"(beklenen >= {NL_MIN_ROWS})")
    return _store(conn, "NL", rows, min_rows=NL_MIN_ROWS)


# ----------------------------------------------------------------------- CLI

_FETCHERS = {"GB": fetch_uk, "NL": fetch_nl}


def cmd_refresh(args: argparse.Namespace) -> None:
    codes = [c.strip().upper() for c in str(args.countries).split(",") if c.strip()]
    if not codes:
        print("ulke verilmedi (ornek: --countries GB,NL)")
        sys.exit(2)
    conn = db()
    ensure_table(conn)
    failed = []
    for code in codes:
        fetcher = _FETCHERS.get(code)
        if fetcher is None:
            print(f"desteklenmeyen ulke: {code} (yalnizca {', '.join(sorted(_FETCHERS))})")
            failed.append(code)
            continue
        try:
            count = fetcher(conn)
            print(f"{code}: {count} sponsor kaydi yuklendi")
        except (RuntimeError, requests.RequestException, OSError) as exc:
            print(f"{code}: HATA - {exc}")
            failed.append(code)
    conn.close()
    if failed:
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    conn = db()
    ensure_table(conn)
    rows = conn.execute(
        "SELECT country, COUNT(*), MAX(fetched_at) FROM sponsors "
        "GROUP BY country ORDER BY country").fetchall()
    conn.close()
    if not rows:
        print("sponsors tablosu bos - once refresh calistir")
        return
    for country, count, fetched_at in rows:
        print(f"{country}: {count} kayit (fetched_at={fetched_at})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("refresh", help="sicil verilerini yerel kopyadan/agdan yukle")
    p.add_argument("--countries", default="GB,NL",
                   help="virgullu ulke listesi (GB,NL)")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("status", help="ulke basina kayit sayisi + fetched_at")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
