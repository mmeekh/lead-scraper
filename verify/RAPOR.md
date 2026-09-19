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

## 12. Deney: `elektroingenieur` tanımını keskinleştirmek — öncesi/sonrası

§7'deki hataların hepsi tek meslek kaydındaydı, o yüzden öneriyi denedim: `meslekler.json`'daki
`elektroingenieur` kaydına "kanıt somut bir elektrik/elektronik öğesi adlandırmalı; Entwicklung /
Konstruktion / Forschung gibi genel sözcükler yetmez" (`beleg_pflicht_de`) kuralı eklendi ve o 35
alan adı iki modelle yeniden yargılandı (14 dk). **Yalnız istem/tanım değişti, sayfa verisi aynı.**

Model kararlarındaki geçişler (34 karşılaştırılabilir alan adı):

| model | değişmeyen | yes→unclear | yes→no | unclear→no | no→unclear | net etki |
|---|---|---|---|---|---|---|
| A `gemma4:12b-it-qat` | **34** (18 no, 14 yes, 2 unclear) | 0 | 0 | 0 | 0 | **hiç değişmedi** |
| B `qwen2.5:14b-instruct` | 28 | 2 | 1 | 2 | 1 | 3 yes azaldı |

- **no→yes geçişi: 0** (iki modelde de). Yani keskinleştirme hiçbir kaydı listeye eklemedi.
- İki modelin aynı kararda olduğu oran: **%68 → %71**.
- Uzlaşma düzeyinde elektro "evet": **6 → 4** (3 evet→belirsiz, 3 evet→evet; ayrıca sonradan
  yargılanan tek sayfalık `ing-buero-sommer.de` yeni "evet" olarak eklendi).

**Sonuç: tanımı keskinleştirmek yetmedi.** A'yı hiç etkilemedi, B'yi yalnız daha temkinli yaptı;
§7'de yanlış bulduğum iki kayıt (`bio-fed.com`, `maxipress.de`) yine listelendi. Hata sınıfı
modelin muhakemesinde, sözcüklerde değil — bu yüzden çözüm koda taşındı (§13).

## 13. Deterministik kanıt geçidi (işe yarayan düzeltme)

İstem yerine koda konan kural: meslek kaydı `beleg_anahtarlar` listesi tanımlıyorsa, "evet" için
**doğrulanmış alıntılardan en az biri** o anahtarlardan birini içermeli (`consensus.kanit_gecidi`).
Model çağrısı gerektirmez; uzlaşma saniyeler içinde yeniden hesaplanır.

| ölçü | v3 istemi | v3 + **geçit** |
|---|---|---|
| listelenen | 11 | **9** |
| incelememde doğru | 7 | **7** |
| sınırda | 2 | 1 |
| **yanlış** | **2** | **1** |
| katı isabet | %64 | **%78** (sınırda doğru sayılırsa %89) |

Geçidin ne yaptığı (ölçüldü): `bio-fed.com` elendi — alıntıları *"research and development
department"*, *"engineering department"*; elektrik/elektronik öğesi yok → doğru eleme.
`wassermann-dental.com` (sınırda) elendi — *"Konstruktion und Produktion"*, *"Feinmechanik"*.
`lessmueller.de` ve `assenmacher.net` geçitten geçiyordu; onları alıntı doğrulama düşürdü
(modelin o koşudaki alıntıları birebir değildi) — bu, sıfır sıcaklıkta bile kalan bir oynaklık.

**Kalan tek yanlış:** `maxipress.de` — alıntılarında *"Steuer- und Regelgeräte"*, *"eigener
Hydraulik und Elektrik"* geçiyor, ama bunlar **sattığı makinenin** özellikleri; firma üretici
değil bayi/servis. Ayırmak için kanıtın *"wir entwickeln/konstruieren"* gibi birinci çoğul bir
fiile bağlanması gerekir — sıradaki tur için somut fikir.

## 14. Kapsam tamamlandı: 96/100 alan adı

Başlangıçta yalnız 2+ sayfa çekilebilen 92 alan adı yargılanmıştı. Tek sayfa çekilebilen 4 site de
(yeni çekim yapılmadan, eldeki sayfayla) yargıya alındı → **96 kayıt**. Kalan 4 alan adı
(`auto-arenz.de`, `buch-benecke.de`, `grossefreiheit-nr7.de`, `stellwerk-duennwald.de`) hiç yanıt
vermedi; içerik olmadan yargı üretilmez, bunlar `cekilemedi` olarak duruyor.

Tek sayfalık sitelerden biri listeye girdi: **Ingenieurbüro Sommer** (`ing-buero-sommer.de`) —
alıntılar *"Elektroplanung"*, *"Lichtplanung"*, *"KNX · Netzwerktechnik · IP-Kameras"*; meslek
kaydındaki "Ingenieurbüros für Elektrotechnik" tanımına birebir uyuyor. Yani tek sayfa da
açık kanıt taşıyorsa yetiyor.

