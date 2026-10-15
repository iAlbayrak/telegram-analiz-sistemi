# Scout v2 — Volatilite Genişlemesi Kırılım Tarayıcısı

MEXC USDT vadeli sözleşmelerinde, **kapanmış 1 saatlik mumlarla** hacimli kırılımları bulur ve Telegram'a
giriş / stop / TP1 / iz süren stop planıyla bildirir. Emir göndermez, API anahtarı istemez. İşlemi siz açarsınız.

## Neden yeniden yazıldı

Eski sistemin gerçek sinyal sonuçları (176 sonuçlanmış sinyal, 1–12 Ekim):

| Bulgu | Değer |
|---|---|
| Kazanma oranı / brüt beklenti | %46 / +0.005R (yani başabaş) |
| ~%0.12 gidiş-dönüş maliyetle net beklenti | **−0.09R / işlem**, toplam −16.5R |
| %95 güven aralığı (gün-kümeli) | −0.22 … +0.30R → kenar kanıtı yok |
| Skor işe yarıyor mu? | 80–82: %55 kazanç (+0.21R) · 86–90: %25 (−0.50R) → **hayır** |
| Yön | LONG %37 (−0.22R) · SHORT %53 (+0.18R) |
| Hedef büyüklüğü | medyan stop %1.5 · TP1 %1.5 · TP3 %4.7 · TP3 ≥ %10 olan sinyal yalnızca %10 |
| Stopların hızı | 92 stopun 27'si ilk 30 dakikada |
| ≥%10 giden 34 coin | %83'ü "geçerli kurulum yok" kapısında elendi |
| Karmaşıklık | 2000+ satır, 15+ kapı, ~20 eşik; 176 örnekle kalibre edilemez |

Sistem, %1–2'lik hedefler üretmek üzere kurulmuştu; %10+ hareketleri yakalamak da hiçbir zaman tasarımın parçası değildi.

## Yeni yaklaşım (küçük ve ölçülebilir)

* **Giriş:** önceki 48 saatlik tepe/dip KAPANIŞLA kırılır + mum güçlü (≥1.3 ATR, gövde ≥%50, tepeye yakın kapanış)
  + hacim ≥ 2× medyan + önceki volatilite normalden fazla değil (hareket zaten patlamamış) + coin yeterince oynak
  (1s ATR ≥ %1) + son 24 saatte zaten çok koşmamış.
* **Çıkış:** stop = 2 ATR. TP1 = +1.5R'de pozisyonun %33'ü kapanır, stop girişe çekilir. Kalan kısım **3×ATR iz süren
  stop** ile taşınır; sabit üst hedef yoktur (büyük hareketleri kesmemek için). 72 saat zaman stopu.
* **Skor yok.** Uydurma "olasılık" yerine şeffaf güç ölçüsü (hacim × mum boyu) ve backtest tercile tablosu.
* **Tek doğru kaynak:** canlı tarama, backtest ve ileriye dönük takip AYNI sinyal ve çıkış kodunu kullanır.

> **%10+ garantisi yoktur.** Sistem, büyük hareketlerin *başlangıcını* yakalamayı ve kazananı koşturmayı hedefler.
> Bu tür stratejilerde kazanma oranı genelde düşüktür (%30–45); kârlılık nadir büyük kazançlardan gelir.
> Raporda "stoplanmadan önce ≥%10 hedefe ulaşan oran" ayrıca ölçülür.

## Kurulum

1. **Eski tarayıcıyı kapatın:** eski repodaki `.github/workflows/scan.yml` ve `altcoin_alert_scanner.py` dosyalarını silin
   (aksi halde ikisi birden çalışır). Bu klasörün içeriğini repoya yükleyin.
2. Secret'lar aynı: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. **Önce backtest:** GitHub → Actions → *Scout backtest (elle başlat)* → Run workflow (varsayılanlar iyidir).
   Rapor `reports/latest.md` olarak repoya yazılır (GitHub uygulamasından okunur).
4. Raporu bana gönderin; birlikte yorumlayalım. **Rapor iyi çıkmadan canlı sinyalle işlem açmayın.**

## Raporu nasıl okurum

