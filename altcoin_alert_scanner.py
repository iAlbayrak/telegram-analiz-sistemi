"""
V9 tek bütünsel skor + güvenilir veri yedeği + kısa ve anlaşılır sinyal -- BULUTTA çalışır (GitHub Actions),
telefondaki/bilgisayardaki hiçbir şeye bağımlı değil. Mevcut MEXC trading
bot'unuzdan TAMAMEN bağımsızdır -- hiçbir dosyasını içe aktarmaz, hiçbir
emir göndermez. Sadece MEXC'nin herkese açık (public) piyasa verisini
okur ve Telegram'a bildirim gönderir. API anahtarı / hesap bilgisi
gerektirmez -- sadece TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID.

--- V9: tek bütünsel skor, daha dayanıklı mum yedeği ve sade Telegram planı ---

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
import os, json, time
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
SPOT_RADAR_MAX_CANDIDATES = int(os.getenv('SPOT_RADAR_MAX_CANDIDATES', '12'))
SPOT_WATCH_COOLDOWN_HOURS = float(os.getenv('SPOT_WATCH_COOLDOWN_HOURS', '6'))
MIN_CONFIRMED_RR = float(os.getenv('MIN_CONFIRMED_RR', '1.8'))
MIN_DIRECTION_GAP = float(os.getenv('MIN_DIRECTION_GAP', '5'))  # yalnızca çok kararsız yönleri engelleyen emniyet tabanı
ALERT_MIN_SCORE      = float(os.getenv('ALERT_MIN_SCORE', '80'))   # base technical score; rank tiers decide the actual alert gate
MARKET_CAP_CACHE_FILE = Path(os.getenv('MARKET_CAP_CACHE_FILE', 'market_cap_cache.json'))
MARKET_CAP_REFRESH_MINUTES = int(os.getenv('MARKET_CAP_REFRESH_MINUTES', '360'))
MARKET_CAP_TOP_N = 500
EARLY_TRIGGER_MAX_ATR = float(os.getenv('EARLY_TRIGGER_MAX_ATR', '0.85'))
MIN_SETUP_READINESS = float(os.getenv('MIN_SETUP_READINESS', '65'))
SLEEP_BETWEEN_COINS  = float(os.getenv('SLEEP_BETWEEN_COINS', '0.02'))
CANDLE_CACHE = {}  # aynı çalıştırmada aynı sembol/zaman dilimi ikinci kez istenmez
STATE_FILE           = Path(os.getenv('STATE_FILE', 'alert_state.json'))
OUTCOME_FILE         = Path(os.getenv('OUTCOME_FILE', 'signal_outcomes.json'))
MAX_STOP_DISTANCE_PCT = float(os.getenv('MAX_STOP_DISTANCE_PCT', '0.08'))
DIAG_LOG             = Path(os.getenv('DIAG_LOG_FILE', 'scan_diagnostic.log'))
DIAG_LOG_MAX_LINES   = 500
WIDE_STOP_BUFFER_ATR = float(os.getenv('WIDE_STOP_BUFFER_ATR', '0.6'))  # ek "geniş stop" ATR payı
OHLCV_LIMIT = 220
PREFERRED_HISTORY_BARS = 60

exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
spot_exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(float(v), hi))

# ---------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------
def add_indicators(df):
    df = df.copy()
    if df.empty or len(df) < 25:
        raise ValueError(f'Yetersiz mum verisi: {len(df)}')
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

def candles(symbol, timeframe):
    """Fetch each symbol/timeframe once. If history is short, try a lower-TF resample."""
    key = (symbol, timeframe)
    if key in CANDLE_CACHE:
        return CANDLE_CACHE[key].copy()
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    df = _ohlcv_frame(rows)
    # Fallback also covers sparse 15m history (e.g. newly listed contracts).
    fallback = {'4h': ('1h', '4h'), '1h': ('15m', '1h'), '15m': ('5m', '15min')}.get(timeframe)
    if len(df) < PREFERRED_HISTORY_BARS and fallback:
        lower_tf, rule = fallback
        try:
            # Fetch enough lower-timeframe bars to build up to ~200 target bars.
            # A 220-bar fallback was too short for 15m candles resampled from 5m.
            source_limit = min(1000, OHLCV_LIMIT * {'4h': 4, '1h': 4, '15min': 3}.get(rule, 4))
            lower_rows = exchange.fetch_ohlcv(symbol, timeframe=lower_tf, limit=source_limit)
            lower_df = _ohlcv_frame(lower_rows)
            resampled = _resample_ohlcv(lower_df, rule)
            if len(resampled) > len(df):
                print(f'MUM YEDEĞİ {symbol} {timeframe}: {len(df)} doğrudan mum yerine {len(resampled)} adet {lower_tf} verisinden birleştirilmiş mum kullanılıyor.')
                df = resampled
        except Exception as e:
            print(f'MUM YEDEĞİ BAŞARISIZ {symbol} {timeframe}: {type(e).__name__}: {str(e)[:100]}')
    if len(df) < 25:
        raise ValueError(f'Yetersiz mum verisi: {len(df)} (daha düşük zaman dilimi yedeği de denendi)')
    result = add_indicators(df)
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

def market_cap_rankings():
    """Market-cap rank with persistent cache and provider fallbacks.

    Tries CoinGecko first, then CoinPaprika and CoinCap. A provider failure
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
    if cache and (now - cache_ts) < MARKET_CAP_REFRESH_MINUTES * 60:
        return cache, cache_ts, 'cache'

    providers = []
    # Provider 1: CoinGecko API (may deny requests from some cloud IP ranges).
    def get_coingecko():
        out = []
        for page in (1, 2):
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
                       for x in rows if x.get('symbol') and x.get('market_cap_rank'))
        return out

    # Provider 2: CoinPaprika public tickers; ranks are supplied by the API.
    def get_coinpaprika():
        r = requests.get('https://api.coinpaprika.com/v1/tickers', params={'quotes':'USD'},
                         timeout=25, headers={'accept':'application/json','user-agent':'altcoin-alert-scanner/5.0'})
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list): raise ValueError('CoinPaprika unexpected response')
        return [(str(x.get('symbol') or '').upper(), int(x['rank']))
                for x in rows if x.get('symbol') and x.get('rank') and int(x['rank']) > 0 and int(x['rank']) <= 500]

    # Provider 3: CoinCap assets endpoint as a separate fallback.
    def get_coincap():
        r = requests.get('https://api.coincap.io/v2/assets', params={'limit':500},
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
    return int(rankings.get(base, 501))

def market_cap_label_for_symbol(symbol, rankings):
    base = str(symbol).split('/')[0].upper()
    return f"#{int(rankings[base])}" if base in rankings else 'bilinmiyor (90+ eşiği uygulanır)'

def alert_gate_for_rank(score, market_cap_rank):
    """Tiered alert gate: 1-200=>80+, 201-500=>85+, 501+=>90+."""
    if market_cap_rank <= 200:
        return score >= 80.0, 80.0
    if market_cap_rank <= 500:
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
    if 52<=c.rsi<=68 if side=='LONG' else 32<=c.rsi<=48: mom_raw += 8
    elif 48<=c.rsi<52 if side=='LONG' else 48<c.rsi<=52: mom_raw += 3
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
        raise ValueError('Yeterli kapalı mum yok: yapı seviyeleri hesaplanamadı')
    local_res = float(local.high.max())
    local_sup = float(local.low.min())
    L = _directional_score(df4,df1,df15,ctx,'LONG',symbol_key)
    S = _directional_score(df4,df1,df15,ctx,'SHORT',symbol_key)
    direction = 'LONG' if L['directional']>=S['directional'] else 'SHORT'
    vals = L if direction=='LONG' else S
    opp = S if direction=='LONG' else L
    directional = vals['directional']; opposite = opp['directional']; gap = directional-opposite

    if direction == 'LONG':
        trigger = local_res + 0.10*atr
        dist_atr = (local_res-ref_price)/atr
        broken = ref_price > trigger and float(c.volume_ratio) >= 1.10
        trend_ok = ref_price >= float(c.ema20) and float(df1.iloc[-2].close) >= float(df1.iloc[-2].ema20)
        if broken:
            phase = 'BREAKOUT_STARTED'
            entry = ref_price
            stretch_atr = max((ref_price-trigger)/atr, 0.0)
        elif 0 <= dist_atr <= EARLY_TRIGGER_MAX_ATR and trend_ok:
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
        broken = ref_price < trigger and float(c.volume_ratio) >= 1.10
        trend_ok = ref_price <= float(c.ema20) and float(df1.iloc[-2].close) <= float(df1.iloc[-2].ema20)
        if broken:
            phase = 'BREAKOUT_STARTED'
            entry = ref_price
            stretch_atr = max((trigger-ref_price)/atr, 0.0)
        elif 0 <= dist_atr <= EARLY_TRIGGER_MAX_ATR and trend_ok:
            phase = 'PRE_BREAKOUT'
            entry = trigger
            stretch_atr = 0.0
        else:
            phase = 'NO_EARLY_SETUP'
            entry = trigger
            stretch_atr = max((trigger-ref_price)/atr, 0.0)

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
    room_score = clamp(room*16,0,32)
    vol_score = clamp((atr_pct/0.01)*16,0,42)
    trend_move = clamp((c.adx-18)*1.5,0,16)
    move_potential = clamp((room_score+vol_score+trend_move)/90*100)
    rr_bonus = clamp((rr-2.0)*1.5,0,4) if pd.notna(rr) else 0

    # Readiness rewards proximity, own-coin direction and volume confirmation.
    proximity_score = clamp(100 - max(level_dist,0)/max(EARLY_TRIGGER_MAX_ATR,0.1)*55, 0, 100)
    volume_score = clamp((float(c.volume_ratio)-0.8)*45,0,100)
    readiness = clamp(0.45*proximity_score + 0.35*directional + 0.20*volume_score)
    early_bonus = min(3.0, max(0.0, (readiness-65.0)*0.08)) if phase=='PRE_BREAKOUT' else 0.0
    # Score favors direction and executable entry quality. Potential remains a
    # separate forecast descriptor, not a way to inflate a weak setup.
    # ONE public score: direction, entry location/payoff, readiness, move room,
    # and clarity between LONG vs SHORT are blended once. No separate public
    # potential/readiness/entry/RR scores are needed to interpret the signal.
    direction_clarity = clamp(gap * 6.0, 0.0, 100.0)
    raw_score = clamp(
        0.40*directional + 0.28*entry_quality + 0.14*readiness +
        0.10*move_potential + 0.08*direction_clarity + rr_bonus + early_bonus
    )
    setup_valid = (phase in ('PRE_BREAKOUT','BREAKOUT_STARTED') and trend_ok and
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
            'directional_confidence':round(directional,1),'direction_gap':round(gap,1),
            'entry_quality':round(entry_quality,1),'move_potential':round(move_potential,1),
            'potential_label':potential_label,'relative_strength':round(vals['relative_strength'],1),
            'relative_1h':round(vals['relative_1h'],2),'relative_4h':round(vals['relative_4h'],2),
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
    """Independent spot discovery layer. It emits WATCH alerts, never trade orders.

    Selects a blend of high-volume and high-24h-move spot pairs, then checks
    closed 15m/1h candles for aligned momentum and relative volume.
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
        if base in stable_bases: continue
        t = tickers.get(sym) or {}
        qv = spot_quote_volume(t)
        if qv < MIN_SPOT_24H_VOLUME: continue
        try: pct = float(t.get('percentage') or 0.0)
        except (TypeError, ValueError): pct = 0.0
        pool.append({'symbol':sym,'base':base,'quote_volume':qv,'pct24':pct,'last':float(t.get('last') or 0)})
    # Avoid scanning every spot pair: blend the most active pairs with the
    # strongest movers so early movers and already-visible movers both appear.
    by_move = sorted(pool, key=lambda x: abs(x['pct24']), reverse=True)[:25]
    by_volume = sorted(pool, key=lambda x: x['quote_volume'], reverse=True)[:25]
    selected = {x['symbol']:x for x in by_move + by_volume}
    candidates = sorted(selected.values(), key=lambda x: (abs(x['pct24']), x['quote_volume']), reverse=True)[:SPOT_RADAR_MAX_CANDIDATES]
    out = []
    for item in candidates:
        sym = item['symbol']
        try:
            d15 = add_indicators(pd.DataFrame(spot_exchange.fetch_ohlcv(sym, timeframe='15m', limit=OHLCV_LIMIT), columns=['timestamp','open','high','low','close','volume']))
            if len(d15) < 10: continue
            c = d15.iloc[-2]
            prev = d15.iloc[-3]
            old1h = d15.iloc[-6]
            px = float(c.close)
            ch15 = (px/float(prev.close)-1)*100 if prev.close else 0.0
            ch1h = (px/float(old1h.close)-1)*100 if old1h.close else 0.0
            vr = float(c.volume_ratio) if pd.notna(c.volume_ratio) else 0.0
            # Direction agreement reduces noisy one-candle reversals. Two
            # routes allow either a steady move or a sharp, volume-backed burst.
            aligned = (ch15 > 0 and ch1h > 0) or (ch15 < 0 and ch1h < 0)
            steady = abs(ch15) >= 0.8 and abs(ch1h) >= 1.8 and vr >= 1.5
            burst = abs(ch15) >= 1.4 and vr >= 2.0
            if not aligned or not (steady or burst): continue
            side = 'YUKARI HAREKET' if ch15 > 0 else 'AŞAĞI HAREKET'
            out.append({'symbol':sym,'base':item['base'],'direction':side,'price':px,
                        'change_15m':ch15,'change_1h':ch1h,'change_24h':item['pct24'],
                        'volume_ratio':vr,'quote_volume':item['quote_volume'],
                        'market_cap_rank':market_cap_rank_for_symbol(sym, market_caps),
                        'market_cap_label':market_cap_label_for_symbol(sym, market_caps),
                        'swap_available':sym in swap_symbols or f"{item['base']}/USDT:USDT" in swap_symbols,
                        'breakout': bool((px > float(d15.iloc[-22:-2].high.max())) if ch15 > 0 else (px < float(d15.iloc[-22:-2].low.min())))})
        except Exception as e:
            print(f'SPOT RADAR {sym}: {type(e).__name__}: {e}')
        time.sleep(0.08)
    out.sort(key=lambda x: (abs(x['change_15m']) * min(x['volume_ratio'],4), abs(x['change_1h'])), reverse=True)
    print(f'SPOT RADARI: {len(pool)} likit aday içinden {len(candidates)} mumla kontrol edildi, {len(out)} erken uyarı bulundu.')
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
    phase_rank = {'PRE_BREAKOUT': 1, 'BREAKOUT_STARTED': 2}
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
                entry_touched = (high >= entry) if direction == 'LONG' else (low <= entry)
                if not entry_touched:
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


def write_diagnostic(all_results, ctx, spot_results=None, error_counts=None, source='live'):
    """Keep a bounded log of top perp candidates, spot watch candidates and gate reasons."""
    stamp = datetime.now().isoformat(timespec='seconds')
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:10]
    best = f"{top[0]['symbol']} {top[0]['score']:.1f}" if top else 'n/a'
    lines = [f"=== {stamp} | BTC4h={ctx['btc']['4h']} BTC1h={ctx['btc']['1h']} "
             f"ETH4h={ctx['eth']['4h']} ETH1h={ctx['eth']['1h']} | "
             f"perp_taranan={len(all_results)} | en_iyi={best} | marketcap={source} ==="]
    for r in top:
        lines.append(f"PERP {r['symbol']:20} {r['direction']:5} phase={r.get('phase','?'):16} score={r['score']:5.1f} rr_internal={r['rr']:.2f} rel1h={r.get('relative_1h',0):+.2f} rel4h={r.get('relative_4h',0):+.2f} mc=#{r.get('market_cap_rank',501):4d} gate={r.get('alert_threshold',90):.0f} valid={r.get('setup_valid',False)} qv={r.get('quote_volume_24h',0):.0f} reject={r.get('gate_reason','')}")
    for r in (spot_results or [])[:10]:
        lines.append(f"SPOT {r['symbol']:20} {r['direction']:15} 15m={r['change_15m']:+.2f}% 1h={r['change_1h']:+.2f}% 24h={r['change_24h']:+.2f}% vol={r['volume_ratio']:.2f}x swap={r['swap_available']}")
    if error_counts:
        lines.append('ERRORS ' + json.dumps(error_counts, ensure_ascii=False, sort_keys=True))
    existing = DIAG_LOG.read_text(encoding='utf-8').splitlines() if DIAG_LOG.exists() else []
    DIAG_LOG.write_text('\n'.join((existing + lines)[-DIAG_LOG_MAX_LINES:]) + '\n', encoding='utf-8')

def main():
    print('=== ALTCOIN ALERT SCANNER V9 — TEK BÜTÜNSEL SKOR ===')
    ctx = market_context()
    print(f"BTC 4h={ctx['btc']['4h']} 1h={ctx['btc']['1h']} | ETH 4h={ctx['eth']['4h']} 1h={ctx['eth']['1h']}")
    all_symbols = list(dict.fromkeys(liquid_usdt_swaps()))
    symbols = list(dict.fromkeys(select_deep_scan_symbols(all_symbols)))
    eligible_count = len(symbols)
    print(f'MEXC aktif USDT vadeli evreni: {len(all_symbols)} benzersiz coin ticker/fiyat-hacim aşamasında tarandı.')
    print(f'Derin mum analizi: {eligible_count}/{len(all_symbols)} benzersiz coin; minimum 24s vadeli hacim {MIN_ALERT_24H_VOLUME:,.0f} USDT; eşiği geçen listenin tamamı analiz ediliyor.')
    state = load_state(); new_state = {}; qualifying = []; all_results = []; error_counts = {}
    market_caps, market_cap_updated_at, market_cap_source = market_cap_rankings()
    print(f'Market-cap kaynağı: {market_cap_source} | eşik: 1-200=>80+, 201-500=>85+, 501+ veya bilinmiyor=>90+')

    def evaluate(s, symbol_key=None):
        try:
            x = score_setup(candles(s,'4h'), candles(s,'1h'), candles(s,'15m'), ctx, symbol_key)
            x['symbol'] = s
            x['market_cap_rank'] = market_cap_rank_for_symbol(s, market_caps)
            x['market_cap_label'] = market_cap_label_for_symbol(s, market_caps)
            x['quote_volume_24h'] = SWAP_QUOTE_VOLUMES.get(s, 0.0)
            x['alert_eligible'], x['alert_threshold'] = alert_gate_for_rank(x['score'], x['market_cap_rank'])
            all_results.append(x)
            rr_ok = pd.notna(x['rr']) and x['rr'] >= MIN_CONFIRMED_RR
            setup_ok = bool(x.get('setup_valid'))
            liquidity_ok = x['quote_volume_24h'] >= MIN_ALERT_24H_VOLUME
            x['gate_reason'] = ('OK' if x['alert_eligible'] and rr_ok and setup_ok and liquidity_ok else
                                'LIKIDITE<%s USDT' % f'{MIN_ALERT_24H_VOLUME:,.0f}' if not liquidity_ok else
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
        except Exception as e:
            name = type(e).__name__; error_counts[name] = error_counts.get(name, 0) + 1
            print(f'VERİ/ANALİZ HATASI {s}: {name}: {e}')

    evaluate('BTC/USDT:USDT', symbol_key='btc')
    evaluate('ETH/USDT:USDT', symbol_key='eth')
    for i, sym in enumerate(symbols, 1):
        evaluate(sym)
        if i % 25 == 0: print(f'  ... {i}/{len(symbols)} vadeli coin tarandı')
        time.sleep(SLEEP_BETWEEN_COINS)

    # Spot radar is retained for discovery/diagnostics only. It sends no generic watch spam.
    spot_results = spot_early_radar(set(all_symbols), market_caps)
    write_diagnostic(all_results, ctx, spot_results, error_counts, market_cap_source)
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
