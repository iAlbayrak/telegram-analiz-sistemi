# Altcoin Alert Scanner — V10.1 spot keşif düzeltmesi

Telegram sinyal kartının tasarımı değiştirilmedi.

## V10.1 düzeltmesi

- Spot radarı artık varsayılan olarak sabit 40 coin limiti kullanmaz. Spot 24 saatlik hacmi en az 500.000 USDT olan aktif USDT spot çiftlerinin 15 dakikalık mumlarını kontrol eder. İsteğe bağlı `SPOT_RADAR_MAX_CANDIDATES` ayarı 0 bırakılırsa limit yoktur; pozitif bir değer verilirse bilinçli bir çalışma sınırı uygulanır.
- Sabit `%6` 24 saatlik hareket koşulu kaldırıldı. Büyük hareketler için göreli hareket sıralaması kullanılır (varsayılan olarak spot havuzunun en hareketli %20'si); ayrıca kısa vadeli momentum, hacim artışı ve yerel yüksek/düşük seviyelere yakınlık fırsat keşfine katkı sağlar.
- Spot radarı ayrı bir Telegram erken-uyarı mesajı göndermez. Spot sadece keşif katmanıdır; eşleşen vadeli sözleşme, vadeli mumlar ve fiyatlarla ayrı değerlendirilir. Sinyal kartı ve bildirim tasarımı korunur.
- Spot keşfiyle vadeli analize eklenen adaylar için güvenlik tabanı korunur: spot 24s hacim >= 500.000 USDT ve vadeli 24s hacim >= 100.000 USDT. Normal vadeli tarama eşiği >= 250.000 USDT olarak kalır.
- `FIRSAT_KONTROL` / `DATA_SKIPS` tanıları GitHub Actions logu ve `scan_diagnostic.log` içindir; Telegram'a ayrı hata/diagnostic mesajı gönderilmez.
- Market-cap eşikleri aynı: global market-cap sırası 1–200 => 80+, 201–500 => 85+, 501+ veya bilinmiyor => 90+. Piyasa değeri genel kripto piyasasından alınır; vadeli işlem hacmiyle hesaplanmaz. Veri sağlayıcı önceliği CoinPaprika, CoinCap, ardından CoinGecko'dur; önceki cache korunur.

## GitHub'a yükleme

Mevcut depodaki `altcoin_alert_scanner.py` ve `requirements.txt` dosyalarını bu ZIP'teki dosyalarla değiştirin. Workflow, GitHub Secrets ve `alert_state.json`, `scan_diagnostic.log`, `market_cap_cache.json`, `signal_outcomes.json` dosyalarını koruyun.

## Sınırlar

Python derleme kontrolü çevrimdışı yapıldı. Canlı MEXC/Telegram testi yapılmadı. Daha geniş spot mum taraması çalışma süresini artırabilir. Daha çok adayın analiz edilmesi, daha çok sinyal veya kârlılık garantisi değildir; gerçek sonuçlar izlenmelidir.
