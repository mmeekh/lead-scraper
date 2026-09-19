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
python scrape.py verify gold --kor       # verify/altin-kume-kor.csv — KÖR kopya (etiketleme için)
python scrape.py verify gold             # verify/altin-kume.csv — hattın kararı + kanıtlar (inceleme)
# insan `insan_karari` sütununu doldurur: evet / hayir / belirsiz
python scrape.py verify score --etiket verify/altin-kume-kor.csv

python verify/test_verify.py             # 25 test, ağ/model gerektirmez
```

**Kör kopya neden:** etiketleyen kişi hattın kararını görürse ölçüm bozulur. `--kor` dosyasında
yalnız `domain`, `legal_name`, `meslek`, `site_linki`, `hakkinda_linki` ve boş `insan_karari`
bulunur; hakem kararları, uzlaşma ve alıntılar yoktur. `score` eşleştirmeyi `domain` + `meslek`
üzerinden yapar, iki CSV biçimini de (`insan_karari` ya da `etiket` sütunu) kabul eder.

Her aşama kaldığı yerden sürer (`domains.status`: `yeni → cekildi → yargilandi → tamam`).
Kesilirse aynı komut tekrar çalıştırılır; biten iş tekrarlanmaz.

## Veri yeri

| ne | nerede |
|---|---|
| kuyruk + yargılar + doğrulanmış kayıtlar | `verify/verify.sqlite3` (ayrı DB; `leads.sqlite3`'e dokunulmaz) |
| çekilen sayfa metinleri | `verify/pages/<domain>/<tur>.txt` (git'e girmez) |
| çıktı | `verify/verified.jsonl` (git'e girmez) |

## Ölçülen koşu (19 Eyl 2026, 100 alan adı)

96 kayıt yargılandı (4 alan adı hiç yanıt vermedi): **9 evet · 69 hayır · 18 belirsiz**.
Çekim 99 alan adı / 4 dk; hakem A 5,8 sn/kayıt (9,8 GB VRAM), hakem B 19,3 sn/kayıt
(%85 GPU / %15 CPU). Ayrıntı ve isabet incelemesi: [RAPOR.md](RAPOR.md).

## Uzlaşma kuralı

`evet` **yalnız** şu durumda: A ve B'nin ikisi de `yes`, ikisinin de alıntıları sayfa metninde
birebir geçiyor (boşluk/noktalama normalize edilir) **ve** kanıt geçidinden geçiyor. Biri `no`
derse → `hayir`. Diğer her durum → `belirsiz` (listelenmez).

**Kanıt geçidi:** meslek kaydı `beleg_anahtarlar` tanımlıyorsa, doğrulanmış alıntılardan en az
biri o anahtarlardan birini içermeli (ör. elektroingenieur için *Schaltung, Steuerung, Sensor,
Antrieb, Elektronik…*). "Entwicklung/Konstruktion" gibi genel sözcüklerle gelen yanlış meslek
atamalarını deterministik olarak keser; ölçülen etkisi isabet %64 → %78.

Amaç: listelenende ≥ %95 isabet, kapsam ikincil.

## Nezaket / sınırlar

robots.txt'e uyulur (`urllib.robotparser`), captcha çözülmez, login istenmez; alan adı başına
tek sekme ve ≥ 1 sn aralık (≤ 2 eşzamanlı sınırının altında), toplam ≤ 8 sekme, istek zaman
aşımı 20 sn, `User-Agent: lead-scraper-verify/0.1 (+SCRAPER_CONTACT)`.
E-posta tahmini yok; kişisel veri işlenmez — yalnız şirketlerin kamuya açık sayfaları.
