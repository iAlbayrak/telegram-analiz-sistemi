"""
Standalone altcoin alert scanner -- BULUTTA çalışır (GitHub Actions),
telefondaki/bilgisayardaki hiçbir şeye bağımlı değil. Mevcut MEXC trading
bot'unuzdan TAMAMEN bağımsızdır -- hiçbir dosyasını içe aktarmaz, hiçbir
emir göndermez. Sadece MEXC'nin herkese açık (public) piyasa verisini
okur ve Telegram'a bildirim gönderir. API anahtarı / hesap bilgisi
gerektirmez -- sadece TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID.

--- Skorlama tasarımı (v2.4 -- bildirim/state güvenilirliği eklendi) ---

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
TELEGRAM_CHANNEL_TITLE = os.getenv('TELEGRAM_CHANNEL_TITLE', 'iAlbayrak Analiz').strip()
MIN_24H_VOLUME      = float(os.getenv('MIN_24H_VOLUME', '1000000'))
ALERT_MIN_SCORE      = float(os.getenv('ALERT_MIN_SCORE', '80'))   # 80-90 "GİRİLEBİLİR", 90+ "ÇOK GÜÇLÜ/ELİT"
SLEEP_BETWEEN_COINS  = float(os.getenv('SLEEP_BETWEEN_COINS', '0.12'))
STATE_FILE           = Path(os.getenv('STATE_FILE', 'alert_state.json'))
DIAG_LOG             = Path(os.getenv('DIAG_LOG_FILE', 'scan_diagnostic.log'))
DIAG_LOG_MAX_LINES   = 500
WIDE_STOP_BUFFER_ATR = float(os.getenv('WIDE_STOP_BUFFER_ATR', '0.6'))  # ek "geniş stop" ATR payı
OHLCV_LIMIT = 220

exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(float(v), hi))

# ---------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------
def add_indicators(df):
    df = df.copy(); h, l, c, v = df.high, df.low, df.close, df.volume
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

def structure_levels(df):
    w = df.iloc[-98:-2]
    return float(w.low.min()), float(w.high.max())

# ---------------------------------------------------------------------
# Directional score: trend + momentum + volume + breakout-confirmation
# + BTC/ETH regime alignment. Nothing here overlaps with entry_quality
# or move_potential.
# ---------------------------------------------------------------------
def _external_alignment_raw(side, ctx, symbol_key):
    """Regime-alignment bonus, source depends on WHO is being scored so a
    symbol never gets credit for 'agreeing with itself'.
      - regular altcoin (symbol_key=None): checked against BTC 4h (primary)
        + BTC 1h / ETH 4h (confirmation) -- unchanged from before.
      - BTC itself (symbol_key='btc'): checked ONLY against ETH's 4h/1h --
        BTC's own regime is never compared to BTC's own regime.
      - ETH itself (symbol_key='eth'): checked ONLY against BTC's 4h/1h.
    """
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
    a, b, c = df4.iloc[-2], df1.iloc[-2], df15.iloc[-2]; prev = df15.iloc[-3]

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

    # Breakout confirmation is a pure short-term EVENT check (vs the prior
    # 15m candle only) -- it does NOT look at where price sits inside the
    # wider range, so it cannot fight entry_quality's location_score.
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
    return {'trend':trend,'momentum':momentum,'volume':volume,'breakout':breakout,
            'market':market,'directional':clamp(directional)}

def score_setup(df4, df1, df15, ctx, symbol_key=None):
    c = df15.iloc[-2]
    entry = float(c.close); sup, res = structure_levels(df15); atr = float(c.atr); atr_pct = float(c.atr_pct)

    L = _directional_score(df4,df1,df15,ctx,'LONG',symbol_key)
    S = _directional_score(df4,df1,df15,ctx,'SHORT',symbol_key)
    direction = 'LONG' if L['directional']>=S['directional'] else 'SHORT'
    vals = L if direction=='LONG' else S; opp = S if direction=='LONG' else L
    directional = vals['directional']; opposite = opp['directional']; gap = directional-opposite

    # entry_quality: distance from EMA (anti-chase) + room to target.
    # Independent of directional -- does not use range position, so it
    # cannot contradict the breakout signal above.
    ema_dist = abs(entry-float(c.ema20))/max(atr,1e-12)
    overextended = clamp((ema_dist-0.8)*18,0,28)
    if direction=='LONG':
        room = (res-entry)/max(atr,1e-12); target = res
    else:
        room = (entry-sup)/max(atr,1e-12); target = sup
    if room<=0: location_score=15.0
    elif room<0.50: location_score=35.0
    elif room<1.00: location_score=55.0
    elif room<1.50: location_score=75.0
    else: location_score=90.0
    ema_entry_score = clamp(86-overextended + (8 if (direction=='LONG' and entry<=c.ema20*1.01) or (direction=='SHORT' and entry>=c.ema20*0.99) else 0))
    entry_quality = clamp(0.70*ema_entry_score + 0.30*location_score)

    # move_potential: room + volatility + trend strength. Independent axis.
    # NOTE (fix): the three sub-terms' true combined ceiling is 90, not 100
    # (32+42+16=90) -- left unscaled, the elite gate's ">=85" was silently
    # ~94% of the real max, far stricter than the number suggested. Rescaled
    # to a genuine 0-100 range so the displayed number and the gate mean
    # what they say.
    room_score = clamp(room*16,0,32); vol_score = clamp((atr_pct/0.01)*16,0,42); trend_move = clamp((c.adx-18)*1.5,0,16)
    move_potential = clamp((room_score+vol_score+trend_move)/90*100)

    stop_mult = clamp(2.2+(0.8 if c.adx<22 else 0)+(0.5 if atr_pct>0.035 else 0),2.2,3.5)
    stop_pct = clamp(atr_pct*stop_mult,0.025,0.055)
    stop = entry*(1-stop_pct) if direction=='LONG' else entry*(1+stop_pct); risk = abs(entry-stop)
    rr = ((target-entry)/risk if direction=='LONG' else (entry-target)/risk) if target and risk else np.nan
    rr_bonus = clamp((rr-2.0)*2.0,0,6) if pd.notna(rr) else 0

    # Wide (buffered) stop: standart stop'un biraz daha gerisinde, ani bir
    # fitilin (stop-hunt / sarkma) sizi standart stop'tan çıkarttıktan hemen
    # sonra fiyat asıl yönüne dönmesi riskine karşı. Puanlamayı, RR'yi ya da
    # TP'leri ETKİLEMEZ -- sadece ek, bilgilendirici bir alan.
    wide_stop_buffer = atr * WIDE_STOP_BUFFER_ATR
    wide_stop = stop - wide_stop_buffer if direction=='LONG' else stop + wide_stop_buffer

    # Each axis counted EXACTLY ONCE. Weights (0.56/0.20/0.24) fold in what
    # used to be separate structure/market terms, sum to 1.00 -- no double
    # counting, no component can silently dominate through two channels.
    raw_score = clamp(0.56*directional + 0.20*entry_quality + 0.24*move_potential + rr_bonus)

    elite_gate = (raw_score>=90 and directional>=85 and entry_quality>=80 and move_potential>=94 and pd.notna(rr) and rr>=2.5 and gap>=15)
    quality = 'ELITE' if elite_gate else 'STRONG' if raw_score>=85 else 'SELECTIVE' if raw_score>=72 else 'WEAK'
    return {'direction':direction,'score':round(raw_score,1),'quality':quality,'elite_gate':elite_gate,
            'entry':entry,'stop':stop,'wide_stop':wide_stop,'target':target,'rr':rr,'support':sup,'resistance':res,
            'directional_confidence':round(directional,1),'entry_quality':round(entry_quality,1),
            'move_potential':round(move_potential,1),'breakout':round(vals['breakout'],1)}

# ---------------------------------------------------------------------
# TP1/TP2/TP3 -- mesafeye göre sıralı (en yakından en uzağa)
# ---------------------------------------------------------------------
def tp_levels(x):
    entry=float(x['entry']); stop=float(x['stop']); direction=x['direction']; risk=abs(entry-stop)
    if risk<=0: return None
    planned_r = abs(float(x['target'])-entry)/risk
    raw = [(planned_r, float(x['target'])),
           (1.5, entry+risk*1.5 if direction=='LONG' else entry-risk*1.5),
           (2.5, entry+risk*2.5 if direction=='LONG' else entry-risk*2.5)]
    raw.sort(key=lambda t: t[0])
    labels = ['TP1','TP2','TP3']
    return {labels[i]: {'r': round(r,2), 'price': price} for i,(r,price) in enumerate(raw)}

def tier(x):
    if x.get('elite_gate'): return '🟢 ELİT (90+, tüm kapılar geçti)'
    s=x['score']
    if s>=90: return '🟢 ÇOK GÜÇLÜ (90+)'
    if s>=80: return '🔵 GİRİLEBİLİR (80-90)'
    return None

TIER_RANK = {'🔵 GİRİLEBİLİR (80-90)':1, '🟢 ÇOK GÜÇLÜ (90+)':2, '🟢 ELİT (90+, tüm kapılar geçti)':3}

def load_state():
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')

def is_new_or_upgraded(x, t, state):
    key = f"{x['symbol']}:{x['direction']}"
    prev = state.get(key)
    if prev is None:
        return True
    # Telegram gönderimi önceki turda başarısız olduysa bu setup
    # bir sonraki turda tekrar gönderilmeye çalışılır.
    if not prev.get('notified', True):
        return True
    return TIER_RANK.get(t, 0) > TIER_RANK.get(prev.get('tier'), 0)

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
    lines = [t,
        f"*{x['symbol']}*  —  {x['direction']}  |  Skor {x['score']} ({x['quality']})",
        f"RR {x['rr']:.2f}  |  Yön{x['directional_confidence']:.0f} Giriş{x['entry_quality']:.0f} "
        f"Potansiyel{x['move_potential']:.0f} Kırılım{x['breakout']:.0f}",
        f"Referans fiyat: {x['entry']:.6g}",
        f"Stop (standart): {x['stop']:.6g}",
        f"Stop (geniş, ani sarkma payı): {x['wide_stop']:.6g}",
        f"Destek: {x['support']:.6g}   Direnç: {x['resistance']:.6g}"]
    if tp:
        lines.append(f"TP1 ({tp['TP1']['r']}R): {tp['TP1']['price']:.6g}  |  "
                      f"TP2 ({tp['TP2']['r']}R): {tp['TP2']['price']:.6g}  |  "
                      f"TP3 ({tp['TP3']['r']}R): {tp['TP3']['price']:.6g}")
    return '\n'.join(lines)

def _discover_channel_chat_id():
    """Configured Chat ID 'chat not found' verirse, botun gördüğü son
    channel_post içinden doğru kanalı bulmayı dener. Kanal başlığı da
    eşleşmelidir; böylece yanlış bir kanala gönderme riski azaltılır."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates',
            params={'limit': 50}, timeout=15
        )
        if r.status_code != 200:
            print(f'TELEGRAM getUpdates HATASI: {r.status_code} {r.text[:200]}')
            return None
        data = r.json()
        matches = []
        for u in data.get('result', []):
            post = u.get('channel_post')
            if not post:
                continue
            chat = post.get('chat') or {}
            if chat.get('type') != 'channel':
                continue
            title = str(chat.get('title', '')).strip()
            if TELEGRAM_CHANNEL_TITLE and title != TELEGRAM_CHANNEL_TITLE:
                continue
            matches.append(chat)
        if not matches:
            print(f'Kanal otomatik bulunamadı. Beklenen kanal: {TELEGRAM_CHANNEL_TITLE!r}')
            return None
        chat = matches[-1]
        print(f"Telegram kanal otomatik bulundu: {chat.get('title')} (ID gizli)")
        return str(chat.get('id'))
    except Exception as e:
        print(f'TELEGRAM getUpdates HATASI: {e}')
        return None

