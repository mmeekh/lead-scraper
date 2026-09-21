<!-- kategori-basi -->
# DURUM — kategori taraması (OSM etiketi → site → havuz)
Güncelleme: 2026-09-21T14:24:11

| # | meslek | etiket | OSM kayıt | siteli | havuzda e-postalı | taranacak | taranan | e-postalı | önceki etikette | yüklenen | +eklenen | ~güncel | red | durum |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | kinder | pc-kita | 40421 | 11098 | 4743 | 6355 | 6355 | 1543 | 0 | 1543 | 1061 | 482 | 2 | bitti |
| 1 | it | pc-it | 5625 | 5165 | 3724 | 1441 | 1441 | 403 | 0 | 403 | 244 | 159 | 1 | bitti |
| 2 | steuer | pc-steuer | 2674 | 2189 | 1495 | 694 | 694 | 184 | 0 | 184 | 72 | 112 | 0 | bitti |
| 3 | logistik | pc-logistik | 2222 | 1936 | 1315 | 621 | 621 | 192 | 0 | 192 | 104 | 88 | 0 | bitti |
| 4 | sozial | pc-sozial | 13201 | 8565 | 5509 | 3056 | 3056 | 801 | 0 | 801 | 350 | 451 | 2 | bitti |
| 5 | industrie | pc-industrie | 9129 | 8498 | 5726 | 2772 | 2772 | 707 | 2 | 705 | 464 | 241 | 0 | bitti |
| 6 | pflege | pc-pflege | 4825 | 3083 | 2126 | 957 | 957 | 164 | 125 | 39 | 13 | 26 | 0 | bitti |
| 7 | kfz | pc-kfz | 16863 | 13369 | 7753 | 5616 | 5616 | 925 | 1 | 924 | 462 | 462 | 1 | bitti |
| 8 | handwerk | pc-handwerk | 9627 | 9306 | 6210 | 3096 | 3096 | 704 | 2 | 702 | 444 | 258 | 0 | bitti |
| 9 | zahn | pc-zahn | 7824 | 7631 | 4989 | 2642 | 2642 | 588 | 0 | 588 | 361 | 227 | 0 | bitti |
| 10 | physio | pc-physio | 7453 | 7050 | 4185 | 2865 | 2865 | 539 | 0 | 539 | 318 | 221 | 0 | bitti |
| 11 | friseur | pc-friseur | 12353 | 10731 | 6037 | 7409 | 7409 | 1376 | 2 | 1374 | 1078 | 296 | 6 | bitti |

Site durumları: park 2665, robots 581, tarandi 20815, ulasilamadi 12413

Kaynak: Geofabrik germany-latest.osm.pbf (pyosmium tek geçiş). Kurallar: robots.txt, alan adı başına ardışık istek ≥1 sn, 20 sn zaman aşımı, ≤5 sayfa/site; yalnız sitede yazılı rol e-postası (kişisel/freemail yok); site yoksa kayıt atlanır; ücretli API yok. Verify/ilan görevleri kapalı.
<!-- kategori-sonu -->
# DURUM — kalınlaştırma koşusu
Güncelleme: 2026-09-21T09:36:14 · istem v3-2026-09-19 · A `gemma4:12b-it-qat` · B `qwen3:8b`

## İlan çıkarımı (birincil ürün, modelsiz)

- Taranan alan adı: 19916 — kariyer sayfası bulunan **9151**, ilanı olan **4714**, toplam ilan **22591**, meslek eşleşen **17073**
- Dosya: `verify/out/ilanlar-001.jsonl` … `ilanlar-004.jsonl` (5.000 alan adı/dosya)

## Model yargısı (ikincil; yalnız ilansız alan adları)

- Şirket: 568512 — çekildi 19970, bekliyor 548542, çekilemedi 0
- Çift: 1165378 — ilanlı (modele gitmedi) 16498, evet **576**, hayır 1160, belirsiz 6288 (ön eleme 4201), bekleyen 1140856
- Son 1 saat: 21882 çift sonuçlandı, 545 evet
- Model çağrısı: A 3932, B 2622 · A=yes olup B bekleyen: 8
- Hız (son 24 s): A 5.7 sn/kayıt, B 5.6 sn/kayıt · disk boş 287 GB
- Duraklatılan meslek: yok

## Meslek başına (öncelik sırasıyla)

