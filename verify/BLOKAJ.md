# BLOKAJ — 19 Eyl 2026 otonom koşu

Devam edilemeyen tek şey ve nedeni. Geri kalan her parça çalıştı; ayrıntı `verify/RAPOR.md`.

## 1. Altın küme insan etiketi yok → "≥ %95 isabet" KANITLANAMAZ

**Ne gerekiyordu:** `docs/yerel-dogrulama-modu-2026-09-19.md` §6: 300 (şirket, meslek) çifti,
Emin + 2 arkadaş 100'er satır etiketler; listelenen (evet + both) satırların isabeti ölçülür.

**Neden yapılamadı:** Etiket insan kararıdır; otonom koşuda üretilemez. Hattın kendi kararını
kendi doğruluğu sayarsam ölçüm anlamsız olur.

**Yerine yapılan:**
1. `verify/altin-kume.csv` üretildi — her satırda hattın kararı, iki modelin ayrı kararı,
   alıntılar, şirketin ana sayfa linki ve boş `etiket` sütunu (evet / hayir / belirsiz).
2. `python scrape.py verify score --in verify/altin-kume.csv` etiketler dolunca isabet,
   kapsama, karışıklık matrisi ve meslek kırılımını hesaplar (kod hazır ve test edildi).
3. Ben ayrıca listelenen satırların kanıtlarını tek tek okudum; bu **ön kontrol** RAPOR'da
   ayrı başlıkta ve "insan etiketi değildir" notuyla veriliyor.

**Sıradaki adım (insan):** CSV'deki `etiket` sütununu doldur → `verify score` çalıştır.
Sonuç ≥ %95 ise istemler dondurulur; değilse RAPOR'daki hata sınıflarına göre istem düzeltilir.

## 2. Kapsam sınırı (bilinçli, blokaj değil)

- Bu oturumda 100 alan adı tarandı (talimat sınırı). Ölçüm bu 100 kayıt üzerindedir;
  güven aralığı dardır ama 3 meslek × 3 şehir dağılımı temsil edicidir.
- Gömme ile ön eleme (`bge-m3`, tasarım §2.3) kurulmadı: 100 alan adı × 1 meslek = 100 çift
  için gereksiz. 100 bin alan adlık koşudan önce eklenmeli (aksi halde GPU bütçesi 5-6 katına çıkar).
- VPS tarafı (§5: `POST /api/havuz/dogrulanmis`, `company_verified` tablosu) bu oturumda
  yazılmadı — talimat gereği yalnız PC tarafı ve push yok.