def _send_chunks(text, chat_id):
    chunks = [text[i:i+3500] for i in range(0, len(text), 3500)] or [text]
    for chunk in chunks:
        try:
            r = requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': chat_id, 'text': chunk, 'parse_mode': 'Markdown'},
                timeout=15
            )
        except Exception as e:
            print(f'TELEGRAM GÖNDERİM HATASI: {e}')
            return False, None
        if r.status_code != 200:
            return False, r
    return True, None

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        print('TELEGRAM AYARLI DEĞİL: TELEGRAM_BOT_TOKEN secret eksik.')
        return False
    if not TELEGRAM_CHAT_ID:
        print('TELEGRAM_CHAT_ID secret boş. Kanal otomatik keşfi deneniyor...')
        fallback = _discover_channel_chat_id()
        if not fallback:
            return False
        ok, err = _send_chunks(text, fallback)
        if ok:
            return True
        print(f'TELEGRAM GÖNDERİM HATASI: {err.status_code if err is not None else "bağlantı"} '
              f'{err.text[:200] if err is not None else ""}')
        return False

    ok, err = _send_chunks(text, TELEGRAM_CHAT_ID)
    if ok:
        return True

    # Mevcut secret yanlış/eski ise, bu proje için daha önce kanalda
    # görülen doğrulanmış channel_post üzerinden doğru ID'yi bulup bir kez
    # daha deneriz. Bu, 'chat not found' yüzünden bildirimin kaybolmasını
    # önler; yetki/403 hatasında sessizce başka yere göndermez.
    if err is not None and err.status_code == 400 and 'chat not found' in err.text.lower():
        print('Telegram Chat ID geçersiz görünüyor; kanal otomatik keşfi deneniyor...')
        fallback = _discover_channel_chat_id()
        if fallback and fallback != str(TELEGRAM_CHAT_ID):
            ok2, err2 = _send_chunks(text, fallback)
            if ok2:
                print('Telegram bildirimi otomatik bulunan kanal ID ile gönderildi.')
                return True
            if err2 is not None:
                print(f'TELEGRAM GÖNDERİM HATASI (otomatik ID): {err2.status_code} {err2.text[:200]}')
    elif err is not None:
        print(f'TELEGRAM HATASI: {err.status_code} {err.text[:200]}')
    return False

