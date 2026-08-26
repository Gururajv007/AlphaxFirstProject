# Pre-Go-Live Audit & Fix Report (Dhan)

Date: 2026-08-23 (Saturday) — go-live target: Monday 2026-08-24, NSE 9:15 AM IST
Mode: sandbox/paper verification only. **No live orders with real capital were placed.**

## Summary

Audited and fixed the Dhan order/scanning pipeline ahead of a Monday go-live.
27 unit tests pass. Every Dhan API behavior was checked against the installed
`dhanhq 2.2.0` SDK source (`/usr/local/lib/python3.11/dist-packages/dhanhq/`)
and the DhanHQ v2 docs (Authentication, Orders, Annexure, Market Quote,
Forever Order, Funds & Margin, Historical Data, Portfolio/Positions).

## Single source of truth

**`app.py` is the one app to run** (`streamlit run app.py`). `app_final.py`,
`app_backup.py` and `app_integrated.py` are superseded snapshots and are kept
only for reference. The final `app.py` is the audited, fixed app **plus** the
enhanced features that existed in `app_final.py`:

- Screener tab (Nifty 50 / Nifty 100 / F&O universes + presets), now wired to
  the connected broker's market data instead of yfinance.
- Orders tab (OrderManager lifecycle + trade history); live entries now
  recorded into the OrderManager and fire notifications.
- Alerts tab (Telegram configuration + recent notifications).
- Sidebar advanced strategy filters (ADX threshold, min volume ratio).
- All previous audit fixes preserved (GTT Forever Order, kill-switch latch,
  broker-first data, CLOSE ALL, daily risk reset, security-list handling).

## Root causes found and fixed

### 1. Security resolution was broken (would reject every symbol)
- `broker_dhan.py` filtered the scrip master with `SEM_SEGMENT == "NSE_EQ"`,
  which matches **0 rows** (segment codes are `E`, `C`, `F`, `M`).
- Confirmed empirically: correct filter `SEM_EXM_EXCH_ID == "NSE" AND
  SEM_SEGMENT == "E"` matches RELIANCE = security_id 2885.
- Fix: `_SCRIP_MASTER_FILTERS` / `_SCRIP_MASTER_FNO_FILTERS` mapping
  (`broker_dhan.py:220`), plus a metadata cache storing security_id, tick
  size and lot units (`_get_security_meta`, `broker_dhan.py:462`).

