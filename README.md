Altcoin Alert Scanner V13.1
Alert-only MEXC USDT perpetual scanner. No MEXC account/API keys and no order placement.
V13.1 changes
Parabolic SAR added as a low/medium-weight trend/reversal confirmation.
Conservative Libra-like structure detector added using confirmed swing pivots and the 78.6% retracement zone. It contributes to the holistic score but is never a standalone alert trigger.
Breakout confirmation strengthened: higher volume requirement plus candle-body direction/quality.
`BREAKOUT_RETEST` phase added. A prior confirmed break followed by a controlled retest can be preferred over chasing a fresh breakout.
Live retest validation prevents sending a retest alert after price has moved too far from the trigger.
Move-potential weight increased so genuinely exceptional setups can score materially above routine small opportunities without artificially widening TP levels.
Unknown market-cap rank is now conservatively treated as the 90+ tier instead of accidentally falling into the 501-800 tier.
Existing spot radar, full active futures discovery, market-cap tiers, RR/geometry, live-price validation, state, diagnostic log and outcome tracking are preserved.
Alert tiers
75-79.9: radar only
80-84.9: normal alert when all gates pass
85-89.9: strong alert
90-94.9: high-potential alert
95+: rare/exceptional
Market-cap gate:
#1-500 -> score 80+
#501-800 -> score 85+
#801+ or unknown -> score 90+
Important
Do not replace `.github/workflows/scan.yml` or GitHub Secrets for this package. Replace only `altcoin_alert_scanner.py`; keep existing `requirements.txt` if it already contains the required dependencies. Preserve `alert_state.json`, `scan_diagnostic.log`, `market_cap_cache.json`, and `signal_outcomes.json`.
Validation
Python `py_compile` passed.
Offline indicator/score smoke tests passed with exchange calls stubbed.
Market-cap unknown-rank gate test passed.
No live MEXC or Telegram request was made during package validation.
