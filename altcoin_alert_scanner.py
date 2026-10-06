"""
V13.2 bütünsel skor + güvenilir veri yedeği + Libra/SAR teyidi + retest koruması + paralel derin tarama -- BULUTTA çalışır (GitHub Actions),
telefondaki/bilgisayardaki hiçbir şeye bağımlı değil. Mevcut MEXC trading
bot'unuzdan TAMAMEN bağımsızdır -- hiçbir dosyasını içe aktarmaz, hiçbir
emir göndermez. Sadece MEXC'nin herkese açık (public) piyasa verisini
okur ve Telegram'a bildirim gönderir. API anahtarı / hesap bilgisi
gerektirmez -- sadece TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID.

--- V11: spot keşif + vadeli doğrulama + erken hareket analizi + recall odaklı market-cap katmanı ---

Üç BAĞIMSIZ eksen var, hiçbiri diğerini beslemiyor / bloklamıyor:

  1) directional   -- "gerçekten bir yön hareketi var mı" (trend + momentum
                       + hacim + kısa vadeli kırılım teyidi + BTC/ETH rejim
                       hizası). Yön (LONG/SHORT) burada belirlenir.
  2) entry_quality  -- "şu an bu fiyattan girmek mantıklı mı" (EMA'dan
                       kovalamama + hedefe kadar kalan mesafe).
  3) move_potential  -- "bu hareketin büyüklük potansiyeli ne" (mesafe +
                       volatilite + trend gücü).

Bir önceki versiyonda iki gerçek hata vardı, ikisi de burada düzeltildi:

  BUG 1 (çelişki): "structure" bileşeni içindeki range_pos (fiyatın
  96-mumluk aralığın TEPESİNE yakın olmasını ödüllendiriyordu) ile
  entry_quality içindeki location_score (fiyatın dirence UZAK olmasını,
  yani aralığın ALTINA yakın olmasını ödüllendiriyordu) matematiksel
  olarak birbirinin ZITTIYDI -- aynı 96-mumluk aralıktan besleniyorlardı.
  Hiçbir setup ikisini aynı anda maksimize edemiyordu. Çözüm: range_pos
  tamamen kaldırıldı. "Kırılım" artık sadece önceki muma göre kısa vadeli
  bir olay (breakout_atr + confirmation) -- aralıktaki konumla hiç ilgisi
  yok, dolayısıyla entry_quality ile çakışmıyor.

  BUG 2 (çifte sayım): "structure" ve "market" bileşenleri hem
  directional'ın İÇİNDE hem final skorda TEKRAR ayrı ayrı toplanıyordu --
  bu da onların nominal ağırlıklarından çok daha fazla etki etmesine yol
  açıyordu. Çözüm: artık sadece directional'ın içinde bir kez sayılıyor,
  final formülde tekrar edilmiyor. Ağırlıklar (0.56/0.20/0.24) bu
  birleştirmeyi yansıtacak şekilde yeniden hesaplandı, toplamı hâlâ 1.00.

Bonus: BTC/ETH çoklu zaman dilimi hizası artık sadece mesaj başlığında
süs değil -- doğrudan directional skoruna giriyor (tam hizalıysa tam
puan, kısmi hizalıysa yarım, ters yönde ise sıfır).
"""
import os, json, time, threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import datetime, timezone
import ccxt
import numpy as np
import pandas as pd
import requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')
# Tarama evreni tüm aktif MEXC USDT perpetual marketlerdir.
# Düşük likiditeli çiftler analiz edilir ancak işlem planı bildirimine geçemez.
MIN_ALERT_24H_VOLUME = float(os.getenv('MIN_ALERT_24H_VOLUME', '250000'))
SWAP_QUOTE_VOLUMES = {}
SWAP_PERCENTAGES = {}
# Derin analiz, minimum hacmi geçen tüm aktif vadeli çiftlere uygulanır.
DEEP_SCAN_MAX = int(os.getenv('DEEP_SCAN_MAX', '0'))  # 0 = limit yok
MIN_SPOT_24H_VOLUME = float(os.getenv('MIN_SPOT_24H_VOLUME', '500000'))
SPOT_RADAR_MAX_CANDIDATES = int(os.getenv('SPOT_RADAR_MAX_CANDIDATES', '0'))  # 0 = no fixed candidate cap
MIN_SPOT_DISCOVERY_PERP_VOLUME = float(os.getenv('MIN_SPOT_DISCOVERY_PERP_VOLUME', '100000'))
SPOT_RELATIVE_MOVER_SHARE = float(os.getenv('SPOT_RELATIVE_MOVER_SHARE', '0.20'))  # dynamic top share, not a fixed % move threshold
MIN_SPOT_DISCOVERY_1H_MOVE = float(os.getenv('MIN_SPOT_DISCOVERY_1H_MOVE', '2.5'))
MIN_SPOT_DISCOVERY_VOLUME_RATIO = float(os.getenv('MIN_SPOT_DISCOVERY_VOLUME_RATIO', '1.15'))
SPOT_WATCH_COOLDOWN_HOURS = float(os.getenv('SPOT_WATCH_COOLDOWN_HOURS', '6'))
MIN_CONFIRMED_RR = float(os.getenv('MIN_CONFIRMED_RR', '1.8'))
MIN_DIRECTION_GAP = float(os.getenv('MIN_DIRECTION_GAP', '5'))  # yalnızca çok kararsız yönleri engelleyen emniyet tabanı
ALERT_MIN_SCORE      = float(os.getenv('ALERT_MIN_SCORE', '80'))   # base technical score; rank tiers decide the actual alert gate
MARKET_CAP_CACHE_FILE = Path(os.getenv('MARKET_CAP_CACHE_FILE', 'market_cap_cache.json'))
MARKET_CAP_REFRESH_MINUTES = int(os.getenv('MARKET_CAP_REFRESH_MINUTES', '360'))
MARKET_CAP_TOP_N = 800
EARLY_TRIGGER_MAX_ATR = float(os.getenv('EARLY_TRIGGER_MAX_ATR', '1.25'))
MIN_SETUP_READINESS = float(os.getenv('MIN_SETUP_READINESS', '65'))
SLEEP_BETWEEN_COINS  = float(os.getenv('SLEEP_BETWEEN_COINS', '0.0'))
SCAN_WORKERS = max(1, min(4, int(os.getenv('SCAN_WORKERS', '2'))))
# MEXC public OHLCV requests are shared across worker threads. A single global
# pacing gate prevents independent CCXT clients from bursting the API.
MEXC_REQUEST_MIN_INTERVAL = float(os.getenv('MEXC_REQUEST_MIN_INTERVAL', '0.12'))
MEXC_RATE_LIMIT_RETRIES = max(1, int(os.getenv('MEXC_RATE_LIMIT_RETRIES', '4')))
MEXC_RATE_LIMIT_BACKOFF = float(os.getenv('MEXC_RATE_LIMIT_BACKOFF', '1.5'))
_MEXC_REQUEST_LOCK = threading.Lock()
_LAST_MEXC_REQUEST_AT = 0.0
CANDLE_CACHE = {}  # aynı çalıştırmada aynı sembol/zaman dilimi ikinci kez istenmez
STATE_FILE           = Path(os.getenv('STATE_FILE', 'alert_state.json'))
OUTCOME_FILE         = Path(os.getenv('OUTCOME_FILE', 'signal_outcomes.json'))
MAX_STOP_DISTANCE_PCT = float(os.getenv('MAX_STOP_DISTANCE_PCT', '0.08'))
DIAG_LOG             = Path(os.getenv('DIAG_LOG_FILE', 'scan_diagnostic.log'))
DIAG_LOG_MAX_LINES   = 500
WIDE_STOP_BUFFER_ATR = float(os.getenv('WIDE_STOP_BUFFER_ATR', '0.6'))  # ek "geniş stop" ATR payı
# V13.2: Libra-benzeri yapı + Parabolic SAR teyidi + sahte kırılım koruması + paralel tarama.
LIBRA_FIB_TARGET = 0.786
LIBRA_FIB_TOLERANCE = float(os.getenv('LIBRA_FIB_TOLERANCE', '0.08'))
LIBRA_MIN_SCORE = float(os.getenv('LIBRA_MIN_SCORE', '55'))
SAR_AF_STEP = float(os.getenv('SAR_AF_STEP', '0.02'))
SAR_AF_MAX = float(os.getenv('SAR_AF_MAX', '0.20'))
BREAKOUT_MIN_VOLUME_RATIO = float(os.getenv('BREAKOUT_MIN_VOLUME_RATIO', '1.15'))
BREAKOUT_MIN_BODY_RATIO = float(os.getenv('BREAKOUT_MIN_BODY_RATIO', '0.45'))
RETEST_MAX_ATR = float(os.getenv('RETEST_MAX_ATR', '0.65'))
OHLCV_LIMIT = 220
PREFERRED_HISTORY_BARS = 60

exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
_THREAD_LOCAL = threading.local()
def _worker_exchange():
    ex = getattr(_THREAD_LOCAL, 'exchange', None)
    if ex is None:
        ex = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        _THREAD_LOCAL.exchange = ex
    return ex

spot_exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

class InsufficientCandleDataError(ValueError):
    """Market has too little candle history for reliable indicators; skip, do not fabricate."""

def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(float(v), hi))

# ---------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------
def _parabolic_sar(high, low, step=0.02, max_af=0.20):
    """Classic Parabolic SAR, calculated sequentially without look-ahead."""
    h = np.asarray(high, dtype=float); l = np.asarray(low, dtype=float)
    n = len(h)
    if n == 0: return np.array([]), np.array([])
    sar = np.empty(n, dtype=float); direction = np.ones(n, dtype=int)
    bull = True; af = step; ep = h[0]; sar[0] = l[0]
    for i in range(1, n):
        prev_sar = sar[i-1]
        if bull:
            sar_i = prev_sar + af * (ep - prev_sar)
            sar_i = min(sar_i, l[i-1], l[i-2] if i >= 2 else l[i-1])
            if l[i] < sar_i:
                bull = False; direction[i] = -1; sar_i = ep; ep = l[i]; af = step
            else:
                direction[i] = 1
                if h[i] > ep:
                    ep = h[i]; af = min(max_af, af + step)
        else:
            sar_i = prev_sar + af * (ep - prev_sar)
            sar_i = max(sar_i, h[i-1], h[i-2] if i >= 2 else h[i-1])
            if h[i] > sar_i:
                bull = True; direction[i] = 1; sar_i = ep; ep = h[i]; af = step
            else:
                direction[i] = -1
                if l[i] < ep:
                    ep = l[i]; af = min(max_af, af + step)
        sar[i] = sar_i
    return sar, direction

def add_indicators(df):
    df = df.copy()
    if df.empty or len(df) < 25:
        raise InsufficientCandleDataError(f'Yetersiz mum verisi: {len(df)}')
    h, l, c, v = df.high, df.low, df.close, df.volume
    df['ema20'] = c.ewm(span=20, adjust=False).mean()
    df['ema50'] = c.ewm(span=50, adjust=False).mean()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = g / loss.replace(0, np.nan)
    rsi = 100 - 100/(1+rs)
    rsi = rsi.where(loss > 0, np.where(g > 0, 100.0, 50.0))
    rsi = rsi.where(g > 0, np.where(loss > 0, 0.0, 50.0))
    df['rsi'] = rsi
    e12 = c.ewm(span=12, adjust=False).mean(); e26 = c.ewm(span=26, adjust=False).mean()
    df['macd'] = e12 - e26; df['macd_signal'] = df.macd.ewm(span=9, adjust=False).mean()
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    df['tr'] = tr; df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    up = h.diff(); dn = l.shift(1) - l
    plus = pd.Series(np.where((up>dn)&(up>0), up, 0.), index=df.index)
    minus = pd.Series(np.where((dn>up)&(dn>0), dn, 0.), index=df.index)
    atrv = tr.ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan)
    pdi = 100*plus.ewm(alpha=1/14, adjust=False).mean()/atrv
    mdi = 100*minus.ewm(alpha=1/14, adjust=False).mean()/atrv
    di_sum = pdi + mdi
    dx = 100*(pdi-mdi).abs()/di_sum.replace(0, np.nan)
    dx = dx.where(di_sum > 0, 0.0)
    df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()
    df['volume_ratio'] = v / v.rolling(20).mean(); df['atr_pct'] = df.atr / c
    psar, psar_dir = _parabolic_sar(h, l, SAR_AF_STEP, SAR_AF_MAX)
    df['psar'] = psar
    df['psar_dir'] = psar_dir
    df['psar_bull'] = df['psar_dir'] > 0
    df['psar_flip'] = df['psar_dir'].diff().fillna(0)
    return df.dropna().reset_index(drop=True)

