# Telegram Analiz Sistemi — V13

V13, mevcut V12.1 scanner'ın üzerine hazırlanmıştır. Bu paket yalnızca scanner kodunu günceller; Telegram secrets, GitHub Actions workflow, `alert_state.json`, `scan_diagnostic.log`, `market_cap_cache.json` ve `signal_outcomes.json` korunmalıdır.

## V13'te yapılan ana değişiklikler

1. **Bütünsel skor yeniden dengelendi**
   - Yön: **%30**
   - Giriş kalitesi: **%25**
   - Hareket potansiyeli: **%30**
   - Hazır oluş / tetik yakınlığı: **%15**
   - Skor, kazanma olasılığı değildir.
   - TP yüzdesi ile skor aynı şey değildir. 1–3% hedefli normal setup'lar devam edebilir; fakat gerçek hareket potansiyeli yüksek adaylar daha yüksek skor alır.

2. **Yüksek potansiyel keşfi güçlendirildi**
   - 15m ivme/acceleration
   - hacim genişlemesi
   - ATR/volatilite genişlemesi
   - BTC/ETH'ye göre relative strength
   - devam baskısı
   - kalan yapısal alan
   - aşırı uzama/exhaustion cezası
   birlikte değerlendirilir.

3. **Breakout kovalamaca azaltıldı**
   - PRE_BREAKOUT yaklaşım korunur.
   - Canlı fiyat tetik seviyesinden fazla uzaksa sinyal gönderilmez.
   - Breakout sonrası uygun durumda **BREAKOUT_RETEST** / pullback teyidi aranır.

4. **Stop sistemi geliştirildi**
   - Tek STOP-LOSS fiyatı korunur.
   - Buna ek olarak destek/direnç + ATR tabanlı **STOP ZONU** hesaplanır.
   - Stop mesafesi yine maksimum %8 güvenlik sınırına tabidir.

5. **Telegram kartı korunarak güçlendirildi**
   - LONG / SHORT
   - BTC 1h / 4h
   - ETH 1h / 4h
   - tek bütünsel skor
   - ideal giriş + giriş bölgesi
   - destek / direnç
   - STOP-LOSS
   - STOP ZONU
   - TP1 / TP2 / TP3
   - retest / seviye farkı

6. **Büyük hareket keşfi korunup güçlendirildi**
   - Spot radar ham watch spamı üretmez.
   - Spotta büyük/erken hareket eden ve yeterli futures likiditesi olan eşleşmeler futures doğrulamasına taşınır.
   - Futures tarafında aktif USDT perpetual evreni korunur.

7. **Market-cap kapıları**
   - #1–500: skor 80+
   - #501–800: skor 85+
   - #801+ veya bilinmeyen: skor 90+

8. **Tekrar alarm mantığı**
   - Aynı setup başarıyla gönderildikten sonra aynı skor/fazda spamlanmaz.
   - Setup eligibility dışına çıkıp tekrar eligible olduğunda yeniden bildirilebilir.
   - Skor yaklaşık 4 puan veya daha fazla yükselirse ya da faz yükselirse yeniden bildirim mümkün.
   - Canlı fiyat/geometry nedeniyle gönderilemeyen setup'ın state'i baskılanmaz; sonraki taramada tekrar değerlendirilebilir.

## Zamanlama

Mevcut GitHub Actions workflow'un **7/24, 15 dakikada bir** çalışmaya devam etmesi hedeflenmiştir. Workflow dosyasını bu paketle değiştirmek zorunda değilsiniz.

## GitHub'a kurulum

1. Repo: `iAlbayrak/telegram-analiz-sistemi`
2. `altcoin_alert_scanner.py` dosyasını V13 dosyasıyla değiştirin.
3. Mevcut `requirements.txt` ve `.github/workflows/scan.yml` dosyalarına dokunmayın.
4. `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` secrets aynen kalır.
5. `alert_state.json`, `scan_diagnostic.log`, `market_cap_cache.json`, `signal_outcomes.json` varsa silmeyin.
6. Manuel `workflow_dispatch` ile bir test çalıştırın.

## Opsiyonel environment ayarları

Mevcut değerler korunur. V13 ek ayarları:

- `RETEST_MAX_ATR=0.65`
- `STOP_ZONE_ATR_NEAR=0.08`
- `STOP_ZONE_ATR_FAR=0.35`
- `ALERT_REARM_COOLDOWN_HOURS=1.0`

Bunları GitHub Secrets içine koymak zorunda değilsiniz; varsayılanlar kod içinde çalışır.

## Güvenlik

Bu scanner MEXC hesabına emir göndermez. Yalnızca public market verisi okuyup Telegram'a analiz bildirimi gönderir. MEXC API key gerekmez.
