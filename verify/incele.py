"""Elle inceleme yardimcisi: bir karar sinifindaki kayitlari kanitlariyla dokum eder.

  python verify/incele.py evet          # listelenen (evet) kayitlar
  python verify/incele.py hayir 10      # ilk 10 'hayir'
  python verify/incele.py ayrisan       # iki modelin ayristigi kayitlar
"""
from __future__ import annotations

import json
import sys

from verify.config import MODEL_A, MODEL_B
from verify.db import db

sinif = sys.argv[1] if len(sys.argv) > 1 else "evet"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

conn = db()
satirlar = conn.execute(
    "SELECT v.*, d.meslek, d.city, d.sector, d.pool_name FROM verified v "
    "JOIN domains d ON d.domain=v.domain ORDER BY d.meslek, v.domain").fetchall()

n = 0
for s in satirlar:
    prof = (json.loads(s["professions_json"] or "[]") or [{}])[0]
    karar, mod = prof.get("karar", ""), prof.get("modeller", {})
    a, b = mod.get(MODEL_A, {}), mod.get(MODEL_B, {})
    if sinif == "ayrisan":
        if a.get("decision") == b.get("decision"):
            continue
    elif karar != sinif:
        continue
    n += 1
    if n > limit:
        break
    print(f"\n=== {n}. {s['domain']}  [{s['meslek']}]  {s['city']}  ({s['sector']})")
    print(f"    havuz adi : {s['pool_name']}")
    print(f"    legal_name: {s['legal_name']}   | boyut: {s['size_hint'] or '-'}   | agreement: {s['agreement']}")
    print(f"    faaliyet  : {(s['activity_de'] or '')[:220]}")
    print(f"    A={a.get('decision')} ({a.get('evidence_type')}, alinti={a.get('quotes_verified')}) "
          f"B={b.get('decision')} ({b.get('evidence_type')}, alinti={b.get('quotes_verified')})")
    print(f"    A gerekce : {(a.get('reason') or '')[:180]}")
    print(f"    B gerekce : {(b.get('reason') or '')[:180]}")
    for k in prof.get("kanit", [])[:4]:
        print(f"    - [{k['model'].split(':')[0]}] {k['alinti'][:200]}")
    ilan = json.loads(s["job_ads_json"] or "[]")
    if ilan:
        print(f"    ilanlar   : {[i['baslik'][:60] for i in ilan[:4]]}")
conn.close()
print(f"\ntoplam {n if n <= limit else limit} kayit gosterildi ({sinif})")
