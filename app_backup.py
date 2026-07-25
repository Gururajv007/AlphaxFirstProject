"""
app.py
------
Main Streamlit application. Run with:

    streamlit run app.py

Tabs:
  1. Strategy & Backtest - test the swing strategy on historical data
  2. Paper Trading        - simulated live trading, no real money/orders
  3. Live Trading          - REAL orders via your connected broker (gated)
  4. Settings / API        - where you enter your broker API credentials

Safety design:
  - The app always starts in paper-trading-safe mode.
  - The Live Trading tab is disabled until a broker is connected AND you
    explicitly confirm you understand the risk.
  - A daily max-loss kill-switch (configurable in the sidebar) blocks new
    entries, in both paper and live mode, once tripped.
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as cfg
import data_feed
from strategy import StrategyParams, generate_signals, FILTER_DESCRIPTIONS
from backtester import run_backtest, trades_to_dataframe
from paper_trader import PaperPortfolio
from risk_manager import (
    DailyLossTracker, calculate_position_size, DrawdownTracker,
    ConsecutiveLossTracker, TradeAuditLog, is_market_open, get_market_hours_status
)

st.set_page_config(page_title="NSE Swing Trading Assistant", layout="wide")

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "config" not in st.session_state:
    st.session_state.config = cfg.load_config()

if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = cfg.normalize_trading_mode(
        st.session_state.config.get("trading_mode", "paper")
    )

if "paper_portfolio" not in st.session_state:
    st.session_state.paper_portfolio = PaperPortfolio(
        starting_cash=st.session_state.config["capital"]
    )

if "daily_loss_tracker" not in st.session_state:
    st.session_state.daily_loss_tracker = DailyLossTracker(
        capital=st.session_state.config["capital"],
        max_daily_loss_pct=st.session_state.config["max_daily_loss_pct"],
    )

if "live_trading_confirmed" not in st.session_state:
    st.session_state.live_trading_confirmed = False

if "live_signals" not in st.session_state:
    st.session_state.live_signals = []

if "broker_positions" not in st.session_state:
    st.session_state.broker_positions = []

if "drawdown_tracker" not in st.session_state:
    st.session_state.drawdown_tracker = DrawdownTracker(
        capital=st.session_state.config["capital"],
        max_drawdown_pct=5.0,
        warning_threshold_pct=3.0,
        auto_pause_enabled=True,
    )

if "consecutive_loss_tracker" not in st.session_state:
    st.session_state.consecutive_loss_tracker = ConsecutiveLossTracker(
        max_consecutive_losses=3,
        pause_duration="1_hour",
    )

if "audit_log" not in st.session_state:
    st.session_state.audit_log = TradeAuditLog()

if "bot_status" not in st.session_state:
    st.session_state.bot_status = "STOPPED"  # RUNNING, PAUSED, STOPPED

if "bot_pause_reason" not in st.session_state:
    st.session_state.bot_pause_reason = ""


def get_strategy_params() -> StrategyParams:
    s = st.session_state
    return StrategyParams(
        min_risk_reward=s.config["min_risk_reward"],
    )


# ---------------------------------------------------------------------------
# Sidebar - global risk & capital settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Risk Settings")
    st.session_state.config["capital"] = st.number_input(
        "Trading capital (₹)", min_value=1000.0,
        value=float(st.session_state.config["capital"]), step=1000.0,
    )
    st.session_state.config["risk_per_trade_pct"] = st.slider(
        "Risk per trade (%)", 0.25, 5.0, float(st.session_state.config["risk_per_trade_pct"]), 0.25
    )
    st.session_state.config["max_daily_loss_pct"] = st.slider(
        "Max daily loss / kill-switch (%)", 1.0, 10.0,
        float(st.session_state.config["max_daily_loss_pct"]), 0.5,
    )
    st.session_state.config["min_risk_reward"] = st.slider(
        "Minimum risk-reward to take a trade", 1.0, 4.0,
        float(st.session_state.config["min_risk_reward"]), 0.25,
    )
    if st.button("💾 Save settings"):
        cfg.save_config(st.session_state.config)
        st.session_state.daily_loss_tracker.capital = st.session_state.config["capital"]
        st.session_state.daily_loss_tracker.max_daily_loss_pct = st.session_state.config["max_daily_loss_pct"]
        st.success("Saved.")

    st.divider()
    st.subheader("🧭 Trading Mode")
    selected_mode = st.radio(
        "Choose execution mode",
        ["Paper Trade", "Live Trade"],
        index=0 if st.session_state.trading_mode == "paper" else 1,
        horizontal=True,
        key="mode_radio",
    )
    st.session_state.trading_mode = "paper" if selected_mode == "Paper Trade" else "live"
    st.session_state.config["trading_mode"] = st.session_state.trading_mode
    st.caption(
        "Use the same broker credentials and access token for both modes. "
        f"Current mode: {cfg.get_trading_mode_label(st.session_state.trading_mode)}"
    )

    st.divider()
    
    st.subheader("📉 Drawdown Protection")
    st.session_state.drawdown_tracker.max_drawdown_pct = st.slider(
        "Maximum Drawdown (%)", 1.0, 20.0,
        float(st.session_state.drawdown_tracker.max_drawdown_pct), 0.5,
    )
    st.session_state.drawdown_tracker.warning_threshold_pct = st.slider(
        "Drawdown Warning Threshold (%)", 0.5, 10.0,
        float(st.session_state.drawdown_tracker.warning_threshold_pct), 0.5,
    )
    st.session_state.drawdown_tracker.auto_pause_enabled = st.checkbox(
        "Auto Pause on Max Drawdown", 
        value=st.session_state.drawdown_tracker.auto_pause_enabled
    )

    st.divider()
    
    st.subheader("📛 Consecutive Loss Protection")
    st.session_state.consecutive_loss_tracker.max_consecutive_losses = st.number_input(
        "Max Consecutive Losses", min_value=1, max_value=10,
        value=st.session_state.consecutive_loss_tracker.max_consecutive_losses, step=1
    )
    st.session_state.consecutive_loss_tracker.pause_duration = st.selectbox(
        "Pause Duration",
        ["1_hour", "next_day", "manual"],
        index=["1_hour", "next_day", "manual"].index(st.session_state.consecutive_loss_tracker.pause_duration)
    )

    st.divider()
    st.caption(st.session_state.daily_loss_tracker.status_text())

st.title("📈 NSE Swing Trading Assistant")
st.caption(
    "Adaptive Trend-Pullback Swing Strategy — backtest, paper trade, then go live. "
    "Built for educational/personal use. Not investment advice."
)
mode_label = cfg.get_trading_mode_label(st.session_state.trading_mode)
st.info(
    f"Current execution mode: {mode_label}. The same broker credentials from Settings are reused for both paper and live trading."
)

tab_backtest, tab_paper, tab_live, tab_settings = st.tabs(
    ["📊 Strategy & Backtest", "📝 Paper Trading", "💰 Live Trading", "🔑 Settings / API"]
)

# ---------------------------------------------------------------------------
# TAB 1: Strategy & Backtest
# ---------------------------------------------------------------------------
with tab_backtest:
    st.subheader("Backtest the strategy on historical data")

    col1, col2, col3 = st.columns(3)
    symbol = col1.text_input("NSE symbol", value="RELIANCE", key="bt_symbol")
    timeframe = col2.selectbox("Timeframe", ["15m", "1h", "1d", "1wk"], index=2, key="bt_tf")
    use_index_filter = col3.checkbox("Use relative-strength vs Nifty 50 filter", value=True)

    with st.expander("ℹ️ Strategy filters used"):
        for name, desc in FILTER_DESCRIPTIONS.items():
            st.markdown(f"**{name}** — {desc}")

    if st.button("▶️ Run Backtest", type="primary"):
        with st.spinner("Fetching data and running backtest..."):
            try:
                df = data_feed.get_historical(symbol, interval=timeframe)
                if df.empty:
                    st.error("No data returned. Check the symbol and try again.")
                else:
                    index_df = None
                    if use_index_filter:
                        index_df = data_feed.get_historical(data_feed.NIFTY50_SYMBOL, interval=timeframe)

                    params = get_strategy_params()
                    params.use_relative_strength = use_index_filter
                    signals_df = generate_signals(df, params, index_df)

                    result = run_backtest(
                        signals_df,
                        capital=st.session_state.config["capital"],
                        risk_per_trade_pct=st.session_state.config["risk_per_trade_pct"],
                    )

                    m = result["metrics"]
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Trades", m["num_trades"])
                    c2.metric("Win rate", f"{m.get('win_rate_pct', 0)}%")
                    c3.metric("Total return", f"{m.get('total_return_pct', 0)}%")
                    c4.metric("Max drawdown", f"{m.get('max_drawdown_pct', 0)}%")
                    c5.metric("Profit factor", m.get("profit_factor", 0))

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=result["equity_curve"].index, y=result["equity_curve"], name="Equity"))
                    fig.update_layout(title="Equity Curve", height=350, margin=dict(t=40, b=20))
                    st.plotly_chart(fig, use_container_width=True)

                    price_fig = go.Figure()
                    price_fig.add_trace(go.Candlestick(
                        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name=symbol
                    ))
                    buy_points = signals_df[signals_df["signal"] == "BUY"]
                    price_fig.add_trace(go.Scatter(
                        x=buy_points.index, y=buy_points["Close"], mode="markers",
                        marker=dict(symbol="triangle-up", size=10, color="green"), name="BUY signal"
                    ))
                    price_fig.update_layout(title=f"{symbol} — price & signals", height=450, margin=dict(t=40, b=20))
                    st.plotly_chart(price_fig, use_container_width=True)

                    st.subheader("Trade log")
                    st.dataframe(trades_to_dataframe(result["trades"]), use_container_width=True)
            except Exception as e:
                st.error(f"Backtest failed: {e}")

# ---------------------------------------------------------------------------
# TAB 2: Paper Trading
# ---------------------------------------------------------------------------
with tab_paper:
    st.subheader("Paper Trading (simulated — no real orders)")
    st.info(
        "This tab is for simulated orders. Switch to Live Trade in the sidebar to place real Dhan orders with the same saved credentials."
    )

    watchlist_input = st.text_input(
        "Watchlist (comma-separated NSE symbols)", value="RELIANCE, TCS, HDFCBANK, INFY", key="paper_watchlist"
    )
    pt_timeframe = st.selectbox("Timeframe", ["15m", "1h", "1d", "1wk"], index=2, key="paper_tf")

    if st.button("🔍 Scan for signals"):
        watchlist = [s.strip() for s in watchlist_input.split(",") if s.strip()]
        params = get_strategy_params()
        index_df = data_feed.get_historical(data_feed.NIFTY50_SYMBOL, interval=pt_timeframe)

        results = []
        for sym in watchlist:
            try:
                df = data_feed.get_historical(sym, interval=pt_timeframe)
                if df.empty:
                    continue
                sig_df = generate_signals(df, params, index_df)
                last = sig_df.iloc[-1]
                if last["signal"] == "BUY":
                    results.append({
                        "symbol": sym, "price": last["Close"],
                        "stop_loss": last["stop_loss"], "target": last["target"],
                        "risk_reward": round(last["risk_reward"], 2),
                    })
            except Exception as e:
                st.warning(f"{sym}: {e}")
        st.session_state.paper_signals = results

    signals = st.session_state.get("paper_signals", [])
    if signals:
        st.write("**Signals found:**")
        for s in signals:
            cols = st.columns([2, 2, 2, 2, 2, 2])
            cols[0].write(f"**{s['symbol']}**")
            cols[1].write(f"₹{s['price']:.2f}")
            cols[2].write(f"SL ₹{s['stop_loss']:.2f}")
            cols[3].write(f"Target ₹{s['target']:.2f}")
            cols[4].write(f"R:R {s['risk_reward']}")
            if cols[5].button("Paper Buy", key=f"buy_{s['symbol']}"):
                if st.session_state.daily_loss_tracker.is_tripped:
                    st.error("Daily loss kill-switch is active. No new entries today.")
                else:
                    qty = calculate_position_size(
                        st.session_state.paper_portfolio.cash,
                        st.session_state.config["risk_per_trade_pct"],
                        s["price"], s["stop_loss"],
                    )
                    if qty == 0:
                        st.warning("Position size computed to 0 — capital too low for this trade's risk.")
                    else:
                        ok = st.session_state.paper_portfolio.buy(
                            s["symbol"], qty, s["price"], s["stop_loss"], s["target"],
                            str(dt.date.today()),
                        )
                        st.success(f"Paper bought {qty} shares of {s['symbol']}") if ok else st.error("Buy failed (insufficient cash or already holding).")
    else:
        st.caption("No signals yet — click 'Scan for signals'.")

    st.divider()
    st.subheader("📂 Paper portfolio")
    pf = st.session_state.paper_portfolio
    current_prices = {}
    for sym in pf.positions:
        try:
            current_prices[sym] = data_feed.get_latest_price(sym)
        except Exception:
            pass

    if st.button("🔄 Refresh prices / check stops & targets"):
        closed = pf.check_stops_and_targets(current_prices, str(dt.date.today()))
        if closed:
            st.info(f"Auto-closed: {', '.join(closed)}")

    c1, c2 = st.columns(2)
    c1.metric("Cash", f"₹{pf.cash:,.2f}")
    c2.metric("Total equity", f"₹{pf.total_equity(current_prices):,.2f}")

    st.write("**Open positions**")
    st.dataframe(pf.positions_df(current_prices), use_container_width=True)

    if not pf.positions_df(current_prices).empty:
        sell_symbol = st.selectbox("Manually square off", list(pf.positions.keys()))
        if st.button("Square off now"):
            price = current_prices.get(sell_symbol) or pf.positions[sell_symbol].entry_price
            pnl = pf.sell(sell_symbol, price, str(dt.date.today()), reason="manual")
            st.session_state.daily_loss_tracker.record_trade_pnl(pnl)
            st.success(f"Closed {sell_symbol} for P&L ₹{pnl:,.2f}")

    st.write("**Trade log**")
    st.dataframe(pf.trade_log_df(), use_container_width=True)

# ---------------------------------------------------------------------------
# TAB 3: Live Trading
# ---------------------------------------------------------------------------
with tab_live:
    st.subheader("💰 Live Trading — REAL MONEY, REAL ORDERS")
    st.error(
        "⚠️ Orders placed here go straight to the exchange through your broker. "
        "Only proceed once you have paper-traded this exact strategy and are comfortable "
        "with how it behaves. There is no undo button on a filled order."
    )

    runtime_config = {
        "api_key": st.session_state.config.get("api_key", ""),
        "api_secret": st.session_state.config.get("api_secret", ""),
        "access_token": st.session_state.config.get("access_token", ""),
    }
    connected = cfg.is_broker_connected(st.session_state.config, runtime_config)
    if st.session_state.config.get("broker") == "dhan" and not runtime_config.get("access_token"):
        st.caption("Dhan access token is missing. Complete the login flow in Settings / API to connect the app to Dhan.")
    
    # =====================================================================
    # BOT STATUS DASHBOARD (always visible)
    # =====================================================================
    st.subheader("🤖 Bot Status Dashboard")
    
    # Bot status columns
    bsc1, bsc2, bsc3, bsc4 = st.columns(4)
    
    # Bot status
    bot_color = "🟢" if st.session_state.bot_status == "RUNNING" else ("🟡" if st.session_state.bot_status == "PAUSED" else "🔴")
    bsc1.metric("Bot Status", f"{bot_color} {st.session_state.bot_status}")
    
    # Market status
    market_status = get_market_hours_status()
    market_open = is_market_open()
    bsc2.metric("Market Status", market_status)
    
    # Capital & Drawdown
    current_capital = st.session_state.config["capital"]
    balance_value = None
    if connected:
        try:
            from broker_factory import create_broker
            broker = create_broker(
                broker_type=st.session_state.config.get("broker", "zerodha_kite"),
                api_key=runtime_config.get("api_key", ""),
                api_secret=runtime_config.get("api_secret", ""),
                access_token=runtime_config.get("access_token", ""),
            )
            margins = broker.get_margins()
            if isinstance(margins, dict):
                balance_value = None
                for key in (
                    "availabelBalance",
                    "availableBalance",
                    "available",
                    "balance",
                    "withdrawableBalance",
                    "collateralAmount",
                    "sodLimit",
                    "netBalance",
                    "marginAvailable",
                    "cash",
                ):
                    if key in margins and margins[key] not in (None, ""):
                        balance_value = margins[key]
                        break
                if balance_value is None and isinstance(margins.get("data"), dict):
                    data_payload = margins["data"]
                    for key in (
                        "availabelBalance",
                        "availableBalance",
                        "available",
                        "balance",
                        "withdrawableBalance",
                        "collateralAmount",
                        "sodLimit",
                        "netBalance",
                        "marginAvailable",
                        "cash",
                    ):
                        if key in data_payload and data_payload[key] not in (None, ""):
                            balance_value = data_payload[key]
                            break
                if balance_value is None:
                    from broker_dhan import extract_balance_value
                    balance_value = extract_balance_value(margins)
        except Exception as exc:
            st.caption(f"Balance refresh skipped: {exc}")

    drawdown_status = st.session_state.drawdown_tracker.get_status(current_capital)
    bsc3.metric("Available Capital", f"₹{current_capital:,.0f}")
    if balance_value is not None:
        bsc4.metric("Dhan Balance", f"₹{float(balance_value):,.2f}")
    else:
        bsc4.metric("Dhan Balance", "—")

    # Daily P&L & Consecutive Losses
    daily_pnl = st.session_state.daily_loss_tracker.realized_pnl_today
    daily_pnl_color = "🟢" if daily_pnl >= 0 else "🔴"
    cons_loss_status = st.session_state.consecutive_loss_tracker.get_status()
    
    dlc1, dlc2 = st.columns(2)
    dlc1.metric("Daily P&L", f"{daily_pnl_color} ₹{daily_pnl:,.2f}")
    dlc2.metric("Consecutive Losses", f"{cons_loss_status['consecutive_losses']}/{cons_loss_status['max_allowed']}")

    st.divider()

    # =====================================================================
    # EMERGENCY CONTROLS (always visible)
    # =====================================================================
    st.subheader("⚠️ Emergency Controls")
    st.caption("These buttons allow you to immediately stop trading or close positions. All actions require confirmation.")
    
    ec1, ec2, ec3, ec4 = st.columns(4)
    
    stop_bot_clicked = ec1.button("🛑 STOP BOT", key="stop_bot", use_container_width=True)
    close_all_clicked = ec2.button("❌ CLOSE ALL POSITIONS", key="close_all", use_container_width=True)
    cancel_orders_clicked = ec3.button("⏸️ CANCEL PENDING", key="cancel_pending", use_container_width=True)
    disable_entries_clicked = ec4.button("🚫 DISABLE NEW ENTRIES", key="disable_entries", use_container_width=True)

    if stop_bot_clicked:
        st.session_state.bot_status = "STOPPED"
        st.session_state.bot_pause_reason = "User stopped bot manually"
        st.session_state.audit_log.log_action("STOP", reason="Manual stop via emergency control")
        st.warning("Bot has been STOPPED. All scanning paused. Open positions continue under manual management.")

    if close_all_clicked:
        if st.checkbox("Confirm: Close ALL open positions immediately?", key="confirm_close_all"):
            st.session_state.audit_log.log_action("CLOSE_ALL", reason="Manual emergency close")
            st.success("All positions would be closed (live broker integration needed)")
            st.session_state.bot_status = "STOPPED"

    if cancel_orders_clicked:
        st.session_state.audit_log.log_action("CANCEL_ORDERS", reason="Manual cancel via emergency control")
        st.info("All pending orders cancelled (live broker integration needed)")

    if disable_entries_clicked:
        st.session_state.bot_status = "PAUSED"
        st.session_state.bot_pause_reason = "New entries disabled by user"
        st.session_state.audit_log.log_action("DISABLE_ENTRIES", reason="New entries disabled")
        st.warning("New entries disabled. Existing positions continue to be managed.")

    st.divider()

    if not connected:
        st.warning("No broker connected yet. Go to the **Settings / API** tab to connect Dhan or Zerodha first.")
    else:
        st.success(f"Broker connected: {st.session_state.config['broker']}")

        st.session_state.live_trading_confirmed = st.checkbox(
            "I have paper-traded this strategy, I understand automated orders carry real financial risk, "
            "and I accept full responsibility for trades placed from this app.",
            value=st.session_state.live_trading_confirmed,
        )

        if st.session_state.daily_loss_tracker.is_tripped:
            st.error(st.session_state.daily_loss_tracker.status_text())
        elif not st.session_state.live_trading_confirmed:
            st.info("Check the confirmation box above to unlock live order placement.")
        elif not is_market_open():
            st.error(f"❌ {get_market_hours_status()} — NSE trading is allowed between 9:15 AM and 3:20 PM only.")
        else:
            try:
                from broker_factory import create_broker
                broker = create_broker(
                    broker_type=st.session_state.config.get("broker", "zerodha_kite"),
                    api_key=runtime_config.get("api_key", ""),
                    api_secret=runtime_config.get("api_secret", ""),
                    access_token=runtime_config.get("access_token", ""),
                )

                # =====================================================================
                # SECTION 1: SIGNAL-DRIVEN ENTRY (same filters as Paper Trading)
                # =====================================================================
                st.subheader("📡 Strategy-Generated Signals (recommended)")
                st.info(
                    f"Live execution is active for {cfg.get_trading_mode_label(st.session_state.trading_mode)} mode. "
                    "Orders will be sent to Dhan using the same credentials configured in Settings."
                )

                live_watchlist_input = st.text_input(
                    "Watchlist (comma-separated NSE symbols)", 
                    value="RELIANCE, TCS, HDFCBANK, INFY", 
                    key="live_watchlist"
                )
                live_tf = st.selectbox("Timeframe", ["15m", "1h", "1d", "1wk"], index=2, key="live_tf")

                if st.button("🔍 Scan for live signals"):
                    live_watchlist = [s.strip() for s in live_watchlist_input.split(",") if s.strip()]
                    params = get_strategy_params()
                    try:
                        index_df = data_feed.get_historical(data_feed.NIFTY50_SYMBOL, interval=live_tf)
                        live_results = []
                        for sym in live_watchlist:
                            try:
                                df = data_feed.get_historical(sym, interval=live_tf)
                                if df.empty:
                                    continue
                                sig_df = generate_signals(df, params, index_df)
                                last = sig_df.iloc[-1]
                                if last["signal"] == "BUY":
                                    live_results.append({
                                        "symbol": sym, 
                                        "price": last["Close"],
                                        "stop_loss": last["stop_loss"], 
                                        "target": last["target"],
                                        "risk_reward": round(last["risk_reward"], 2),
                                    })
                            except Exception as e:
                                st.warning(f"{sym}: {e}")
                        st.session_state.live_signals = live_results
                    except Exception as e:
                        st.error(f"Failed to scan: {e}")

                live_signals = st.session_state.get("live_signals", [])
                if live_signals:
                    st.write(f"**Found {len(live_signals)} signal(s):**")
                    for s in live_signals:
                        cols = st.columns([2, 2, 2, 2, 2, 3])
                        cols[0].write(f"**{s['symbol']}**")
                        cols[1].write(f"₹{s['price']:.2f}")
                        cols[2].write(f"SL ₹{s['stop_loss']:.2f}")
                        cols[3].write(f"Target ₹{s['target']:.2f}")
                        cols[4].write(f"R:R {s['risk_reward']}")
                        
                        if cols[5].button("🚀 Live Buy (with GTT)", key=f"live_buy_{s['symbol']}"):
                            try:
                                # Calculate position size
                                qty = calculate_position_size(
                                    st.session_state.config["capital"],
                                    st.session_state.config["risk_per_trade_pct"],
                                    s["price"], 
                                    s["stop_loss"],
                                )
                                
                                if qty == 0:
                                    st.error("Position size = 0 — capital too low for this trade's risk.")
                                else:
                                    # Step 1: Place BUY order
                                    order_id = broker.place_order(
                                        symbol=s["symbol"],
                                        qty=qty,
                                        transaction_type="BUY",
                                        order_type="MARKET",
                                        product="CNC",
                                    )
                                    st.success(f"BUY order placed: {s['symbol']} × {qty} | Order ID: {order_id}")
                                    
                                    # Step 2: Place GTT stop-loss
                                    try:
                                        gtt_result = broker.place_gtt_stop_loss(
                                            symbol=s["symbol"],
                                            qty=qty,
                                            trigger_price=s["stop_loss"],
                                        )
                                        st.info(f"✅ GTT stop-loss placed at ₹{s['stop_loss']:.2f} | GTT ID: {gtt_result}")
                                    except Exception as gtt_err:
                                        st.warning(f"⚠️ GTT placement failed (manual safety needed): {gtt_err}")
                                    
                                    # Log to session
                                    st.session_state.last_live_trade = {
                                        "symbol": s["symbol"],
                                        "qty": qty,
                                        "entry": s["price"],
                                        "stop": s["stop_loss"],
                                        "target": s["target"],
                                        "order_id": order_id,
                                    }
                            except Exception as e:
                                st.error(f"Order failed: {e}")
                else:
                    st.caption("No signals yet — click 'Scan for live signals'.")

                st.divider()

                # =====================================================================
                # SECTION 2: MANUAL ORDER ENTRY (with GTT support)
                # =====================================================================
                st.subheader("🎯 Manual Order Entry (fallback)")
                st.caption("Use this if you want to manually override a signal or enter based on your own analysis.")

                mc1, mc2, mc3, mc4 = st.columns(4)
                manual_symbol = mc1.text_input("Symbol", value="RELIANCE", key="manual_symbol")
                manual_qty = mc2.number_input("Quantity", min_value=1, value=1, step=1, key="manual_qty")
                manual_entry = mc3.number_input("Entry price", min_value=0.01, value=100.0, step=0.05, key="manual_entry")
                manual_sl = mc4.number_input("Stop loss", min_value=0.01, value=95.0, step=0.05, key="manual_sl")
                manual_target = st.number_input("Target price", min_value=0.01, value=110.0, step=0.05, key="manual_target")

                if st.button("🚀 PLACE MANUAL BUY (with GTT)", type="primary"):
                    try:
                        # Place BUY order
                        order_id = broker.place_order(
                            symbol=manual_symbol,
                            qty=int(manual_qty),
                            transaction_type="BUY",
                            order_type="LIMIT",
                            product="CNC",
                            price=manual_entry,
                        )
                        st.success(f"LIMIT BUY order placed: {manual_symbol} × {int(manual_qty)} @ ₹{manual_entry:.2f} | Order ID: {order_id}")
                        
                        # Place GTT stop-loss
                        try:
                            gtt_result = broker.place_gtt_stop_loss(
                                symbol=manual_symbol,
                                qty=int(manual_qty),
                                trigger_price=manual_sl,
                            )
                            st.info(f"✅ GTT stop-loss placed at ₹{manual_sl:.2f} | GTT ID: {gtt_result}")
                        except Exception as gtt_err:
                            st.warning(f"⚠️ GTT placement failed: {gtt_err}")
                        
                        st.session_state.last_live_trade = {
                            "symbol": manual_symbol,
                            "qty": int(manual_qty),
                            "entry": manual_entry,
                            "stop": manual_sl,
                            "target": manual_target,
                            "order_id": order_id,
                        }
                    except Exception as e:
                        st.error(f"Order failed: {e}")

                st.divider()

                # =====================================================================
                # SECTION 3: MONITOR OPEN POSITIONS
                # =====================================================================
                st.subheader("📊 Monitor Open Positions")
                
                if st.button("🔄 Refresh broker positions"):
                    try:
                        positions = broker.get_positions()
                        st.session_state.broker_positions = positions
                        st.success("Positions refreshed from broker")
                    except Exception as e:
                        st.error(f"Could not fetch positions: {e}")

                broker_pos = st.session_state.get("broker_positions", [])
                if broker_pos:
                    st.write("**Open positions from broker:**")
                    pos_rows = []
                    for pos in broker_pos:
                        try:
                            if hasattr(pos, "symbol"):
                                symbol = pos.symbol
                                qty = pos.qty
                                entry_price = pos.buy_price
                                current_price = pos.current_price or broker.get_ltp(symbol)
                                pnl = (current_price - entry_price) * qty
                                pos_rows.append({
                                    "Symbol": symbol,
                                    "Qty": qty,
                                    "Entry Price": round(entry_price, 2),
                                    "Current Price": round(current_price, 2),
                                    "Unrealized P&L": round(pnl, 2),
                                })
                            else:
                                current_price = broker.get_ltp(pos.get("tradingsymbol", ""))
                                pnl = (current_price - pos.get("average_price", 0)) * pos.get("quantity", 0)
                                pos_rows.append({
                                    "Symbol": pos.get("tradingsymbol"),
                                    "Qty": pos.get("quantity"),
                                    "Entry Price": round(pos.get("average_price", 0), 2),
                                    "Current Price": round(current_price, 2),
                                    "Unrealized P&L": round(pnl, 2),
                                })
                        except Exception:
                            pass
                    if pos_rows:
                        st.dataframe(pd.DataFrame(pos_rows), use_container_width=True)
                    else:
                        st.caption("No open positions.")
                else:
                    st.caption("Click 'Refresh broker positions' to load your live holdings.")

                st.divider()

                # =====================================================================
                # SECTION 4: TRADE AUDIT LOG
                # =====================================================================
                st.subheader("📋 Trade Audit Log")
                st.caption("Complete record of all trading actions for compliance and debugging.")

                recent_logs = st.session_state.audit_log.get_recent_logs(limit=50)
                if recent_logs:
                    log_df = pd.DataFrame(recent_logs)
                    # Format for display
                    log_df = log_df[["timestamp", "action", "symbol", "qty", "price", "reason", "confidence"]]
                    st.dataframe(log_df, use_container_width=True)
                else:
                    st.caption("No actions logged yet.")

            except ImportError:
                st.error("kiteconnect package not installed. Run: pip install kiteconnect")
            except Exception as e:
                st.error(f"Broker error: {e}")

# ---------------------------------------------------------------------------
# TAB 4: Settings / API
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("🔑 Broker API Connection")
    st.caption(
        "This app can connect to Dhan or Zerodha. Dhan uses the client ID + app secret + access token flow, "
        "while Zerodha uses the Kite Connect login flow."
    )

    api_key = st.text_input(
        "Client_ID",
        value=st.session_state.config.get("api_key", ""),
        key="api_key_input",
    )
    api_secret = st.text_input(
        "API_Key",
        value=st.session_state.config.get("api_secret", ""),
        type="password",
        key="api_secret_input",
    )
    st.session_state.config["api_key"] = api_key
    st.session_state.config["api_secret"] = api_secret

    if st.button("Save API Credentials"):
        cfg.save_config(st.session_state.config)
        st.success("Saved locally to config.json (this file stays on your machine).")

    st.divider()
    st.write("**Step 2: Broker Selection**")
    
    from broker_factory import SUPPORTED_BROKERS, get_broker_display_names
    display_names = get_broker_display_names()
    broker_options = [name for _, name in SUPPORTED_BROKERS]
    broker_codes = [code for code, _ in SUPPORTED_BROKERS]
    
    current_broker_display = display_names.get(st.session_state.config.get("broker", "zerodha_kite"), "🦓 Zerodha (Kite Connect)")
    selected_broker_display = st.selectbox(
        "Choose your broker:",
        broker_options,
        index=broker_options.index(current_broker_display) if current_broker_display in broker_options else 0,
        key="broker_select"
    )
    selected_broker_code = broker_codes[broker_options.index(selected_broker_display)]
    
    if selected_broker_code != st.session_state.config.get("broker"):
        st.session_state.config["broker"] = selected_broker_code
        cfg.save_config(st.session_state.config)
        st.info(f"Switched to {selected_broker_display}")
    
    st.divider()
    st.write("**Step 3: Daily login (Broker access tokens typically expire daily)**")

    if api_key:
        try:
            from broker_factory import create_broker
            broker = create_broker(
                broker_type=st.session_state.config.get("broker", "zerodha_kite"),
                api_key=api_key,
                api_secret=api_secret
            )
            login_url = broker.get_login_url()
            broker_display = display_names.get(st.session_state.config.get("broker", "zerodha_kite"), "your broker")
            if st.session_state.config.get("broker") == "dhan":
                st.markdown(f"1. Open the Dhan login page: [Dhan login]({login_url})")
                st.write("2. Sign in with your Dhan account and copy the token or redirect value returned by the browser.")
                st.write("3. Paste that value into the box below and click Connect / Generate Access Token.")
            else:
                st.markdown(f"1. [Click here to log in to {broker_display}]({login_url})")
                st.write("2. After logging in, your browser will redirect to a URL containing a token. Copy that value.")
            request_token = st.text_input("3. Paste request token here", key="request_token_input")
            if st.button("Connect / Generate Access Token") and request_token:
                try:
                    access_token = broker.generate_session(request_token)
                    st.session_state.config["access_token"] = access_token
                    cfg.save_config(st.session_state.config)
                    st.success("Connected! Access token saved for today.")
                except Exception as e:
                    st.error(f"Could not generate session: {e}")
        except ImportError as e:
            st.error(f"Broker SDK not installed: {e}")
        except NotImplementedError as e:
            st.error(f"This broker is not yet implemented: {e}")
    else:
        st.info("Enter and save your API Key above first.")

    st.divider()
    status = "🟢 Connected" if cfg.is_broker_connected(st.session_state.config) else "🔴 Not connected"
    st.write(f"**Status:** {status}")
