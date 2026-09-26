Altcoin Alert Scanner (v2)
BTC/ETH öncülüğünde piyasa bağlamı + tüm likit MEXC USDT vadeli
altcoin'lerin taranıp skorlandığı, sadece Telegram'a bildirim gönderen
bağımsız bir sistem. Hiçbir emir açmaz, hiçbir API anahtarı gerektirmez.
v2 notu: Skorlama mantığındaki iki iç çelişki giderildi (kırılım
sinyali artık aralıktaki konumla çakışmıyor; hiçbir bileşen iki kez
sayılmıyor) ve gürültüyü azaltmak için 80-85 "ORTA" bandı kaldırıldı --
artık sadece 85-90 (GÜÇLÜ) ve 90+ (ÇOK YÜKSEK / ELİT) bildiriliyor.
Ayrıntılar `altcoin_alert_scanner.py`'nin başındaki yorum bloğunda.
v2.1 notu: BTC ve ETH artık sadece piyasa bağlamı değil, kendileri
de birer LONG/SHORT aday olarak taranıp skorlanıyor. Döngüsel şişmeyi
önlemek için BTC'nin hizalık bonusu sadece ETH'ye göre, ETH'ninki
sadece BTC'ye göre hesaplanıyor (bir varlık kendi yönüyle "uyumlu"
sayılıp yapay puan kazanamıyor). Ayrıca her bildirimin en üstünde artık
tek satırlık net bir "Genel yön: YUKARI/AŞAĞI/NÖTR" özeti var.
v2.2 notu (son gözden geçirme): İki ek düzeltme yapıldı:
`move_potential` bileşeninin matematiksel tavanı aslında 90'dı (100
değil) -- yani ELİT kapısının "≥85" şartı görünenden çok daha katıydı
(gerçek tavanın %94'ü). Bileşen artık gerçek 0-100 ölçeğine
normalize ediliyor, eşik de aynı orantıyı koruyacak şekilde (94)
güncellendi -- ne gevşetildi ne sıkılaştırıldı, sadece dürüstleşti.
Bildirim durumu (`alert_state.json`) artık bulut cache yerine
doğrudan repoya commit ediliyor -- hem şeffaflık için hem de
GitHub'ın "60 gün hareketsiz repoda zamanlanmış görevleri otomatik
durdurma" kuralına takılmamak için (her çalışma kendiliğinden bir
push ürettiğinden repo hiç "hareketsiz" sayılmıyor).
Küçük, düşük etkili bir not: `entry_quality`'nin gerçek tavanı da
100 değil ~93 -- ama ELİT eşiği (80) bu tavanın zaten rahat ulaşılabilir
bir yüzdesinde (%86) olduğundan pratikte gözle görülür bir çarpıtma
yaratmıyor, bu yüzden değiştirmedim. Şeffaflık için not düşüyorum.
v2.3 notu:
Bant yeniden ayarlandı: 80-90 → GİRİLEBİLİR, 90+ → ÇOK GÜÇLÜ,
içindeki en sıkı alt küme ELİT. `ALERT_MIN_SCORE` varsayılanı 80.
Sistem artık 7/24 çalışıyor, aktif saat kısıtlaması kaldırıldı --
kripto hiç kapanmadığı için gece penceresini kapatmak fırsat kaçırmak
demekti. Gece rahatsız olmak istemiyorsanız Telegram sohbetini kendiniz
sessize alın; tarama yine de birikir, sabah hepsini görürsünüz.
Tarama sıklığı 15 dakikaya çekildi (stratejinin en ince zaman
dilimiyle -- 15dk mum -- birebir örtüşsün diye). Bildirim sıklığı
değişmedi: dedup mantığı aynı, sadece gerçekten yeni/yükselen bir
setup çıktığında bildirim geliyor.
Tanı logu (`scan_diagnostic.log`) eklendi: her turun en iyi 10
adayı, eşiği geçmemiş olsalar bile, skorlarıyla birlikte kaydediliyor.
"Çok mu kısıtladık?" sorusunun cevabı artık tahmin değil, bu dosyada.
Geniş stop eklendi: standart stop'un yanında, ATR bazlı ek bir
tampon mesafesiyle hesaplanan ikinci bir stop seviyesi de gösteriliyor
-- ani bir fitilin (stop-hunt) sizi standart stoptan çıkarttıktan hemen
sonra fiyatın asıl yönüne dönmesi riskine karşı. Puanlamayı, RR'yi ya
da TP'leri etkilemiyor, sadece ek bilgi.
Bir taramanın 15 dakikadan uzun sürme ihtimaline karşı workflow'a
eşzamanlılık kilidi eklendi -- üst üste binen iki tarama artık aynı
anda değil sırayla çalışıyor, durum dosyasında çakışma olmuyor.
Kurulum (tek seferlik, ~5 dakika)
GitHub hesabı yoksa açın (ücretsiz) — github.com
Bu klasördeki dosyalarla yeni bir repo oluşturun (public seçin —
private'a göre çalışma dakikası limitiniz sınırsız olur, ve bu
repoda hiçbir sır/anahtar yok, sadece halka açık piyasa verisi
okuyan bir script var).
Telegram bot oluşturun:
Telegram'da @BotFather'a yazıp `/newbot` deyin → size bir token verir
@userinfobot'a yazıp kendi chat ID'nizi öğrenin
Repo → Settings → Secrets and variables → Actions → New repository secret
ile iki secret ekleyin:
`TELEGRAM_BOT_TOKEN`
`TELEGRAM_CHAT_ID`
Hepsi bu. `.github/workflows/scan.yml` dosyası GitHub'a otomatik
olarak saatte bir (05:00-21:00 UTC = TR 08:00-24:00) çalışır ve
Telegram'a bildirim atar.
Hemen test etmek isterseniz
Repo → Actions sekmesi → "Altcoin Alert Scanner" → Run workflow
butonuyla bir sonraki saati beklemeden elle tetikleyebilirsiniz.
Ayarları değiştirmek
Tarama sıklığı (varsayılan 15 dk, 7/24): `.github/workflows/scan.yml`
içindeki `cron` satırı.
Alarm eşiği (varsayılan 80): aynı dosyadaki `ALERT_MIN_SCORE`.
Likidite eşiği (varsayılan 1M USDT): `MIN_24H_VOLUME`.
Bu sistem ne yapmaz
Emir açmaz, hesabınıza bağlanmaz, API anahtarı istemez.
Sadece okur ve bildirir — karar ve işlem tamamen sizde.