def write_diagnostic(all_results, ctx, notify_keys=None):
    """Her turun en iyi adaylarını ve bildirim uygunluğunu kaydeder.
    Böylece skor >= 80 olsa bile neden bildirime girmediği görülebilir."""
    notify_keys = notify_keys or set()
    stamp = datetime.now().isoformat(timespec='seconds')
    top = sorted(all_results, key=lambda r: r['score'], reverse=True)[:10]
    best = f"{top[0]['score']:.1f}" if top else 'n/a'
    lines = [f"=== {stamp} | BTC4h={ctx['btc']['4h']} BTC1h={ctx['btc']['1h']} "
             f"ETH4h={ctx['eth']['4h']} ETH1h={ctx['eth']['1h']} | "
             f"taranan={len(all_results)} | en_iyi={best} ==="]
    for r in top:
        key = f"{r['symbol']}:{r['direction']}"
        rr = r.get('rr')
        rr_text = f"{float(rr):.2f}" if pd.notna(rr) else 'NA'
        tier_text = tier(r) if r['score'] >= ALERT_MIN_SCORE and pd.notna(r['rr']) else '-'
        eligible = 'EVET' if key in notify_keys else 'HAYIR'
        lines.append(
            f"  {r['symbol']:20} {r['direction']:5} score={r['score']:5.1f} "
            f"rr={rr_text:>5} tier={tier_text} bildirim={eligible}"
        )
    existing = DIAG_LOG.read_text(encoding='utf-8').splitlines() if DIAG_LOG.exists() else []
    combined = (existing + lines)[-DIAG_LOG_MAX_LINES:]
    DIAG_LOG.write_text('\n'.join(combined) + '\n', encoding='utf-8')