def _ohlcv_frame(rows):
    df = pd.DataFrame(rows or [], columns=['timestamp','open','high','low','close','volume'])
    if df.empty:
        return df
    for col in ('timestamp','open','high','low','close','volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=['timestamp','open','high','low','close','volume'])
    df = df[df['timestamp'] > 0]
    df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last')
    return df.reset_index(drop=True)

def _resample_ohlcv(df, rule):
    if df.empty:
        return df
    tmp = df.copy()
    tmp['dt'] = pd.to_datetime(tmp['timestamp'], unit='ms', utc=True)
    tmp = tmp.set_index('dt')
    agg = tmp.resample(rule).agg({
        'timestamp':'last', 'open':'first', 'high':'max', 'low':'min',
        'close':'last', 'volume':'sum'
    }).dropna(subset=['open','high','low','close'])
    agg['timestamp'] = (agg.index.astype('int64') // 10**6)
    return agg[['timestamp','open','high','low','close','volume']].reset_index(drop=True)

def _fetch_ohlcv_rate_limited(ex, symbol, timeframe, limit):
    """Fetch OHLCV with global pacing and automatic MEXC rate-limit backoff."""
    global _LAST_MEXC_REQUEST_AT
    last_error = None

    for attempt in range(MEXC_RATE_LIMIT_RETRIES + 1):
        try:
            # All worker threads share one pacing gate. Correct coverage is
            # more important than shaving a few minutes off the scan.
            with _MEXC_REQUEST_LOCK:
                now = time.monotonic()
                wait = MEXC_REQUEST_MIN_INTERVAL - (now - _LAST_MEXC_REQUEST_AT)
                if wait > 0:
                    time.sleep(wait)
                _LAST_MEXC_REQUEST_AT = time.monotonic()
                return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except ccxt.RateLimitExceeded as exc:
            last_error = exc
            if attempt >= MEXC_RATE_LIMIT_RETRIES:
                break
            delay = MEXC_RATE_LIMIT_BACKOFF * (2 ** attempt)
            print(
                f'RATE-LIMIT {symbol} {timeframe}: '
                f'{attempt + 1}/{MEXC_RATE_LIMIT_RETRIES} yeniden deneme; '
                f'{delay:.1f}s bekleniyor.'
            )
            time.sleep(delay)
        except (ccxt.DDoSProtection, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable) as exc:
            last_error = exc
            if attempt >= MEXC_RATE_LIMIT_RETRIES:
                break
            delay = max(1.0, MEXC_RATE_LIMIT_BACKOFF * (2 ** attempt))
            print(
                f'GEÇİCİ API HATASI {symbol} {timeframe}: '
                f'{type(exc).__name__}; {delay:.1f}s sonra yeniden deneme.'
            )
            time.sleep(delay)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f'OHLCV alınamadı: {symbol} {timeframe}')


def candles(symbol, timeframe):
    """Fetch each symbol/timeframe once. If history is short, try a lower-TF resample."""
    key = (symbol, timeframe)
    if key in CANDLE_CACHE:
        return CANDLE_CACHE[key].copy()
    ex = _worker_exchange() if threading.current_thread() is not threading.main_thread() else exchange
    rows = _fetch_ohlcv_rate_limited(ex, symbol, timeframe, OHLCV_LIMIT)
    df = _ohlcv_frame(rows)
    # Fallback also covers sparse 15m history (e.g. newly listed contracts).
    fallback = {'4h': ('1h', '4h'), '1h': ('15m', '1h'), '15m': ('5m', '15min')}.get(timeframe)
    if len(df) < PREFERRED_HISTORY_BARS and fallback:
        lower_tf, rule = fallback
        try:
            # Fetch enough lower-timeframe bars to build up to ~200 target bars.
            # A 220-bar fallback was too short for 15m candles resampled from 5m.
            source_limit = min(1000, OHLCV_LIMIT * {'4h': 4, '1h': 4, '15min': 3}.get(rule, 4))
            lower_rows = _fetch_ohlcv_rate_limited(ex, symbol, lower_tf, source_limit)
            lower_df = _ohlcv_frame(lower_rows)
            resampled = _resample_ohlcv(lower_df, rule)
            if len(resampled) > len(df):
                print(f'MUM YEDEĞİ {symbol} {timeframe}: {len(df)} doğrudan mum yerine {len(resampled)} adet {lower_tf} verisinden birleştirilmiş mum kullanılıyor.')
                df = resampled
        except Exception as e:
            print(f'MUM YEDEĞİ BAŞARISIZ {symbol} {timeframe}: {type(e).__name__}: {str(e)[:100]}')
    if len(df) < 25:
        raise InsufficientCandleDataError(f'Yetersiz mum verisi: {len(df)} (daha düşük zaman dilimi yedeği de denendi)')
    result = add_indicators(df)
    if len(result) < 25:
        raise InsufficientCandleDataError(f'Yetersiz gösterge sonrası mum verisi: {len(result)} (kaynak mum: {len(df)})')
    CANDLE_CACHE[key] = result
    return result.copy()

def regime_from_row(x):
    if x.close > x.ema50 > x.ema200 and x.rsi >= 52: return 'BULLISH'
    if x.close < x.ema50 < x.ema200 and x.rsi <= 48: return 'BEARISH'
    return 'NEUTRAL'

def _closed_return_pct(df, bars=1):
    # Yalnızca kapanmış mumlar: -2 son kapanmış, -2-bars karşılaştırma mumu.
    if len(df) < bars + 3:
        return 0.0
    old = float(df.iloc[-2-bars].close)
    new = float(df.iloc[-2].close)
    return ((new / old) - 1.0) * 100.0 if old else 0.0

def symbol_regime(symbol):
    d4 = candles(symbol, '4h'); d1 = candles(symbol, '1h')
    return {
        '4h': regime_from_row(d4.iloc[-2]), '1h': regime_from_row(d1.iloc[-2]),
        'ret_4h': _closed_return_pct(d4, 1), 'ret_1h': _closed_return_pct(d1, 1),
    }

def market_context():
    btc = symbol_regime('BTC/USDT:USDT')
    eth = symbol_regime('ETH/USDT:USDT')
    primary = btc['4h']  # BTC 4h öncül
    aligned = (btc['1h'] == primary) and (eth['4h'] == primary)
    return {'btc': btc, 'eth': eth, 'primary_bias': primary, 'aligned': aligned}

def liquid_usdt_swaps():
    # Discover all active USDT perpetual markets; liquidity is a separate alert safety gate.
    global SWAP_QUOTE_VOLUMES, SWAP_PERCENTAGES
    markets = exchange.load_markets(); tickers = exchange.fetch_tickers(); out = []
    SWAP_QUOTE_VOLUMES = {}
    SWAP_PERCENTAGES = {}
    for s, m in markets.items():
        if not (m.get('active') and m.get('swap') and m.get('quote') == 'USDT') or s in ('BTC/USDT:USDT','ETH/USDT:USDT'):
            continue
        t = tickers.get(s, {}) or {}; q = t.get('quoteVolume')
        if q is None and t.get('baseVolume') and t.get('last'):
            try: q = float(t['baseVolume']) * float(t['last'])
            except (TypeError, ValueError): q = 0.0
        try: q = max(0.0, float(q or 0.0))
        except (TypeError, ValueError): q = 0.0
        SWAP_QUOTE_VOLUMES[s] = q
        try: pct = float(t.get('percentage') or 0.0)
        except (TypeError, ValueError): pct = 0.0
        SWAP_PERCENTAGES[s] = pct
        out.append((s, q))
    return [s for s, _ in sorted(out, key=lambda x: x[1], reverse=True)]

def select_deep_scan_symbols(all_symbols):
    """Deep-analyze every active USDT perpetual pair above the liquidity floor.

    All markets are still discovered at ticker level. Low-volume pairs are not
    sent as trade plans, so their extra OHLCV requests are intentionally skipped.
    """
    eligible = [s for s in all_symbols if SWAP_QUOTE_VOLUMES.get(s, 0.0) >= MIN_ALERT_24H_VOLUME]
    eligible.sort(key=lambda s: SWAP_QUOTE_VOLUMES.get(s, 0.0), reverse=True)
    if DEEP_SCAN_MAX > 0:
        return eligible[:DEEP_SCAN_MAX]
    return eligible

def fast_priority_symbols(symbols, spot_discovery_symbols):
    """Keep full ticker discovery, but prioritize deep analysis by opportunity signals.

    All active contracts remain visible to the missed-opportunity audit. Deep OHLCV
    work is ordered so high-volume, fast-moving, relative-strength and spot-discovered
    contracts are analyzed first. With DEEP_SCAN_MAX=0 the complete eligible universe
    is still analyzed; concurrency is the primary speedup.
    """
    return sorted(symbols, key=lambda s: (
        s in spot_discovery_symbols,
        abs(float(SWAP_PERCENTAGES.get(s, 0.0))),
        float(SWAP_QUOTE_VOLUMES.get(s, 0.0))
    ), reverse=True)

def market_cap_rankings():
    """Market-cap rank with persistent cache and provider fallbacks.

    Uses CoinPaprika first, then CoinCap, then CoinGecko as fallback. A provider failure
    never prevents scanning; a previous cache is retained. Unknown ranks use
    the conservative 90+ tier. Symbols with ambiguous duplicate tickers are
    removed rather than assigned a potentially wrong rank.
    """
    now = time.time()
    cache, cache_ts = {}, 0.0
    if MARKET_CAP_CACHE_FILE.exists():
        try:
            raw = json.loads(MARKET_CAP_CACHE_FILE.read_text(encoding='utf-8'))
            cache_ts = float(raw.get('updated_at', 0))
            cache = raw.get('ranks', {}) or {}
        except Exception:
            cache, cache_ts = {}, 0.0
    cache_max_rank = max((int(v) for v in cache.values() if str(v).isdigit()), default=0)
    if cache and cache_max_rank >= min(MARKET_CAP_TOP_N, 800) and (now - cache_ts) < MARKET_CAP_REFRESH_MINUTES * 60:
        return cache, cache_ts, 'cache'
    if cache and cache_max_rank < min(MARKET_CAP_TOP_N, 800):
        print(f'Market-cap önbelleği eski kapsamda ({cache_max_rank} sıra); yeni 800+ kapsamı için yenileniyor.')

    providers = []
    # CoinGecko API fallback (may deny requests from some cloud IP ranges).
    def get_coingecko():
        out = []
        for page in range(1, 5):
            r = requests.get(
                'https://api.coingecko.com/api/v3/coins/markets',
                params={'vs_currency':'usd','order':'market_cap_desc','per_page':250,'page':page,'sparkline':'false'},
                timeout=18,
                headers={'accept':'application/json','user-agent':'Mozilla/5.0 altcoin-alert-scanner/5.0'}
            )
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list):
                raise ValueError('CoinGecko unexpected response')
            out.extend((str(x.get('symbol') or '').upper(), int(x['market_cap_rank']))
                       for x in rows if x.get('symbol') and x.get('market_cap_rank') and int(x['market_cap_rank']) <= MARKET_CAP_TOP_N)
        return out

    # CoinPaprika public tickers; ranks are supplied by the API.
    def get_coinpaprika():
        r = requests.get('https://api.coinpaprika.com/v1/tickers', params={'quotes':'USD'},
                         timeout=25, headers={'accept':'application/json','user-agent':'altcoin-alert-scanner/5.0'})
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list): raise ValueError('CoinPaprika unexpected response')
        return [(str(x.get('symbol') or '').upper(), int(x['rank']))
                for x in rows if x.get('symbol') and x.get('rank') and int(x['rank']) > 0 and int(x['rank']) <= MARKET_CAP_TOP_N]

    # CoinCap assets endpoint as a separate fallback.
    def get_coincap():
        r = requests.get('https://api.coincap.io/v2/assets', params={'limit':MARKET_CAP_TOP_N},
                         timeout=20, headers={'accept':'application/json','user-agent':'altcoin-alert-scanner/5.0'})
        r.raise_for_status()
        payload = r.json(); rows = payload.get('data', []) if isinstance(payload, dict) else []
        if not isinstance(rows, list): raise ValueError('CoinCap unexpected response')
        return [(str(x.get('symbol') or '').upper(), int(x['rank']))
                for x in rows if x.get('symbol') and x.get('rank') and str(x['rank']).isdigit()]

    # GitHub-hosted runners commonly receive 403 from CoinGecko's public API.
    # Prefer the providers that worked in the latest deployment; CoinGecko remains
    # the final fallback instead of adding a predictable 403 to every fresh scan.
    providers = [('coinpaprika', get_coinpaprika), ('coincap', get_coincap), ('coingecko', get_coingecko)]
    for provider_name, fetcher in providers:
        try:
            pairs = fetcher()
            fresh, collisions = {}, set()
            for sym, rank in pairs:
                if not sym or rank <= 0: continue
                if sym in fresh and fresh[sym] != rank:
                    collisions.add(sym)
                else:
                    fresh[sym] = rank
            for sym in collisions: fresh.pop(sym, None)
            if len(fresh) >= 100:
                MARKET_CAP_CACHE_FILE.write_text(json.dumps({'updated_at':now,'source':provider_name,'ranks':fresh}, indent=2), encoding='utf-8')
                print(f'Market-cap kaynağı: {provider_name} | {len(fresh)} sembol sıralandı.')
                return fresh, now, provider_name
            print(f'Market-cap {provider_name}: yetersiz veri ({len(fresh)} sembol), yedek kaynağa geçiliyor.')
        except Exception as e:
            print(f'Market-cap {provider_name} başarısız: {type(e).__name__}: {str(e)[:180]}')

    if cache:
        print(f'Market-cap: kayıtlı önbellek kullanılıyor ({len(cache)} sembol; güncellik sınırlı olabilir).')
        return cache, cache_ts, 'stale-cache'
    return {}, 0.0, 'unavailable'

