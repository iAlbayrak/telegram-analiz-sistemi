# iAlbayrak Analiz Sistemi — V12

MEXC USDT perpetual piyasalarını **public piyasa verisi** ile tarayan, teknik fırsatları analiz eden ve uygun LONG/SHORT kurulumlarını Telegram kanalına gönderen alert-only sistemidir.

> **Önemli:** Bu sistem otomatik işlem açmaz/kapatmaz. MEXC API key veya hesap bilgisi kullanmaz. Yalnızca public market data + Telegram bot secret'ları kullanır.

## 1. V12'nin amacı

V12'nin ana hedefi yalnızca çok hareket etmiş coinleri yakalamak değildir.

Sistem özellikle:

- büyük hareket başlamadan önce oluşan erken kurulumu,
- hareket başladıktan sonra devam etme ihtimali bulunan momentum kurulumunu,
- aşırı hareket sonrasında gerçek bir dönüş kurulumu oluşmasını

birbirinden ayırır.

Böylece:

**“Coin %20 yükseldi → otomatik LONG”**

mantığı kullanılmaz.

Aynı şekilde:

**“Coin zaten %8–10 hareket etti → artık tamamen görmezden gel”**

mantığı da kullanılmaz.

Geçerli bir işlem planı yoksa Telegram'a sinyal gönderilmez.

---

## 2. Veri mimarisi

### Spot

Spot piyasa **fırsat keşfi** için kullanılır.

Spot hareketi tek başına işlem sinyali değildir.

Spotta dikkat çeken bir hareket görüldüğünde sistem ilgili MEXC perpetual sözleşmesini bulur ve asıl işlem planını vadeli piyasa verisiyle doğrular.

### Vadeli

LONG/SHORT yönü, giriş, stop ve hedefler vadeli mumlardan hesaplanır.

İşlem planında spot fiyat kullanılmaz.

---

## 3. Tarama evreni

Sistem aktif MEXC USDT perpetual sözleşmelerini keşfeder.

Ticker seviyesinde tüm aktif evren taranır.

OHLCV/mum analizi için likidite güvenlik tabanı uygulanır.

Varsayılan V12 yaklaşımı:

- normal vadeli detaylı analiz: **100.000 USDT+ 24s quote volume**
- spot keşfiyle eklenen vadeli aday: **100.000 USDT+ vadeli hacim**
- spot keşif tarafı: **500.000 USDT+ spot hacim**

Sabit `40 coin` limiti veya sabit `%6 hareket şartı kullanılmaz.

---

## 4. V12 fırsat aşamaları

### EARLY_OPPORTUNITY / PRE_BREAKOUT

Fiyat önemli seviyeye yaklaşırken:

- yön,
- trend,
- momentum,
- hacim,
- volatilite,
- EMA eğimi,
- BTC/ETH bağlamı,
- göreceli güç,
- kırılım seviyesine mesafe

birlikte değerlendirilir.

Amaç hareketi mümkün olduğunca erken yakalamaktır.

### ACTIVE_MOMENTUM / BREAKOUT_STARTED

Hareket başlamıştır.

Sistem artık yalnızca “çok yükseldi” diye adayı silmez.

Şunları kontrol eder:

- momentum devam ediyor mu?
- hacim destekliyor mu?
- volatilite genişliyor mu?
- kırılım hâlâ sağlıklı mı?
- fiyat giriş bölgesinden fazla uzaklaştı mı?
- stop/TP geometrisi hâlâ mantıklı mı?

Fiyat fazla uzaklaşmışsa sinyal gönderilmez; adayın tamamen yok sayılması da zorunlu değildir.

### REVERSAL / EXHAUSTION

Çok sert hareket etmiş coinlerde devam mı, tükenme mi olduğu ayrıca değerlendirilir.

Büyük hareket tek başına dönüş sinyali değildir.

Dönüş için yön ve yapı teyidi gerekir.

---

## 5. Bütünsel skor

V12 tek bir **BÜTÜNSEL SKOR** kullanır.

Skor:

- yön/trend,
- momentum,
- hacim,
- kırılım teyidi,
- giriş kalitesi,
- risk/ödül geometrisi,
- hareket potansiyeli,
- hazırlık/readiness,
- LONG/SHORT yön netliği

gibi bileşenlerin birlikte değerlendirilmesinden oluşur.

Skor **kazanma olasılığı değildir**.

Örneğin `87/100`, işlemin %87 kazanacağı anlamına gelmez.

Gerçek performans `signal_outcomes.json` üzerinden ileriye dönük takip edilmelidir.

---

## 6. Market-cap alarm katmanı

Market-cap sırası yön tahmini değildir.

Yalnızca alarm kapsamı için ek bir güvenlik/kalite katmanıdır.

| Market-cap | Minimum bütünsel skor |
|---|---:|
| #1–500 | 80+ |
| #501–800 | 85+ |
| #801+ | 90+ |
| Bilinmiyor | 90+ |

Market-cap verisi için kaynak sırası ve önbellek kullanılır. Kaynak geçici olarak çalışmazsa mevcut cache kullanılabilir.

---

## 7. Risk ve plan geometrisi

Telegram'a gönderilmeden önce plan tekrar doğrulanır.

### LONG

`Stop < Entry < Target`

### SHORT

`Target < Entry < Stop`

Ayrıca:

- stop mesafesi aşırı geniş olmamalı,
- minimum R:R şartı sağlanmalı,
- fiyat giriş bölgesinden kaçmış olmamalı,
- kırılım başarısız olmuşsa alarm gönderilmemeli.

Varsayılan minimum doğrulanmış R:R:

**1.8**

Varsayılan maksimum stop mesafesi:

**%8**

Bunlar güvenlik filtresidir; garanti değildir.

---

## 8. Telegram bildirimi

Telegram kartının ana yapısı korunur.

Kartta:

- coin
- LONG / SHORT
- canlı fiyat
- BTC 1h / 4h
- ETH 1h / 4h
- bütünsel skor
- giriş bölgesi
- ideal giriş
- stop
- TP1
- TP2
- TP3
- destek/direnç
- gerekli teknik bilgiler

yer alır.

Kullanıcıya gereksiz açıklama metni veya market-cap sıralama tablosu eklenmez.

---

## 9. BTC / ETH bağlamı

BTC ve ETH 1h/4h rejimi analizde kullanılır.

Ancak altcoin:

- kendi trendinde güçlü ise,
- BTC/ETH'den pozitif göreceli güç gösteriyorsa,
- kendi vadeli yapısı ve giriş geometrisi uygunsa

sırf BTC/ETH aynı yönde değil diye otomatik olarak elenmez.

BTC/ETH bağlamı **hard veto** değildir.

---

## 10. Mum verisi güvenliği

Yetersiz mum geçmişi olan coin için veri uydurulmaz.

Sistem:

1. doğrudan istenen timeframe'i dener,
2. gerektiğinde daha düşük timeframe'den resampling yapar,
3. yine yeterli veri yoksa coin'i o turda puanlamaz.

Bu durum tanı logunda `YETERSIZ_MUM` olarak görülür.

---

## 11. Tekrar alarm kontrolü

`alert_state.json` gönderilmiş kurulumları takip eder.

Aynı coin aynı yönde sürekli spamlanmaz.

Daha güçlü bir skor/tier veya daha ileri bir setup aşaması oluştuğunda yeniden alarm verilmesine izin verilebilir.

Telegram gönderimi başarısız olursa sinyalin gönderilmiş kabul edilmemesi için durum korunur.

---

## 12. Sinyal sonuç takibi

`signal_outcomes.json` gönderilmiş sinyalleri takip eder.

Kapalı 15 dakikalık mumlar kullanılarak:

- TP1
- TP2
- TP3
- STOP
- aynı mumda stop + hedef
- EXPIRED

gibi sonuçlar kaydedilir.

Bu kayıtlar gelecekte skorlamanın gerçek performansını ölçmek için kullanılabilir.

> Geçmişteki birkaç başarılı/başarısız sinyal tek başına stratejinin istatistiksel olarak kanıtlandığı anlamına gelmez. Daha anlamlı değerlendirme için yeterli sayıda forward outcome gerekir.

---

# GitHub kurulumu

Repository:

`iAlbayrak/telegram-analiz-sistemi`

Gerekli temel dosyalar:

```text
.github/
└── workflows/
    └── scan.yml

