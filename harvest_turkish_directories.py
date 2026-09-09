#!/usr/bin/env python3
"""Turk is dernekleri ve odalarinin acik uye listelerinden aday firma cikar.

9 Eyl 2026: kampanyanin uc gorusmesi de Turk sahipli/Turkce konusan
firmalardan geldi (Berlin Steuerberater, Amsterdam administratie, BG
muhasebe). Bu kaynak, isim sezgisi yerine kurumlarin kendi uye listelerini
okur: TD-IHK (Turk-Alman Ticaret ve Sanayi Odasi) uye listesi ve TBCCI
(Turkish-British Chamber) yonetim kurulu sayfasi. Turkiye merkezli uyeler
(.tr, odalar, borsalar) atlanir; yalnizca hedef ulkede yerlesik firmalar
alinir. Kaynak etiketi 'turkish-directory:<kurum>' deep_enrich tarafindan
turkish_company sinyaline cevrilir; yayinci bunu one alir.

  python3 harvest_turkish_directories.py --dry-run      # DB'ye yazmaz, sayar
  python3 harvest_turkish_directories.py --countries DE,GB
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from urllib.parse import urlparse

import requests

import scrape

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
SOURCES = {
    "DE": [("td-ihk", "https://td-ihk.de/de/seiten/mitgliederliste")],
    # TBCCI yalnizca yonetim kurulu adlarini yayinlar, firma sitesi yok; lead olamaz.
}
SKIP_HOSTS = ("facebook.", "instagram.", "linkedin.", "twitter.", "x.com", "youtube.", "google.",
              "xing.", "alibaba.", "td-ihk.de", "tbcci.org", "ihk.de", "tobb.org")
# Turkiye merkezli uyeler: odalar, borsalar, .tr alanlari ve Turk sirket
# unvanlari (Ltd. Sti., A.S., San. Tic.). Hedef ulkede yerlesik olanlar ya
# ulke alan adi tasir ya da o ulkenin sirket unvanini (GmbH, AG, Ltd, LLP).
TR_MARKERS = (".tr", "ticaret odas", "ticaret borsas", "sanayi odas", "tso", "tb.org", "belediye",
              "ltd. şti", "ltd.şti", "ltd sti", "a.ş.", " a.ş", "san. ", "tic. ", "sanayi", "ticaret",
              "anonim", "şirketi", "ith. ", "ihr. ", "üniversite", "university", "hastane")
LOCAL_FORMS = {"DE": ("gmbh", " ag", "e.k.", " kg", " ug", "kanzlei", "steuerberat", "rechtsanw",
                      "übersetz", "partg", "mbh", "e.v.", "beratung", "architekt"),
               "GB": ("ltd", "limited", "llp", "plc", "solicitor", "chartered", "accountant")}
COUNTRY_TLD = {"DE": (".de",), "GB": (".uk", ".co.uk", ".org.uk", ".london")}


def fetch(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    response.raise_for_status()
    return response.text


def td_ihk(page: str, code: str) -> list[tuple[str, str]]:
    """Uye listesi: '<Firma adi> <site>' ciftleri; sayfa metninden okunur."""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S | re.I)))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if not re.match(r"^(https?://|www\.)\S+$", line):
            continue
        website = line if line.startswith("http") else f"https://{line}"
        host = (urlparse(website).hostname or "").casefold().removeprefix("www.")
        name = lines[index - 1] if index else ""
        low = f"{name} {host}".casefold()
        if not host or any(s in host for s in SKIP_HOSTS):
            continue
        local = host.endswith(COUNTRY_TLD[code]) or any(f in f" {name.casefold()} " for f in LOCAL_FORMS[code])
        if any(m in low for m in TR_MARKERS) and not host.endswith(COUNTRY_TLD[code]):
            continue
        if not local:
            continue
        if len(name) < 3 or len(name) > 120:
            continue
        out.append((name, host))
    return out


def tbcci(page: str, code: str) -> list[tuple[str, str]]:
    """Yonetim kurulu: 'Isim TBCCI Rol Firma Unvan' dizisi; firma adlari alinir, site yok."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S | re.I))))
    firms = set(re.findall(r"(?:Director|Partner|Founder|Manager|Head of|CEO|Chairman|Treasurer)\s+([A-Z][\w&.' -]{2,50}?)\s+(?:Director|Partner|Founder|Managing|General|Head|CEO|Chairman)", text))
    return [(f.strip(), "") for f in firms if "tbcci" not in f.casefold()]


PARSERS = {"td-ihk": td_ihk, "tbcci": tbcci}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", default="DE,GB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    codes = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    conn = None if args.dry_run else scrape.db()
    for code in codes:
        for label, url in SOURCES.get(code, []):
            try:
                page = fetch(url)
            except requests.RequestException as exc:
                print(f"{code} {label}: erisilemedi ({type(exc).__name__})", flush=True)
                continue
            found = PARSERS[label](page, code)
            added = 0
            for name, host in found:
                if not host:
                    continue        # sitesi bilinmeyen firma lead olamaz (kanit sarti)
                if conn is not None and scrape.add_lead(conn, host, name, code, code, f"turkish-directory:{label}"):
                    added += 1
            print(f"{code} {label}: {len(found)} firma, siteli {sum(1 for _, h in found if h)}, "
                  f"{'yazilmadi (dry-run)' if conn is None else f'{added} yeni lead'}", flush=True)
            if args.dry_run:
                for name, host in found[:8]:
                    print(f"   {name[:50]:<50} {host}")
    if conn is not None:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
