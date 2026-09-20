"""Kapanan ilan dosyalarini atjobfind.com havuzuna yukler (21 Eyl 2026).

verify/out/ilanlar-NNN.jsonl dosyasi, ilan iscisi (ilan.py) bir sonraki parcaya gectiginde
(ilan_tarama.MAX(parca) > NNN) "kapanmis" sayilir. Kapanan ve daha once yuklenmemis her dosya icin
    python verify/havuz-gonder.py --ilanlar <dosya> --etiket pc-ilan
calistirilir; sonuc verify/out/havuz-yukleme.log'a ve verify/out/yuklenen.json'a yazilir.
Ayni dosyayi ikinci kez gondermek zararsiz (sunucuda upsert). Anahtar yalniz JOBFIND_HAVUZ_ANAHTAR
ortam degiskeninden okunur; dosyaya yazilmaz.

  python verify/ilan_yukle.py            # kapananlari yukle, cik (Gorev Zamanlayici: 30 dk'da bir)
  python verify/ilan_yukle.py --hepsi    # acik olan son dosyayi da (kismi) yukle
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify.db import db, now  # noqa: E402

BASE = Path(__file__).resolve().parent
OUT = BASE / "out"
DURUM = OUT / "yuklenen.json"
LOG = OUT / "havuz-yukleme.log"
GONDER = BASE / "havuz-gonder.py"
ETIKET = "pc-ilan"


def log(m: str) -> None:
    s = f"[{now()}] {m}"
    print(s, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def kapanan_parca() -> int:
    """Su an yazilan parca numarasi; ondan kucukler kapanmistir."""
    conn = db()
    try:
        r = conn.execute("SELECT MAX(parca) FROM ilan_tarama").fetchone()[0]
    except Exception:
        r = None
    conn.close()
    return int(r or 1)


def yukle(dosya: Path) -> dict | None:
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("JOBFIND_HAVUZ_ANAHTAR tanimsiz; yukleme atlandi")
        return None
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(GONDER), "--ilanlar", str(dosya), "--etiket", ETIKET],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE.parent)
    for satir in (p.stdout + p.stderr).splitlines():
        log(f"  {dosya.name}: {satir}")
    m = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not m:
        log(f"{dosya.name}: yukleme basarisiz (kod {p.returncode})")
        return None
    return json.loads(m.group(1))


def main() -> None:
    hepsi = "--hepsi" in sys.argv
    yuklenen = json.loads(DURUM.read_text(encoding="utf-8")) if DURUM.exists() else {}
    acik = kapanan_parca()
    for dosya in sorted(OUT.glob("ilanlar-*.jsonl")):
        n = int(dosya.stem.split("-")[1])
        kapali = n < acik
        if not kapali and not hepsi:
            continue
        if kapali and dosya.name in yuklenen:
            continue
        satir = sum(1 for _ in dosya.open(encoding="utf-8"))
        log(f"{dosya.name}: {'kapandi' if kapali else 'acik (kismi)'}, {satir} satir, yukleniyor")
        toplam = yukle(dosya)
        if toplam is None:
            continue
        log(f"{dosya.name}: TOPLAM {json.dumps(toplam, ensure_ascii=False)}")
        if kapali:
            yuklenen[dosya.name] = {"satir": satir, "toplam": toplam, "at": now()}
            DURUM.write_text(json.dumps(yuklenen, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