v2.4 notu (bildirim güvenilirliği):
Telegram gönderimi başarısız olursa ilgili setup `bildirildi` olarak işaretlenmez; sonraki taramada yeniden denenir.
Aynı setup 80+ bandında kalırken spam yapılmaz; skor 80'in altına düştüğünde state'den çıkar ve daha sonra tekrar 80+ olduğunda yeniden bildirim adayı olur.
Eski `alert_state.json` kayıtlarında `notified` alanı yoksa güvenli tarafta kalınır ve setup daha önce bildirilmemiş kabul edilir; böylece eski sürümde kaçırılmış 80+ adaylar yeniden değerlendirilebilir.
Telegram Chat ID hatalı/eski ise, yalnızca `chat not found` durumunda botun daha önce gördüğü ve `TELEGRAM_CHANNEL_TITLE` ile eşleşen kanal postundan ID otomatik keşfedilip bir kez yeniden denenir.
Geçici Telegram teşhis adımları workflow'dan kaldırılmıştır; normal workflow yalnızca tarama ve state/log kaydını çalıştırır.
`scan_diagnostic.log` artık üst adaylarda skor, RR, tier ve bildirim uygunluğunu gösterir.
Bildirim mesajında BTC/ETH piyasa bağlamı, yön, destek, direnç, standart stop, ATR tamponlu geniş stop ve TP1/TP2/TP3 birlikte yer alır.