**Son durum (96 kayıt):** 9 evet · 69 hayır · 18 belirsiz. Uzlaşma: 64 `both`, 32 `one`.
Meslek kırılımı (evet): berufskraftfahrer 2, elektroingenieur 4, marketing_manager 3.

Listelenen 9 kaydın incelemem: 7 doğru (abc-umzuege, j-wunder, epe.ed.tum, ing-buero-sommer,
bauermedia, citinaut, nexxel), 1 sınırda (cml.fraunhofer), 1 yanlış (maxipress).

## 15. Hız, VRAM ve ölçek (son ölçüm)

| | ölçülen | not |
|---|---|---|
| çekim | 99 alan adı / 4 dk 1 sn | 8 eşzamanlı sekme, 421 sayfa |
| hakem A `gemma4:12b-it-qat` | **5,8 sn/kayıt** (96 yargı) | 9,8 GB VRAM, tamamen GPU'da |
| hakem B `qwen2.5:14b-instruct-q4_K_M` | **19,3 sn/kayıt** (96 yargı) | %85 GPU / %15 CPU (12 GB kartta %80 payı) |
| hakem B (kullanılmayan 7B) | 4,8 sn/kayıt (92 yargı) | 6,5 GB; karar üretmediği için elendi |
| toplam yargı | 284 | üç model yapılandırması |
| boştaki VRAM | 978 MiB / 12 282 MiB | koşu bitince model boşaltıldı |

100 bin alan adına izdüşüm: çekim ~3 gün, A ~6,5 gün, B ~21 gün → **~28 gün tek makine**.
Kısaltma yolları §9'da (gömme ile ön eleme; B'yi yalnız A=evet'te koşturmak — bu koşuda
listelenenlerin tamamı A=evet'ten geldiği için sonucu değiştirmezdi, GPU'yu %74 düşürürdü).

## 16. Şu anki durum ve devam

Çalışan komut dizisi (hepsi kaldığı yerden sürer):

```
python scrape.py verify candidates --limit 100
python scrape.py verify fetch --limit 100
python scrape.py verify judge --limit 100          # A sonra B, sirayla
python scrape.py verify consensus --hepsi
python scrape.py verify export                     # verify/verified.jsonl
python scrape.py verify gold --kor                 # verify/altin-kume-kor.csv (KOR)
python scrape.py verify score --etiket verify/altin-kume-kor.csv
python verify/test_verify.py                       # 27 test, ag/model gerektirmez
```

Veritabanında: 100 alan adı, 421 sayfa, 284 yargı, 96 doğrulanmış kayıt.
`verify/verified.jsonl` VPS'e gidecek biçimde hazır (96 kayıt; 9'u `evet`).
Yedekler: `verify-v1-yedek.sqlite3` (istem v1), `-v2-`, `-v3-`, `-v4-elektro-` (keskin tanım deneyi).

## 17. İNSAN ETİKETLİ ÖLÇÜM — gerçek sonuç (20 Eyl 2026)

Emin 96 satırın tamamını **kör** kopyada etiketledi (`altin-kume-kor.csv`: hakem kararı, uzlaşma
ve alıntı görünmüyordu). Ölçüm: `verify score --etiket verify/altin-kume-etiketli.csv`.

