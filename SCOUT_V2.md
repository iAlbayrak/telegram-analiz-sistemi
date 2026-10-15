# Scout v2 — deployment notes

Scout analyzes public MEXC USDT perpetual-market data only. It never submits orders and does not require exchange API keys.

- Live scan: closed 1-hour candles, scheduled at minute 4 of each hour.
- Strategy: volume-backed Donchian breakout; stop at 2 ATR; TP1 at 1.5R for 33% of the position; remaining position managed by a 3×ATR trailing stop. There is no fixed upper target.
- Telegram: alerts are rendered as image cards; if image sending fails, the sender falls back to plain text.
- State: `state/alert_state.json`, `state/open_signals.json`, and `state/closed_signals.json` are committed by the live workflow.
- Safety: the old 15-minute scanner and its workflow are removed from the active repository to prevent duplicate alert streams. Existing GitHub secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are reused.
- Quality gate: the live workflow runs the full unittest suite before scanning. The backtest is manual and publishes a report artifact.

Do not treat synthetic tests as evidence of profitability. Run the live workflow in observation mode and evaluate real forward results before risking capital.
