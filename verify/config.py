"""verify modu ayarlari - tek yerden sinirlar, yollar, model adlari."""
from __future__ import annotations

import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "verify.sqlite3"
PAGES_DIR = BASE / "pages"
MESLEKLER_PATH = BASE / "meslekler.json"

# --- kimlik / nezaket (docs/yerel-dogrulama-modu-2026-09-19.md §1, §7)
CONTACT = os.environ.get("SCRAPER_CONTACT", "").strip()
UA = f"lead-scraper-verify/0.1 (+{CONTACT})" if CONTACT else "lead-scraper-verify/0.1"
REQUEST_TIMEOUT_S = 20
DOMAIN_DELAY_S = 1.0          # ayni alan adina ardisik istekler arasi en az
MAX_TABS = 8                  # Chromium sekme tavani (makinenin %80 payi)
MAX_CONCURRENT = 8            # toplam es zamanli istek (<= 12 siniri icinde)
MAX_PER_DOMAIN = 2            # ayni alan adina es zamanli istek tavani
PAGE_CHARS_CAP = 40_000       # sayfa basina saklanan metin tavani
MIN_PAGES = 5
MAX_PAGES = 8
REFETCH_DAYS = 90

# --- modeller (yerel; ucretli/bulut API yok)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL_A = os.environ.get("VERIFY_MODEL_A", "gemma4:12b-it-qat")
MODEL_B = os.environ.get("VERIFY_MODEL_B", "qwen3:8b")   # 20 Eyl olcumu: %81,8 tek basina, 6,5 sn, uzlasmada 7/7
NUM_CTX = int(os.environ.get("VERIFY_NUM_CTX", "8192"))
MODEL_TIMEOUT_S = int(os.environ.get("VERIFY_MODEL_TIMEOUT", "300"))
PROMPT_VERSION = "v3-2026-09-19"

# hakeme giden toplam kanit metni (karakter). Alman metni ~3,5 karakter/token.
EVIDENCE_CHARS = int(os.environ.get("VERIFY_EVIDENCE_CHARS", "16000"))
# sayfa turu basina kanit payi - Impressum kisa, ana sayfa/kariyer uzun olabilir
EVIDENCE_PER_PAGE = {
    "home": 4000,
    "ueber": 3500,
    "leistungen": 3500,
    "karriere": 6000,
    "impressum": 2500,
    "kontakt": 1500,
}
PAGE_TYPES = ("home", "ueber", "leistungen", "karriere", "impressum", "kontakt")
