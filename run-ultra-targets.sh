#!/bin/bash
# CV-uyumlu temiz sirket hedefi: mevcut Avrupa havuzu + İngilizce ana çalışma
# dili olan GB/CA/NZ'nin yalnızca büyük şehirleri. Gönderim kuyruğuna yazmaz.
# CSV uretir; outreach kuyruguna otomatik eklemez.
set -u
cd /root/projects/lead-scraper || exit 1

exec 9>pipeline.lock
flock 9

export SCRAPER_CONTACT="${SCRAPER_CONTACT:-muhammeteminkilic012@gmail.com}"
COUNTRIES="IE,SE,DK,NO,MT,GB,CA,NZ"
QUALIFIED_TARGET=3400
EXPORT_TARGET=3000

echo "=== $(date '+%F %T') ULTRA hedef turu basladi ==="

# 1) 5.000+ sirketlik acik kariyer katalogunu sabitlenmis GitHub commit'inden al.
python3 ats_discovery.py sync >> ats-targets.log 2>&1
# Yeni ülkeler için önceki "hedef dışı" ATS sonuçlarını da güvenle yeniden değerlendir.
python3 ats_discovery.py scan --limit 5000 --workers 4 --rescan >> ats-targets.log 2>&1

# 2) Yalnizca kabul edilen buyuk sehirlerde web sitesi/e-postasi olan ofisleri topla.
python3 harvest_all.py --only "$COUNTRIES" --wide --sleep 6 >> harvest-targets.log 2>&1
python3 scrape.py reclean >> emails-targets.log 2>&1

# 3) Her sirketin hakkimizda/urun/kariyer/iletisim sayfalarini derin tara.
for pass_no in $(seq 1 30); do
    qualified=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(DISTINCT lower(email)) FROM leads WHERE country IN ('IE','SE','DK','NO','MT','GB','CA','NZ') AND status='done' AND email IS NOT NULL AND profile_status='qualified' AND email_source_url LIKE 'https://%'\").fetchone()[0])")
    unscored=$(python3 -c "import sqlite3; c=sqlite3.connect('leads.sqlite3'); print(c.execute(\"SELECT COUNT(*) FROM leads WHERE country IN ('IE','SE','DK','NO','MT','GB','CA','NZ') AND profile_status='unscored'\").fetchone()[0])")
    echo "--- pass=$pass_no kanitli-uygun=$qualified ara-hedef=$QUALIFIED_TARGET puanlanmamis=$unscored ---"
    [ "$qualified" -ge "$QUALIFIED_TARGET" ] && break
    [ "$unscored" -eq 0 ] && break
    python3 deep_enrich.py --countries "$COUNTRIES" --limit 160 --workers 5 \
        --max-pages 8 --min-score 35 >> deep-targets.log 2>&1
done

# 4) En yuksek puanli 3.000 yeni kaydi ve ayrintili kanit raporunu uret.
python3 scrape.py export \
    --countries "$COUNTRIES" \
    --min-fit-score 35 \
    --require-email-source \
    --limit "$EXPORT_TARGET" \
    --out yeni-firmalar-english-priority.csv \
    >> export-targets.log 2>&1
python3 export_fit_audit.py \
    yeni-firmalar-english-priority.csv \
    yeni-firmalar-english-priority-audit.csv \
    >> export-targets.log 2>&1
python3 /root/projects/nl-job-outreach/personalize_outreach.py \
    yeni-firmalar-english-priority-audit.csv \
    yeni-firmalar-english-priority-personalizations.csv \
    --preview yeni-firmalar-english-priority-preview.md \
    >> export-targets.log 2>&1

# Kanitli kayıtlar ancak kuyruk, gönderim geçmişi, exclusion ve SQLite teslim
# defteriyle tekrar karşılaştırıldıktan sonra atomik olarak yayımlanır. Bu adım
# hiçbir zaman aynı e-posta/şirkete ikinci kez ekleme yapmaz.
python3 /root/projects/nl-job-outreach/publish_verified_batch.py \
    yeni-firmalar-english-priority-audit.csv \
    yeni-firmalar-english-priority-personalizations.csv \
    --apply >> export-targets.log 2>&1

# Yeni kayıtlar çalışma saatinde beklemesin. Gönderici kendi kilidi ve günlük
# 450 limitini uygular; başka bir teslim süreci varsa güvenle çıkar.
systemd-run --collect --unit="outreach-auto-dispatch-$(date +%s)" \
    /usr/bin/python3 /root/projects/nl-job-outreach/daily_batch.py \
    >> export-targets.log 2>&1 || echo "otomatik gonderici baslatilamadi" >> export-targets.log

echo "=== $(date '+%F %T') ULTRA hedef turu bitti ==="
python3 ats_discovery.py stats
python3 scrape.py stats