def market_cap_rank_for_symbol(symbol, rankings):
    base = str(symbol).split('/')[0].upper()
    return int(rankings.get(base, 9999))

def market_cap_label_for_symbol(symbol, rankings):
    base = str(symbol).split('/')[0].upper()
    return f"#{int(rankings[base])}" if base in rankings else 'bilinmiyor (90+ eşiği uygulanır)'

def alert_gate_for_rank(score, market_cap_rank):
    """Recall-oriented market-cap gate: 1-500=>80+, 501-800=>85+, 801+/unknown=>90+.

    Market cap is a coverage/liquidity tier, not a prediction of direction. The
    unified technical score remains the signal quality measure.
    """
    if market_cap_rank <= 500:
        return score >= 80.0, 80.0
    if market_cap_rank <= 800:
        return score >= 85.0, 85.0
    return score >= 90.0, 90.0

def structure_levels(df):
    """Use closed candles only; the latest candle is intentionally excluded."""
    w = df.iloc[-98:-2]
    return float(w.low.min()), float(w.high.max())

# ---------------------------------------------------------------------
# Directional score: trend + momentum + volume + breakout-confirmation
# + BTC/ETH regime alignment.
# ---------------------------------------------------------------------
def _external_alignment_raw(side, ctx, symbol_key):
    side_regime = 'BULLISH' if side == 'LONG' else 'BEARISH'
    if symbol_key is None:
        btc4 = ctx['btc']['4h'] == side_regime
        btc1 = ctx['btc']['1h'] == side_regime
        eth4 = ctx['eth']['4h'] == side_regime
        if btc4 and btc1 and eth4: return 8
        if btc4 and (btc1 or eth4): return 6
        if btc4: return 4
        if ctx['btc']['4h'] == 'NEUTRAL': return 2
        return 0
    other = 'eth' if symbol_key == 'btc' else 'btc'
    o4 = ctx[other]['4h'] == side_regime
    o1 = ctx[other]['1h'] == side_regime
    if o4 and o1: return 8
    if o4 or o1: return 5
    if ctx[other]['4h'] == 'NEUTRAL': return 2
    return 0

def _directional_score(df4, df1, df15, ctx, side, symbol_key=None):
    a, b, c = df4.iloc[-2], df1.iloc[-2], df15.iloc[-2]
    prev = df15.iloc[-3]
    trend_raw = 0.0
    if (a.close>a.ema50>a.ema200) if side=='LONG' else (a.close<a.ema50<a.ema200): trend_raw += 28
    elif (a.close>a.ema50) if side=='LONG' else (a.close<a.ema50): trend_raw += 12
    if (b.close>b.ema50>b.ema200) if side=='LONG' else (b.close<b.ema50<b.ema200): trend_raw += 22
    elif (b.close>b.ema50) if side=='LONG' else (b.close<b.ema50): trend_raw += 10

    mom_raw = 0.0
    if (c.close>c.ema20>c.ema50) if side=='LONG' else (c.close<c.ema20<c.ema50): mom_raw += 12
    if (c.macd>c.macd_signal) if side=='LONG' else (c.macd<c.macd_signal): mom_raw += 10
    # RSI is a momentum state, not a binary overbought/oversold veto.
    # Strong but not extreme RSI should still support an early/continuation setup.
    if side == 'LONG':
        if 52 <= c.rsi <= 68: mom_raw += 8
        elif 68 < c.rsi <= 75: mom_raw += 6
        elif 75 < c.rsi <= 82: mom_raw += 3
        elif 48 <= c.rsi < 52: mom_raw += 3
    else:
        if 32 <= c.rsi <= 48: mom_raw += 8
        elif 25 <= c.rsi < 32: mom_raw += 6
        elif 18 <= c.rsi < 25: mom_raw += 3
        elif 48 < c.rsi <= 52: mom_raw += 3
    if c.adx>=25: mom_raw += 8
    elif c.adx>=20: mom_raw += 4

    vol_raw = 0.0
    if c.volume_ratio>=1.30 and ((c.close>c.open) if side=='LONG' else (c.close<c.open)): vol_raw = 12
    elif c.volume_ratio>=1.10 and ((c.close>c.open) if side=='LONG' else (c.close<c.open)): vol_raw = 7

    if side == 'LONG':
        breakout_atr = max((float(c.close)-float(prev.high))/max(float(c.atr),1e-12), 0.0)
        confirmation = 1.0 if float(c.close) > float(prev.close) else 0.0
    else:
        breakout_atr = max((float(prev.low)-float(c.close))/max(float(c.atr),1e-12), 0.0)
        confirmation = 1.0 if float(c.close) < float(prev.close) else 0.0
    breakout_raw = clamp(14.0*clamp(breakout_atr/1.5,0,1) + 8.0*confirmation, 0, 22)
    market_raw = _external_alignment_raw(side, ctx, symbol_key)
    # Relative strength explicitly detects coins moving against a weak BTC/ETH
    # tape. This is a positive feature, not a hard market-regime veto.
    coin_1h = _closed_return_pct(df1, 1)
    coin_4h = _closed_return_pct(df4, 1)
    if symbol_key in ('btc', 'eth'):
        peers = ['eth', 'btc']
        peer = ctx[peers[0] if symbol_key == 'btc' else peers[1]]
        rel_1h = coin_1h - float(peer.get('ret_1h', 0.0))
        rel_4h = coin_4h - float(peer.get('ret_4h', 0.0))
    else:
        market_1h = (float(ctx['btc'].get('ret_1h', 0.0)) + float(ctx['eth'].get('ret_1h', 0.0))) / 2.0
        market_4h = (float(ctx['btc'].get('ret_4h', 0.0)) + float(ctx['eth'].get('ret_4h', 0.0))) / 2.0
        rel_1h = coin_1h - market_1h
        rel_4h = coin_4h - market_4h
    rel_signed = (0.65 * rel_1h + 0.35 * rel_4h) * (1.0 if side == 'LONG' else -1.0)
    relative_strength = clamp(50.0 + rel_signed * 9.0, 0.0, 100.0)
    trend = trend_raw/50*100; momentum = mom_raw/38*100; volume = vol_raw/12*100
    breakout = breakout_raw/22*100; market = market_raw/8*100
    # Own-coin structure dominates; BTC/ETH regime is context, not a veto.
    directional = (0.29*trend + 0.29*momentum + 0.12*volume +
                   0.16*breakout + 0.04*market + 0.10*relative_strength)
    return {'trend':trend,'momentum':momentum,'volume':volume,'breakout':breakout,
            'market':market,'relative_strength':relative_strength,
            'relative_1h':rel_1h,'relative_4h':rel_4h,'coin_1h':coin_1h,'coin_4h':coin_4h,
            'directional':clamp(directional)}

def _early_move_score(df4, df1, df15, ctx, side, ref_price, trigger, atr, local_res, local_sup):
    """Score whether a meaningful move is *forming*, not merely how far price ran.

    This deliberately rewards acceleration, volume/volatility expansion, trend slope,
    relative strength and proximity to a breakout level. It does not require a large
    24h move, so it can identify a setup before the eventual 8-10% move.
    """
    c = df15.iloc[-2]
    prev = df15.iloc[-3]
    p4 = df15.iloc[-6] if len(df15) >= 8 else df15.iloc[0]
    signed = 1.0 if side == 'LONG' else -1.0
    r15_1 = ((float(c.close)/float(prev.close))-1.0)*100.0 if float(prev.close) else 0.0
    r15_4 = ((float(c.close)/float(p4.close))-1.0)*100.0 if float(p4.close) else 0.0
    s15 = signed * r15_1
    s1h = signed * _closed_return_pct(df1, 1)
    s4h = signed * _closed_return_pct(df4, 1)
    # Directional acceleration: recent bars improving in the intended direction.
    accel = clamp(50.0 + s15*12.0 + s1h*5.0 + s4h*2.0, 0.0, 100.0)

    vr = float(c.volume_ratio) if pd.notna(c.volume_ratio) else 0.0
    volume_exp = clamp((vr - 0.8) / 1.7 * 100.0, 0.0, 100.0)

    atr_pct = float(c.atr_pct) if pd.notna(c.atr_pct) else 0.0
    prev_atr_pct = float(df15.iloc[-7:-2].atr_pct.median()) if len(df15) >= 8 else atr_pct
    vol_expand = clamp(50.0 + ((atr_pct/max(prev_atr_pct, 1e-6))-1.0)*55.0, 0.0, 100.0)

    ema20_now = float(c.ema20)
    ema20_old = float(p4.ema20)
    ema_slope = signed * ((ema20_now-ema20_old)/max(atr,1e-12))
    h1_c = df1.iloc[-2]
    h1_old = df1.iloc[-6] if len(df1) >= 8 else df1.iloc[0]
    h1_atr = max(float(h1_c.atr), 1e-12)
    h1_slope = signed * ((float(h1_c.ema20)-float(h1_old.ema20))/h1_atr)
    slope_score = clamp(50.0 + ema_slope*18.0 + h1_slope*10.0, 0.0, 100.0)

    level = local_res if side == 'LONG' else local_sup
    dist_atr = abs(level-ref_price)/max(atr,1e-12)
    proximity = clamp(100.0 - max(dist_atr,0.0)/max(EARLY_TRIGGER_MAX_ATR,0.1)*65.0, 0.0, 100.0)

    market_1h = (float(ctx['btc'].get('ret_1h',0.0))+float(ctx['eth'].get('ret_1h',0.0)))/2.0
    market_4h = (float(ctx['btc'].get('ret_4h',0.0))+float(ctx['eth'].get('ret_4h',0.0)))/2.0
    rel1 = signed * (_closed_return_pct(df1,1)-market_1h)
    rel4 = signed * (_closed_return_pct(df4,1)-market_4h)
    relative = clamp(50.0 + rel1*8.0 + rel4*3.0, 0.0, 100.0)

    return clamp(0.22*accel + 0.18*volume_exp + 0.16*vol_expand +
                 0.18*slope_score + 0.14*proximity + 0.12*relative)

def _pivot_points(df, left=2, right=2, max_points=12):
    """Return recent confirmed swing pivots from closed candles only."""
    closed = df.iloc[:-1].copy() if len(df) > 1 else df.copy()
    if len(closed) < left + right + 5:
        return []
    pts = []
    for i in range(left, len(closed)-right):
        hi = float(closed.high.iloc[i]); lo = float(closed.low.iloc[i])
        if hi >= float(closed.high.iloc[i-left:i].max()) and hi > float(closed.high.iloc[i+1:i+right+1].max()):
            pts.append((i, 'H', hi))
        if lo <= float(closed.low.iloc[i-left:i].min()) and lo < float(closed.low.iloc[i+1:i+right+1].min()):
            pts.append((i, 'L', lo))
    pts.sort(key=lambda x: x[0])
    compact = []
    for p in pts:
        if compact and compact[-1][1] == p[1]:
            if (p[2] > compact[-1][2]) if p[1] == 'H' else (p[2] < compact[-1][2]):
                compact[-1] = p
        else:
            compact.append(p)
    return compact[-max_points:]