| meslek | durum | çift | biten | ön eleme | model kararı | evet | listeleme % (modelli) |
|---|---|---|---|---|---|---|---|
| fahrer | aktif | 21150 | 11633 | 4174 | 7459 | 566 | 7.6 |
| pflege | aktif | 12141 | 750 | 0 | 750 | 0 | 0.0 |
| elektro_ing | aktif | 6420 | 0 | 0 | 0 | 0 | - |
| dev | aktif | 15905 | 0 | 0 | 0 | 0 | - |
| it | aktif | 22487 | 0 | 0 | 0 | 0 | - |
| marketing | aktif | 36764 | 0 | 0 | 0 | 0 | - |
| buchhaltung | aktif | 14809 | 0 | 0 | 0 | 0 | - |
| sozial | aktif | 17336 | 624 | 0 | 624 | 0 | 0.0 |
| kinder | aktif | 5577 | 0 | 0 | 0 | 0 | - |
| koch | aktif | 74275 | 125 | 0 | 125 | 0 | 0.0 |
| backer | aktif | 4711 | 0 | 0 | 0 | 0 | - |
| fleischer | aktif | 5304 | 0 | 0 | 0 | 0 | - |
| metall | aktif | 19106 | 0 | 0 | 0 | 0 | - |
| cnc | aktif | 16979 | 0 | 0 | 0 | 0 | - |
| shk | aktif | 13176 | 0 | 0 | 0 | 0 | - |
| elektro | aktif | 20282 | 0 | 0 | 0 | 0 | - |
| kfz | aktif | 30238 | 0 | 0 | 0 | 0 | - |
| lager | aktif | 11152 | 1104 | 2 | 1102 | 1 | 0.1 |
| dispo | aktif | 10394 | 2300 | 7 | 2293 | 7 | 0.3 |
| kurier | aktif | 5724 | 1104 | 4 | 1100 | 1 | 0.1 |
| bus | aktif | 761 | 0 | 0 | 0 | 0 | - |
| arzt | aktif | 26915 | 126 | 0 | 126 | 0 | 0.0 |
| zahn | aktif | 15289 | 0 | 0 | 0 | 0 | - |
| physio | aktif | 17440 | 0 | 0 | 0 | 0 | - |
| ergo | aktif | 4841 | 56 | 0 | 56 | 0 | 0.0 |
| apotheke | aktif | 11639 | 0 | 0 | 0 | 0 | - |
| rettung | aktif | 3842 | 0 | 0 | 0 | 0 | - |
| kinderkrankenpflege | aktif | 3139 | 56 | 0 | 56 | 0 | 0.0 |
| service | aktif | 78459 | 125 | 0 | 125 | 0 | 0.0 |
| rezeption | aktif | 24531 | 0 | 0 | 0 | 0 | - |
| reinigung | aktif | 11066 | 72 | 0 | 72 | 0 | 0.0 |
| maler | aktif | 10146 | 0 | 0 | 0 | 0 | - |
| holz | aktif | 16873 | 0 | 0 | 0 | 0 | - |
| zimmerer | aktif | 10210 | 0 | 0 | 0 | 0 | - |
| dach | aktif | 10333 | 0 | 0 | 0 | 0 | - |
| bau | aktif | 23435 | 0 | 0 | 0 | 0 | - |
| bauleitung | aktif | 41967 | 0 | 0 | 0 | 0 | - |
| fliesen | aktif | 6390 | 0 | 0 | 0 | 0 | - |
| karosserie | aktif | 8021 | 0 | 0 | 0 | 0 | - |
| kalte | aktif | 5839 | 0 | 0 | 0 | 0 | - |
| haustechnik | aktif | 25419 | 56 | 0 | 56 | 0 | 0.0 |
| gartner | aktif | 18344 | 0 | 0 | 0 | 0 | - |
| friseur | aktif | 12284 | 0 | 0 | 0 | 0 | - |
| ingenieur | aktif | 21387 | 0 | 0 | 0 | 0 | - |
| fahrzeug_ing | aktif | 2415 | 0 | 0 | 0 | 0 | - |
| produktion | aktif | 46634 | 1104 | 3 | 1101 | 0 | 0.0 |
| qualitat | aktif | 45837 | 0 | 0 | 0 | 0 | - |
| controlling | aktif | 25406 | 0 | 0 | 0 | 0 | - |
| seo | aktif | 23672 | 0 | 0 | 0 | 0 | - |
| grafik | aktif | 43566 | 0 | 0 | 0 | 0 | - |
| vertrieb | aktif | 48494 | 1104 | 0 | 1104 | 0 | 0.0 |
| verkauf | aktif | 80403 | 1542 | 3 | 1539 | 0 | 0.0 |
| industriekauf | aktif | 76315 | 2641 | 8 | 2633 | 1 | 0.0 |
| werkzeugmacher | aktif | 136 | 0 | 0 | 0 | 0 | - |

Çıktı: `verify/out/verified-<meslek>-<n>.jsonl` (evet) · `redd-<meslek>-<n>.jsonl` (hayır/belirsiz) · 5.000 çift/parça.
Durdurmak: `verify/DUR` dosyası oluştur. Günlük: `verify/kalinlastir.log`. Blokajlar: `verify/BLOKAJ.md`.
