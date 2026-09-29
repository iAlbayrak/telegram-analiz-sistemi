Altcoin Alert Scanner V4
Bu proje yalnızca herkese açık MEXC spot/vadeli piyasa verisini okur ve Telegram'a analiz/izleme uyarıları yollar. Borsa hesabına bağlanmaz ve emir göndermez.
V4'teki ana değişiklikler
MEXC USDT perpetual taraması ve mevcut market-cap eşik sistemi korunur.
Ayrı spot erken hareket radarı eklendi: likit spot çiftlerinden hacim ve hareket adayları seçilir; kapanmış 15m mum, 1h momentum ve göreceli hacim kontrol edilir.
Spot erken uyarıları açıkça `izleme uyarısı` olarak etiketlenir; tek başına LONG/SHORT işlem sinyali değildir. Aynı sembol/yön için 6 saatlik varsayılan tekrar aralığı vardır.
Doğrulanmış vadeli setup'lar için minimum R:R 1.8, yön farkı 8 ve giriş kalitesi 55 kontrolü eklenir.
Hedef eski destek/direnç seviyesinin yanlış tarafında kalamaz. Yapısal hedef yoksa ATR/R projeksiyonu açıkça etiketlenir ve minimum R:R kontrolü uygulanır.
`scan\\\\\\\_diagnostic.log` en iyi vadeli coin sembollerini, skorları, RR'yi, alarm eşiğini, hedef yöntemini ve spot radar adaylarını yazar.
Telegram gönderimi başarısızsa yeni uyarı durumu gönderilmiş gibi kaydedilmez; sonraki taramada tekrar denenebilir.
Workflow, durum/log/market-cap önbelleğini kaydeder ve push öncesinde remote değişiklikleri rebase ederek önceki push-rejection sorununu azaltır.
GitHub Secrets
Repository Settings → Secrets and variables → Actions → Repository secrets:
`TELEGRAM\\\\\\\_BOT\\\\\\\_TOKEN`
`TELEGRAM\\\\\\\_CHAT\\\\\\\_ID`
Gerekli dosyalar
`altcoin\\\\\\\_alert\\\\\\\_scanner.py`
`requirements.txt` (ccxt, pandas, numpy, requests)
`.github/workflows/scan.yml`
`alert\\\\\\\_state.json`, `scan\\\\\\\_diagnostic.log` ve `market\\\\\\\_cap\\\\\\\_cache.json` çalışma sırasında oluşturulur/güncellenir.