def _libra_pattern_score(df15, side, atr):
    """Conservative Libra-like detector; a score contributor, never a standalone gate."""
    piv = _pivot_points(df15)
    if len(piv) < 5 or atr <= 0:
        return {'score':50.0,'match':False,'fib':np.nan,'distance_atr':np.nan}
    c = float(df15.iloc[-2].close)
    best = None
    for j in range(len(piv)-4):
        seq = piv[j:j+5]
        types = ''.join(p[1] for p in seq)
        if side == 'LONG' and types != 'LHLHL':
            continue
        if side == 'SHORT' and types != 'HLHLH':
            continue
        a,b,m,d,e = [p[2] for p in seq]
        if side == 'LONG':
            shoulder_ref = max(a,e); head=m
            asym = (shoulder_ref-head)/atr
            impulse_low, impulse_high = min(a,m,e), max(b,d)
            if impulse_high <= impulse_low: continue
            fib = impulse_high - LIBRA_FIB_TARGET*(impulse_high-impulse_low)
        else:
            shoulder_ref = min(a,e); head=m
            asym = (head-shoulder_ref)/atr
            impulse_low, impulse_high = min(b,d), max(a,m,e)
            if impulse_high <= impulse_low: continue
            fib = impulse_low + LIBRA_FIB_TARGET*(impulse_high-impulse_low)
        fib_dist = abs(c-fib)/max(atr,1e-12)
        shape = clamp(50 + asym*18, 0, 100)
        fib_score = clamp(100 - fib_dist*45, 0, 100)
        recency = clamp(100 - max(0, len(df15)-1-seq[-1][0])*1.8, 0, 100)
        score = 0.50*shape + 0.38*fib_score + 0.12*recency
        candidate=(score, fib, fib_dist, asym)
        if best is None or candidate[0] > best[0]: best=candidate
    if best is None:
        return {'score':50.0,'match':False,'fib':np.nan,'distance_atr':np.nan}
    score,fib,fib_dist,asym=best
    match = score >= LIBRA_MIN_SCORE and fib_dist <= max(1.5, LIBRA_FIB_TOLERANCE*10)
    return {'score':round(score,1),'match':bool(match),'fib':float(fib),'distance_atr':round(fib_dist,2),'asym_atr':round(asym,2)}


def _sar_confirmation(df15, side):
    c=df15.iloc[-2]; prev=df15.iloc[-3]
    direction_ok = bool(c.psar_bull) if side=='LONG' else not bool(c.psar_bull)
    flipped = (float(c.psar_flip) > 0) if side=='LONG' else (float(c.psar_flip) < 0)
    price_ok = float(c.close) > float(c.psar) if side=='LONG' else float(c.close) < float(c.psar)
    prev_ok = float(prev.close) > float(prev.psar) if side=='LONG' else float(prev.close) < float(prev.psar)
    score = 50.0
    if direction_ok: score += 22
    if price_ok: score += 15
    if flipped: score += 13
    elif prev_ok: score += 5
    return clamp(score), bool(direction_ok and price_ok), bool(flipped)

def score_setup(df4, df1, df15, ctx, symbol_key=None):
    """Build a pre-breakout or newly-started breakout plan from closed candles.

    The current reference price is kept separate from the planned trigger entry.
    Pre-breakout alerts only qualify when price is close to a local level, trend
    context agrees, and a projected plan meets the market-cap score gate and RR.
    """
    c = df15.iloc[-2]
    ref_price = float(c.close)
    sup, res = structure_levels(df15)
    atr = max(float(c.atr), ref_price*0.0001)
    atr_pct = float(c.atr_pct)
    local = df15.iloc[-22:-2]
    if len(local) < 10:
        raise InsufficientCandleDataError('Yeterli kapalı mum yok: yapı seviyeleri hesaplanamadı')
    local_res = float(local.high.max())
    local_sup = float(local.low.min())
    L = _directional_score(df4,df1,df15,ctx,'LONG',symbol_key)
    S = _directional_score(df4,df1,df15,ctx,'SHORT',symbol_key)
    long_early = _early_move_score(df4, df1, df15, ctx, 'LONG', ref_price, local_res + 0.10*atr, atr, local_res, local_sup)
    short_early = _early_move_score(df4, df1, df15, ctx, 'SHORT', ref_price, local_sup - 0.10*atr, atr, local_res, local_sup)
    long_select = 0.72*L['directional'] + 0.28*long_early
    short_select = 0.72*S['directional'] + 0.28*short_early
    direction = 'LONG' if long_select>=short_select else 'SHORT'
    vals = L if direction=='LONG' else S
    opp = S if direction=='LONG' else L
    directional = vals['directional']; opposite = opp['directional']; gap = directional-opposite
    early_move = long_early if direction=='LONG' else short_early
    libra = _libra_pattern_score(df15, direction, atr)
    sar_score, sar_ok, sar_flip = _sar_confirmation(df15, direction)

    if direction == 'LONG':
        trigger = local_res + 0.10*atr
        dist_atr = (local_res-ref_price)/atr
        body = abs(float(c.close)-float(c.open)); candle_range=max(float(c.high)-float(c.low), 1e-12)
        body_ratio = body/candle_range
        broken = (ref_price > trigger and float(c.volume_ratio) >= BREAKOUT_MIN_VOLUME_RATIO and
                  body_ratio >= BREAKOUT_MIN_BODY_RATIO and float(c.close) > float(c.open))
        trend_ok = ref_price >= float(c.ema20) and float(df1.iloc[-2].close) >= float(df1.iloc[-2].ema20)
        early_trend_ok = trend_ok
        if broken:
            phase = 'BREAKOUT_STARTED'
            entry = ref_price
            stretch_atr = max((ref_price-trigger)/atr, 0.0)
        emerging_trend_ok = (float(c.ema20) >= float(df15.iloc[-6].ema20) and
                              float(df1.iloc[-2].ema20) >= float(df1.iloc[-6].ema20))
        early_trend_ok = trend_ok or (emerging_trend_ok and early_move >= 58.0)
        if 0 <= dist_atr <= EARLY_TRIGGER_MAX_ATR and early_trend_ok:
            phase = 'PRE_BREAKOUT'
            entry = trigger
            stretch_atr = 0.0
        else:
            phase = 'NO_EARLY_SETUP'
            entry = trigger
            stretch_atr = max((ref_price-trigger)/atr, 0.0)
    else:
        trigger = local_sup - 0.10*atr
        dist_atr = (ref_price-local_sup)/atr
        body = abs(float(c.close)-float(c.open)); candle_range=max(float(c.high)-float(c.low), 1e-12)
        body_ratio = body/candle_range
        broken = (ref_price < trigger and float(c.volume_ratio) >= BREAKOUT_MIN_VOLUME_RATIO and
                  body_ratio >= BREAKOUT_MIN_BODY_RATIO and float(c.close) < float(c.open))
        trend_ok = ref_price <= float(c.ema20) and float(df1.iloc[-2].close) <= float(df1.iloc[-2].ema20)
        early_trend_ok = trend_ok
        if broken:
            phase = 'BREAKOUT_STARTED'
            entry = ref_price
            stretch_atr = max((trigger-ref_price)/atr, 0.0)
        emerging_trend_ok = (float(c.ema20) <= float(df15.iloc[-6].ema20) and
                              float(df1.iloc[-2].ema20) <= float(df1.iloc[-6].ema20))
        early_trend_ok = trend_ok or (emerging_trend_ok and early_move >= 58.0)
        if 0 <= dist_atr <= EARLY_TRIGGER_MAX_ATR and early_trend_ok:
            phase = 'PRE_BREAKOUT'
            entry = trigger
            stretch_atr = 0.0
        else:
            phase = 'NO_EARLY_SETUP'
            entry = trigger
            stretch_atr = max((trigger-ref_price)/atr, 0.0)

    # A confirmed breakout followed by a controlled retest is preferred over chasing.
    prev_closed = df15.iloc[-3]
    if phase == 'PRE_BREAKOUT' and len(df15) >= 5:
        # The previous closed candle must have broken the trigger; the latest
        # candle may then retest it and close back in the intended direction.
        prior_break = (float(prev_closed.close) > trigger and float(prev_closed.volume_ratio) >= BREAKOUT_MIN_VOLUME_RATIO) if direction=='LONG' else (float(prev_closed.close) < trigger and float(prev_closed.volume_ratio) >= BREAKOUT_MIN_VOLUME_RATIO)
        retest_touch = ((float(c.low) <= trigger + 0.20*atr) if direction=='LONG' else (float(c.high) >= trigger - 0.20*atr))
        retest_close = (ref_price > trigger and float(c.close) >= float(c.open)) if direction=='LONG' else (ref_price < trigger and float(c.close) <= float(c.open))
        if prior_break and retest_touch and retest_close and float(c.volume_ratio) >= 0.95:
            phase = 'BREAKOUT_RETEST'; entry = ref_price; stretch_atr = abs(ref_price-trigger)/max(atr,1e-12)

    # A breakout that has already run too far is not an entry signal.
    overextended = stretch_atr > EARLY_TRIGGER_MAX_ATR
    stop_mult = clamp(2.0+(0.4 if c.adx<22 else 0)+(0.25 if atr_pct>0.035 else 0),2.0,2.8)
    atr_stop = entry - stop_mult*atr if direction=='LONG' else entry + stop_mult*atr
    # Use a nearby structure invalidation when it is neither too tight nor too wide.
    structural_stop = (local_sup - 0.12*atr) if direction=='LONG' else (local_res + 0.12*atr)
    structural_dist_atr = abs(entry-structural_stop)/atr if atr else 999.0
    if 1.15 <= structural_dist_atr <= 3.0:
        stop = structural_stop
        stop_method = 'yakın yapı + ATR tamponu'
    else:
        stop = atr_stop
        stop_method = 'ATR volatilite stopu'
    risk = abs(entry-stop)

    if direction == 'LONG':
        if res > entry + 0.5*atr:
            target = res; target_method = 'üst yapısal direnç'
        else:
            target = entry + max(2.2*risk, 2.5*atr)
            target_method = 'kırılım sonrası ATR/R projeksiyonu'
        room = (target-entry)/atr
        level_dist = max((local_res-ref_price)/atr, 0.0)
    else:
        if sup < entry - 0.5*atr:
            target = sup; target_method = 'alt yapısal destek'
        else:
            target = entry - max(2.2*risk, 2.5*atr)
            target_method = 'kırılım sonrası ATR/R projeksiyonu'
        room = (entry-target)/atr
        level_dist = max((ref_price-local_sup)/atr, 0.0)

    ema_dist = abs(ref_price-float(c.ema20))/max(atr,1e-12)
    extension_penalty = clamp((ema_dist-0.8)*24,0,40)
    if room<=0: location_score=10.0
    elif room<0.50: location_score=25.0
    elif room<1.00: location_score=45.0
    elif room<1.50: location_score=68.0
    else: location_score=88.0
    if target_method.startswith('kırılım'):
        location_score = min(location_score, 70.0)
    ema_entry_score = clamp(86-extension_penalty + (6 if (direction=='LONG' and ref_price<=c.ema20*1.01) or (direction=='SHORT' and ref_price>=c.ema20*0.99) else 0))
    rr = ((target-entry)/risk if direction=='LONG' else (entry-target)/risk) if risk>0 else np.nan
    # Entry quality explicitly includes payoff quality; a large projected move
    # cannot compensate for a poor stop/target geometry.
    rr_quality = clamp((float(rr)-1.0)/2.0*100.0, 0.0, 100.0) if pd.notna(rr) else 0.0
    entry_quality = clamp(0.40*ema_entry_score + 0.25*location_score + 0.35*rr_quality)
    # IMPORTANT: "early move" and "move potential" are not the same thing.
    # early_move says whether a move is forming now; it must NOT be allowed to
    # masquerade as the expected size of the future move.  The new potential
    # model explicitly asks: "if this setup triggers, how much directional room
    # is realistically available before the next major barrier, relative to ATR?"
    #
    # It combines four independent ideas:
    #   1) structural room on 1h/4h,
    #   2) ATR-normalized room (not raw % alone),
    #   3) expansion conditions (volume/volatility/acceleration),
    #   4) relative strength / higher-timeframe trend.
    #
    # This makes a routine clean 1-3% setup remain valuable, but allows a
    # genuinely open 5-10%+ opportunity to score materially higher BEFORE the
    # move, provided the entry is still executable and not already extended.
    signed = 1.0 if direction == 'LONG' else -1.0
    c1 = df1.iloc[-2]
    c4 = df4.iloc[-2]
    atr1 = max(float(c1.atr), 1e-12)
    atr4 = max(float(c4.atr), 1e-12)
    price1 = max(float(c1.close), 1e-12)
    price4 = max(float(c4.close), 1e-12)

    # Forward structural room: look only at confirmed/closed highs/lows ABOVE
    # the entry for LONG (BELOW for SHORT). The nearest barrier is the first
    # realistic objective; the wider envelope measures whether the move can
    # plausibly expand beyond that objective.
    h1_closed = df1.iloc[:-1] if len(df1) > 1 else df1
    h4_closed = df4.iloc[:-1] if len(df4) > 1 else df4
    if direction == 'LONG':
        barriers1 = [float(v) for v in h1_closed.high if float(v) > entry * 1.002]
        barriers4 = [float(v) for v in h4_closed.high if float(v) > entry * 1.005]
        near_room1 = min(barriers1) - entry if barriers1 else 0.0
        near_room4 = min(barriers4) - entry if barriers4 else 0.0
        far_room1 = max(barriers1) - entry if barriers1 else 0.0
        far_room4 = max(barriers4) - entry if barriers4 else 0.0
    else:
        barriers1 = [float(v) for v in h1_closed.low if float(v) < entry * 0.998]
        barriers4 = [float(v) for v in h4_closed.low if float(v) < entry * 0.995]
        near_room1 = entry - max(barriers1) if barriers1 else 0.0
        near_room4 = entry - max(barriers4) if barriers4 else 0.0
        far_room1 = entry - min(barriers1) if barriers1 else 0.0
        far_room4 = entry - min(barriers4) if barriers4 else 0.0

    near_room = max(near_room1, near_room4)
    far_room = max(far_room1, far_room4)
    near_room_atr = near_room / max(atr1, atr4 * (price1 / price4), 1e-12)
    far_room_atr = far_room / max(atr1, atr4 * (price1 / price4), 1e-12)

    # Do not reward unlimited historical room. Cap the normalized room at a
    # practical range; the score is an opportunity-quality ranking, not a
    # promise that price will travel the whole distance.
    room_score = clamp(25.0 + 16.0 * min(far_room_atr, 5.0), 0.0, 100.0)
    near_room_score = clamp(100.0 - 22.0 * max(near_room_atr - 0.5, 0.0), 35.0, 100.0)
    expansion_score = clamp(
        0.35 * early_move +
        0.25 * clamp((float(c.volume_ratio) - 0.8) / 1.7 * 100.0, 0.0, 100.0) +
        0.20 * clamp(50.0 + ((float(c.atr_pct) / max(float(df15.iloc[-7:-2].atr_pct.median()), 1e-6)) - 1.0) * 55.0, 0.0, 100.0) +
        0.20 * vals['relative_strength']
    )
    htf_trend_score = clamp(
        0.55 * vals['trend'] +
        0.25 * vals['momentum'] +
        0.20 * vals['relative_strength']
    )

    # Realistic directional move area used internally for ranking. It is NOT a
    # guaranteed return and is deliberately separate from TP1/TP2/TP3.
    atr_pct_base = max(float(c.atr_pct), float(c1.atr_pct), float(c4.atr_pct), 1e-6)
    modeled_room_pct = max(
        far_room / max(entry, 1e-12) * 100.0,
        atr_pct_base * min(max(2.5, far_room_atr * 0.65), 7.0)
    )
    # Early formation is still important: a huge historical room without an
    # active catalyst should not become an elite score.
    potential_core = (
        0.42 * room_score +
        0.18 * near_room_score +
        0.25 * expansion_score +
        0.15 * htf_trend_score
    )
    move_potential = clamp(potential_core)
    potential_label = (
        'ÇOK YÜKSEK' if move_potential >= 85 else
        'YÜKSEK' if move_potential >= 70 else
        'ORTA' if move_potential >= 50 else 'SINIRLI'
    )
    rr_bonus = clamp((rr-2.0)*1.5,0,4) if pd.notna(rr) else 0

    # Readiness rewards proximity, own-coin direction and actual move formation,
    # while keeping future-size potential separate from current readiness.
    proximity_score = clamp(100 - max(level_dist,0)/max(EARLY_TRIGGER_MAX_ATR,0.1)*55, 0, 100)
    volume_score = clamp((float(c.volume_ratio)-0.8)*45,0,100)
    readiness = clamp(0.45*proximity_score + 0.30*directional + 0.25*early_move)
    early_bonus = min(3.0, max(0.0, (early_move-60.0)*0.06)) if phase=='PRE_BREAKOUT' else 0.0

    # ONE public score: setup quality + future move potential.  Potential is
    # now materially tied to forward room and expected expansion, rather than
    # being a renamed copy of "how strongly price moved in the last candles".
    direction_clarity = clamp(gap * 6.0, 0.0, 100.0)
    pattern_bonus = 0.05*max(0.0, libra['score']-50.0) + 0.03*max(0.0, sar_score-50.0)

    # Setup-quality side: direction/entry/readiness remain the safety core.
    setup_quality = clamp(
        0.42*directional +
        0.34*entry_quality +
        0.18*readiness +
        0.06*direction_clarity
    )
    # Final score gives future opportunity enough weight to distinguish a
    # routine trade from a genuinely open, expanding move, but it cannot rescue
    # a weak setup because setup_quality remains 58% of the score.
    raw_score = clamp(
        0.58*setup_quality +
        0.42*move_potential +
        pattern_bonus + rr_bonus + early_bonus
    )
    setup_valid = (phase in ('PRE_BREAKOUT','BREAKOUT_STARTED','BREAKOUT_RETEST') and early_trend_ok and
                   not overextended and readiness >= MIN_SETUP_READINESS and gap >= MIN_DIRECTION_GAP)
    elite_gate = (raw_score>=90 and directional>=85 and entry_quality>=80 and move_potential>=85 and pd.notna(rr) and rr>=2.0 and gap>=15 and setup_valid)
    quality = 'ELITE' if elite_gate else 'STRONG' if raw_score>=85 else 'SELECTIVE' if raw_score>=72 else 'WEAK'
    # Outlier flag: candidate has notable relative strength against the BTC/ETH tape.
    rel_side = vals['relative_1h'] * (1 if direction == 'LONG' else -1)
    rel4_side = vals['relative_4h'] * (1 if direction == 'LONG' else -1)
    relative_outlier = (rel_side >= 1.5 or rel4_side >= 3.0)
    potential_label = ('ÇOK YÜKSEK' if move_potential >= 80 else 'YÜKSEK' if move_potential >= 65 else 'ORTA' if move_potential >= 45 else 'SINIRLI')
    return {'direction':direction,'score':round(raw_score,1),'quality':quality,'elite_gate':elite_gate,
            'entry':float(entry),'reference_price':ref_price,'trigger':float(trigger),'phase':phase,
            'setup_valid':bool(setup_valid),'readiness':round(readiness,1),'overextended':bool(overextended),
            'stop':float(stop),'stop_method':stop_method,'wide_stop':float(stop-(atr*WIDE_STOP_BUFFER_ATR) if direction=='LONG' else stop+(atr*WIDE_STOP_BUFFER_ATR)),
            'target':float(target),'target_method':target_method,'rr':float(rr) if pd.notna(rr) else float('nan'),
            'support':sup,'resistance':res,'local_support':local_sup,'local_resistance':local_res,
            'directional_confidence':round(directional,1),'direction_gap':round(gap,1),'long_early_score':round(long_early,1),'short_early_score':round(short_early,1),
            'entry_quality':round(entry_quality,1),'move_potential':round(move_potential,1),'early_move_score':round(early_move,1),
            'modeled_move_pct':round(float(modeled_room_pct),2),
            'forward_room_atr':round(float(far_room_atr),2),
            'near_room_atr':round(float(near_room_atr),2),
            'room_score':round(float(room_score),1),
            'expansion_score':round(float(expansion_score),1),
            'setup_quality':round(float(setup_quality),1),
            'potential_label':potential_label,'relative_strength':round(vals['relative_strength'],1),
            'relative_1h':round(vals['relative_1h'],2),'relative_4h':round(vals['relative_4h'],2),
            'libra_score':float(libra['score']),'libra_match':bool(libra['match']),'libra_fib':float(libra['fib']) if np.isfinite(libra['fib']) else float('nan'),
            'libra_distance_atr':float(libra['distance_atr']) if np.isfinite(libra['distance_atr']) else float('nan'),
            'sar_score':round(float(sar_score),1),'sar_ok':bool(sar_ok),'sar_flip':bool(sar_flip),
            'relative_outlier':bool(relative_outlier),
            'breakout':round(vals['breakout'],1),'atr':atr,'atr_pct':atr_pct}

