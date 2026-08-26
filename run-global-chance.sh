#!/bin/bash
# Up to 200 country-wide discovery candidates per under-served market. Existing
# high-volume GB and the already-running NL/DE rounds are intentionally omitted.
set -u
cd /root/projects/lead-scraper || exit 1

exec 9>global-chance.lock
flock 9

COUNTRIES="IE,CA,AU,SG,AE,NZ,QA,MT,CH,SE,DK,LU,NO,FI,PT"
echo "=== $(date '+%F %T') global chance discovery started: $COUNTRIES ==="

python3 ats_discovery.py sync >> global-chance.log 2>&1
python3 ats_discovery.py scan --limit 5000 --workers 4 --rescan --countrywide "$COUNTRIES" \
    >> global-chance.log 2>&1
python3 harvest_countrywide.py --countries "$COUNTRIES" --new-per-country 200 --workers 3 \
    >> global-chance.log 2>&1
python3 scrape.py reclean >> global-chance.log 2>&1

# Score at most 200 unseen candidates per country. The incremental worker polls
# the qualified+HTTPS subset every two minutes and publishes it immediately.
for code in ${COUNTRIES//,/ }; do
    echo "--- deep enrichment $code (up to 200) ---"
    python3 deep_enrich.py --countries "$code" --limit 200 --workers 5 --max-pages 8 --min-score 35 \
        >> global-chance.log 2>&1
done

echo "=== $(date '+%F %T') global chance discovery finished ==="