altcoin_alert_scanner.py
requirements.txt

alert_state.json
market_cap_cache.json
scan_diagnostic.log
signal_outcomes.json
```

## GitHub Secrets

Repository:

**Settings → Secrets and variables → Actions**

Aşağıdaki iki secret bulunmalıdır:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Secret değerleri kodun içine yazılmaz.

---

# GitHub Actions

Workflow:

```text
.github/workflows/scan.yml
```

Varsayılan çalışma planı:

```text
Her 15 dakika
02, 17, 32, 47
```

UTC cron kullanılır.

Ayrıca GitHub Actions içinden:

**Actions → Altcoin Alert Scanner → Run workflow**

ile manuel çalıştırılabilir.

---

# Kurulum sırasında korunacak dosyalar

V12 yüklenirken mevcut aşağıdaki dosyalar silinmemelidir:

```text
alert_state.json
market_cap_cache.json
scan_diagnostic.log
signal_outcomes.json
```

Bunlar sistemin çalışma durumu, tanı geçmişi, market-cap cache'i ve sonuç geçmişidir.

`README_v10_1.md` gibi eski sürüm dokümanları V12 kurulumundan sonra kaldırılabilir.

---

# İlk test

Kurulumdan sonra:

1. GitHub → Actions
2. `Altcoin Alert Scanner`
3. `Run workflow`
4. Çalışmanın tamamlanmasını bekle
5. `scan_diagnostic.log` dosyasını kontrol et

Logda özellikle şu başlıklar izlenmelidir:

```text
BTC4h=
BTC1h=
ETH4h=
ETH1h=

PERP
SPOT
FIRSAT_KONTROL
DATA_SKIPS
ERRORS
```

İlk çalışmada Telegram'a sinyal gelmemesi tek başına hata değildir.

Önemli olan workflow'un hatasız tamamlanması ve analiz/log çıktısının doğru olmasıdır.

---

# V12'nin temel prensibi

Sistem şu üç hatadan kaçınmayı hedefler:

### 1. Geç kalmış hareketi kovalamak

Büyük hareket olmuş diye fiyatın peşinden LONG/SHORT açılmaz.

### 2. Erken fırsatı tamamen kaçırmak

Coin henüz büyük hareket yapmadı diye güçlü oluşum göz ardı edilmez.

### 3. Büyük hareketi otomatik sinyal sanmak

%10, %20 veya %30 hareket tek başına işlem sinyali değildir.

Önce yapı, yön, momentum, hacim, giriş konumu ve risk/ödül doğrulanır.

---

## Uyarı

Bu sistem finansal tavsiye veya kâr garantisi sağlamaz.

Skor, kazanma olasılığı değildir.

Kripto perpetual işlemler yüksek volatilite ve kaldıraç riski içerir.

Sistem yalnızca teknik analiz tabanlı erken uyarı/işlem planı üretir.
