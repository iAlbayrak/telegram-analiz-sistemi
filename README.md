Altcoin Alert Scanner V9 — tek bütünsel skor
Bu sistem yalnızca MEXC'nin herkese açık piyasa verisini okur ve Telegram'a analiz gönderir. Otomatik emir açmaz ve MEXC API anahtarı istemez.
V9'da yapılan son düzenlemeler
Telegram mesajında ayrı ayrı potansiyel, hazırlık, giriş kalitesi ve R:R puanları gösterilmez. Bunlar yön/trend, mum ve hacim, göreceli güç, giriş konumu, hedef alanı ve stop-hedef geometrisiyle birlikte tek bir BÜTÜNSEL SKOR içinde değerlendirilir.
Market-cap puan eşikleri korunur: 1–200 için 80+, 201–500 için 85+, 501+ veya bilinmeyen için 90+.
Güvenlik için işlem planı yine de geçerli erken/yeni kırılım yapısı, yeterli likidite ve makul stop-hedef geometrisi koşullarını geçmelidir. Bunlar kullanıcıya ayrı skorlar olarak sunulmaz.
Tüm aktif MEXC USDT perpetual çiftleri keşfedilir. 24 saatlik vadeli hacmi en az 250.000 USDT olan tüm benzersiz çiftler derin analize girer. Tarama sayıları aynı benzersiz sembol listesinden hesaplanır.
Kısa mum geçmişinde alt zaman diliminden veri birleştirilirken artık daha fazla kaynak mum istenir; özellikle 5 dakikadan 15 dakikaya yedekleme daha yeterli geçmiş oluşturmayı dener. Yeterli veri yine yoksa sistem hatayı kaydeder ve o sembolü atlar.
BTC/ETH 4 saatlik ve 1 saatlik rejimi, altcoinin göreceli gücü, erken kırılım/yeni kırılım, destek/direnç, stop ve TP1/TP2/TP3 korunur.
Market-cap için CoinPaprika → CoinCap → CoinGecko yedek zinciri ve kalıcı önbellek korunur.
Genel spot izleme mesajı spam olarak gönderilmez; Telegram yalnızca eşiği geçen vadeli planları alır.
İş akışı 15 dakikada bir çalışır; GitHub Actions çalışma zamanı ve state/log/cache kayıtları korunur.
Puanlama
Market-cap 1–200: 80+
Market-cap 201–500: 85+
Market-cap 501+ veya bilinmiyor: 90+
Vadeli 24 saatlik hacim: en az 250.000 USDT
İç risk kontrolü: hedef-stop geometrisi için en az 1.8 oran; bu metrik Telegram'da ayrı puan olarak gösterilmez.
Skor bir olasılık yüzdesi veya kazanma garantisi değildir. Gerçek performans ancak sinyallerin sonraki fiyat hareketleriyle sistematik biçimde karşılaştırılmasıyla değerlendirilebilir.
GitHub'a yükleme
Depodaki şu üç dosyanın içeriğini paketteki karşılıklarıyla değiştirin:
`altcoin_alert_scanner.py`
`requirements.txt`
`.github/workflows/scan.yml`
`TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` secrets değerlerini değiştirmeyin. `alert_state.json`, `scan_diagnostic.log` veya varsa `market_cap_cache.json` dosyalarını silmeyin.
İlk çalıştırmada kontrol
GitHub → Actions → Altcoin Alert Scanner → en son run → `Tarayıcıyı çalıştır`:
`Derin mum analizi: N/N benzersiz coin` satırındaki sayımlar birbiriyle tutarlı olmalı.
Market-cap kaynağı ve `VERİ/ANALİZ HATASI` satırları kontrol edilmeli.
Telegram'a giden planda tek bir bütünsel skor ile giriş koşulu, stop, hedefler ve destek/direnç görünmeli.
Bildirim gelmemesi tek başına hata değildir; o turda yeni ve bütün koşulları geçen bir plan olmayabilir.
Canlı MEXC/Telegram verisi GitHub Actions üzerinde doğrulanmalıdır. Bu sürüm otomatik işlem yapmaz.
