#!/bin/bash
# Wave 2 + otomatik kuyruk yayini (kullanici istegi, 31 Agu 2026):
# kesif/puanlama surerken incremental_publish_worker her 15 dakikada yeni
# nitelikli firmalari outreach kuyruguna aktarir. Cift mail korumasi uc
# bagimsiz katmanda: scrape.py export (kampanya log+csv), publish_verified_batch
# (kuyruk+gonderilmis+exclusion+teslimat defteri, kilit altinda), daily_batch
# (gunluk tavan + saat penceresi). Wave 2 bitince son bir tarama yapip cikar.
set -uo pipefail

OUTREACH=/root/projects/nl-job-outreach
PUBLOG="$OUTREACH/runtime/logs/incremental-publish.log"
mkdir -p "$OUTREACH/runtime/logs"

nohup python3 "$OUTREACH/incremental_publish_worker.py" --interval 900 \
    >> "$PUBLOG" 2>&1 &
WORKER_PID=$!
# wave2 nasil biterse bitsin (Ctrl+C/TERM dahil) worker yetim kalmasin:
# onaysiz kalici oto-yayin dongusu olusmamali
trap 'kill "$WORKER_PID" 2>/dev/null || true' EXIT INT TERM
echo "$(date '+%F %T') publish worker basladi pid=$WORKER_PID" >> "$PUBLOG"

bash /root/projects/lead-scraper/run-visa-priority-2000-wave2.sh
rc=$?

kill "$WORKER_PID" 2>/dev/null || true
sleep 2
# Son tarama: dongusel worker'in kacirmis olabilecegi kuyruk artigi kalmasin.
python3 "$OUTREACH/incremental_publish_worker.py" --once >> "$PUBLOG" 2>&1 || true
echo "$(date '+%F %T') wave2 rc=$rc; publish worker kapatildi, son tarama bitti" >> "$PUBLOG"
exit "$rc"
