# Altcoin Alert Scanner (v2)

BTC/ETH öncülüğünde piyasa bağlamı + tüm likit MEXC USDT vadeli
altcoin'lerin taranıp skorlandığı, sadece Telegram'a bildirim gönderen
bağımsız bir sistem. Hiçbir emir açmaz, hiçbir API anahtarı gerektirmez.

**v2 notu:** Skorlama mantığındaki iki iç çelişki giderildi (kırılım
sinyali artık aralıktaki konumla çakışmıyor; hiçbir bileşen iki kez
sayılmıyor) ve gürültüyü azaltmak için 80-85 "ORTA" bandı kaldırıldı --
artık sadece 85-90 (GÜÇLÜ) ve 90+ (ÇOK YÜKSEK / ELİT) bildiriliyor.
Ayrıntılar `altcoin_alert_scanner.py`'nin başındaki yorum bloğunda.

**v2.1 notu:** BTC ve ETH artık sadece piyasa bağlamı değil, kendileri
de birer LONG/SHORT aday olarak taranıp skorlanıyor. Döngüsel şişmeyi
önlemek için BTC'nin hizalık bonusu sadece ETH'ye göre, ETH'ninki
sadece BTC'ye göre hesaplanıyor (bir varlık kendi yönüyle "uyumlu"
sayılıp yapay puan kazanamıyor). Ayrıca her bildirimin en üstünde artık
tek satırlık net bir "Genel yön: YUKARI/AŞAĞI/NÖTR" özeti var.

**v2.2 notu (son gözden geçirme):** İki ek düzeltme yapıldı:
1. `move_potential` bileşeninin matematiksel tavanı aslında 90'dı (100
   değil) -- yani ELİT kapısının "≥85" şartı görünenden çok daha katıydı
   (gerçek tavanın %94'ü). Bileşen artık gerçek 0-100 ölçeğine
   normalize ediliyor, eşik de aynı orantıyı koruyacak şekilde (94)
   güncellendi -- ne gevşetildi ne sıkılaştırıldı, sadece dürüstleşti.
2. Bildirim durumu (`alert_state.json`) artık bulut cache yerine
   doğrudan repoya commit ediliyor -- hem şeffaflık için hem de
   GitHub'ın "60 gün hareketsiz repoda zamanlanmış görevleri otomatik
   durdurma" kuralına takılmamak için (her çalışma kendiliğinden bir
   push ürettiğinden repo hiç "hareketsiz" sayılmıyor).

Küçük, düşük etkili bir not: `entry_quality`'nin gerçek tavanı da
100 değil ~93 -- ama ELİT eşiği (80) bu tavanın zaten rahat ulaşılabilir
bir yüzdesinde (%86) olduğundan pratikte gözle görülür bir çarpıtma
yaratmıyor, bu yüzden değiştirmedim. Şeffaflık için not düşüyorum.

## Kurulum (tek seferlik, ~5 dakika)

1. **GitHub hesabı** yoksa açın (ücretsiz) — github.com
2. Bu klasördeki dosyalarla **yeni bir repo** oluşturun (public seçin —
   private'a göre çalışma dakikası limitiniz sınırsız olur, ve bu
   repoda hiçbir sır/anahtar yok, sadece halka açık piyasa verisi
   okuyan bir script var).
3. **Telegram bot oluşturun:**
   - Telegram'da @BotFather'a yazıp `/newbot` deyin → size bir **token** verir
   - @userinfobot'a yazıp kendi **chat ID**'nizi öğrenin
4. Repo → **Settings → Secrets and variables → Actions → New repository secret**
   ile iki secret ekleyin:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Hepsi bu. `.github/workflows/scan.yml` dosyası GitHub'a otomatik
   olarak saatte bir (05:00-21:00 UTC = TR 08:00-24:00) çalışır ve
   Telegram'a bildirim atar.

## Hemen test etmek isterseniz

Repo → **Actions** sekmesi → "Altcoin Alert Scanner" → **Run workflow**
butonuyla bir sonraki saati beklemeden elle tetikleyebilirsiniz.

## Ayarları değiştirmek

- **Aktif saat aralığı:** `.github/workflows/scan.yml` içindeki `cron`
  satırı (UTC bazlı, TR saatinden 3 saat geridir).
- **Alarm eşiği (varsayılan 85):** aynı dosyadaki `ALERT_MIN_SCORE`.
- **Likidite eşiği (varsayılan 1M USDT):** `MIN_24H_VOLUME`.

## Bu sistem ne yapmaz

- Emir açmaz, hesabınıza bağlanmaz, API anahtarı istemez.
- Sadece okur ve bildirir — karar ve işlem tamamen sizde.