# ---------------------------------------------------------------------
# TP1/TP2/TP3 -- mesafeye göre sıralı (en yakından en uzağa)
# ---------------------------------------------------------------------
def tp_levels(x):
    entry=float(x['entry']); stop=float(x['stop']); direction=x['direction']; risk=abs(entry-stop)
    if risk<=0: return None
    planned_r = abs(float(x['target'])-entry)/risk
    # Never advertise a take-profit beyond the actual modeled target/structure.
    r_values = [min(1.0, planned_r), min(1.5, planned_r), planned_r]
    labels = ['TP1','TP2','TP3']
    out = {}
    for label, r in zip(labels, r_values):
        price = entry + risk*r if direction=='LONG' else entry-risk*r
        out[label] = {'r': round(r,2), 'price': float(x['target']) if label=='TP3' else price}
    return out

def tier(x):
    # Repeat-alert state uses only the unified score, not hidden component labels.
    s=x['score']
    if s>=90: return 'SKOR 90+'
    if s>=85: return 'SKOR 85+'
    if s>=80: return 'SKOR 80+'
    return None

# Legacy labels are retained only to read existing alert_state.json safely.
TIER_RANK = {
    'SKOR 80+':1, 'SKOR 85+':2, 'SKOR 90+':3,
    '🔵 GİRİLEBİLİR (80-90)':1,
    '🟢 ÇOK GÜÇLÜ (90+)':2,
    '🟢 ELİT (90+, tüm kapılar geçti)':3,
}

def spot_quote_volume(ticker):
    q = ticker.get('quoteVolume')
    if q is None and ticker.get('baseVolume') and ticker.get('last'):
        q = float(ticker['baseVolume']) * float(ticker['last'])
    try: return float(q or 0)
    except (TypeError, ValueError): return 0.0

