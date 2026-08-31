#!/bin/bash
# Germany-only, no city restriction. The incremental publisher owns queueing
# and delivery; this process only discovers, validates and scores candidates.
set -u
cd /root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper || exit 1

exec 9>de-nationwide.lock
flock 9

TARGET=300
echo "=== $(date '+%F %T') DE nationwide discovery started ==="
python3 harvest_de_nationwide.py >> de-nationwide.log 2>&1
python3 scrape.py reclean >> de-nationwide.log 2>&1

for pass_no in $(seq 1 70); do
    qualified=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(DISTINCT lower(email)) FROM leads WHERE country='DE' AND status='done' AND email IS NOT NULL AND profile_status='qualified' AND email_source_url LIKE 'https://%'\").fetchone()[0])")
    unscored=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(*) FROM leads WHERE country='DE' AND profile_status='unscored'\").fetchone()[0])")
    echo "--- DE pass=$pass_no qualified_https=$qualified target=$TARGET unscored=$unscored ---"
    [ "$qualified" -ge "$TARGET" ] && break
    [ "$unscored" -eq 0 ] && break
    python3 deep_enrich.py --countries DE --limit 120 --workers 5 --max-pages 8 --min-score 35 \
        >> de-nationwide.log 2>&1
done

echo "=== $(date '+%F %T') DE nationwide discovery finished ==="
