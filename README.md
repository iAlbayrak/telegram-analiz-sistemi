MEXC TELEGRAM ALTCOIN ALERT SCANNER — V13.6
================================================

Bu paket sadece analiz ve Telegram uyarısı üretir; otomatik emir açmaz.
GitHub Actions'taki mevcut workflow dosyanızı ( .github/workflows/scan.yml ),
Secrets değerlerini ve mevcut durum dosyalarını değiştirmeyin.

GÜNCELLEME
1. ZIP içindeki altcoin_alert_scanner.py dosyasını GitHub deposundaki aynı isimli dosyayla değiştirin.
2. README dosyasını isterseniz inceleyin; workflow, secrets ve state/log dosyalarını silmeyin.
3. GitHub Actions > Altcoin Alert Scanner > Run workflow ile bir kez elle çalıştırın.
4. Logda V13.6 başlığını ve hedef filtresi açıklamasını kontrol edin.

YENİ MANTIK
- Teknik skor hâlâ yön, giriş kalitesi, hazırlık ve hareket potansiyelini birlikte ölçer.
- Ayrı “hedef güven skoru” 0–100 arası sezgisel bir teknik puandır; kazanma olasılığı yüzdesi değildir.
  Örneğin 90/100, geçmişte %90 hedef tutmuş demek değildir. Bunu söylemek için yeterli örneklemle
  ileriye dönük sonuç kalibrasyonu gerekir.
- TP3 hedefinin giriş fiyatına göre gerçek fiyat mesafesi ayrıca hesaplanır. Kaldıraç bu yüzdeye dahil değildir.
- İşlem uyarısı için mevcut sıralama/teknik skor, setup doğrulaması, likidite, R:R ve hedef kapıları birlikte geçmelidir.
- Market-cap rank'a göre minimum TP3 fiyat mesafesi:
  rank 1–300: mevcut hedef mesafesi yeterli (0% ek taban)
  rank 301–600: en az 3%
  rank 601–1000: en az 5%
  rank 1001–2000: en az 10%
  rank 2001+: en az 15%
  rank bilinmiyorsa: en az 15% (temkinli)
- Hedef güven skoru için varsayılan minimum 58/100.
- LONG ve SHORT aynı filtrelerden geçer.
- Bu yüzdeler ilk seçicilik ayarlarıdır; kanıtlanmış başarı oranı değildir. Sonuç dosyası ve sinyal
  sonuçları izlenerek ileride kalibre edilmelidir.

AYARLAR (isteğe bağlı GitHub Variables / environment)
MIN_TARGET_MOVE_RANK_1_300=0
MIN_TARGET_MOVE_RANK_301_600=3
MIN_TARGET_MOVE_RANK_601_1000=5
MIN_TARGET_MOVE_RANK_1001_2000=10
MIN_TARGET_MOVE_RANK_2001_PLUS=15
MIN_TARGET_MOVE_UNKNOWN=15
MIN_TARGET_CONFIDENCE=58

ÖNEMLİ
- Yüksek skor daha iyi teknik yapı ve hedef alanına ilişkin daha güçlü sinyal demektir; garanti değildir.
- Düşük market-cap coinlerde %15 TP3 şartı, sinyal sayısını ciddi azaltabilir. Bu kasıtlı kalite filtresidir.
- Hedef yüzdesi dayandığı teknik hedef/direnç-destek yapısını aşacak şekilde zorla büyütülmez.
- Sadece kaynak kodunu güncelleyin; scan.yml, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
  alert_state.json, scan_diagnostic.log, market_cap_cache.json ve signal_outcomes.json dosyalarını koruyun.
