#!/bin/bash
# Restart-safe 2,000-company discovery run for Emin's visa-priority search.
# This script discovers and verifies public company contact details only. It
# never writes to the outreach queue; the existing publisher remains the sole
# duplicate-safe bridge after evidence and fit checks are complete.
set -euo pipefail

cd /root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper
exec 9>visa-priority-2000.lock
flock 9

: "${SCRAPER_CONTACT:?SCRAPER_CONTACT env degiskeni gerekli}"
export SCRAPER_CONTACT
run_label="visa-priority-$(date +%Y%m%d)"
log="visa-priority-2000.log"

echo "=== $(date '+%F %T') started label=$run_label ===" | tee -a "$log"

# User-approved allocation: Germany 40%, UK 25%, Ireland 20%, Netherlands
# 10%, Finland+Malta 5%. A unique run label makes every stage resumable and
# prevents older pending leads from consuming this campaign's processing.
for target in "DE 800" "GB 500" "IE 400" "NL 200" "FI 50" "MT 50"; do
    set -- $target
    python3 harvest_countrywide.py --countries "$1" --new-per-country "$2" \
        --workers 1 --source-label "$run_label" >> "$log" 2>&1
done

python3 - <<'PY' >> "$log"
import sqlite3
from collections import Counter
run_label = "visa-priority-" + __import__("datetime").date.today().strftime("%Y%m%d")
conn = sqlite3.connect("leads.sqlite3")
rows = conn.execute(
    "SELECT country, COUNT(*) FROM leads WHERE source LIKE ? GROUP BY country ORDER BY country",
    (run_label + ":%",),
).fetchall()
print("discovered_by_country=" + repr(dict(rows)))
print("discovered_total=" + str(sum(count for _, count in rows)))
PY

# First crawl each newly discovered site for a published address, then perform
# an evidence-based deep fit pass. Both commands are scoped to this run label.
for code in DE GB IE NL FI MT; do
    while true; do
        pending=$(python3 - "$code" "$run_label" <<'PY'
import sqlite3, sys
conn = sqlite3.connect("leads.sqlite3")
print(conn.execute(
    "SELECT COUNT(*) FROM leads WHERE country=? AND source LIKE ? AND status='pending'",
    (sys.argv[1], sys.argv[2] + ":%"),
).fetchone()[0])
PY
)
        [ "$pending" -eq 0 ] && break
        python3 scrape.py emails --countries "$code" --source-prefix "$run_label:" \
            --limit 120 --workers 4 >> "$log" 2>&1
    done
    while true; do
        unscored=$(python3 - "$code" "$run_label" <<'PY'
import sqlite3, sys
conn = sqlite3.connect("leads.sqlite3")
print(conn.execute(
    "SELECT COUNT(*) FROM leads WHERE country=? AND source LIKE ? AND profile_status='unscored'",
    (sys.argv[1], sys.argv[2] + ":%"),
).fetchone()[0])
PY
)
        [ "$unscored" -eq 0 ] && break
        python3 deep_enrich.py --countries "$code" --source-prefix "$run_label:" \
            --limit 120 --workers 4 --max-pages 8 --min-score 35 >> "$log" 2>&1
    done
done

python3 - <<'PY' >> "$log"
import sqlite3
from collections import defaultdict
run_label = "visa-priority-" + __import__("datetime").date.today().strftime("%Y%m%d")
conn = sqlite3.connect("leads.sqlite3")
rows = conn.execute(
    "SELECT country, profile_status, COUNT(*) FROM leads WHERE source LIKE ? "
    "GROUP BY country, profile_status ORDER BY country, profile_status",
    (run_label + ":%",),
).fetchall()
print("final_by_country_status=")
for row in rows:
    print("  %s\t%s\t%s" % row)
PY
echo "=== $(date '+%F %T') completed label=$run_label ===" | tee -a "$log"
