#!/bin/bash
# Hasat + e-posta cikarma dongusu. Arka planda calisir, kesilirse veri kaybolmaz.
cd /root/projects/lead-scraper || exit 1

# Overpass kullanim kosullari gercek bir iletisim adresi ister
export SCRAPER_CONTACT="${SCRAPER_CONTACT:-muhammeteminkilic012@gmail.com}"

echo "=== $(date '+%F %T') pipeline basladi ==="

# 1) Tum sehirlerden aday topla
python3 harvest_all.py >> harvest.log 2>&1

# 2) Bekleyen tum sitelerden e-posta cikar (partiler halinde, VPS'i yormadan)
while true; do
    pending=$(python3 -c "
import sqlite3
c = sqlite3.connect('leads.sqlite3')
print(c.execute(\"SELECT COUNT(*) FROM leads WHERE status='pending'\").fetchone()[0])
" 2>/dev/null)
    [ -z "$pending" ] && break
    [ "$pending" -eq 0 ] && break
    echo "--- $(date '+%T') bekleyen: $pending ---"
    python3 scrape.py emails --limit 60 --workers 5 >> emails.log 2>&1
    sleep 5
done

# 3) Kampanya formatinda disa aktar
python3 scrape.py export --out yeni-firmalar-ham.csv --tag TBD >> export.log 2>&1
echo "=== $(date '+%F %T') pipeline bitti ==="
python3 scrape.py stats
