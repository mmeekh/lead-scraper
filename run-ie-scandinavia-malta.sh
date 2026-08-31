#!/bin/bash
# IE + Scandinavia (SE/DK/NO) + Malta hedef turu.
# DE/NL pipeline ile ayni SQLite kilidini kullanir; paralel tarama yapmaz.
set -u
cd /root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper || exit 1

exec 9>pipeline.lock
flock 9

export SCRAPER_CONTACT="${SCRAPER_CONTACT:-muhammeteminkilic012@gmail.com}"
echo "=== $(date '+%F %T') IE/SE/DK/NO/MT turu basladi ==="

python3 harvest_all.py --only IE,SE,DK,NO,MT >> harvest-targets.log 2>&1
python3 scrape.py reclean >> emails-targets.log 2>&1

while true; do
    pending=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(*) FROM leads WHERE status='pending'\").fetchone()[0])" 2>/dev/null)
    [ -z "$pending" ] && break
    [ "$pending" -eq 0 ] && break
    echo "--- $(date '+%T') bekleyen: $pending ---"
    python3 scrape.py emails --limit 60 --workers 5 >> emails-targets.log 2>&1
    sleep 5
done

python3 scrape.py export \
    --countries IE,SE,DK,NO,MT \
    --out yeni-firmalar-ie-scandinavia-malta.csv \
    >> export-targets.log 2>&1
echo "=== $(date '+%F %T') IE/SE/DK/NO/MT turu bitti ==="
python3 scrape.py stats
