"""
V5 erken kırılım + doğrulanmış işlem planı tarayıcısı -- BULUTTA çalışır (GitHub Actions),
telefondaki/bilgisayardaki hiçbir şeye bağımlı değil. Mevcut MEXC trading
bot'unuzdan TAMAMEN bağımsızdır -- hiçbir dosyasını içe aktarmaz, hiçbir
emir göndermez. Sadece MEXC'nin herkese açık (public) piyasa verisini
okur ve Telegram'a bildirim gönderir. API anahtarı / hesap bilgisi
gerektirmez -- sadece TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID.

--- V4: mevcut doğrulanmış setup + ayrı spot erken hareket radarı ---

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
from datetime import datetime
import ccxt
import numpy as np
import pandas as pd
import requests
from pathlib import Path

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')
MIN_24H_VOLUME      = float(os.getenv('MIN_24H_VOLUME', '1000000'))
MIN_SPOT_24H_VOLUME = float(os.getenv('MIN_SPOT_24H_VOLUME', '500000'))
SPOT_RADAR_MAX_CANDIDATES = int(os.getenv('SPOT_RADAR_MAX_CANDIDATES', '50'))
SPOT_WATCH_COOLDOWN_HOURS = float(os.getenv('SPOT_WATCH_COOLDOWN_HOURS', '6'))
MIN_CONFIRMED_RR = float(os.getenv('MIN_CONFIRMED_RR', '2.0'))
MIN_DIRECTION_GAP = float(os.getenv('MIN_DIRECTION_GAP', '8'))
MIN_ENTRY_QUALITY = float(os.getenv('MIN_ENTRY_QUALITY', '65'))
ALERT_MIN_SCORE      = float(os.getenv('ALERT_MIN_SCORE', '80'))   # base technical score; rank tiers decide the actual alert gate
MARKET_CAP_CACHE_FILE = Path(os.getenv('MARKET_CAP_CACHE_FILE', 'market_cap_cache.json'))
MARKET_CAP_REFRESH_MINUTES = int(os.getenv('MARKET_CAP_REFRESH_MINUTES', '360'))
MARKET_CAP_TOP_N = 500
EARLY_TRIGGER_MAX_ATR = float(os.getenv('EARLY_TRIGGER_MAX_ATR', '0.85'))
MIN_SETUP_READINESS = float(os.getenv('MIN_SETUP_READINESS', '65'))
SLEEP_BETWEEN_COINS  = float(os.getenv('SLEEP_BETWEEN_COINS', '0.12'))
STATE_FILE           = Path(os.getenv('STATE_FILE', 'alert_state.json'))
DIAG_LOG             = Path(os.getenv('DIAG_LOG_FILE', 'scan_diagnostic.log'))
DIAG_LOG_MAX_LINES   = 500
WIDE_STOP_BUFFER_ATR = float(os.getenv('WIDE_STOP_BUFFER_ATR', '0.6'))  # ek "geniş stop" ATR payı
OHLCV_LIMIT = 220

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
    rs = g / loss.replace(0, np.nan); df['rsi'] = 100 - 100/(1+rs)
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
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()
    df['volume_ratio'] = v / v.rolling(20).mean(); df['atr_pct'] = df.atr / c
    return df.dropna().reset_index(drop=True)

def candles(symbol, timeframe):
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    if not rows:
        raise ValueError('Mum verisi gelmedi')
    df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
    return add_indicators(df)

def regime_from_row(x):
    if x.close > x.ema50 > x.ema200 and x.rsi >= 52: return 'BULLISH'
    if x.close < x.ema50 < x.ema200 and x.rsi <= 48: return 'BEARISH'
    return 'NEUTRAL'

def symbol_regime(symbol):
    d4 = candles(symbol, '4h'); d1 = candles(symbol, '1h')
    return {'4h': regime_from_row(d4.iloc[-2]), '1h': regime_from_row(d1.iloc[-2])}

def market_context():
    btc = symbol_regime('BTC/USDT:USDT')
    eth = symbol_regime('ETH/USDT:USDT')
    primary = btc['4h']  # BTC 4h öncül
    aligned = (btc['1h'] == primary) and (eth['4h'] == primary)
    return {'btc': btc, 'eth': eth, 'primary_bias': primary, 'aligned': aligned}

def liquid_usdt_swaps():
    markets = exchange.load_markets(); tickers = exchange.fetch_tickers(); out = []
    for s, m in markets.items():
        if not (m.get('active') and m.get('swap') and m.get('quote') == 'USDT') or s in ('BTC/USDT:USDT','ETH/USDT:USDT'):
            continue
        t = tickers.get(s, {}) or {}; q = t.get('quoteVolume')
        if q is None and t.get('baseVolume') and t.get('last'):
            q = t['baseVolume'] * t['last']
        if q and float(q) >= MIN_24H_VOLUME:
            out.append((s, float(q)))
    return [s for s, _ in sorted(out, key=lambda x: x[1], reverse=True)]

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

    providers = [('coingecko', get_coingecko), ('coinpaprika', get_coinpaprika), ('coincap', get_coincap)]
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
    trend = trend_raw/50*100; momentum = mom_raw/38*100; volume = vol_raw/12*100
    breakout = breakout_raw/22*100; market = market_raw/8*100
    directional = 0.32*trend + 0.32*momentum + 0.12*volume + 0.16*breakout + 0.08*market
    return {'trend':trend,'momentum':momentum,'volume':volume,'breakout':breakout,'market':market,'directional':clamp(directional)}

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
    stop_mult = clamp(2.2+(0.5 if c.adx<22 else 0)+(0.3 if atr_pct>0.035 else 0),2.2,3.0)
    stop_pct = clamp(atr_pct*stop_mult,0.008,0.05)
    stop = entry*(1-stop_pct) if direction=='LONG' else entry*(1+stop_pct)
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
    extension_penalty = clamp((ema_dist-0.8)*22,0,35)
    if room<=0: location_score=10.0
    elif room<0.50: location_score=30.0
    elif room<1.00: location_score=50.0
    elif room<1.50: location_score=70.0
    else: location_score=88.0
    if target_method.startswith('kırılım'):
        location_score = min(location_score, 72.0)
    ema_entry_score = clamp(88-extension_penalty + (6 if (direction=='LONG' and ref_price<=c.ema20*1.01) or (direction=='SHORT' and ref_price>=c.ema20*0.99) else 0))
    entry_quality = clamp(0.70*ema_entry_score + 0.30*location_score)
    room_score = clamp(room*16,0,32)
    vol_score = clamp((atr_pct/0.01)*16,0,42)
    trend_move = clamp((c.adx-18)*1.5,0,16)
    move_potential = clamp((room_score+vol_score+trend_move)/90*100)
    rr = ((target-entry)/risk if direction=='LONG' else (entry-target)/risk) if risk>0 else np.nan
    rr_bonus = clamp((rr-2.0)*2.0,0,5) if pd.notna(rr) else 0

    # Readiness score rewards being near the level without rewarding a late chase.
    proximity_score = clamp(100 - max(level_dist,0)/max(EARLY_TRIGGER_MAX_ATR,0.1)*55, 0, 100)
    volume_score = clamp((float(c.volume_ratio)-0.8)*45,0,100)
    readiness = clamp(0.45*proximity_score + 0.35*directional + 0.20*volume_score)
    early_bonus = min(4.0, max(0.0, (readiness-60.0)*0.10)) if phase=='PRE_BREAKOUT' else 0.0
    raw_score = clamp(0.56*directional + 0.20*entry_quality + 0.24*move_potential + rr_bonus + early_bonus)
    setup_valid = phase in ('PRE_BREAKOUT','BREAKOUT_STARTED') and trend_ok and not overextended and readiness >= MIN_SETUP_READINESS
    elite_gate = (raw_score>=90 and directional>=85 and entry_quality>=80 and move_potential>=85 and pd.notna(rr) and rr>=2.0 and gap>=15 and setup_valid)
    quality = 'ELITE' if elite_gate else 'STRONG' if raw_score>=85 else 'SELECTIVE' if raw_score>=72 else 'WEAK'
    return {'direction':direction,'score':round(raw_score,1),'quality':quality,'elite_gate':elite_gate,
            'entry':float(entry),'reference_price':ref_price,'trigger':float(trigger),'phase':phase,
            'setup_valid':bool(setup_valid),'readiness':round(readiness,1),'overextended':bool(overextended),
            'stop':float(stop),'wide_stop':float(stop-(atr*WIDE_STOP_BUFFER_ATR) if direction=='LONG' else stop+(atr*WIDE_STOP_BUFFER_ATR)),
            'target':float(target),'target_method':target_method,'rr':float(rr) if pd.notna(rr) else float('nan'),
            'support':sup,'resistance':res,'local_support':local_sup,'local_resistance':local_res,
            'directional_confidence':round(directional,1),'direction_gap':round(gap,1),
            'entry_quality':round(entry_quality,1),'move_potential':round(move_potential,1),
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
    if x.get('elite_gate'): return '🟢 ELİT (90+, tüm kapılar geçti)'
    s=x['score']
    if s>=90: return '🟢 ÇOK GÜÇLÜ (90+)'
    if s>=80: return '🔵 GİRİLEBİLİR (80-90)'
    return None

TIER_RANK = {'🔵 GİRİLEBİLİR (80-90)':1, '🟢 ÇOK GÜÇLÜ (90+)':2, '🟢 ELİT (90+, tüm kapılar geçti)':3}

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

def format_context_header(ctx):
    flag = '✅ hizalı' if ctx['aligned'] else '⚠️ karışık'
    bias = ctx['primary_bias']
    bias_line = ('🟢 Genel yön: YUKARI (BTC öncülüğünde)' if bias=='BULLISH'
                 else '🔴 Genel yön: AŞAĞI (BTC öncülüğünde)' if bias=='BEARISH'
                 else '⚪ Genel yön: NÖTR (BTC yatay)')
    return (f"{bias_line}\n"
            f"*PİYASA BAĞLAMI* ({flag})\n"
            f"BTC 4h: {ctx['btc']['4h']}  |  BTC 1h: {ctx['btc']['1h']}\n"
            f"ETH 4h: {ctx['eth']['4h']}  |  ETH 1h: {ctx['eth']['1h']}")

def format_setup(x, t):
    tp = tp_levels(x)
    phase_title = ('🟡 ERKEN KIRILIM PLANI — SEVİYEYE YAKIN' if x['phase']=='PRE_BREAKOUT'
                   else '🟢 KIRILIM YENİ BAŞLADI — GİRİŞ ADAYI')
    entry_instruction = (f"Tetik/giriş seviyesi: {x['trigger']:.8g} | 15 dk mum kapanışı bu seviyeyi doğrulamalı"
                         if x['phase']=='PRE_BREAKOUT' else
                         f"Kırılım sonrası referans giriş: {x['entry']:.8g} | aşırı uzama filtresi geçti")
    lines = [phase_title,
        f"*{x['symbol']}* — {x['direction']} | Skor {x['score']} ({x['quality']})",
        f"Market-cap: {x.get('market_cap_label', 'bilinmiyor')} | Gerekli eşik: {x.get('alert_threshold', 90):.0f}+",
        f"Hazırlık {x['readiness']:.0f}/100 | Yön {x['directional_confidence']:.0f} | Giriş kalitesi {x['entry_quality']:.0f} | Hareket potansiyeli {x['move_potential']:.0f}",
        f"Mevcut referans fiyat: {x['reference_price']:.8g}",
        entry_instruction,
        f"Stop: {x['stop']:.8g} | Geniş stop bölgesi: {x['wide_stop']:.8g}",
        f"Hedef: {x['target']:.8g} ({x['target_method']}) | R:R {x['rr']:.2f}",
        f"TP1 ({tp['TP1']['r']}R): {tp['TP1']['price']:.8g} | TP2 ({tp['TP2']['r']}R): {tp['TP2']['price']:.8g} | TP3: {tp['TP3']['price']:.8g}",
        f"Yakın destek: {x['local_support']:.8g} | Yakın direnç: {x['local_resistance']:.8g}",
        f"Geniş destek: {x['support']:.8g} | Geniş direnç: {x['resistance']:.8g}",
        f"Kırılım teyidi: {'EVET' if x['phase']=='BREAKOUT_STARTED' else 'HENÜZ DEĞİL — kapanış şartı bekleniyor'}",
        'Plan yalnızca belirtilen tetik seviyesi ve kapanış koşulu geçerliyse değerlendirilmelidir; otomatik emir gönderilmez.']
    return '\n'.join(lines)

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('TELEGRAM AYARLI DEĞİL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret olarak ekleyin.')
        return False
    # Split only between lines so Telegram Markdown entities such as *bold*
    # are not cut in half by a fixed-character slice.
    chunks = []
    current = ''
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > 3500:
            chunks.append(current)
            current = ''
        current += line
    if current or not chunks:
        chunks.append(current or text)
    ok = True
    for chunk in chunks:
        try:
            r = requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                               json={'chat_id':TELEGRAM_CHAT_ID,'text':chunk,'parse_mode':'Markdown'}, timeout=15)
            if r.status_code != 200:
                print(f'TELEGRAM HATASI: {r.status_code} {r.text[:200]}'); ok=False
        except Exception as e:
            print(f'TELEGRAM GÖNDERİM HATASI: {e}'); ok=False
    return ok

def write_diagnostic(all_results, ctx, spot_results=None, error_counts=None, source='live'):
    """Keep a bounded log of top perp candidates, spot watch candidates and gate reasons."""
    stamp = datetime.now().isoformat(timespec='seconds')
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:10]
    best = f"{top[0]['symbol']} {top[0]['score']:.1f}" if top else 'n/a'
    lines = [f"=== {stamp} | BTC4h={ctx['btc']['4h']} BTC1h={ctx['btc']['1h']} "
             f"ETH4h={ctx['eth']['4h']} ETH1h={ctx['eth']['1h']} | "
             f"perp_taranan={len(all_results)} | en_iyi={best} | marketcap={source} ==="]
    for r in top:
        lines.append(f"PERP {r['symbol']:20} {r['direction']:5} phase={r.get('phase','?'):16} score={r['score']:5.1f} rr={r['rr']:.2f} mc=#{r.get('market_cap_rank',501):4d} gate={r.get('alert_threshold',90):.0f} ready={r.get('readiness',0):.0f} valid={r.get('setup_valid',False)} entry={r['entry_quality']:.0f} gap={r.get('direction_gap',0):.0f}")
    for r in (spot_results or [])[:10]:
        lines.append(f"SPOT {r['symbol']:20} {r['direction']:15} 15m={r['change_15m']:+.2f}% 1h={r['change_1h']:+.2f}% 24h={r['change_24h']:+.2f}% vol={r['volume_ratio']:.2f}x swap={r['swap_available']}")
    if error_counts:
        lines.append('ERRORS ' + json.dumps(error_counts, ensure_ascii=False, sort_keys=True))
    existing = DIAG_LOG.read_text(encoding='utf-8').splitlines() if DIAG_LOG.exists() else []
    DIAG_LOG.write_text('\n'.join((existing + lines)[-DIAG_LOG_MAX_LINES:]) + '\n', encoding='utf-8')

def main():
    print('=== ALTCOIN ALERT SCANNER V5 ===')
    ctx = market_context()
    print(f"BTC 4h={ctx['btc']['4h']} 1h={ctx['btc']['1h']} | ETH 4h={ctx['eth']['4h']} 1h={ctx['eth']['1h']}")
    symbols = liquid_usdt_swaps()
    print(f'MEXC USDT vadeli tarama: {len(symbols)} coin | minimum 24s hacim {MIN_24H_VOLUME:,.0f} USDT')
    state = load_state(); new_state = {}; qualifying = []; all_results = []; error_counts = {}
    market_caps, market_cap_updated_at, market_cap_source = market_cap_rankings()
    print(f'Market-cap kaynağı: {market_cap_source} | eşik: 1-200=>80+, 201-500=>85+, 501+ veya bilinmiyor=>90+')

    def evaluate(s, symbol_key=None):
        try:
            x = score_setup(candles(s,'4h'), candles(s,'1h'), candles(s,'15m'), ctx, symbol_key)
            x['symbol'] = s
            x['market_cap_rank'] = market_cap_rank_for_symbol(s, market_caps)
            x['market_cap_label'] = market_cap_label_for_symbol(s, market_caps)
            x['alert_eligible'], x['alert_threshold'] = alert_gate_for_rank(x['score'], x['market_cap_rank'])
            all_results.append(x)
            rr_ok = pd.notna(x['rr']) and x['rr'] >= MIN_CONFIRMED_RR
            quality_ok = x['entry_quality'] >= MIN_ENTRY_QUALITY and x['direction_gap'] >= MIN_DIRECTION_GAP
            setup_ok = bool(x.get('setup_valid'))
            x['gate_reason'] = ('OK' if x['alert_eligible'] and rr_ok and quality_ok and setup_ok else
                                'ERKEN KIRILIM/TEYİT KOŞULU' if not setup_ok else
                                'SKOR/EŞİK' if not x['alert_eligible'] else
                                'RR<%.1f' % MIN_CONFIRMED_RR if not rr_ok else
                                'GİRİŞ/YÖN KALİTESİ')
            if x['alert_eligible'] and rr_ok and quality_ok and setup_ok:
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

    # Spot radar is retained for discovery/diagnostics only. It no longer sends
    # generic "watch this" messages; Telegram is reserved for level-based swap plans.
    spot_results = spot_early_radar(set(symbols), market_caps)
    write_diagnostic(all_results, ctx, spot_results, error_counts, market_cap_source)
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:8]
    if top:
        print('En iyi vadeli adaylar: ' + ' | '.join(
            f"{x['symbol']} {x['direction']} {x['score']:.1f} eşik={x['alert_threshold']:.0f} RR={x['rr']:.2f} faz={x['phase']} valid={x['setup_valid']} neden={x.get('gate_reason','')}"
            for x in top))
    if spot_results:
        print('Spot radar (tanı amaçlı; Telegram izleme spamı gönderilmez): ' + ', '.join(
            f"{x['symbol']} {x['direction']} 15m={x['change_15m']:+.1f}% 1h={x['change_1h']:+.1f}%"
            for x in spot_results[:8]))

    messages = [format_context_header(ctx), '']
    if qualifying:
        qualifying.sort(key=lambda p: p[0]['score'], reverse=True)
        messages.append('🎯 *GİRİŞ SEVİYESİ OLAN ERKEN KIRILIM / YENİ KIRILIM PLANLARI*')
        for x, t in qualifying:
            messages.append(format_setup(x, t)); messages.append('')
        messages.append('⚠️ Otomatik emir gönderilmez. Tetik seviyesinin 15 dk kapanış koşulu sağlanmadan plan tetiklenmiş sayılmaz.')
        send_ok = send_telegram('\n'.join(messages))
        print('Telegram işlem planları gönderildi.' if send_ok else 'Telegram bildirimi gönderilemedi; yeni alarm durumu tekrar deneme için korunuyor.')
        if not send_ok:
            for x, _t in qualifying:
                key = f"{x['symbol']}:{x['direction']}"
                if key in state: new_state[key] = state[key]
                else: new_state.pop(key, None)
    else:
        print('Bu turda puan/eşik + R:R + giriş kalitesi + erken kırılım koşullarının tamamını geçen yeni plan yok. Genel spot izleme bildirimi gönderilmedi.')

    save_state(new_state)

if __name__ == '__main__':
    main()
