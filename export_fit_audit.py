#!/usr/bin/env python3
"""Kampanya CSV'sinin kanit/uygunluk denetim kopyasini uretir."""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from scrape import DB_PATH


FIELDS = (
    "oncelik", "firma", "sehir", "email", "site", "fit_score", "fit_tracks",
    "fit_reasons", "fit_keywords", "career_url", "email_source_url", "job_titles",
    "english_signal", "source",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    rows = list(csv.DictReader(Path(args.input).open(encoding="utf-8")))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    audit: list[dict] = []
    for row in rows:
        lead = conn.execute(
            "SELECT * FROM leads WHERE lower(email)=lower(?) ORDER BY fit_score DESC LIMIT 1",
            (row["email"],),
        ).fetchone()
        if not lead:
            continue
        audit.append({
            "oncelik": row["oncelik"], "firma": row["firma"], "sehir": row["sehir"],
            "email": row["email"], "site": row["site"], "fit_score": lead["fit_score"],
            "fit_tracks": lead["fit_tracks"] or "", "fit_reasons": lead["fit_reasons"] or "",
            "fit_keywords": lead["fit_keywords"] or "", "career_url": lead["career_url"] or "",
            "email_source_url": lead["email_source_url"] or "", "job_titles": lead["job_titles"] or "",
            "english_signal": lead["english_signal"], "source": lead["source"] or "",
        })
    conn.close()
    with Path(args.output).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(audit)
    print(f"{len(audit)} kanitli satir -> {args.output}")


if __name__ == "__main__":
    main()

