# DURUM — kalınlaştırma koşusu
Güncelleme: 2026-09-20T23:58:02 · istem v3-2026-09-19 · A `gemma4:12b-it-qat` · B `qwen3:8b`

## İlan çıkarımı (birincil ürün, modelsiz)

- Taranan alan adı: 3051 — kariyer sayfası bulunan **1352**, ilanı olan **604**, toplam ilan **2344**, meslek eşleşen **1704**
- Dosya: `verify/out/ilanlar-001.jsonl` … `ilanlar-001.jsonl` (5.000 alan adı/dosya)

## Model yargısı (ikincil; yalnız ilansız alan adları)

- Şirket: 538061 — çekildi 3101, bekliyor 534960, çekilemedi 0
- Çift: 1127293 — ilanlı (modele gitmedi) 1, evet **31**, hayır 94, belirsiz 249 (ön eleme 125), bekleyen 1126918
- Son 1 saat: 375 çift sonuçlandı, 31 evet
- Model çağrısı: A 358, B 101 · A=yes olup B bekleyen: 13
- Hız (son 24 s): A 5.9 sn/kayıt, B 6.5 sn/kayıt · disk boş 291 GB
- Duraklatılan meslek: yok

## Meslek başına (öncelik sırasıyla)

| meslek | durum | çift | biten | ön eleme | model kararı | evet | listeleme % (modelli) |
|---|---|---|---|---|---|---|---|
| fahrer | aktif | 21150 | 791 | 98 | 693 | 21 | 3.0 |
| pflege | aktif | 12141 | 0 | 0 | 0 | 0 | - |
| elektro_ing | aktif | 5241 | 0 | 0 | 0 | 0 | - |
| dev | aktif | 9206 | 0 | 0 | 0 | 0 | - |
| it | aktif | 15788 | 0 | 0 | 0 | 0 | - |
| marketing | aktif | 36764 | 0 | 0 | 0 | 0 | - |
| buchhaltung | aktif | 9133 | 0 | 0 | 0 | 0 | - |
| sozial | aktif | 17336 | 0 | 0 | 0 | 0 | - |
| kinder | aktif | 5577 | 0 | 0 | 0 | 0 | - |
| koch | aktif | 74275 | 22 | 0 | 22 | 0 | 0.0 |
| backer | aktif | 3839 | 0 | 0 | 0 | 0 | - |
| fleischer | aktif | 4429 | 0 | 0 | 0 | 0 | - |
| metall | aktif | 19106 | 0 | 0 | 0 | 0 | - |
| cnc | aktif | 16979 | 0 | 0 | 0 | 0 | - |
| shk | aktif | 13176 | 0 | 0 | 0 | 0 | - |
| elektro | aktif | 15537 | 0 | 0 | 0 | 0 | - |
| kfz | aktif | 30238 | 0 | 0 | 0 | 0 | - |
| lager | aktif | 11152 | 166 | 2 | 164 | 1 | 0.6 |
| dispo | aktif | 10394 | 367 | 7 | 360 | 7 | 1.9 |
| kurier | aktif | 5724 | 166 | 4 | 162 | 1 | 0.6 |
| bus | aktif | 318 | 0 | 0 | 0 | 0 | - |
| arzt | aktif | 26915 | 0 | 0 | 0 | 0 | - |
| zahn | aktif | 15289 | 0 | 0 | 0 | 0 | - |
| physio | aktif | 17440 | 0 | 0 | 0 | 0 | - |
| ergo | aktif | 3361 | 0 | 0 | 0 | 0 | - |
| apotheke | aktif | 11639 | 0 | 0 | 0 | 0 | - |
| rettung | aktif | 3631 | 0 | 0 | 0 | 0 | - |
| kinderkrankenpflege | aktif | 2071 | 0 | 0 | 0 | 0 | - |
| service | aktif | 78459 | 22 | 0 | 22 | 0 | 0.0 |
| rezeption | aktif | 24531 | 0 | 0 | 0 | 0 | - |
| reinigung | aktif | 11066 | 13 | 0 | 13 | 0 | 0.0 |
| maler | aktif | 10146 | 0 | 0 | 0 | 0 | - |
| holz | aktif | 16873 | 0 | 0 | 0 | 0 | - |
| zimmerer | aktif | 10210 | 0 | 0 | 0 | 0 | - |
| dach | aktif | 10333 | 0 | 0 | 0 | 0 | - |
| bau | aktif | 23435 | 0 | 0 | 0 | 0 | - |
| bauleitung | aktif | 41967 | 0 | 0 | 0 | 0 | - |
| fliesen | aktif | 4489 | 0 | 0 | 0 | 0 | - |
| karosserie | aktif | 7155 | 0 | 0 | 0 | 0 | - |
| kalte | aktif | 5137 | 0 | 0 | 0 | 0 | - |
| haustechnik | aktif | 25419 | 0 | 0 | 0 | 0 | - |
| gartner | aktif | 14541 | 0 | 0 | 0 | 0 | - |
| friseur | aktif | 12284 | 0 | 0 | 0 | 0 | - |
| ingenieur | aktif | 21387 | 0 | 0 | 0 | 0 | - |
| fahrzeug_ing | aktif | 1549 | 0 | 0 | 0 | 0 | - |
| produktion | aktif | 46634 | 166 | 3 | 163 | 0 | 0.0 |
| qualitat | aktif | 45837 | 0 | 0 | 0 | 0 | - |
| controlling | aktif | 25406 | 0 | 0 | 0 | 0 | - |
| seo | aktif | 23672 | 0 | 0 | 0 | 0 | - |
| grafik | aktif | 43566 | 0 | 0 | 0 | 0 | - |
| vertrieb | aktif | 48494 | 166 | 0 | 166 | 0 | 0.0 |
| verkauf | aktif | 80403 | 283 | 3 | 280 | 0 | 0.0 |
| industriekauf | aktif | 76315 | 444 | 8 | 436 | 1 | 0.2 |
| werkzeugmacher | aktif | 136 | 0 | 0 | 0 | 0 | - |

Çıktı: `verify/out/verified-<meslek>-<n>.jsonl` (evet) · `redd-<meslek>-<n>.jsonl` (hayır/belirsiz) · 5.000 çift/parça.
Durdurmak: `verify/DUR` dosyası oluştur. Günlük: `verify/kalinlastir.log`. Blokajlar: `verify/BLOKAJ.md`.