| ölçü | değer |
|---|---|
| etiketli satır | 96 (96'sı eşleşti, eşleşmeyen 0) |
| **listelenen satırda isabet** | **%88,9** (9 listelenen, 8 doğru) |
| hedef | %95 — **tutturulamadı** (1 hata) |
| kapsama | insan 24 satıra "evet" dedi, hat 9'unu listeledi → **%37,5** |
| %95 güven aralığı | **%56,5 – %98,0** (9 satır çok küçük örneklem) |

**Meslek kırılımı:** berufskraftfahrer 2/2 (%100) · marketing_manager 3/3 (%100) ·
**elektroingenieur 3/4 (%75)** — tek hata yine burada.

**Tek yanlış:** `maxipress.de` — insan "hayır" dedi. Bu, §13'te önceden işaret ettiğim kayıt:
alıntıları (*"Steuer- und Regelgeräte"*, *"eigener Hydraulik und Elektrik"*) **sattığı makinenin**
özellikleri, firmanın kendi mühendisliği değil. Kendi incelememde de tek kalan yanlış buydu;
insan etiketi bunu doğruladı. (İncelememde "sınırda" dediğim `cml.fraunhofer.de`'ye insan "evet"
demiş — yani ben hattan daha sert davranmışım.)

### Uzlaşma kuralı gerçekten işe yarıyor (ölçüldü)

| yapılandırma | listelenen | doğru | isabet | %95 güven aralığı |
|---|---|---|---|---|
| A `gemma4:12b` tek başına | 25 | 18 | %72,0 | 52,4 – 85,7 |
| B `qwen2.5:14b` tek başına | 24 | 14 | %58,3 | 38,8 – 75,5 |
| **uzlaşma + alıntı + geçit** | **9** | **8** | **%88,9** | 56,5 – 98,0 |

İki modelin uzlaşması en iyi tek modele göre **+17 puan** isabet getiriyor. Tasarımın temel
iddiası bu; artık insan etiketiyle ölçülmüş durumda.

### Sıkılık / kapsam eğrisi (aynı veriden, ölçülmüş)

İki model de "evet" dediği halde listelenmeyen 7 kayıt var; hangi kuralın elediği ayrıştırıldı:

| gevşetilen kural | listelenen | doğru | isabet | kapsama (insan evet 24) |
|---|---|---|---|---|
| **mevcut** (iki alıntı da doğrulanmalı + geçit) | 9 | 8 | **%88,9** | %33,3 |
| alıntı kuralı gevşek (bir taraf yeterli) | 14 | 12 | %85,7 | %50,0 |
| kanıt geçidi kapalı | 11 | 9 | %81,8 | %37,5 |

- Alıntı doğrulaması 5 kaydı düşürdü; 4'ü insan etiketine göre **doğruydu**
  (assenmacher.net, lessmueller.de, oekom.de, simple.de) — modelin o koşudaki alıntısı birebir
  değildi, kararı değil. Bu kuralı gevşetmek kapsamı %33 → %50 çıkarıyor, isabeti 3 puan düşürüyor.
- Kanıt geçidi 2 kaydı düşürdü: `bio-fed.com` (insan: **evet** → geçit yanıldı) ve
  `wassermann-dental.com` (insan: belirsiz → doğru eleme). Yani geçit net kazanç değil;
  bir doğruyu da eliyor.

### Neden %95 "kanıtlanamaz" — istatistik

9 listelenen satırda tek hata isabeti 11 puan düşürüyor; güven aralığının alt sınırı %56.
Bir yapılandırmanın ≥%95 olduğunu **göstermek** için listelenen satır sayısı büyümeli:

| listelenen satır | 1 hata varsa isabet | güven aralığı alt sınırı |
|---|---|---|
| 20 | %95,0 | %76,4 |
| 40 | %97,5 | %87,1 |
| 60 | %98,3 | %91,1 |
| 100 | %99,0 | %94,6 |

Yani "≥%95" iddiası için ~60–100 listelenen satırın etiketlenmesi gerekir; bu da ~600–1 000 alan
adlık bir koşu demek (mevcut listeleme oranı %9). Bu koşunun verdiği şey: **hat %88,9 ölçüldü,
en iyi tek modelden 17 puan iyi, ve kalan hatanın sınıfı tek ve tarifli.**

### Sıradaki tek somut iş

`maxipress.de` sınıfı hata: ürün özelliğini firmanın kendi işi sanmak. Kanıt geçidine ikinci
koşul eklenebilir — alıntı, firmayı özne yapan bir fiile bağlı olmalı (*"wir entwickeln",
"wir konstruieren", "unsere Ingenieure", "entwickelt das Unternehmen"*). Bu koşulun bu veri
üzerindeki etkisi ölçülmeden açılmamalı: `ing-buero-sommer.de` gibi fiilsiz ama doğru kayıtları
(*"Elektroplanung · Lichtplanung · KNX"*) düşürme riski var.

---

# ✅ Etiketler geldi — ölçüm yapıldı (§17)

`verify/altin-kume-kor.csv` **kör** kopyadır: içinde hakem kararı, uzlaşma ya da alıntı **yoktur**;
yalnız `domain`, `legal_name`, `meslek`, `site_linki`, `hakkinda_linki` ve boş `insan_karari` +
`not` sütunları var. Her satırda sorulan soru meslek koduna göre şudur:

| meslek | soru |
|---|---|
| `berufskraftfahrer` | Bu şirket **kendi bünyesinde** Berufskraftfahrer (LKW, C/CE) çalıştırır mı? |
| `elektroingenieur` | Bu şirket **kendi bünyesinde** Ingenieur Elektrotechnik çalıştırır mı? |
| `marketing_manager` | Bu şirket **kendi bünyesinde** Marketing Manager çalıştırır mı? |

`insan_karari` sütununa **evet / hayir / belirsiz** yazılır (boş bırakılan satır ölçüme girmez).
Emin 96 satırın tamamını etiketledi; sonuç §17'de. Ölçümü tekrarlamak için:

```
python scrape.py verify score --etiket verify/altin-kume-kor.csv
```

Çıktı: listelenen satırlarda **isabet**, kapsama, karışıklık matrisi, meslek kırılımı, iki modelin
tek başına isabeti, yanlış listelenenler ve kaçırılanlar (domain listesiyle). Eşleştirme
`domain` + `meslek` üzerinden yapılır; kör CSV'de sütun sırası değişse de çalışır.

Etiketli dosya: `verify/altin-kume-etiketli.csv` (git dışında; sürümlemek istersen
`git add -f verify/altin-kume-etiketli.csv`). Sonuç: **%88,9 isabet, %37,5 kapsama** (§17).
Hedef %95 tutturulamadı; neden ve ne gerektiği §17'de.