def main():
    print('=== ALTCOIN ALERT SCANNER v2.4 ===')
    ctx = market_context()
    print(f"BTC 4h={ctx['btc']['4h']} 1h={ctx['btc']['1h']} | ETH 4h={ctx['eth']['4h']} 1h={ctx['eth']['1h']}")
    symbols = liquid_usdt_swaps()
    print(f'Taranacak likit altcoin sayısı: {len(symbols)}')

    state = load_state()
    new_state = {}
    qualifying = []
    all_results = []
    notify_keys = set()

    def evaluate(s, symbol_key=None):
        try:
            x = score_setup(candles(s,'4h'), candles(s,'1h'), candles(s,'15m'), ctx, symbol_key)
            x['symbol'] = s
            all_results.append(x)
            if x['score'] >= ALERT_MIN_SCORE and pd.notna(x['rr']):
                key = f"{s}:{x['direction']}"
                t = tier(x)
                prev = state.get(key) or {}
                new_state[key] = {
                    'tier': t,
                    'score': x['score'],
                    # Eski state dosyaları için geriye dönük uyum:
                    # Eski state dosyalarında notified alanı yoksa, güvenli tarafta kalıp
                    # bu setup'ı daha önce bildirilmemiş kabul ederiz.
                    'notified': prev.get('notified', False)
                }
                if is_new_or_upgraded(x, t, state):
                    qualifying.append((x, t))
                    notify_keys.add(key)
        except Exception as e:
            # Tek bir sembolün veri/indikatör hatası bütün taramayı durdurmasın.
            print(f'SKIP {s}: {e}')

    # BTC ve ETH'nin kendisi de adaydır; hizalık bonusu kendi kendilerine değil
    # diğer varlığa göre hesaplanır.
    evaluate('BTC/USDT:USDT', symbol_key='btc')
    evaluate('ETH/USDT:USDT', symbol_key='eth')

    for i, s in enumerate(symbols, 1):
        evaluate(s)
        if i % 25 == 0:
            print(f'  ... {i}/{len(symbols)} tarandı')
        time.sleep(SLEEP_BETWEEN_COINS)

    # Önce tanı logunu yaz; state ise bildirim sonucuna göre aşağıda kesinleştirilir.
    write_diagnostic(all_results, ctx, notify_keys)

    if not qualifying:
        # Mevcut 80+ setup'lar korunur; 80'in altına düşenler new_state'e hiç
        # girmediği için doğal olarak resetlenir.
        save_state(new_state)
        best = max((r['score'] for r in all_results), default=None)
        print(f'Bu turda yeni/yükselmiş bir setup yok. (En iyi aday: {best})')
        return

    qualifying.sort(key=lambda p: p[0]['score'], reverse=True)
    parts = [format_context_header(ctx), '']
    for x, t in qualifying:
        parts.append(format_setup(x, t)); parts.append('')
    parts.append('⚠️ Yatırım tavsiyesi değildir. Sadece kurduğunuz skor sisteminin çıktısıdır.')

    ok = send_telegram('\n'.join(parts))

    # KRİTİK: Telegram başarısızsa qualifying setup'ları 'bildirildi' sayma.
    # Böylece bir sonraki turda otomatik yeniden denenir.
    for x, _ in qualifying:
        key = f"{x['symbol']}:{x['direction']}"
        if key in new_state:
            new_state[key]['notified'] = bool(ok)

    save_state(new_state)
    print('Bildirim gönderildi.' if ok else 'Bildirim gönderilemedi; ilgili setup(lar) bir sonraki turda yeniden denenecek.')

if __name__ == '__main__':
    main()