* **Beklenti (net R):** işlem başına ortalama kazanç, maliyetler düşülmüş. 1R = stop mesafesi.
* **%95 güven aralığı:** alt sınır > 0 ise istatistiksel kanıt var. Aralık 0'ı içeriyorsa sonuç şans olabilir.
* **Zaman bölmesi:** ilk %60 ve son %40 aynı işaretteyse daha güvenilirdir.
* **Parametre taraması:** parlak tek hücreye değil *platoya* bakın. Sentetik rastgele veride bile "en iyi" hücre
  sürekli iyi görünür (aşırı uyum); tarama bunu görünür kılmak içindir.
* **Önerilen karar kuralı (canlı paradan önce):** ≥200 işlem · güven aralığı alt sınırı > 0 (ya da en azından iki dönem
  de pozitif) · plato hücrelerinin çoğu pozitif · 2–4 hafta canlı *ileriye dönük* takipte (Telegram'daki
  "İLERİYE DÖNÜK SONUÇ" satırı) beklenti pozitif. Hepsi sağlansa bile garanti değildir.

## Risk yönetimi

* İşlem başına hesabın **%0.5'inden fazlasını riske atmayın.** Pozisyon büyüklüğü = (hesap × %0.5) / stop%.
  Örn. 1000 USDT hesap, stop %4 → risk 5 USDT → pozisyon ≈ 125 USDT (5x'te marj ≈ 25 USDT).
* Aynı anda çok sinyal genelde tek bir piyasa hareketidir (hepsi birlikte düşer). Toplam açık riski %2 ile sınırlayın.
* Rapordaki "Maks. düşüş" R cinsindendir; işlem başına risk ile çarpın (örn. 40R × %0.5 = %20).

## Sınırlamalar (dürüst liste)

* Backtest evreni bugünün likit coinleridir (hayatta kalma yanlılığı: delist olanlar yok).
* Funding ücreti hesaba katılmaz; kayma sabit varsayılır (az likit coinlerde gerçek maliyet daha yüksek olabilir).
* Mum içi sıra bilinmez; belirsizlikte hep kötümser varsayım kullanılır (önce stop).
* 1 saatlik mum çözünürlüğü: ilk dakikalardaki hızlı hareketler yakalanmaz, giriş 1 saate kadar gecikebilir
  (canlı fiyat kontrolü "fiyat kaçtıysa" bildirimi eler).
* Sentetik testler motorun doğruluğunu kanıtlar, stratejinin gerçek piyasada çalıştığını DEĞİL.

## Ayarlar

`SCOUT_<ALAN>` ortam değişkeni ile `scout/config.py` içindeki her parametre ezilebilir
(örn. `SCOUT_MIN_VOL_MULT=3`, `SCOUT_TRAIL_ATR=4`, `SCOUT_USE_BTC_FILTER=true`); maliyetler `SCOUT_COST_*`,
canlı ayarlar `SCOUT_LIVE_*` (örn. `SCOUT_LIVE_MIN_QUOTE_VOLUME`, `SCOUT_LIVE_MAX_ALERTS_PER_RUN`).
Parametreleri **tahminle değil backtest raporuna bakarak** değiştirin.

## Dosya haritası

```
scout/config.py      tüm parametreler (tek yer)
scout/indicators.py  göstergeler (gelecek sızıntısı testli)
scout/strategy.py    sinyal üretimi (backtest = canlı)
scout/exits.py       çıkış durum makinesi (kötümser mum-içi sıralama)
scout/backtest.py    backtest + maliyet + güven aralığı + zaman bölmesi + parametre taraması
scout/data.py        MEXC verisi (ağ gerektiren tek modül)
scout/signals.py     aday, mesaj, tekrar engeli
scout/tracker.py     canlı sinyallerin ileriye dönük takibi
scout/live.py        saatlik tarama akışı
tests/               30 test (indikatör, sızıntı, çıkışlar, backtest, uçtan uca)
```

Testler her çalıştırmada otomatik koşar: `python -m unittest discover -s tests -t .`

## Telegram cards and live activation

Scout keeps Telegram alerts visual: each alert is rendered as a readable image card, with plain-text fallback if Telegram rejects the image. The live workflow runs once per hour after the hourly candle closes and runs all unit tests before scanning. The previous 15-minute scanner workflow is removed so the same market is not alerted by two competing scanners.