def spot_early_radar(swap_symbols, market_caps):
    """Spot is a discovery layer; only the matched perpetual setup can alert.

    Return reviewed candidates as well as active momentum flags, so a large 24h
    mover is still sent to perpetual analysis even if its 15m momentum paused.
    """
    try:
        markets = spot_exchange.load_markets()
        tickers = spot_exchange.fetch_tickers()
    except Exception as e:
        print(f'SPOT RADARI VERİ ALAMADI: {e}')
        return []
    stable_bases = {'USDT','USDC','BUSD','TUSD','FDUSD','DAI','USDE','USDD','EUR','USD'}
    pool = []
    for sym, m in markets.items():
        if not (m.get('active') and m.get('spot') and m.get('quote') == 'USDT'):
            continue
        base = str(m.get('base') or sym.split('/')[0]).upper()
        if base in stable_bases:
            continue
        t = tickers.get(sym) or {}
        qv = spot_quote_volume(t)
        if qv < MIN_SPOT_24H_VOLUME:
            continue
        try:
            pct = float(t.get('percentage') or 0.0)
            last = float(t.get('last') or 0.0)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(pct) or not np.isfinite(last) or last <= 0:
            continue
        pool.append({'symbol':sym,'base':base,'quote_volume':qv,'pct24':pct,'last':last})

    # Every spot USDT pair above the spot-liquidity floor is checked; there is
    # no fixed 40-coin cap. A nonzero env override can cap OHLCV work if needed.
    by_move = sorted(pool, key=lambda x: abs(x['pct24']), reverse=True)
    mover_count = max(1, int(np.ceil(len(by_move) * clamp(SPOT_RELATIVE_MOVER_SHARE, 0.05, 1.0)))) if by_move else 0
    relative_mover_symbols = {x['symbol'] for x in by_move[:mover_count]}
    if SPOT_RADAR_MAX_CANDIDATES > 0 and len(pool) > SPOT_RADAR_MAX_CANDIDATES:
        by_volume = sorted(pool, key=lambda x: x['quote_volume'], reverse=True)
        limit = SPOT_RADAR_MAX_CANDIDATES
        move_quota = (limit + 1) // 2
        volume_quota = limit // 2
        selected = {x['symbol']:x for x in by_move[:move_quota] + by_volume[:volume_quota]}
        if len(selected) < limit:
            for item in sorted(pool, key=lambda x: (abs(x['pct24']), x['quote_volume']), reverse=True):
                selected.setdefault(item['symbol'], item)
                if len(selected) >= limit:
                    break
        candidates = list(selected.values())[:limit]
    else:
        candidates = sorted(pool, key=lambda x: (abs(x['pct24']), x['quote_volume']), reverse=True)
    out = []
    for item in candidates:
        sym = item['symbol']
        try:
            rows = spot_exchange.fetch_ohlcv(sym, timeframe='15m', limit=OHLCV_LIMIT)
            raw = _ohlcv_frame(rows)
            if len(raw) < 60:
                print(f'SPOT RADAR VERİ ATLANDI {sym}: yetersiz 15m mum ({len(raw)}; en az 60 gerekli); ticker hareketi keşif için korunuyor.')
                perp_symbol = next((ps for ps in swap_symbols if ps.split('/')[0].upper() == item['base']), None)
                perp_qv = float(SWAP_QUOTE_VOLUMES.get(perp_symbol, 0.0)) if perp_symbol else 0.0
                if sym in relative_mover_symbols:
                    out.append({
                        'symbol':sym, 'base':item['base'],
                        'direction':'YUKARI HAREKET' if item['pct24'] >= 0 else 'AŞAĞI HAREKET',
                        'price':item['last'], 'change_15m':0.0, 'change_1h':0.0,
                        'change_24h':item['pct24'], 'volume_ratio':0.0,
                        'quote_volume':item['quote_volume'],
                        'market_cap_rank':market_cap_rank_for_symbol(sym, market_caps),
                        'market_cap_label':market_cap_label_for_symbol(sym, market_caps),
                        'swap_available':bool(perp_symbol), 'perp_symbol':perp_symbol,
                        'perp_quote_volume':perp_qv, 'breakout':False,
                        'momentum_flag':False, 'discovery_candidate':True,
                        'discovery_reason':'spot göreli 24h hareketli; spot mum geçmişi yetersiz'
                    })
                continue
            d15 = add_indicators(raw)
            if len(d15) < 10:
                continue
            c = d15.iloc[-2]
            prev = d15.iloc[-3]
            old1h = d15.iloc[-6]
            px = float(c.close)
            ch15 = (px/float(prev.close)-1)*100 if prev.close else 0.0
            ch1h = (px/float(old1h.close)-1)*100 if old1h.close else 0.0
            vr = float(c.volume_ratio) if pd.notna(c.volume_ratio) else 0.0
            aligned = (ch15 > 0 and ch1h > 0) or (ch15 < 0 and ch1h < 0)
            steady = abs(ch15) >= 0.8 and abs(ch1h) >= 1.8 and vr >= 1.5
            burst = abs(ch15) >= 1.4 and vr >= 2.0
            momentum_flag = bool(aligned and (steady or burst))
            side = 'YUKARI HAREKET' if ch15 >= 0 else 'AŞAĞI HAREKET'
            local = d15.iloc[-22:-2]
            local_high = float(local.high.max()) if len(local) else px
            local_low = float(local.low.min()) if len(local) else px
            breakout = bool(len(local) and (px > local_high if ch15 >= 0 else px < local_low))
            atr_pct = float(c.atr_pct) if pd.notna(c.atr_pct) else 0.0
            near_band = max(0.005, 0.5 * atr_pct)
            near_high = bool(len(local) and 0 <= (local_high - px) / max(px, 1e-12) <= near_band)
            near_low = bool(len(local) and 0 <= (px - local_low) / max(px, 1e-12) <= near_band)
            near_breakout = bool((near_high or near_low) and vr >= 1.0)
            discovery_flag = bool(
                momentum_flag
                or sym in relative_mover_symbols
                or near_breakout
                or (abs(ch1h) >= MIN_SPOT_DISCOVERY_1H_MOVE and vr >= MIN_SPOT_DISCOVERY_VOLUME_RATIO)
            )
            perp_symbol = next((ps for ps in swap_symbols if ps.split('/')[0].upper() == item['base']), None)
            perp_qv = float(SWAP_QUOTE_VOLUMES.get(perp_symbol, 0.0)) if perp_symbol else 0.0
            out.append({
                'symbol':sym, 'base':item['base'], 'direction':side, 'price':px,
                'change_15m':ch15, 'change_1h':ch1h, 'change_24h':item['pct24'],
                'volume_ratio':vr, 'quote_volume':item['quote_volume'],
                'market_cap_rank':market_cap_rank_for_symbol(sym, market_caps),
                'market_cap_label':market_cap_label_for_symbol(sym, market_caps),
                'swap_available':bool(perp_symbol), 'perp_symbol':perp_symbol,
                'perp_quote_volume':perp_qv, 'breakout':breakout,
                'momentum_flag':momentum_flag, 'discovery_candidate':discovery_flag,
                'near_breakout':near_breakout,
                'discovery_reason':('spot 15m/1h momentum' if momentum_flag else
                                    'spot göreli 24h hareket' if sym in relative_mover_symbols else
                                    'yakın kırılım bölgesi' if near_breakout else
                                    'spot 1h impulse' if abs(ch1h) >= MIN_SPOT_DISCOVERY_1H_MOVE else 'radar only')
            })
        except Exception as e:
            print(f'SPOT RADAR {sym}: {type(e).__name__}: {e}')
        time.sleep(0.05)
    out.sort(key=lambda x: (bool(x.get('discovery_candidate')), abs(x['change_24h']),
                            abs(x['change_1h']) * min(x['volume_ratio'],4)), reverse=True)
    flagged = sum(1 for x in out if x.get('discovery_candidate'))
    cap_label = SPOT_RADAR_MAX_CANDIDATES if SPOT_RADAR_MAX_CANDIDATES > 0 else 'yok'
    print(f'SPOT RADARI: {len(pool)} likit spot USDT çifti bulundu; {len(candidates)} çiftin 15m mumları kontrol edildi (sabit limit={cap_label}); {flagged} vadeli keşif adayı.')
    return out

def format_spot_watch(x):
    swap = 'VAR — vadeli setup ayrıca kontrol edilmeli' if x['swap_available'] else 'YOK — bu piyasada vadeli setup doğrulanamaz'
    stretched = abs(x['change_1h']) >= 7.0 or abs(x['change_24h']) >= 15.0
    caution = '\n⚠️ Hareket çok uzamış olabilir; fiyata atlamayın, geri çekilme/teyit bekleyin.' if stretched else ''
    return (f"👀 SPOT ERKEN UYARI — {x['direction']}\n"
            f"*{x['symbol']}* | Fiyat: {x['price']:.8g}\n"
            f"15 dk: {x['change_15m']:+.2f}% | 1 saat: {x['change_1h']:+.2f}% | 24 saat: {x['change_24h']:+.2f}%\n"
            f"Hacim: {x['volume_ratio']:.2f}x ortalama | 24s spot hacmi: {x['quote_volume']:,.0f} USDT\n"
            f"Market-cap sırası: {x.get('market_cap_label', '#' + str(x['market_cap_rank']))} | MEXC vadeli eşleşmesi: {swap}\n"
            f"Kırılım teyidi: {'EVET' if x['breakout'] else 'henüz yok'}\n"
            f"Bu bir izleme uyarısıdır; tek başına LONG/SHORT işlem sinyali değildir.{caution}")

def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception: return {}
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')

def is_new_or_upgraded(x, t, state):
    key = f"{x['symbol']}:{x['direction']}"
    prev = state.get(key)
    if prev is None: return True
    phase_rank = {'PRE_BREAKOUT': 1, 'BREAKOUT_STARTED': 2, 'BREAKOUT_RETEST': 3}
    return (TIER_RANK.get(t, 0) > TIER_RANK.get(prev.get('tier'), 0)
            or phase_rank.get(x.get('phase'), 0) > phase_rank.get(prev.get('phase'), 0))

def _font(size, bold=False):
    candidates = ([
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    ] if bold else [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
    ])
    for name in candidates:
        if Path(name).exists():
            try: return ImageFont.truetype(name, size=size)
            except Exception: pass
    return ImageFont.load_default()


def _price(value):
    return f'{float(value):.8g}'


def _validate_plan_geometry(x):
    """Reject inverted/implausibly wide plans before considering a live alert."""
    entry, stop, target = float(x['entry']), float(x['stop']), float(x['target'])
    if not all(np.isfinite(v) and v > 0 for v in (entry, stop, target)):
        return False, 'Giriş/stop/hedef fiyatı geçersiz'
    if x['direction'] == 'LONG':
        if not stop < entry < target:
            return False, 'LONG stop/hedef geometrisi ters'
        stop_pct = (entry - stop) / entry
        rr = (target - entry) / (entry - stop)
    else:
        if not target < entry < stop:
            return False, 'SHORT stop/hedef geometrisi ters'
        stop_pct = (stop - entry) / entry
        rr = (entry - target) / (stop - entry)
    if stop_pct > MAX_STOP_DISTANCE_PCT:
        return False, f'Stop mesafesi çok geniş (%{stop_pct*100:.2f})'
    if not np.isfinite(rr) or rr < MIN_CONFIRMED_RR:
        return False, f'Hedef/stop oranı yetersiz ({rr:.2f})'
    return True, 'OK'


