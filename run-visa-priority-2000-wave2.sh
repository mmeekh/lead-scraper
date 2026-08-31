#!/bin/bash
# Wave 2: visa-priority-2000 kampanyasinin kesif acigini kapatir.
# Wave 1 (run-visa-priority-2000.sh) OSM'den yalnizca 166 firma bulabildi;
# bu dalga ayni etiket altinda eksigi yerel Overture verisi (DE/NL/IE),
# UK sponsor sicili (GB), OSM tekrar denemesi (FI/MT) ve kuratorlu LU
# listesiyle tamamlar. Outreach kuyruguna ASLA yazmaz; tek kopru publisher.
# Ayni kilidi kullanir: wave 1 hala calisiyorsa arkasina siralanir.
set -euo pipefail

cd /root/projects/otomasyon-paneli/apps/personal-job-outreach/lead-scraper
exec 9>visa-priority-2000.lock
flock 9

# Nezaket UA kimligi env'den gelir; repoya e-posta gomulmez (env-only tasarim).
: "${SCRAPER_CONTACT:?SCRAPER_CONTACT env degiskeni gerekli (Overpass/UA kimligi)}"
export SCRAPER_CONTACT
# Etiket sabit: kampanya gece yarisini gecse de ayni kapsamda kalir.
run_label="visa-priority-20260831"
log="visa-priority-2000.log"

echo "=== $(date '+%F %T') wave2 started label=$run_label ===" | tee -a "$log"

# Etiket kapsaminda ulke basina mevcut aday sayisi (restart guvenligi:
# hedefe ulasan ulke icin kesif adimi otomatik atlanir).
have() {
    python3 - "$1" "$run_label" <<'PY'
import sqlite3, sys
conn = sqlite3.connect("leads.sqlite3")
print(conn.execute(
    "SELECT COUNT(*) FROM leads WHERE country=? AND source LIKE ?",
    (sys.argv[1], sys.argv[2] + ":%"),
).fetchone()[0])
PY
}

need() { # need <ulke> <hedef>
    local mevcut
    mevcut=$(have "$1")
    local kalan=$(( $2 - mevcut ))
    (( kalan > 0 )) && echo "$kalan" || echo 0
}

# GB/NL sert kapisi icin sicilleri tazele (deep_enrich bunlarsiz calismaz).
python3 sponsor_registry.py refresh --countries GB,NL >> "$log" 2>&1
python3 sponsor_registry.py status >> "$log" 2>&1

# --- Kesif -----------------------------------------------------------------
# Kullanici onayli dagilim: DE 800, GB 500, IE 400, NL 200, FI 25, MT 25,
# LU 50 (LU yalnizca banka/fon yonetimi/regtech/fintech/uzman danismanlik;
# Big Four ve cok buyuk global markalar haric - kuratorlu liste).

for target in "DE 800" "NL 200" "IE 400"; do
    set -- $target
    kalan=$(need "$1" "$2")
    if (( kalan > 0 )); then
        python3 harvest_overture.py --countries "$1" --new-per-country "$kalan" \
            --source-label "$run_label" >> "$log" 2>&1
    else
        echo "$1: kesif hedefi zaten dolu" >> "$log"
    fi
done

kalan=$(need GB 500)
if (( kalan > 0 )); then
    # devre kesici: probe ~%30 dogrulama oraniyla kalan*8 deneme fazlasiyla
    # yeter; DNS/ag bozulursa 13k firmalik havuzu gunlerce ogutmesin
    python3 harvest_uk_sponsors.py --limit "$kalan" --workers 2 \
        --max-attempts $(( kalan * 8 )) \
        --source-label "$run_label" >> "$log" 2>&1
else
    echo "GB: kesif hedefi zaten dolu" >> "$log"
fi

# FI/MT: OSM gun icinde yanit vermeyebiliyor; araliklarla 3 deneme yeter.
# Ulkeler ayri cagrilir ki tekrar denemede ulke basina kalan hedef asilmasin.
for attempt in 1 2 3; do
    fi_kalan=$(need FI 25); mt_kalan=$(need MT 25)
    (( fi_kalan == 0 && mt_kalan == 0 )) && break
    if (( fi_kalan > 0 )); then
        python3 harvest_countrywide.py --countries FI --new-per-country "$fi_kalan" \
            --workers 1 --source-label "$run_label" >> "$log" 2>&1 || true
    fi
    if (( mt_kalan > 0 )); then
        python3 harvest_countrywide.py --countries MT --new-per-country "$mt_kalan" \
            --workers 1 --source-label "$run_label" >> "$log" 2>&1 || true
    fi
    (( attempt < 3 )) && sleep 90
done

kalan=$(need LU 50)
if (( kalan > 0 )); then
    python3 seed_luxembourg.py --limit "$kalan" --source-label "$run_label" >> "$log" 2>&1
else
    echo "LU: kesif hedefi zaten dolu" >> "$log"
fi

python3 - <<'PY' >> "$log"
import sqlite3
run_label = "visa-priority-20260831"
conn = sqlite3.connect("leads.sqlite3")
rows = conn.execute(
    "SELECT country, COUNT(*) FROM leads WHERE source LIKE ? GROUP BY country ORDER BY country",
    (run_label + ":%",),
).fetchall()
print("wave2 discovered_by_country=" + repr(dict(rows)))
print("wave2 discovered_total=" + str(sum(count for _, count in rows)))
PY

# --- E-posta + kanit temelli derin uygunluk --------------------------------
# Dongu tavani: bir aday takilip 'pending'/'unscored' kalirsa sonsuz dongu
# olmasin (120'lik partilerle 60 tur = 7200 aday tavani fazlasiyla yeter).
for code in DE GB IE NL FI MT LU; do
    for tur in $(seq 1 60); do
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
    for tur in $(seq 1 60); do
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
run_label = "visa-priority-20260831"
conn = sqlite3.connect("leads.sqlite3")
rows = conn.execute(
    "SELECT country, profile_status, COUNT(*) FROM leads WHERE source LIKE ? "
    "GROUP BY country, profile_status ORDER BY country, profile_status",
    (run_label + ":%",),
).fetchall()
print("wave2 final_by_country_status=")
for row in rows:
    print("  %s\t%s\t%s" % row)
PY
echo "=== $(date '+%F %T') wave2 completed label=$run_label ===" | tee -a "$log"
