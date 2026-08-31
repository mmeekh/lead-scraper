#!/bin/bash
# Netherlands-only, no city restriction. The incremental publisher handles
# queueing and delivery; this script only discovers, verifies and scores.
set -u
cd /root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper || exit 1

exec 9>nl-nationwide.lock
flock 9

TARGET=300
echo "=== $(date '+%F %T') NL nationwide discovery started ==="
python3 harvest_nl_nationwide.py >> nl-nationwide.log 2>&1
python3 scrape.py reclean >> nl-nationwide.log 2>&1

for pass_no in $(seq 1 60); do
    qualified=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(DISTINCT lower(email)) FROM leads WHERE country='NL' AND status='done' AND email IS NOT NULL AND profile_status='qualified' AND email_source_url LIKE 'https://%'\").fetchone()[0])")
    unscored=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(*) FROM leads WHERE country='NL' AND profile_status='unscored'\").fetchone()[0])")
    echo "--- NL pass=$pass_no qualified_https=$qualified target=$TARGET unscored=$unscored ---"
    [ "$qualified" -ge "$TARGET" ] && break
    [ "$unscored" -eq 0 ] && break
    python3 deep_enrich.py --countries NL --limit 120 --workers 5 --max-pages 8 --min-score 35 \
        >> nl-nationwide.log 2>&1
done

echo "=== $(date '+%F %T') NL nationwide discovery finished ==="
