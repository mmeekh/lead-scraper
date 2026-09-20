# Kalınlaştırma ön kontrolü — 20 Eylül 2026

Durum: **Toplu koşu başlatılmadı.** Bu dosya ön kontrol raporudur; saatlik otomatik
güncelleme henüz kurulmadı. Mevcut 96 kayıtlık ölçüm veritabanına yazılmadı.

## Aday dosyası

- VPS kaynağı: `/root/.claude/jobs/0992b980/tmp/jobfind-adaylar-53meslek-2026-09-20.csv.gz`
- Yerel kopya: `verify/jobfind-adaylar-53meslek-2026-09-20.csv.gz`
- SHA-256 (VPS ve PC aynı): `0e1fb641c8da72737403b07fb039a671d669399f877e12a405e2502bd92c58a6`
- 539.659 satır ve 539.659 farklı alan adı.
- Dosya adına rağmen **54 farklı meslek**, **1.130.163 şirket–meslek çifti**.
- Mesleklerin tamamının yerel katalogda tanımı var; bilinmeyen kimlik yok.
- `data` ve `sap` yok. `dev` ve `it` ayrı kimlikler.
- Bir şirkette en fazla **8 meslek** var. Önceki prompt'un “en çok 3” ifadesi
  yeni dosyayla uyuşmuyor; 3'e sessizce kırpmak meslekleri kaybettirir.
- Eski aday dosyası yerel `verify/` içinde bulunmadı; VPS dosyaları değiştirilmedi.

## Kaynaktan bulunan önceki koşu kuralları

Önceki prompt VPS'teki `/root/.claude/jobs/0992b980/timeline.jsonl` kaydından okundu.
Son mesajdaki 54 meslek sırası ve dört büyük meslek için ilk turda 20.000 sınırı
önceki meslek listesinin yerine geçer. Diğer koşullar:

- Büyük koşudan önce v5: cümle düzeyi alıntı kontrolü, bayi/servis ayrımı,
  elektro kanıt geçidi, uygun model/bellek ayarı; 96 insan etiketli kayıtta yeniden
  ölçüm. İsabet %85 altındaysa koşu durur.
- bge-m3 ön eleme eşiği, 96 kayıt içindeki insan “evet”lerinin en az %95'ini
  geçirecek şekilde ölçülerek seçilir.
- Alan adı başına en fazla 4 sayfa, robots kuralları, en az 1 saniye aralık;
  alan adı başına en fazla 2, toplam en fazla 12 eşzamanlı istek.
- Çekilemeyen alan adına 7 gün sonra bir kez daha deneme.
- A tüm uygun çiftlerde; B yalnız A=yes çiftlerinde. A=no → hayır,
  A=unclear → belirsiz. Evet için iki hakem, alıntı kontrolü ve kanıt geçidi.
- Çekici ve model ayrı süreçler, SQLite üzerinden sürdürülebilir kuyruk;
  donanım payı en fazla %80.
- 5.000 şirketlik JSONL parçaları; hayır/belirsiz ayrı dosyada.
- Meslek başına evetlerin rastgele %3'ü ve listelenmeyen 20 satır kör denetime.
- Saatlik DURUM.md ve günlük RAPOR.md güncellemesi.
- Bir mesleğin listeleme oranı %2 altı veya %40 üstüyse o meslek duraklatılır,
  BLOKAJ.md'ye yazılır, sonraki mesleğe geçilir. Bu oranın kaç kayıt sonrasında
  değerlendirileceği kaynak prompt'ta belirtilmemiş.
- Sayfa verisi 50 GB'ı aşınca gzip sıkıştırma.

## Başlatmadan önce tamamlanacaklar

1. Mevcut aday seçici hâlâ eski JSONL girdili, üç meslek/üç şehir pilotu.
   Yeni CSV.gz için şirket–meslek çiftlerini ayrı tutan içe aktarma ve kuyruk gerekli.
   `domains.meslek` tek meslek tutuyor; dosyayı doğrudan buraya yüklemek yeterli değil.
2. Mevcut judge iki modeli de tüm seçilen kayıtlarda çalıştırıyor.
   A=yes filtresi ve buna uygun uzlaşma akışı gerekli.
3. v5 ön koşulları ve yeni 96 kayıtlık ölçüm tamamlanmamış; kayıtlı yargılar v2/v3.
   Önceki %88,9 ölçümü v5 sonucu olarak kullanılamaz.
4. bge-m3 yerel model manifestlerinde yok; eşik kalibrasyonu yapılmamış.
5. Dört sayfa sınırı, zamanlı yeniden deneme, kaynak gözetimi, parçalı çıktı,
   kör örnekleme, dur koşulları ve otomatik durum raporu toplu koşuya bağlanmalı.

Öneri: önce bu eksikleri tamamlayıp 96 kayıtla ölçüm ve küçük bir uçtan uca deneme;
başarılıysa öncelik sırasıyla uzun koşu. Şu an 1,13 milyon çifti mevcut pilot
komutuyla başlatmak, VPS talimatlarını yerine getirmez.

Donanım kontrolü: RTX 4070 (12.282 MiB VRAM), yaklaşık 32 GB RAM,
12 mantıksal işlemci, yaklaşık 129 GB boş disk. Gemma 12B ve Qwen 14B/7B
model dosyaları mevcut. Bu gözlem, %80 sınırının koşu sırasında sağlandığı
veya toplam sürenin ölçüldüğü anlamına gelmez.
