# `verify` modu — 19 Eyl 2026 otonom koşu raporu

Süre: ~4 saat, tek oturum. Makine: RTX 4070 12 GB / 32 GB RAM / Ryzen 5 7600.
Dal: `verify-modu` (push edilmedi). Ücretli/bulut API kullanılmadı; her karar yerel modelden.
Ölçüm dosyaları: `verify/olcum-v1.json`, `verify/olcum-v2.json`, `verify/olcum-v3.json`.

## 1. Ne kuruldu

`docs/yerel-dogrulama-modu-2026-09-19.md` §2'deki boru hattının PC tarafı, `scrape.py verify`
alt komutu olarak. Mevcut komutlara (`osm/links/seed/emails/export/stats`) ve `leads.sqlite3`'e
dokunulmadı; verify kendi SQLite dosyasında çalışır.

| aşama | komut | ne yapar |
|---|---|---|
| aday | `verify candidates` | sektör+şehir kotasıyla 100 alan adı kuyruğa |
| çekim | `verify fetch` | Playwright+Chromium, 5–8 sayfa (Impressum dahil), robots.txt |
| hakem | `verify judge` | iki yerel model **sırayla**, şema zorunlu JSON, alıntı doğrulama |
| uzlaşma | `verify consensus` | evet/hayır/belirsiz + Impressum'dan tüzel ad |
| çıktı | `verify export` | `verified.jsonl` (VPS'e gidecek biçim) |
| ölçüm | `verify gold` / `verify score` | insan etiketi CSV'si ve isabet hesabı |

Her aşama kaldığı yerden sürer (`domains.status`). Kesildi → aynı komut; biten iş tekrarlanmaz.

Kurulan bağımlılıklar (PC'de yoktu): Python 3.12, Playwright+Chromium, Ollama 0.34.2,
`gemma4:12b-it-qat` (7,2 GB), `qwen2.5:7b-instruct-q4_K_M` (4,7 GB),
`qwen2.5:14b-instruct-q4_K_M` (9,0 GB).

## 2. Aday küme

100 alan adı, 3 meslek × 3 şehir (kaynak: bu makinedeki OSM tabanlı şirket verisi, e-postalı kayıtlar):

| meslek kaydı | sektör | adet |
|---|---|---|
| `berufskraftfahrer` (LKW C/CE) | Otomotiv & Lojistik | 35 |
| `elektroingenieur` (Ingenieur Elektrotechnik) | Sanayi & Mühendislik | 35 |
| `marketing_manager` | Medya, Kültür & Etkinlik | 30 |

Şehir: Hamburg 36, München 32, Köln 32. Meslek tanımları (1 cümle Almanca + "kim istihdam eder /
kim etmez" notu) `verify/meslekler.json`'da — modelin sektör-sözcüğü tuzağına düşmemesi için.

## 3. Çekim ölçümü

| ölçü | değer |
|---|---|
| işlenen alan adı | 99 (1'i kuyrukta kaldı) |
| süre | **4 dk 1 sn** (8 eşzamanlı sekme) |
| toplam sayfa | 421 |
| alan adı başına sayfa | 4,4 |
| sayfa başına ortalama metin | 3 720 karakter |
| erişilemeyen | 4 |
| 2'den az sayfa | 4 |

Sayfa türü dağılımı: home 96, **impressum 88 (%92)**, kontakt 73, leistungen 62, ueber 61,
karriere 41. Impressum oranı kritik: tüzel ad oradan çıkıyor.

Kör testteki "200 dönüp boş sayfa" sınıfı kapandı: Playwright JS ile çizilen içeriği de alıyor,
metin 4 KB değil sayfa başına 40 KB'a kadar saklanıyor.

## 4. Ad doğrulama (Impressum) — "Kampf Immobilien" sınıfı hata

| ölçü | değer |
|---|---|
| Impressum sayfası olan | 88 |
| `legal_name` çıkarıldı | 69 (%78) |
| hukuki biçim içeren (GmbH/AG/e.K. …) | 64 (%93) |
| **havuz adıyla çelişen** | **28 (%41)** |

Çelişen adlarda havuz adı atılıyor. Örnekler:

| alan adı | havuzdaki ad | Impressum'dan |
|---|---|---|
| 1acarservice.de | 1A Car Service | Johannes J. Matthies GmbH & Co. KG |
| motorradvermietung-hamburg.de | Motorradvermietung Hamburg | dubb AG |
| helmstudio-hamburg.de | Helm-Studio Hamburg | MCW Profil Hamburg e.K |
| kreutziger-automobile.de | Harry Kreutziger Kfz-Handel GmbH | Kreutziger Automobile GmbH |

Koşu sırasında iki kusur bulunup düzeltildi: adresin ada yapışması
("AutoService-Helmer e.K Osterrade 29" → "AutoService-Helmer e.K") ve yumuşak tire (U+00AD).

## 5. Hakem ölçümleri — üç yapılandırma

Aynı 92 alan adı, aynı kanıt. Hız ve karar dağılımı:

| yapılandırma | evet | hayır | belirsiz | alıntı doğrulanan | sn/kayıt | VRAM |
|---|---|---|---|---|---|---|
| A `gemma4:12b-it-qat` v1 | 35 | 45 | 12 | %77 | 6,2 | 9,8 GB |
| A `gemma4:12b-it-qat` v2 | 24 | 55 | 13 | %69 | 5,6 | 9,8 GB |
| B `qwen2.5:7b` v1 | **0** | 9 | 83 | %76 | 4,9 | 6,5 GB |
| B `qwen2.5:7b` v2 | 1 | 80 | 11 | %75 | 4,8 | 6,5 GB |
| B `qwen2.5:14b` v2 | 3 | 60 | 29 | — | 14,3 | 12 GB (%85 GPU / %15 CPU) |
| B `qwen2.5:14b` **v3** | **25** | 61 | 6 | — | 18,6 | 12 GB (%85 GPU / %15 CPU) |

**Bulgu 1 — Gemma 4 "düşünen" bir model.** İlk koşuda çıktı bütçesinin tamamını `thinking`
alanına harcayıp `content`'i boş bıraktı (92 kayıtta JSON yok). `think: false` ile düzeldi;
bu, hattın çalışması için zorunlu ayar (`verify/judge.py`).

**Bulgu 2 — 7B hakem bu iş için yetersiz.** v1'de her şeye "belirsiz", v2'de her şeye "hayır"
dedi; gerekçesi hep aynı kalıptı: *"Das Material spricht nicht davon, dass ... selbst Personal
beschäftigt"*. İstem açıkça "böyle bir cümle gerekmez" dese de davranış değişmedi. Tasarım
belgesinin ilk tercihi olan 14B'ye geçildi.

**Bulgu 3 — VRAM payı.** `OLLAMA_GPU_OVERHEAD=2,4 GB` (%80 payı) altında 12B tam GPU'da
(9,8 GB), 14B %85 GPU / %15 CPU bölünüyor → 6 sn yerine 14 sn/kayıt. Makine boyunca
kullanılabilir kaldı.

## 6. Uzlaşma sonucu — iki yapılandırma

### 6a. A(v2) × B 14B **v2** istemi

| ölçü | değer |
|---|---|
| işlenen | 92 |
| **evet (listelenen)** | **2** |
| hayır | 58 |
| belirsiz | 32 |
| iki modelin aynı kararda olduğu | 57 (%62) |

A × B karar matrisi:

| | B=evet | B=hayır | B=belirsiz |
|---|---|---|---|
| **A=evet** | 3 | 2 | 19 |
| **A=hayır** | 0 | 43 | 12 |
| **A=belirsiz** | 0 | 1 | 12 |

3 "iki taraf da evet"in biri alıntı doğrulamasında elendi → 2 listelendi.

### 6b. A(v2) × B 14B **v3** istemi (örnekli karar listesi)

| ölçü | v2 | **v3** |
|---|---|---|
| evet (listelenen) | 2 | **11** |
| hayır | 58 | 65 |
| belirsiz | 32 | 16 |
| iki modelin aynı kararda olduğu | 57 (%62) | 64 (%70) |

A × B(v3) matrisi: A=evet∩B=evet **15** (→ alıntı doğrulamasından sonra 11 listelendi),
A=hayır∩B=hayır 51, A=evet∩B=hayır 6, A=belirsiz∩B=evet 7, A=hayır∩B=evet 3.

v3'ün tek farkı B'nin istemi: a–e sıralı karar listesi + 5 somut örnek. Bu, "açık cümle yoksa
belirsiz" refleksini kırdı (belirsiz 29 → 6) ve kapsamı 5,5 katına çıkardı.

## 7. Listelenen 11 kayıt ve kanıt incelemem

| # | şirket (Impressum'dan) | meslek | değerlendirmem |
|---|---|---|---|
| 1 | ABC Umzüge Verkerk GmbH | Berufskraftfahrer | doğru — nakliye + depo + dağıtım, ilan var |
| 2 | Johann Wunder GmbH | Berufskraftfahrer | doğru — *"Erfahrene Umzugshelfer und Fahrer"*, *"unserem modernen Fuhrpark"* |
| 3 | Branch of AKRO-PLASTIC GmbH (BIO-FED) | Elektroingenieur | **yanlış** — Ar-Ge var ama plastik/proses; elektrik değil |
| 4 | Fraunhofer CML | Elektroingenieur | sınırda — otomasyon/otonom sistemler, B'nin gerekçesi bile *"erfordern könnten"* |
| 5 | Technische Universität München (HLU kürsüsü) | Elektroingenieur | doğru — *"modellieren wir zukunftsfähige elektrische Energiesysteme"*, Power Engineering kürsüleri |
| 6 | iQLASE GmbH (Lessmüller Lasertechnik) | Elektroingenieur | doğru — *"60 Mitarbeitern aus Ingenieuren…"*, ilan: *"Praktikum in der Elektronik"* |
| 7 | Gebrüder Stöve GmbH (Maxipress) | Elektroingenieur | **yanlış** — makine satıcısı/servisi; "Steuerung" ürün özelliği, kendi mühendisi değil |
| 8 | Wassermann Dental-Maschinen GmbH | Elektroingenieur | sınırda — kendi üretimi var ama 1-9 kişi, ağırlık ince mekanik |
| 9 | Heinrich Bauer Verlag KG | Marketing Manager | doğru — kendi *Communications and Media Relations* birimi |
| 10 | Citinaut GmbH | Marketing Manager | doğru — reklam ajansı, kendi Marketingkommunikation çırağı yetiştiriyor |
| 11 | Nexxel GmbH | Marketing Manager | doğru — SEO/görünürlük ajansı, hizmeti kendi veriyor |

**Sonuç: 7 açıkça doğru, 2 sınırda, 2 yanlış.** Katı sayımla %64, sınırdakiler doğru sayılırsa %82.
Hedef %95 **tutturulamadı** ve hataların hepsi tek yerde toplanıyor: `elektroingenieur`.
Model "mühendislik/geliştirme yapıyor" ile "elektrik mühendisi çalıştırıyor"u ayırt edemiyor
(plastik Ar-Ge, makine bayisi, ince mekanik → hepsi "evet").

## 8. İsabet ölçümü — ne ölçülebildi, ne ölçülemedi

**Ölçülemeyen:** tasarımın §6'daki "≥ %95 isabet" iddiası **kanıtlanamaz**, çünkü insan etiketli
altın küme yok (300 çift, 3 kişi). Bkz. `verify/BLOKAJ.md`. Hazırlanan: `verify/altin-kume.csv`
(92 satır, her satırda hattın kararı, iki modelin ayrı kararı, alıntılar, ana sayfa linki, boş
`etiket` sütunu) ve `verify score` komutu (isabet/kapsama/karışıklık matrisi, meslek kırılımı).

**Ölçülen (insan etiketi değil, benim kanıt incelemem):** iki küme inceledim. Önce A'nın tek
başına "evet" dediği 24 kayıt:

| değerlendirme | adet | örnek |
|---|---|---|
| açıkça doğru | 13 | derlichtpeter.de (*"Dipl.-Ing. (DH) für Elektrotechnik"*), lessmueller.de (*"Team von etwa 60 Mitarbeitern aus Ingenieuren…"*), citinaut.de (Werbeagentur) |
| sınırda | 6 | epe.ed.tum.de, mos.ed.tum.de (üniversite kürsüsü), embl-hamburg.de, feierwerk.de (dernek) |
| büyük olasılıkla yanlış | 5 | beissbarth.de (tamir servisi → LKW şoförü değil), raedervogel.de ve westring-dichtungstechnik.de (makine mühendisi var, elektrik değil), bio-fed.com (kimya Ar-Ge) |

Yani **A tek başına %95'in belirgin altında** (en iyimser sayımla %79, sınırdakiler yanlış
sayılırsa %54).

Uzlaşma kuralının iki yapılandırmasında (§7 ayrıntısı):

| yapılandırma | listelenen | incelememde doğru | sınırda | yanlış | katı isabet |
|---|---|---|---|---|---|
| A(v2) × B 14B v2 | 2 | 2 | 0 | 0 | %100 (örneklem çok küçük) |
| A(v2) × B 14B **v3** | 11 | 7 | 2 | 2 | **%64** (sınırdakiler doğru sayılırsa %82) |
| A tek başına | 24 | 13 | 6 | 5 | %54–79 |

Yani uzlaşma kuralı isabeti gerçekten yükseltiyor, ama v3'le kapsamı açınca %95'in altına
düşüyor. Bu bir ayar noktasıdır, hattın çöküşü değil: hatanın tamamı tek meslek kaydında.
Ve unutulmamalı: bu satırlar **benim** değerlendirmem; karar insan etiketiyle verilmelidir.

## 9. Hız ve ölçek tahmini (ölçülen sayılarla)

| iş | ölçülen | 100 bin alan adına izdüşüm |
|---|---|---|
| çekim | 99 alan adı / 4 dk (8 sekme) | ~67 saat ≈ **3 gün** (tek makine) |
| hakem A (12B) | 5,6 sn/kayıt | ~156 saat ≈ 6,5 gün |
| hakem B (14B, v3 istemi) | 18,6 sn/kayıt | ~517 saat ≈ 21,5 gün |
| **toplam GPU** | | **~28 gün** (tek meslek/şirket) |

Tasarımın "~1 hafta" tahmini, hakem B için 14B kullanılırsa **tutmuyor**. Seçenekler:
gömme ile ön eleme (§2.3, henüz kurulmadı), B'yi yalnız A'nın "evet" dediği kayıtlarda koşturmak
veya B için daha küçük/hızlı ama yeterli bir model bulmak.

> **Dikkat:** "B'yi yalnız A=evet dediğinde koştur" kısayolu v2'de zararsızdı ama **v3'te
> değil**: v3'te listelenen 11 kaydın hiçbiri A=belirsiz'den gelmiyor, ancak B'nin tek başına
> "evet" dediği 10 kayıt (A=belirsiz∩B=evet 7 + A=hayır∩B=evet 3) hattın gelecekteki
> ayarları için bilgi taşıyor. Kısayol alınırsa bu görünürlük kaybedilir; GPU bütçesi
> %74 düşer. Karar, ölçüm bittikten sonra verilmeli.

## 10. Sırada ne var

1. **İnsan etiketi** (blokaj): `verify/altin-kume.csv` → `etiket` sütunu → `verify score`.
   Bu yapılmadan "%95" iddiası yazılamaz.
2. **Kapsam**: 92'de 2 listeleme (%2). Kapsamı büyüten tek meşru yol, B'nin 19 adet
   "A=evet ama B=belirsiz" kaydını doğru tarafa çekmek (v3 istemi bunu hedefliyor, §11).
3. **Ön eleme** (`bge-m3`): 100 bin ölçeğinde GPU bütçesini düşürür.
4. **VPS tarafı** (§5): `company_verified` tablosu + `POST /api/havuz/dogrulanmis`; bu oturumda
   dokunulmadı (talimat: yalnız PC tarafı, push yok).

## 11. v3 istemi (örnekli karar listesi) — sonuç

Hakem B için üçüncü istem sürümü yazıldı: a–e sıralı karar listesi + 5 somut örnek
(nakliyeci → evet, oto galeri → hayır, reklam ajansı → evet, makine üreticisi → evet,
elektronik satan e-ticaret → hayır). Amaç: "açık cümle yoksa belirsiz" refleksini kırmak.

**Ölçülen etki:** B'nin "belirsiz"i 29 → 6'ya düştü, "evet"i 3 → 25'e çıktı; uzlaşmadan geçen
kayıt 2 → 11 (kapsam %2 → %12). Maliyet: 14,3 → 18,6 sn/kayıt (istem uzadı).
İsabet ise §7'ye göre düştü — kapsam/isabet dengesi tam olarak burada ayarlanacak.

**Öneri (bir sonraki tur, ölçülerek):** `meslekler.json`'daki `elektroingenieur` kaydına
"elektrik/elektronik/kontrol kanıtı zorunlu" sınırı eklensin — ör. *"Beleg muss ein
elektrisches/elektronisches Artefakt nennen (Schaltung, Steuerung, Antrieb, Sensorik,
Leistungselektronik, Messtechnik); allgemeine Begriffe wie Entwicklung, Konstruktion,
Ingenieurbüro genügen nicht."* Bu koşudaki 2 yanlışın ikisi de bu sınırla elenirdi
(plastik Ar-Ge, makine bayisi). Bu kayıt **bilerek değiştirilmedi**: ölçüm ile veri
tutarsız kalmasın.

## 12. Şu anki durum ve devam

Çalışan komut dizisi (hepsi kaldığı yerden sürer):

```
python scrape.py verify candidates --limit 100
python scrape.py verify fetch --limit 100
python scrape.py verify judge --limit 100          # A sonra B, sirayla
python scrape.py verify consensus --hepsi
python scrape.py verify export                     # verify/verified.jsonl
python scrape.py verify gold                       # verify/altin-kume.csv  <-- SIRA SENDE
python scrape.py verify score --in verify/altin-kume.csv
```

Veritabanında: 100 alan adı, 421 sayfa, 276 yargı (3 model yapılandırması), 92 doğrulanmış kayıt.
`verify/verified.jsonl` VPS'e gidecek biçimde hazır (92 kayıt; 11'i `evet`).
`verify/verify-v1-yedek.sqlite3` ve `verify-v2-yedek.sqlite3` önceki istem sürümlerinin yedeği.

**Senin sıradaki adımın:** `verify/altin-kume.csv`'deki `etiket` sütununu doldur (evet/hayir/belirsiz),
`verify score` çalıştır. O sayı gelmeden "%95" yazılmamalı — §8'deki rakamlar benim incelemem,
insan etiketi değil.
