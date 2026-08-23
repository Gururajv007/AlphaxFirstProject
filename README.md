# NSE Swing Trading Assistant

A Python + Streamlit desktop-style app to backtest, paper-trade, and (once you
connect a broker) live-trade a swing trading strategy on NSE stocks.

**This is a tool, not financial advice, and it does not guarantee profit.**
You are responsible for every order it places once you connect live trading.

---

## 1. Setup (VS Code, one-time)

1. Open this folder (`trading_app/`) in VS Code.
2. Open a terminal in VS Code (`` Ctrl+` ``) and create a virtual environment:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # macOS/Linux
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## 2. Run the app

```bash
streamlit run app.py
```

This opens the app in your browser at `http://localhost:8501`. It keeps
running from your terminal — closing the browser tab doesn't stop it; press
`Ctrl+C` in the terminal to stop it.

## 3. Where to put your broker API key

Go to the **🔑 Settings / API** tab inside the app. That's where you paste
your Dhan client ID / API secret, and where the daily login flow (described in
`broker_dhan.py`) walks you through generating an access token. Credentials
are saved only to a local `config.json` in this folder (excluded via
`.gitignore` — never commit it or share it).

To trade live you need a Dhan trading account and DhanHQ API access:
1. Create/get your Dhan client ID at https://dhan.co
2. Enable the DhanHQ API (Data-API subscription for historical/intraday
   market data endpoints) and register your static IP with Dhan.
3. Paste your client ID + app secret into the Settings tab and complete the
   daily login flow to generate an access token (tokens expire daily).

The app's multi-broker architecture isolates all broker-specific code in
`broker_dhan.py` — that file can be swapped/extended for another broker's SDK
without touching the strategy, backtester, or UI.

## 4. The strategy: Adaptive Trend-Pullback Swing Strategy

Implemented in `strategy.py`. Designed to apply unchanged across timeframes
(15m / 1h / 1d / 1wk) by using ratio- and percentile-based filters instead of
fixed absolute thresholds wherever possible. Long-only (matches standard NSE
cash-segment swing trading; short swing positions need F&O and aren't covered
here).

| # | Filter | What it checks |
|---|--------|-----------------|
| 1 | Trend | Price above EMA50, EMA50 above EMA200 — only trades with the prevailing trend |
| 2 | Pullback entry trigger | Price pulled back near EMA20, RSI cooled to 38–55 and turning up, bullish candle confirms |
| 3 | Momentum | MACD histogram rising, RSI below 70 (not chasing an overbought spike) |
| 4 | Volatility | ATR% ranks 20th–90th percentile of its *own* last 100 bars — adapts automatically per stock/timeframe instead of a fixed number |
| 5 | Volume | Today's volume ≥ 1.1× its 20-period average |
| 6 | Relative strength | Stock's trailing return must beat the Nifty 50's over the same lookback |
| 7 | Liquidity | Average daily turnover (₹) must clear a configurable floor |
| 8 | Risk-reward | Computed reward (to target) ÷ risk (to ATR-based stop) must be ≥ 1.5 (configurable) |

All thresholds are adjustable in `strategy.py`'s `StrategyParams` dataclass,
and the key risk numbers are also adjustable live from the app's sidebar.

## 5. Tabs in the app

- **📊 Strategy & Backtest** — pick a symbol + timeframe, run the strategy
  against historical data, see the equity curve, trade list, and metrics
  (win rate, total return, max drawdown, profit factor).
- **📝 Paper Trading** — scans a watchlist for live signals using recent
  data and lets you simulate taking/exiting trades with fake money. Nothing
  here touches a real broker.
- **💰 Live Trading** — places REAL orders through your connected broker.
  Locked until (a) a broker is connected in Settings, and (b) you tick an
  explicit risk-acknowledgement checkbox. Also respects the daily loss
  kill-switch configured in the sidebar.
- **🔑 Settings / API** — broker connection and credentials.

## 6. Before you flip on Live Trading

Even with your trading experience, treat this software itself as unproven
until you've watched it behave correctly:

1. Run backtests across a few different stocks and timeframes — read the
   trade log, not just the headline metrics.
2. Run Paper Trading for at least a couple of weeks. Confirm signals,
   stop-losses, and targets behave the way you expect.
3. Start live with the smallest position size your broker allows.
4. The Live Trading tab automatically places a server-side GTT stop-loss
   (Dhan Forever Order, see `DhanBroker.place_gtt_stop_loss` in
   `broker_dhan.py`) after every entry, so your position is protected even if
   your laptop, internet, or this app goes down.
5. Re-check SEBI's and your broker's current algo-trading compliance
   requirements before scaling up order frequency — these rules have
   changed more than once recently.

## 7. Known limitations / what to extend next

- Dhan is the currently wired broker (`broker_dhan.py`); the app can be
  extended to other brokers via `broker_factory.py`.
- Paper/live price refresh is manual (button-triggered), not a background
  auto-refresh loop — intentional for a first version, but worth automating
  later with `streamlit-autorefresh` or a separate polling process.
- The backtester is a single-position-at-a-time, single-symbol engine. It
  does not model portfolio-level effects across multiple simultaneous
  positions.
- yfinance is used as the fallback market-data source when no Dhan broker is
  connected; it is delayed and unsuitable as the source of truth for live
  order execution (Dhan's own APIs are used once a broker is connected).
- The Dhan symbol→security-id map (`dhan_security_list.csv`) is downloaded
  and cached on demand by `broker_dhan.py`; keep it fresh before market open.
