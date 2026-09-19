"""`python scrape.py verify <asama>` girisi.

Asamalar: candidates -> fetch -> judge -> consensus -> export | stats
Her asama kaldigi yerden surer (domains.status).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from . import altin_kume as altin
from . import candidates as aday_modul
from . import consensus as uzlasma
from . import judge as hakem
from .config import (BASE, MODEL_A, MODEL_B, PROMPT_VERSION)
from .db import db, domains_by_status, pages_of, save_judgment, set_status


def _meslekler() -> dict:
    return aday_modul.meslekler()


def cmd_candidates(args) -> None:
    kaynak = Path(args.kaynak) if args.kaynak else None
    ozet = aday_modul.yukle(kaynak, limit=args.limit)
    print(json.dumps(ozet, ensure_ascii=False, indent=2))


def cmd_fetch(args) -> None:
    from . import fetch as cekici
    ozet = asyncio.run(cekici.cek(limit=args.limit))
    print(json.dumps(ozet, ensure_ascii=False, indent=2))


def cmd_judge(args) -> None:
    """Modelleri SIRAYLA kosar: once A tum kuyrugu, sonra B (tek model GPU'da)."""
    meslekler = _meslekler()
    conn = db()
    satirlar = [dict(r) for r in conn.execute(
        # 'tamam' da dahil: yeni bir modelle yeniden yargilamak mumkun olsun
        "SELECT * FROM domains WHERE status IN ('cekildi','yargilandi','tamam') ORDER BY domain LIMIT ?",
        (args.limit,)).fetchall()]
    conn.close()
    if not satirlar:
        print(json.dumps({"islenen": 0, "not": "cekilmis alan adi yok"}, ensure_ascii=False))
        return

    modeller = [(MODEL_A, "A"), (MODEL_B, "B")]
    if args.model in ("A", "B"):
        modeller = [m for m in modeller if m[1] == args.model]

    genel = {"prompt": PROMPT_VERSION, "modeller": {}}
    for model, rol in modeller:
        t0 = time.time()
        sayac = {"yes": 0, "no": 0, "unclear": 0, "alinti_dogru": 0, "hata": 0, "atlanan": 0, "toplam_ms": 0}
        conn = db()
        for i, s in enumerate(satirlar, 1):
            domain, meslek_id = s["domain"], s["meslek"]
            var = conn.execute("SELECT 1 FROM judgments WHERE domain=? AND meslek=? AND model=?",
                               (domain, meslek_id, model)).fetchone()
            if var and not args.yeniden:
                sayac["atlanan"] += 1
                continue
            sayfalar = pages_of(conn, domain)
            if not sayfalar:
                sayac["atlanan"] += 1
                continue
            kanit, ham = hakem.kanit_metni(conn, domain, sayfalar)
            if not kanit:
                sayac["atlanan"] += 1
                continue
            meslek = meslekler[meslek_id]
            try:
                j = hakem.yargila(model, rol, meslek, [s["pool_name"]], kanit, ham)
            except Exception as e:
                sayac["hata"] += 1
                print(f"  !! {domain} ({model}): {type(e).__name__}: {e}"[:160], flush=True)
                continue
            save_judgment(conn, domain, meslek_id, model, j)
            conn.commit()
            sayac[j["decision"]] = sayac.get(j["decision"], 0) + 1
            sayac["alinti_dogru"] += 1 if j["quotes_verified"] else 0
            sayac["toplam_ms"] += j["latency_ms"]
            if i % 10 == 0 or i == len(satirlar):
                gecen = time.time() - t0
                print(f"  {rol}/{model}: {i}/{len(satirlar)} | yes {sayac['yes']} no {sayac['no']} "
                      f"unclear {sayac['unclear']} | {gecen:.0f} sn ({gecen / max(1, i):.1f} sn/kayit)", flush=True)
        conn.close()
        sayac["sure_sn"] = round(time.time() - t0, 1)
        sayac["ort_ms"] = round(sayac["toplam_ms"] / max(1, sayac["yes"] + sayac["no"] + sayac["unclear"]))
        genel["modeller"][model] = sayac
        hakem.bosalt(model)          # GPU'yu bosalt: modeller sirayla yuklensin

    conn = db()
    for s in satirlar:
        tam = conn.execute("SELECT COUNT(*) FROM judgments WHERE domain=? AND meslek=?",
                           (s["domain"], s["meslek"])).fetchone()[0]
        if tam >= len(modeller) and s["status"] == "cekildi":
            set_status(conn, s["domain"], "yargilandi")
    conn.commit()
    conn.close()
    print(json.dumps(genel, ensure_ascii=False, indent=2))


def cmd_consensus(args) -> None:
    ozet = uzlasma.isle(sadece_yeni=not args.hepsi)
    print(json.dumps(ozet, ensure_ascii=False, indent=2))


def cmd_export(args) -> None:
    yol = Path(args.out) if args.out else BASE / "verified.jsonl"
    n = uzlasma.disa_aktar(yol)
    print(json.dumps({"kayit": n, "dosya": str(yol)}, ensure_ascii=False))


def cmd_stats(args) -> None:
    conn = db()
    say = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
    ozet = {
        "domains": {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM domains GROUP BY status ORDER BY 2 DESC")},
        "domains_toplam": say("SELECT COUNT(*) FROM domains"),
        "sayfa_toplam": say("SELECT COUNT(*) FROM pages"),
        "sayfa_turu": {r[0]: r[1] for r in conn.execute(
            "SELECT tur, COUNT(*) FROM pages GROUP BY tur ORDER BY 2 DESC")},
        "ortalama_sayfa_karakter": say("SELECT COALESCE(ROUND(AVG(chars)),0) FROM pages"),
        "yargi": {f"{r[0]}|{r[1]}": r[2] for r in conn.execute(
            "SELECT model, decision, COUNT(*) FROM judgments GROUP BY model, decision")},
        "alinti_dogrulanan": say("SELECT COUNT(*) FROM judgments WHERE quotes_verified=1"),
        "yargi_toplam": say("SELECT COUNT(*) FROM judgments"),
        "verified": say("SELECT COUNT(*) FROM verified"),
        "agreement": {r[0]: r[1] for r in conn.execute(
            "SELECT agreement, COUNT(*) FROM verified GROUP BY agreement")},
    }
    conn.close()
    print(json.dumps(ozet, ensure_ascii=False, indent=2))


def cmd_gold(args) -> None:
    yol = Path(args.out) if args.out else BASE / "altin-kume.csv"
    print(json.dumps(altin.disa(yol, limit=args.limit), ensure_ascii=False, indent=2))


def cmd_score(args) -> None:
    yol = Path(getattr(args, "girdi", "") or (BASE / "altin-kume.csv"))
    print(json.dumps(altin.olc(yol), ensure_ascii=False, indent=2))


def cmd_run(args) -> None:
    """candidates -> fetch -> judge -> consensus -> export (tek komut)."""
    class A:
        pass
    a = A()
    a.kaynak, a.limit = args.kaynak, args.limit
    print("== 1/5 adaylar"); cmd_candidates(a)
    a.limit = args.limit
    print("== 2/5 cekim"); cmd_fetch(a)
    a.model, a.yeniden = "", False
    print("== 3/5 hakemler"); cmd_judge(a)
    a.hepsi = False
    print("== 4/5 uzlasma"); cmd_consensus(a)
    a.out = args.out
    print("== 5/5 disa aktarim"); cmd_export(a)
    cmd_stats(a)


def ekle(sub) -> None:
    """scrape.py'nin argparse alt komutlarina `verify` ekler."""
    p = sub.add_parser("verify", help="yerel modelle sirket dogrulama (ayri DB: verify/verify.sqlite3)")
    vs = p.add_subparsers(dest="verify_cmd", required=True)

    q = vs.add_parser("candidates", help="aday alan adlarini kuyruga yukle")
    q.add_argument("--kaynak", default="", help="JSONL kaynak (varsayilan: yerel sirket verisi)")
    q.add_argument("--limit", type=int, default=100)
    q.set_defaults(func=cmd_candidates)

    q = vs.add_parser("fetch", help="Playwright ile 5-8 sayfa cek")
    q.add_argument("--limit", type=int, default=100)
    q.set_defaults(func=cmd_fetch)

    q = vs.add_parser("judge", help="iki modeli sirayla kosar")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--model", default="", choices=["", "A", "B"])
    q.add_argument("--yeniden", action="store_true", help="mevcut yargilari da yenile")
    q.set_defaults(func=cmd_judge)

    q = vs.add_parser("consensus", help="uzlasma + ad dogrulama")
    q.add_argument("--hepsi", action="store_true")
    q.set_defaults(func=cmd_consensus)

    q = vs.add_parser("export", help="verified.jsonl yaz")
    q.add_argument("--out", default="")
    q.set_defaults(func=cmd_export)

    q = vs.add_parser("stats", help="verify durum ozeti")
    q.set_defaults(func=cmd_stats)

    q = vs.add_parser("gold", help="altin kume icin etiketlenecek CSV uret")
    q.add_argument("--out", default="")
    q.add_argument("--limit", type=int, default=0)
    q.set_defaults(func=cmd_gold)

    q = vs.add_parser("score", help="etiketlenmis CSV'den isabet/kapsama olc")
    q.add_argument("--in", dest="girdi", default="")
    q.set_defaults(func=cmd_score)

    q = vs.add_parser("run", help="tum asamalar sirayla")
    q.add_argument("--kaynak", default="")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--out", default="")
    q.set_defaults(func=cmd_run)