def refresh_live_price_and_validate(x):
    """Refresh price and reject pending triggers already crossed or stale breakouts."""
    try:
        ticker = exchange.fetch_ticker(x['symbol'])
        live = ticker.get('last')
        if live is None or not np.isfinite(float(live)) or float(live) <= 0:
            bid, ask = ticker.get('bid'), ticker.get('ask')
            if bid and ask: live = (float(bid) + float(ask)) / 2.0
        live = float(live)
        if not np.isfinite(live) or live <= 0:
            return False, 'Canlı fiyat alınamadı'
    except Exception as exc:
        return False, f'Canlı fiyat kontrolü başarısız: {type(exc).__name__}'

    entry = float(x['entry'])
    trigger = float(x.get('trigger', entry))
    atr = max(float(x.get('atr', 0.0) or 0.0), entry * 0.0001)
    zone_pad = max(0.15 * atr, entry * 0.0010)
    # Pending alerts are only useful before the trigger is crossed; wait for the
    # next closed 15m candle rather than labeling an intrabar cross as pending.
    if x.get('phase') == 'PRE_BREAKOUT':
        if x['direction'] == 'LONG' and live >= trigger:
            return False, 'LONG tetik seviyesi canlı fiyatta aşıldı; 15 dk kapanış teyidi beklenmeli'
        if x['direction'] == 'SHORT' and live <= trigger:
            return False, 'SHORT tetik seviyesi canlı fiyatta aşıldı; 15 dk kapanış teyidi beklenmeli'
        # Do not send a pending setup if it has moved materially away from the level.
        if abs(live-trigger) > max(EARLY_TRIGGER_MAX_ATR * atr, entry * 0.008):
            return False, 'Fiyat tetik seviyesinden fazla uzak; kurulum güncelliğini yitirdi'
    else:
        # Confirmed breakout/retest: entry must remain close to the trigger and not chase.
        if x.get('phase') == 'BREAKOUT_RETEST' and abs(live-trigger) > max(RETEST_MAX_ATR*atr, entry*0.004):
            return False, 'Retest fiyatı tetik bölgesinden fazla uzak'
        # Confirmed breakout: entry must remain within the displayed entry zone.
        if abs(live-entry) > zone_pad:
            return False, f'Kırılım fiyatı giriş bölgesinden çıktı (son={_price(live)}, giriş={_price(entry)})'
        # If the live price falls back through the trigger, the breakout failed before alert.
        if x['direction'] == 'LONG' and live < trigger - 0.10*atr:
            return False, 'LONG kırılımı canlı fiyatta geri verildi'
        if x['direction'] == 'SHORT' and live > trigger + 0.10*atr:
            return False, 'SHORT kırılımı canlı fiyatta geri verildi'

    x['live_price'] = live
    x['zone_pad'] = zone_pad
    raw = ((live / trigger) - 1.0) * 100.0 if trigger > 0 else 0.0
    x['level_progress'] = raw if x['direction'] == 'LONG' else -raw
    x['live_price_checked_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return True, 'OK'


def render_signal_card(x, ctx):
    """Render a compact, clean green LONG / red SHORT Telegram signal card."""
    W, H = 1000, 1200
    bg = (10, 15, 25); panel = (19, 29, 43); panel2 = (23, 37, 53)
    white = (239, 244, 250); muted = (166, 181, 198); border = (43, 59, 77)
    green = (24, 190, 111); green_bg = (10, 68, 49)
    red = (242, 76, 91); red_bg = (82, 25, 38)
    blue = (92, 165, 255); yellow = (246, 190, 70)
    accent = green if x['direction'] == 'LONG' else red
    accent_bg = green_bg if x['direction'] == 'LONG' else red_bg
    im = Image.new('RGB', (W, H), bg); d = ImageDraw.Draw(im)
    f_title = _font(43, True); f_sub = _font(22); f_section = _font(26, True)
    f_label = _font(22); f_value = _font(29, True); f_big = _font(50, True)
    f_small = _font(19); f_badge = _font(25, True)
    m = 36
    def card(box, fill=panel, outline=border, radius=22, width=2):
        d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    def txt(pos, text, font, fill=white, anchor=None):
        d.text(pos, str(text), font=font, fill=fill, anchor=anchor)
    def section(y, label):
        txt((m+2, y), label, f_section, white)
    def trend_color(v):
        return green if v == 'BULLISH' else red if v == 'BEARISH' else muted

    # Header: symbol and direction only; no explanatory paragraph.
    card((m, 24, W-m, 132), fill=panel, outline=accent, radius=25, width=4)
    txt((m+26, 43), x['symbol'], f_title)
    badge_box = (W-250, 43, W-m-20, 100)
    d.rounded_rectangle(badge_box, radius=18, fill=accent_bg, outline=accent, width=2)
    txt(((badge_box[0]+badge_box[2])//2, 71), ('↗  LONG' if x['direction'] == 'LONG' else '↘  SHORT'), f_badge, accent, anchor='mm')
    txt((m+28, 96), f"Fiyat: {_price(x['live_price'])}", f_sub, muted)

    # BTC / ETH context
    section(154, 'BTC / ETH YÖNÜ')
    col_gap = 18; cw = (W-2*m-col_gap)//2; top = 195; ch = 135
    for i, name in enumerate(('btc', 'eth')):
        xx = m + i*(cw+col_gap)
        card((xx, top, xx+cw, top+ch), fill=panel2, radius=18)
        ctx_item = ctx[name]
        txt((xx+22, top+14), name.upper(), f_label, muted)
        for j, tf in enumerate(('1h','4h')):
            yy = top+52+j*38
            val = ctx_item.get(tf, 'NEUTRAL')
            txt((xx+22, yy), tf.upper(), f_small, muted)
            txt((xx+115, yy-4), val, f_value, trend_color(val))

    # One holistic score + exact trigger distance; percentage is not presented as win probability.
    section(354, 'ANALİZ SKORU')
    card((m, 392, W-m, 520), fill=panel, radius=20)
    txt((m+24, 410), f"{float(x['score']):.1f}/100", f_big, accent)
    status = 'KIRILIM TEYİTLİ' if x['phase'] == 'BREAKOUT_STARTED' else 'TETİK BEKLENİYOR'
    status_color = accent if x['phase'] == 'BREAKOUT_STARTED' else yellow
    txt((m+390, 412), status, _font(23, True), status_color)
    prog = float(x.get('level_progress', 0.0))
    txt((m+390, 463), f'Retest / seviye farkı: {prog:+.2f}%', f_sub, white)
    txt((m+24, 475), f"Libra: {'VAR' if x.get('libra_match') else 'YOK'}  |  SAR: {'ONAY' if x.get('sar_ok') else 'ZAYIF'}", f_small, muted)

    # Entry / stop levels
    section(542, 'GİRİŞ PLANI')
    card((m, 580, W-m, 838), fill=panel, radius=20)
    entry = float(x['entry']); live = float(x['live_price']); pad = float(x['zone_pad'])
    stop = float(x['stop'])
    stop_pct = ((entry-stop)/entry*100.0) if x['direction']=='LONG' else ((stop-entry)/entry*100.0)
    rows = [
        ('İDEAL GİRİŞ', _price(entry), white),
        ('GİRİŞ BÖLGESİ', f'{_price(max(entry-pad, 1e-12))} – {_price(entry+pad)}', white),
        ('STOP-LOSS', f'{_price(stop)}  ({stop_pct:.2f}%)', red),
    ]
    for i,(lab,val,col) in enumerate(rows):
        yy = 602 + i*78
        if i: d.line((m+22, yy-9, W-m-22, yy-9), fill=border, width=1)
        txt((m+24, yy+8), lab, f_label, muted)
        txt((W-m-24, yy+7), val, _font(25 if len(val)<25 else 21, True), col, anchor='ra')

    # Targets
    section(862, 'KÂR HEDEFLERİ')
    card((m, 900, W-m, 1168), fill=panel, radius=20)
    tps = tp_levels(x)
    for i, lab in enumerate(('TP1','TP2','TP3')):
        yy = 920 + i*78
        if i: d.line((m+22, yy-8, W-m-22, yy-8), fill=border, width=1)
        d.rounded_rectangle((m+22, yy+1, m+122, yy+52), radius=12, fill=accent_bg, outline=accent, width=1)
        txt((m+72, yy+26), lab, f_badge, accent, anchor='mm')
        px = float(tps[lab]['price'])
        move = ((px/entry)-1.0)*100.0
        txt((W-m-25, yy+8), _price(px), f_value, white, anchor='ra')
        txt((W-m-25, yy+42), f'{move:+.2f}%', f_small, accent, anchor='ra')
    out = BytesIO(); im.save(out, format='PNG', optimize=True); out.seek(0)
    return out


def send_telegram_card(x, ctx):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('TELEGRAM AYARLI DEĞİL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret olarak ekleyin.')
        return False
    image = None
    try:
        image = render_signal_card(x, ctx)
        caption = f"{'🟢' if x['direction']=='LONG' else '🔴'} {x['symbol']} · {x['direction']} · {x['score']:.1f}/100"
        r = requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto',
            data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
            files={'photo': ('signal.png', image.getvalue(), 'image/png')}, timeout=25)
        try: body = r.json()
        except Exception: body = {}
        if r.status_code != 200 or not body.get('ok', False):
            print(f"TELEGRAM GÖNDERİM HATASI: {r.status_code} {str(body or r.text)[:240]}")
            return False
        print(f"Telegram görsel bildirimi gönderildi: {x['symbol']} {x['direction']}")
        return True
    except Exception as exc:
        print(f'TELEGRAM GÖNDERİM HATASI: {type(exc).__name__}: {str(exc)[:180]}')
        return False
    finally:
        if image:
            image.close()


def load_outcomes():
    if not OUTCOME_FILE.exists(): return []
    try:
        data = json.loads(OUTCOME_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def update_signal_outcomes():
    """Measure first target/stop touch using closed 15m candles; stop wins same-candle ties."""
    records = load_outcomes()
    now_ms = int(time.time() * 1000)
    changed = False
    # Rotate through open signals so outcome tracking cannot overwhelm the scan.
    open_records = sorted(
        (r for r in records if r.get('status') == 'OPEN'),
        key=lambda r: int(r.get('last_checked_ms', 0))
    )[:25]
    for rec in open_records:
        rec['last_checked_ms'] = now_ms
        changed = True
        age_ms = now_ms - int(rec.get('signal_ts_ms', now_ms))
        if age_ms > 72 * 60 * 60 * 1000:
            rec['status'] = 'EXPIRED'; rec['resolved_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            changed = True; continue
        try:
            df = candles(rec['symbol'], '15m')
        except Exception as exc:
            print(f"SONUÇ TAKİBİ ATLANDI {rec.get('symbol')}: {type(exc).__name__}")
            continue
        closed = df.iloc[:-1] if len(df) > 1 else df.iloc[0:0]
        after = closed[closed['timestamp'].astype('int64') > int(rec.get('signal_ts_ms', 0))]
        for _, candle in after.iterrows():
            high, low = float(candle.high), float(candle.low)
            stop = float(rec['stop']); direction = rec['direction']
            entry = float(rec.get('entry', 0.0))
            # Older records predate activation tracking; preserve their historical
            # interpretation. New PRE_BREAKOUT alerts must touch entry first.
            activated = bool(rec.get('activated', True))
            if not activated:
                close_price = float(candle.close)
                entry_confirmed = (close_price >= entry) if direction == 'LONG' else (close_price <= entry)
                if not entry_confirmed:
                    continue
                rec['activated'] = True
                rec['activated_at_ms'] = int(candle.timestamp)
                changed = True
            targets = [float(rec[k]) for k in ('tp1','tp2','tp3')]
            stop_hit = (low <= stop) if direction == 'LONG' else (high >= stop)
            hit = [i+1 for i,t in enumerate(targets) if (high >= t if direction == 'LONG' else low <= t)]
            # OHLC candles cannot reveal the intrabar path; count stop-first if
            # stop and any target were touched in the same candle.
            if stop_hit and hit:
                rec['status'] = 'STOP_SAME_CANDLE'; rec['resolved_at_ms'] = int(candle.timestamp); changed = True; break
            if stop_hit:
                rec['status'] = 'STOP'; rec['resolved_at_ms'] = int(candle.timestamp); changed = True; break
            if hit:
                rec['status'] = f'TP{max(hit)}'; rec['resolved_at_ms'] = int(candle.timestamp); changed = True; break
    if changed:
        OUTCOME_FILE.write_text(json.dumps(records[-500:], ensure_ascii=False, indent=2), encoding='utf-8')
    counts = {}
    for rec in records:
        counts[rec.get('status','?')] = counts.get(rec.get('status','?'), 0) + 1
    if records:
        print('SİNYAL SONUÇ TAKİBİ: ' + ', '.join(f'{k}={v}' for k,v in sorted(counts.items())))
    return records


def record_sent_signal(x):
    """Persist a sent signal for later outcome measurement."""
    records = load_outcomes()
    tps = tp_levels(x)
    if not tps: return
    records.append({
        'symbol': x['symbol'], 'direction': x['direction'], 'score': float(x['score']),
        'entry': float(x['entry']), 'stop': float(x['stop']),
        'tp1': float(tps['TP1']['price']), 'tp2': float(tps['TP2']['price']), 'tp3': float(tps['TP3']['price']),
        'phase': x.get('phase', 'BREAKOUT_STARTED'),
        'trigger': float(x.get('trigger', x['entry'])),
        'activated': x.get('phase', 'BREAKOUT_STARTED') != 'PRE_BREAKOUT',
        'signal_ts_ms': int(time.time()*1000), 'status': 'OPEN'
    })
    OUTCOME_FILE.write_text(json.dumps(records[-500:], ensure_ascii=False, indent=2), encoding='utf-8')


def write_diagnostic(all_results, ctx, spot_results=None, error_counts=None, source='live', missed_candidates=None, data_skips=None):
    """Keep a bounded log of top perp candidates, spot watch candidates and gate reasons."""
    stamp = datetime.now().isoformat(timespec='seconds')
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:10]
    best = f"{top[0]['symbol']} {top[0]['score']:.1f}" if top else 'n/a'
    lines = [f"=== {stamp} | BTC4h={ctx['btc']['4h']} BTC1h={ctx['btc']['1h']} "
             f"ETH4h={ctx['eth']['4h']} ETH1h={ctx['eth']['1h']} | "
             f"perp_taranan={len(all_results)} | en_iyi={best} | marketcap={source} ==="]
    for r in top:
        lines.append(f"PERP {r['symbol']:20} {r['direction']:5} phase={r.get('phase','?'):16} score={r['score']:5.1f} early={r.get('early_move_score',0):4.1f} rr_internal={r['rr']:.2f} rel1h={r.get('relative_1h',0):+.2f} rel4h={r.get('relative_4h',0):+.2f} mc=#{r.get('market_cap_rank',501):4d} gate={r.get('alert_threshold',90):.0f} valid={r.get('setup_valid',False)} qv={r.get('quote_volume_24h',0):.0f} reject={r.get('gate_reason','')}")
    for r in (spot_results or [])[:10]:
        lines.append(f"SPOT {r['symbol']:20} {r['direction']:15} 15m={r['change_15m']:+.2f}% 1h={r['change_1h']:+.2f}% 24h={r['change_24h']:+.2f}% vol={r['volume_ratio']:.2f}x swap={r['swap_available']} discovery={r.get('discovery_candidate',False)}")
    for r in (missed_candidates or [])[:20]:
        lines.append(f"FIRSAT_KONTROL {r.get('symbol','?'):20} 24h={r.get('change_24h',0):+.2f}% perp_qv={r.get('perp_quote_volume',0):.0f} spot_qv={r.get('spot_quote_volume',0):.0f} score={r.get('score','n/a')} faz={r.get('phase','n/a')} neden={r.get('reason','')}")
    if data_skips:
        lines.append('DATA_SKIPS ' + json.dumps(data_skips, ensure_ascii=False, sort_keys=True))
    if error_counts:
        lines.append('ERRORS ' + json.dumps(error_counts, ensure_ascii=False, sort_keys=True))
    existing = DIAG_LOG.read_text(encoding='utf-8').splitlines() if DIAG_LOG.exists() else []
    DIAG_LOG.write_text('\n'.join((existing + lines)[-DIAG_LOG_MAX_LINES:]) + '\n', encoding='utf-8')


def collect_missed_candidates(all_symbols, analyzed_symbols, all_results, spot_results):
    """Record large movers that were not alerted, including scan/liquidity exclusions."""
    result_by_symbol = {r.get('symbol'): r for r in all_results}
    spot_by_base = {str(r.get('base','')).upper(): r for r in (spot_results or [])}
    rows = []
    for sym in all_symbols:
        pct = float(SWAP_PERCENTAGES.get(sym, 0.0) or 0.0)
        qv = float(SWAP_QUOTE_VOLUMES.get(sym, 0.0) or 0.0)
        base = sym.split('/')[0].upper()
        spot = spot_by_base.get(base)
        spot_qv = float(spot.get('quote_volume', 0.0)) if spot else 0.0
        r = result_by_symbol.get(sym)
        # Focus the log on meaningful movers, not every low-volume market.
        notable = abs(pct) >= 5.0
        if not notable:
            continue
        if r:
            reason = r.get('gate_reason', 'analiz edildi; sinyal gönderilmedi')
            if reason == 'OK':
                reason = 'uygun plan bulunduysa canlı fiyat/tekrar alarm filtresi ayrıca kontrol edilir'
            rows.append({'symbol':sym,'change_24h':pct,'perp_quote_volume':qv,
                         'spot_quote_volume':spot_qv,'score':r.get('score'),'phase':r.get('phase'),
                         'reason':reason})
        elif sym not in analyzed_symbols:
            if spot and spot.get('discovery_candidate') and qv < MIN_SPOT_DISCOVERY_PERP_VOLUME:
                reason = f'spot hareketli fakat vadeli hacim güvenlik tabanının altında (<{MIN_SPOT_DISCOVERY_PERP_VOLUME:.0f})'
            elif qv < MIN_ALERT_24H_VOLUME:
                reason = 'detaylı tarama dışında: normal vadeli hacim eşiğinin altında ve spot radar önceliği yok'
            else:
                reason = 'detaylı analiz listesine girmedi; evren/seçim kontrolü gerekli'
            rows.append({'symbol':sym,'change_24h':pct,'perp_quote_volume':qv,
                         'spot_quote_volume':spot_qv,'score':'analiz yok','phase':'n/a','reason':reason})
    # Also retain major spot movers even when the perpetual ticker's reported
    # 24h percentage is missing/different; this is the explicit missed-opportunity audit.
    seen = {r.get('symbol') for r in rows}
    for spot in (spot_results or []):
        spot_pct = float(spot.get('change_24h', 0.0) or 0.0)
        if abs(spot_pct) < 5.0 and not spot.get('discovery_candidate'):
            continue
        ps = spot.get('perp_symbol')
        if not ps or ps in seen:
            continue
        perp_qv = float(SWAP_QUOTE_VOLUMES.get(ps, 0.0) or 0.0)
        result = result_by_symbol.get(ps)
        if result:
            reason = result.get('gate_reason', 'analiz edildi; sinyal gönderilmedi')
            score = result.get('score')
            phase = result.get('phase')
        elif perp_qv < MIN_SPOT_DISCOVERY_PERP_VOLUME:
            reason = f'spot büyük hareket yaptı; vadeli hacim güvenlik tabanının altında (<{MIN_SPOT_DISCOVERY_PERP_VOLUME:.0f})'
            score, phase = 'analiz yok', 'n/a'
        elif not spot.get('discovery_candidate'):
            reason = 'spot 24s hareketi görüldü ancak erken-momentum keşif koşulları oluşmadı'
            score, phase = 'analiz yok', 'radar koşulu yok'
        else:
            reason = 'spot adayı vadeli derin tarama listesinde; sonuç kontrol edilmeli'
            score, phase = 'analiz yok', 'n/a'
        rows.append({'symbol':ps,'change_24h':spot_pct,'perp_quote_volume':perp_qv,
                     'spot_quote_volume':float(spot.get('quote_volume',0.0) or 0.0),
                     'score':score,'phase':phase,'reason':reason})
        seen.add(ps)
    rows.sort(key=lambda r: (abs(float(r.get('change_24h',0))), float(r.get('perp_quote_volume',0))), reverse=True)
    return rows[:30]


def main():
    print('=== ALTCOIN ALERT SCANNER V13.4 — QUALITY + MOVE POTENTIAL + LIBRA + SAR + RETEST ===')
    ctx = market_context()
    print(f"BTC 4h={ctx['btc']['4h']} 1h={ctx['btc']['1h']} | ETH 4h={ctx['eth']['4h']} 1h={ctx['eth']['1h']}")
    all_symbols = list(dict.fromkeys(liquid_usdt_swaps()))
    state = load_state(); new_state = {}; qualifying = []; all_results = []; error_counts = {}; data_skips = {}
    market_caps, market_cap_updated_at, market_cap_source = market_cap_rankings()
    print(f'Market-cap kaynağı: {market_cap_source} | eşik: 1-500=>80+, 501-800=>85+, 801+ veya bilinmiyor=>90+')

    # Spot discovers unusual movers first; the corresponding perpetual contract
    # is then analyzed with futures candles and futures prices, never spot prices.
    spot_results = spot_early_radar(set(all_symbols), market_caps)
    regular_symbols = select_deep_scan_symbols(all_symbols)
    spot_discovery_symbols = set()
    spot_discovery_by_perp = {}
    for sr in spot_results:
        ps = sr.get('perp_symbol')
        if not ps or not sr.get('discovery_candidate'):
            continue
        perp_qv = float(SWAP_QUOTE_VOLUMES.get(ps, 0.0))
        if float(sr.get('quote_volume', 0.0)) < MIN_SPOT_24H_VOLUME:
            continue
        if perp_qv < MIN_SPOT_DISCOVERY_PERP_VOLUME:
            continue
        spot_discovery_symbols.add(ps)
        spot_discovery_by_perp[ps] = sr
    symbols = list(dict.fromkeys(regular_symbols + [s for s in all_symbols if s in spot_discovery_symbols]))
    eligible_count = len(symbols)
    extra_count = len(set(symbols) - set(regular_symbols))
    print(f'MEXC aktif USDT vadeli evreni: {len(all_symbols)} benzersiz sözleşme ticker/fiyat-hacim aşamasında tarandı.')
    print(f'Derin mum analizi: {eligible_count} sözleşme; normal vadeli hacim eşiği {MIN_ALERT_24H_VOLUME:,.0f} USDT; spot radarıyla eklenen {extra_count} aday (spot hacim >= {MIN_SPOT_24H_VOLUME:,.0f}, vadeli hacim >= {MIN_SPOT_DISCOVERY_PERP_VOLUME:,.0f} USDT; sabit %6 hareket şartı yok).')

    def analyze_symbol(s):
        try:
            x = score_setup(candles(s,'4h'), candles(s,'1h'), candles(s,'15m'), ctx, None)
            x['symbol'] = s
            return ('OK', x, None)
        except InsufficientCandleDataError as e:
            return ('DATA', s, str(e))
        except Exception as e:
            return ('ERR', s, f'{type(e).__name__}: {e}')

    def accept_result(x):
        s = x['symbol']
        x['market_cap_rank'] = market_cap_rank_for_symbol(s, market_caps)
        x['market_cap_label'] = market_cap_label_for_symbol(s, market_caps)
        x['quote_volume_24h'] = SWAP_QUOTE_VOLUMES.get(s, 0.0)
        x['spot_discovered'] = s in spot_discovery_symbols
        x['spot_discovery_change_24h'] = float(spot_discovery_by_perp.get(s, {}).get('change_24h', 0.0))
        x['alert_eligible'], x['alert_threshold'] = alert_gate_for_rank(x['score'], x['market_cap_rank'])
        all_results.append(x)
        rr_ok = pd.notna(x['rr']) and x['rr'] >= MIN_CONFIRMED_RR
        setup_ok = bool(x.get('setup_valid'))
        if x['spot_discovered']:
            spot_qv_ok = float(spot_discovery_by_perp[s].get('quote_volume', 0.0)) >= MIN_SPOT_24H_VOLUME
            liquidity_ok = spot_qv_ok and x['quote_volume_24h'] >= MIN_SPOT_DISCOVERY_PERP_VOLUME
        else:
            liquidity_ok = x['quote_volume_24h'] >= MIN_ALERT_24H_VOLUME
        x['gate_reason'] = ('OK' if x['alert_eligible'] and rr_ok and setup_ok and liquidity_ok else
                            (f'SPOT KEŞİF LİKİDİTE FİLTRESİ (spot>={MIN_SPOT_24H_VOLUME:,.0f}, vadeli>={MIN_SPOT_DISCOVERY_PERP_VOLUME:,.0f})' if x.get('spot_discovered') else f'LIKIDITE<{MIN_ALERT_24H_VOLUME:,.0f} USDT') if not liquidity_ok else
                            'ERKEN KIRILIM/TEYİT/YÖN NETLİĞİ' if not setup_ok else
                            'SKOR/EŞİK' if not x['alert_eligible'] else
                            'HEDEF/STOP GEOMETRİSİ UYGUN DEĞİL')
        if x['alert_eligible'] and rr_ok and setup_ok and liquidity_ok:
            t = tier(x)
            key = f"{s}:{x['direction']}"
            new_state[key] = {'tier':t,'score':x['score'],'phase':x['phase'],
                              'market_cap_rank':x['market_cap_rank'],'alert_threshold':x['alert_threshold'],
                              'last_seen':int(time.time())}
            if is_new_or_upgraded(x, t, state): qualifying.append((x,t))

    # BTC/ETH context is calculated before the parallel futures scan.
    for base, key in (('BTC/USDT:USDT','btc'), ('ETH/USDT:USDT','eth')):
        try:
            status, item, err = analyze_symbol(base)
            if status == 'OK':
                item['symbol'] = base
                # Keep BTC/ETH diagnostic context but do not let them become trade alerts.
                item['market_cap_rank'] = market_cap_rank_for_symbol(base, market_caps)
                item['market_cap_label'] = market_cap_label_for_symbol(base, market_caps)
                item['quote_volume_24h'] = SWAP_QUOTE_VOLUMES.get(base, 0.0)
                item['alert_eligible'] = False; item['alert_threshold'] = 999
                item['gate_reason'] = 'PİYASA BAĞLAMI'
                all_results.append(item)
            else:
                print(f'VERİ/ANALİZ HATASI {base}: {err}')
        except Exception as e:
            print(f'VERİ/ANALİZ HATASI {base}: {type(e).__name__}: {e}')

    symbols = fast_priority_symbols(symbols, spot_discovery_symbols)
    print(f'Paralel derin analiz: {SCAN_WORKERS} worker; tam evren={len(symbols)} sözleşme; ticker keşfi korunuyor.')
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for i, (status, item, err) in enumerate(pool.map(analyze_symbol, symbols), 1):
            if status == 'OK':
                accept_result(item)
            elif status == 'DATA':
                data_skips['YETERSIZ_MUM'] = data_skips.get('YETERSIZ_MUM', 0) + 1
                print(f'VERİ ATLANDI {item}: {err}; bu coin bu turda puanlanmadı, diğer tarama sürüyor.')
            else:
                name = str(err).split(':',1)[0]
                error_counts[name] = error_counts.get(name, 0) + 1
                print(f'VERİ/ANALİZ HATASI {item}: {err}')
            if i % 25 == 0: print(f'  ... {i}/{len(symbols)} vadeli coin tarandı')

    analyzed_symbol_set = {r.get('symbol') for r in all_results}
    successful_deep = len(analyzed_symbol_set - {'BTC/USDT:USDT', 'ETH/USDT:USDT'})
    failed_deep = len(symbols) - successful_deep
    print(
        f'Derin analiz kapsam özeti: {successful_deep}/{len(symbols)} başarılı; '
        f'{failed_deep} sözleşmede kalıcı veri/analiz hatası; '
        f'geçici rate-limit hataları otomatik yeniden denendi.'
    )
    missed_candidates = collect_missed_candidates(all_symbols, analyzed_symbol_set, all_results, spot_results)
    write_diagnostic(all_results, ctx, spot_results, error_counts, market_cap_source, missed_candidates, data_skips)
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:8]
    if top:
        print('En iyi vadeli adaylar: ' + ' | '.join(
            f"{x['symbol']} {x['direction']} BÜTÜNSEL_SKOR={x['score']:.1f} eşik={x['alert_threshold']:.0f} faz={x['phase']} valid={x['setup_valid']} neden={x.get('gate_reason','')}"
            for x in top))
    if spot_results:
        print('Spot radar (tanı amaçlı; Telegram izleme spamı gönderilmez): ' + ', '.join(
            f"{x['symbol']} {x['direction']} 15m={x['change_15m']:+.1f}% 1h={x['change_1h']:+.1f}%"
            for x in spot_results[:8]))

    # Resolve prior signals from closed 15m candles before adding this scan's alerts.
    update_signal_outcomes()

    # Re-check plan geometry and live price immediately before sending; do not chase stale breakouts.
    sendable = []
    for x, t in sorted(qualifying, key=lambda p: p[0]['score'], reverse=True):
        geometry_ok, geometry_reason = _validate_plan_geometry(x)
        if not geometry_ok:
            print(f"SİNYAL ELENDİ {x['symbol']} {x['direction']}: {geometry_reason}")
            key = f"{x['symbol']}:{x['direction']}"
            if key in state: new_state[key] = state[key]
            else: new_state.pop(key, None)
            continue
        live_ok, live_reason = refresh_live_price_and_validate(x)
        if not live_ok:
            print(f"SİNYAL ELENDİ {x['symbol']} {x['direction']}: {live_reason}")
            key = f"{x['symbol']}:{x['direction']}"
            if key in state: new_state[key] = state[key]
            else: new_state.pop(key, None)
            continue
        sendable.append((x,t))

    if sendable:
        for x, _t in sendable:
            send_ok = send_telegram_card(x, ctx)
            key = f"{x['symbol']}:{x['direction']}"
            if send_ok:
                record_sent_signal(x)
            else:
                print(f"Telegram bildirimi gönderilemedi; tekrar denenebilmesi için alarm durumu geri alınıyor: {x['symbol']}")
                if key in state: new_state[key] = state[key]
                else: new_state.pop(key, None)
    else:
        print('Bu turda gönderilebilir yeni plan yok. Uygun kurulum çıkmaması normaldir; elenen planlar için yukarıdaki SİNYAL ELENDİ nedenlerine bakın. Bildirim gönderilmedi.')

    save_state(new_state)

if __name__ == '__main__':
    main()