### 2. Corrupt security list
- Committed `dhan_security_list.csv` was 111 bytes of XML `AccessDenied`
  (from Dhan's signed-URL error body).
- Fix: regenerated from a fresh master (26.9 MB, 212,736 rows); the file is
  now untracked and treated as a regenerable cache (`_load_security_master`
  with weekly TTL + network refresh + stale flag, `broker_dhan.py:405`).

### 3. Expired credentials committed to git
- `config.json` (client `1109176427`, expired JWT access tokens from
  2026-07-28/29, `tokenConsumerType: SELF`) was tracked.
- Fix: added to `.gitignore`; `git rm --cached config.json`. The file remains
  locally for the runbook, but is no longer in the repo.

### 4. Wrong product type
- Old code sent `INTRA`; Dhan Annexure values are `CNC, INTRADAY, MARGIN, MTF,
  CO, BO`. `INTRA` is invalid.
- Fix: `place_order` maps `MIS`/`INTRA` → `INTRADAY` (`broker_dhan.py:853`);
  tests updated to assert `INTRADAY`.

### 5. GTT stop-loss was not a server-side stop
- Old code placed an immediate SELL-LIMIT, which executes immediately instead
  of waiting for the trigger.
- Fix: `place_gtt_stop_loss` now uses the **Forever Order API**
  (`SELL/CNC/LIMIT`, trigger = price) and returns a `GTTOrder` with `.gtt_id`
  (`broker_dhan.py:895`). `app.py` reads `getattr(gtt_result, 'gtt_id', ...)`.

### 6. App assumed a working broker for market data
- `data_feed.py` was yfinance-only, and yfinance is currently broken in this
  sandbox (JSONDecodeError on RELIANCE.NS).
- Fix: `get_historical`/`get_latest_price` accept a broker and try it first,
  falling back to yfinance (`data_feed.py:40, 104`). App + screener thread
  the connected broker through (`app.py` `get_broker()` helper; `screener.py`
  accepts `broker=`).

### 7. Emergency close-all was a no-op
- Live tab's "CLOSE ALL POSITIONS" only logged and printed a stub message.
- Fix: calls `broker.exit_all_positions()` (Dhan `DELETE /positions`,
  `broker_dhan.py:975`) when a broker is connected.

### 8. Kill-switch could un-trip after a recovery profit
- `DailyLossTracker.is_tripped` was computed from the running net P&L, so a
  loss that tripped the switch would unlock after a later profit — contrary to
  its own docstring ("refuse any further new entries for the rest of the day").
- Fix: latch once tripped until the daily reset (`risk_manager.py:99`);
  `app.py` now resets the daily/cons-loss trackers on the new trading day.

## Files changed

- `broker_dhan.py` — full rewrite: typed `DhanAPIError`, JWT expiry check,
  `renew_token`, real scrip-master resolution, tick/lot validation,
  INTRADAY product mapping, Forever-Order GTT, normalized positions, exit-all,
  Dhan historical data backend.
- `paper_trader.py` — reconstructed (module file was missing; only the pyc
  remained; `import app` crashed).
- `app.py` — `get_broker()` helper, broker threaded into all data-feed/scanner
  calls, GTT display uses `.gtt_id`, CLOSE ALL wired to `exit_all_positions`,
  stale-security-list warning, daily risk-tracker reset.
- `data_feed.py` — broker-first historical/LTP with yfinance fallback.
- `screener.py` — optional `broker` threaded through `__init__`/scan/cache.
- `risk_manager.py` — kill-switch latch + docstring-compliant behavior.
- `.gitignore` — `config.json`, `dhan_security_list*.csv` now ignored.
- `config.json`, `dhan_security_list.csv` — untracked.
- `tests/test_dhan_broker.py` — rewritten for new behavior (INTRADAY, real
  resolution, typed errors, GTTOrder).
- `tests/test_paper_trader.py`, `tests/test_risk_manager.py` — new.
- Docs: `README.md`, `MULTI_BROKER_ARCHITECTURE.md` corrected (Dhan is the
  implemented broker; Zerodha/Upstox/Angel/Fyers are stubs).

## Verification evidence

- 27 unit tests pass: `python3 -m unittest discover -s tests` → `OK`.
- Security resolution against the regenerated master:
  RELIANCE→2885, TCS→11536, HDFCBANK→1333, INFY→1594 (tick/lot parsed).
- Broker-first data path returns Dhan-shaped OHLCV (DatetimeIndex "Date",
  Asia/Kolkata); fallback to broken yfinance returns empty, not a crash.
- Screener end-to-end on 4 symbols with a broker: 0 failed symbols.
- Kill-switch latch: trips at -3%, stays tripped after +5k recovery, clears on
  `reset()`.
- Real failure paths:
  - expired local JWT → `DhanAPIError` code `EXPIRED` (no network call).
  - fabricated future-expiry token → live DH-901 `Invalid_Authentication`
    translated to typed `DhanAPIError`, `retryable=False`.
  - unknown symbol → `RuntimeError` with actionable message.

## Not verified before Monday (must confirm in the runbook)

- Real order placement / GTT / positions require valid Dhan credentials —
  current ones are expired and unverifiable here.
- FNO scrip-master URL (`api-scrip-master-fno.csv`) returned 403 on HEAD;
  NSE_EQ master downloads fine. F&O resolution is untested in this sandbox.
- Historical/intraday endpoints need the Dhan **Data-API subscription** and
  registered **static IP**; both must be confirmed on the Dhan dashboard.
- Intraday (15m/1h) Dhan history requires the Data-API subscription
  (otherwise DH-902) — the fallback to yfinance covers this but yfinance is
  currently offline in this environment.

---

# Monday 2026-08-24 Pre-Market Runbook (Dhan)

Order of operations, before the 9:15 AM open.

## 0. Pre-flight (now / Sunday)

- [ ] Confirm the app still imports and all 27 tests pass:
      `python3 -m unittest discover -s tests`
- [ ] Re-run `git status`; ensure `config.json` is NOT tracked.
- [ ] Keep a backup copy of `config.json` outside the repo (it holds your
      live client ID + app secret).

## 1. Dhan account setup (one-time, from the Dhan dashboard)

- [ ] Register your **static public IP** under DhanHQ API settings. Requests
      from unregistered IPs are rejected. (Required for token generation.)
- [ ] Enable the **Data-API subscription** (needed for historical + intraday
      market data endpoints; without it Dhan returns DH-902).
- [ ] Confirm your client ID is enabled for the segments you trade (NSE_EQ).

## 2. Morning credentials refresh (daily, token expires in ~24h)

- [ ] Open the app → **Settings / API**.
- [ ] Confirm client ID + app secret are saved.
- [ ] Complete **Step 3: Daily login** to generate a fresh access token.
      The app detects an expired token and raises `DhanAPIError code=EXPIRED`
      with a clear "re-generate" message; do not place orders until this is
      refreshed.
- [ ] Verify connection: Live tab should show "Broker connected" and a
      Dhan balance (not `—`). A DH-901 error means the token/IP is wrong.

## 3. Data + security list sanity

- [ ] Open **Strategy & Backtest**, run RELIANCE on `1d`. If the table is
      empty, either Dhan historical (DH-902) or yfinance is unavailable —
      do NOT rely on signals computed from empty data.
- [ ] Confirm no "security list is stale" warning in the Live tab; if shown,
      restart once with network access so `dhan_security_list.csv` refreshes.

## 4. Risk checks (before any live entry)

- [ ] Verify sidebar risk settings: risk per trade 1.25%, max daily loss 2.5%
      (defaults), capital = intended amount.
- [ ] Confirm kill-switch state: Live tab must show "🟢 OK — ₹X of daily loss
      budget remaining" (not KILL-SWITCH ACTIVE).
- [ ] Confirm you ticked the live-trading risk acknowledgement checkbox.
- [ ] Ensure the app's clock is set to IST so `is_market_open()` (9:15–15:20)
      gates order placement correctly.

## 5. Paper-mode rehearsal (recommended first thing)

- [ ] Run Paper Trading scan on your watchlist and confirm signals/stops match
      your manual read before enabling live.

## 6. Live — smallest possible size first

- [ ] Place **one** manual live BUY (smallest quantity, e.g. 1) via
      **Manual Order Entry**, with GTT.
- [ ] Confirm the order shows an order ID, the GTT shows a GTT ID
      (Forever Order), and positions appear in **Monitor Open Positions**.
- [ ] Test the emergency controls: STOP BOT and CLOSE ALL POSITIONS
      (`DELETE /positions`) on a tiny position.
- [ ] Only after all six steps behave as expected, run signal-driven live
      entries at your normal size.

## 7. Known unverified items to watch

- F&O symbols (NIFTY/BANKNIFTY futures/options) are NOT in the NSE_EQ master;
  do not assume F&O resolution works — verify against the FNO master once
  accessible.
- yfinance is offline in this environment; if Dhan historical returns empty,
  the app will show "No data returned" rather than fabricate prices.
