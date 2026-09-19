# `verify` — yerel doğrulama modu

Şirketleri **araştırma anında değil, önceden ve çevrimdışı** doğrular: şirketin kendi sitesinden
5–8 sayfa (Impressum dahil) çeker, iki bağımsız **yerel** model aynı soruya kanıtlı cevap üretir,
ikisi uzlaşınca kayıt "doğrulanmış" sayılır. Ücretli/bulut API kullanılmaz.

Tasarım: `docs/yerel-dogrulama-modu-2026-09-19.md` (jobfind deposu).

## Kurulum

```bash
python -m venv .venv && .venv/Scripts/pip install playwright requests
.venv/Scripts/python -m playwright install chromium
export SCRAPER_CONTACT="you@example.com"        # User-Agent'a yazılır (zorunlu nezaket)

# Ollama (yerel model sunucusu) + iki hakem modeli
winget install -e --id Ollama.Ollama --scope user
ollama pull gemma4:12b-it-qat                   # hakem A (~7,2 GB)
ollama pull qwen2.5:7b-instruct-q4_K_M          # hakem B (~4,7 GB)
```

Ollama ortam değişkenleri (makinenin %80 payı, modeller sırayla yüklensin):

| değişken | değer | neden |
|---|---|---|
| `OLLAMA_MAX_LOADED_MODELS` | `1` | iki model aynı anda VRAM'e girmesin |
| `OLLAMA_GPU_OVERHEAD` | `2576980378` (2,4 GB) | 12 GB kartın %20'si boş kalsın → makine kullanılabilir |
| `OLLAMA_NUM_PARALLEL` | `2` | eşzamanlı slot |
| `OLLAMA_FLASH_ATTENTION` | `1` | KV bellek |

## Kullanım

```bash
python scrape.py verify candidates --limit 100     # aday alan adlarını kuyruğa al
python scrape.py verify fetch --limit 100          # Playwright ile 5-8 sayfa çek
python scrape.py verify judge --limit 100          # iki modeli SIRAYLA koş
python scrape.py verify consensus                  # uzlaşma + Impressum'dan ad doğrulama
python scrape.py verify export --out verified.jsonl
python scrape.py verify stats
python scrape.py verify run --limit 100            # hepsi sırayla

# ölçüm (docs §6)
python scrape.py verify gold --out verify/altin-kume.csv   # etiketlenecek CSV üret
# insan `etiket` sütununu doldurur: evet / hayir / belirsiz
python scrape.py verify score --in verify/altin-kume.csv   # isabet / kapsama / karışıklık
```

Her aşama kaldığı yerden sürer (`domains.status`: `yeni → cekildi → yargilandi → tamam`).
Kesilirse aynı komut tekrar çalıştırılır; biten iş tekrarlanmaz.

## Veri yeri

| ne | nerede |
|---|---|
| kuyruk + yargılar + doğrulanmış kayıtlar | `verify/verify.sqlite3` (ayrı DB; `leads.sqlite3`'e dokunulmaz) |
| çekilen sayfa metinleri | `verify/pages/<domain>/<tur>.txt` (git'e girmez) |
| çıktı | `verify/verified.jsonl` (git'e girmez) |

## Uzlaşma kuralı

`evet` **yalnız** şu durumda: A ve B'nin ikisi de `yes` **ve** ikisinin de alıntıları sayfa
metninde birebir geçiyor (boşluk/noktalama normalize edilir). Biri `no` derse → `hayir`.
Diğer her durum → `belirsiz` (listelenmez). Amaç: listelenende ≥ %95 isabet, kapsam ikincil.

## Nezaket / sınırlar

robots.txt'e uyulur (`urllib.robotparser`), captcha çözülmez, login istenmez; alan adı başına
tek sekme ve ≥ 1 sn aralık (≤ 2 eşzamanlı sınırının altında), toplam ≤ 8 sekme, istek zaman
aşımı 20 sn, `User-Agent: lead-scraper-verify/0.1 (+SCRAPER_CONTACT)`.
E-posta tahmini yok; kişisel veri işlenmez — yalnız şirketlerin kamuya açık sayfaları.
