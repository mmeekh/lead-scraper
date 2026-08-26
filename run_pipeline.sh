#!/bin/bash
# Hasat + e-posta cikarma dongusu. Arka planda calisir, kesilirse veri kaybolmaz.
cd /root/projects/lead-scraper || exit 1

# Ayni pipeline iki kez baslatilirsa ayni siteleri paralel taramasin.
exec 9>pipeline.lock
if ! flock -n 9; then
    echo "=== $(date '+%F %T') pipeline zaten calisiyor; ikinci calisma iptal ==="
    exit 0
fi

# Overpass kullanim kosullari gercek bir iletisim adresi ister
export SCRAPER_CONTACT="${SCRAPER_CONTACT:-muhammeteminkilic012@gmail.com}"
SCRAPER_COUNTRIES="${SCRAPER_COUNTRIES:-DE,NL}"

echo "=== $(date '+%F %T') pipeline basladi ==="

# 1) Varsayilan olarak Almanya + Hollanda'dan aday topla.
# Gerektiginde SCRAPER_COUNTRIES ortam degiskeniyle farkli ulkeler verilebilir.
python3 harvest_all.py --only "$SCRAPER_COUNTRIES" >> harvest.log 2>&1
python3 harvest_nlde_extra.py --only "$SCRAPER_COUNTRIES" >> harvest.log 2>&1
python3 scrape.py reclean >> emails.log 2>&1

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
python3 scrape.py export --out yeni-firmalar-ham.csv >> export.log 2>&1
echo "=== $(date '+%F %T') pipeline bitti ==="
python3 scrape.py stats
