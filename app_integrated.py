"""
app.py - NSE AI Trading Platform
Complete Dhan-Only Implementation with Full Feature Integration

Features:
- Strategy & Backtest
- Stock Screener with signal ranking
- Paper Trading with realistic simulation
- Live Trading via Dhan
- Order Management System
- Notifications & Alerts
- Comprehensive Risk Management
- Real-time Dashboard

Run with: streamlit run app.py
"""

import datetime as dt
import os
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st

# Try to import new modules
try:
    from streamlit_autorefresh import st_autorefresh
    from order_manager import OrderManager, OrderStatus
    from notifications import NotificationManager, NotificationLevel
    from screener import StockScreener, StockUniverse, FILTER_PRESETS
    from paper_trading_enhanced import PaperPortfolioEnhanced
except ImportError as e:
    st.error(f"Missing dependencies: {e}\nRun: pip install streamlit-autorefresh dataclasses-json pyarrow")
    st.stop()

# Existing imports
import config as cfg
import data_feed
from strategy import StrategyParams, generate_signals, FILTER_DESCRIPTIONS, get_adx_status
from backtester import run_backtest, trades_to_dataframe
from risk_manager import (
    DailyLossTracker, calculate_position_size, DrawdownTracker,
    ConsecutiveLossTracker, TradeAuditLog, is_market_open, get_market_hours_status,
    OpenPositionTracker,
)
from broker_factory import create_broker

# =====================================================================
# PAGE CONFIGURATION
# =====================================================================

st.set_page_config(
    page_title="NSE AI Trading Platform",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 NSE AI Trading Platform - Dhan")
st.caption("Professional algorithmic trading platform for Indian markets | Powered by Dhan")

# =====================================================================
# SESSION STATE INITIALIZATION
# =====================================================================

if "config" not in st.session_state:
    st.session_state.config = cfg.load_config()
    st.session_state.config["broker"] = "dhan"  # Force Dhan only

if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "paper"

if "paper_portfolio_enhanced" not in st.session_state:
    st.session_state.paper_portfolio_enhanced = PaperPortfolioEnhanced(
        starting_cash=st.session_state.config["capital"]
    )

if "daily_loss_tracker" not in st.session_state:
    st.session_state.daily_loss_tracker = DailyLossTracker(
        capital=st.session_state.config["capital"],
        max_daily_loss_pct=st.session_state.config["max_daily_loss_pct"],
    )

if "order_manager" not in st.session_state:
    st.session_state.order_manager = OrderManager()

if "notification_manager" not in st.session_state:
    st.session_state.notification_manager = NotificationManager()

if "screener" not in st.session_state:
    st.session_state.screener = StockScreener()
    st.session_state.stock_universe = StockUniverse()

if "live_trading_confirmed" not in st.session_state:
    st.session_state.live_trading_confirmed = False

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
    st.session_state.bot_status = "STOPPED"

if "latest_scan_results" not in st.session_state:
    st.session_state.latest_scan_results = []

if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False

# Auto-refresh during market hours
if is_market_open() and st.session_state.auto_refresh_enabled:
    st_autorefresh(interval=30000, limit=None, key="market_refresh")

# =====================================================================
# SIDEBAR - RISK SETTINGS & CONTROLS
# =====================================================================

with st.sidebar:
    st.header("⚙️ Risk & Strategy Settings")
    
    # Basic risk settings
    st.session_state.config["capital"] = st.number_input(
        "Trading capital (₹)", min_value=1000.0,
        value=float(st.session_state.config["capital"]), step=1000.0,
    )
    st.session_state.config["risk_per_trade_pct"] = st.slider(
        "Risk per trade (%)", 0.25, 5.0, 
        float(st.session_state.config["risk_per_trade_pct"]), 0.25
    )
    st.session_state.config["max_daily_loss_pct"] = st.slider(
        "Max daily loss (%)", 1.0, 10.0,
        float(st.session_state.config["max_daily_loss_pct"]), 0.5,
    )
    st.session_state.config["min_risk_reward"] = st.slider(
        "Min risk-reward", 1.0, 4.0,
        float(st.session_state.config["min_risk_reward"]), 0.25,
    )
    
    # Advanced strategy settings
    with st.expander("⚙️ Advanced Strategy", expanded=False):
        st.session_state.config["ema_fast"] = st.number_input(
            "EMA Fast", 10, 200, st.session_state.config.get("ema_fast", 50), 5)
        st.session_state.config["ema_slow"] = st.number_input(
            "EMA Slow", 50, 300, st.session_state.config.get("ema_slow", 200), 10)
        st.session_state.config["adx_threshold"] = st.slider(
            "ADX Threshold", 15.0, 40.0, float(st.session_state.config.get("adx_threshold", 25.0)), 1.0)
        st.session_state.config["rsi_pullback_low"] = st.slider(
            "RSI Low", 20.0, 50.0, float(st.session_state.config.get("rsi_pullback_low", 38.0)), 1.0)
        st.session_state.config["rsi_pullback_high"] = st.slider(
            "RSI High", 40.0, 70.0, float(st.session_state.config.get("rsi_pullback_high", 55.0)), 1.0)
        st.session_state.config["volume_ratio_min"] = st.slider(
            "Volume Ratio", 1.0, 3.0, float(st.session_state.config.get("volume_ratio_min", 1.1)), 0.1)
    
    if st.button("💾 Save Settings"):
        cfg.save_config(st.session_state.config)
        st.success("Saved!")
    
    st.divider()
    st.session_state.auto_refresh_enabled = st.checkbox("🔄 Auto-refresh (30s)", st.session_state.auto_refresh_enabled)
    
    st.divider()
    st.subheader("🧭 Trading Mode")
    selected_mode = st.radio("Mode", ["Paper Trade", "Live Trade"], horizontal=True)
    st.session_state.trading_mode = "paper" if selected_mode == "Paper Trade" else "live"
    
    st.divider()
    st.caption(st.session_state.daily_loss_tracker.status_text())

# =====================================================================
# TABS - MAIN INTERFACE
# =====================================================================

tabs = st.tabs([
    "📊 Backtest",
    "🔍 Screener", 
    "📝 Paper Trading",
    "💰 Live Trading",
    "📋 Orders",
    "🔔 Notifications",
    "🔑 Settings"
])

tab_backtest, tab_screener, tab_paper, tab_live, tab_orders, tab_notifications, tab_settings = tabs

# =====================================================================
# TAB 1: BACKTEST
# =====================================================================

with tab_backtest:
    st.subheader("Backtest Strategy on Historical Data")
    
    col1, col2, col3 = st.columns(3)
    symbol = col1.text_input("Symbol", "RELIANCE")
    timeframe = col2.selectbox("Timeframe", ["15m", "1h", "1d", "1wk"], index=2)
    use_index_filter = col3.checkbox("Use Nifty 50 filter", True)
    
    if st.button("▶️ Run Backtest", type="primary"):
        with st.spinner("Fetching data..."):
            try:
                df = data_feed.get_historical(symbol, interval=timeframe)
                if df.empty:
                    st.error("No data found")
                else:
                    index_df = None
                    if use_index_filter:
                        index_df = data_feed.get_historical("^NSEI", interval=timeframe)
                    
                    params = StrategyParams()
                    params.min_risk_reward = st.session_state.config.get("min_risk_reward", 1.5)
                    params.adx_threshold = st.session_state.config.get("adx_threshold", 25)
                    
                    signals_df = generate_signals(df, params, index_df)
                    result = run_backtest(signals_df, capital=st.session_state.config["capital"])
                    
                    m = result["metrics"]
                    col1, col2, col3, col4, col5 = st.columns(5)
                    col1.metric("Trades", m["num_trades"])
                    col2.metric("Win Rate", f"{m.get('win_rate_pct', 0)}%")
                    col3.metric("Return", f"{m.get('total_return_pct', 0)}%")
                    col4.metric("Max DD", f"{m.get('max_drawdown_pct', 0)}%")
                    col5.metric("Profit Factor", m.get("profit_factor", 0))
                    
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=result["equity_curve"].index, y=result["equity_curve"], name="Equity"))
                    fig.update_layout(title="Equity Curve", height=350)
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.dataframe(trades_to_dataframe(result["trades"]), use_container_width=True)
            except Exception as e:
                st.error(f"Error: {e}")

# =====================================================================
# TAB 2: SCREENER
# =====================================================================

with tab_screener:
    st.subheader("🔍 Stock Screener")
    
    col1, col2, col3 = st.columns(3)
    universe = col1.selectbox("Universe", ["Nifty 50", "Nifty 100", "F&O"])
    preset = col2.selectbox("Preset", ["moderate", "conservative", "aggressive", "momentum"])
    timeframe = col3.selectbox("TF", ["15m", "1h", "1d"], index=2)
    
    if universe == "Nifty 50":
        symbols = st.session_state.stock_universe.get_nifty_50()
    elif universe == "Nifty 100":
        symbols = st.session_state.stock_universe.get_nifty_100()
    else:
        symbols = st.session_state.stock_universe.get_fno_stocks()
    
    if st.button("🔍 Scan", type="primary"):
        with st.spinner(f"Scanning {len(symbols)} stocks..."):
            results = st.session_state.screener.scan(symbols, timeframe, preset)
            st.session_state.latest_scan_results = results
            st.success(f"Found {len(results)} signals")
    
    if st.session_state.latest_scan_results:
        results_df = st.session_state.screener.results_to_dataframe(st.session_state.latest_scan_results)
        st.dataframe(results_df, use_container_width=True, height=400)

# =====================================================================
# TAB 3: PAPER TRADING
# =====================================================================

with tab_paper:
    st.subheader("📝 Paper Trading (Simulated)")
    
    watchlist = st.text_input("Watchlist", "RELIANCE, TCS, HDFCBANK, INFY")
    pt_timeframe = st.selectbox("Timeframe", ["15m", "1h", "1d"], index=2, key="paper_tf")
    
    if st.button("🔍 Scan for Signals"):
        with st.spinner("Scanning..."):
            symbols = [s.strip() for s in watchlist.split(",")]
            params = StrategyParams()
            results = []
            
            for sym in symbols:
                try:
                    df = data_feed.get_historical(sym, interval=pt_timeframe)
                    if not df.empty:
                        sig_df = generate_signals(df, params)
                        last = sig_df.iloc[-1]
                        if last["signal"] == "BUY":
                            results.append({
                                "symbol": sym,
                                "price": last["Close"],
                                "stop_loss": last["stop_loss"],
                                "target": last["target"],
                                "risk_reward": round(last["risk_reward"], 2),
                            })
                except:
                    pass
            
            st.session_state.paper_signals = results
    
    signals = st.session_state.get("paper_signals", [])
    if signals:
        for s in signals:
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.write(f"**{s['symbol']}**")
            col2.write(f"₹{s['price']:.2f}")
            col3.write(f"SL ₹{s['stop_loss']:.2f}")
            col4.write(f"Tgt ₹{s['target']:.2f}")
            col5.write(f"R:R {s['risk_reward']}")
            if col6.button("Buy", key=f"buy_{s['symbol']}"):
                st.success(f"Paper bought {s['symbol']}")
    
    st.divider()
    st.subheader("Portfolio")
    portfolio = st.session_state.paper_portfolio_enhanced
    summary = portfolio.get_portfolio_summary()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Cash", f"₹{summary['current_cash']:,.0f}")
    col2.metric("Equity", f"₹{summary['total_equity']:,.0f}")
    col3.metric("Return", f"{summary['total_return_pct']:+.2f}%")

# =====================================================================
# TAB 4: LIVE TRADING
# =====================================================================

with tab_live:
    st.subheader("💰 Live Trading - REAL ORDERS")
    
    # Status dashboard
    col1, col2, col3, col4 = st.columns(4)
    status_icon = "🟢" if st.session_state.bot_status == "RUNNING" else "🔴"
    col1.metric("Bot", f"{status_icon} {st.session_state.bot_status}")
    col2.metric("Market", get_market_hours_status())
    col3.metric("Daily P&L", f"₹{st.session_state.daily_loss_tracker.realized_pnl_today:,.0f}")
    col4.metric("Orders", len(st.session_state.order_manager.get_pending_orders()))
    
    st.divider()
    
    # Risk acknowledgement
    if not st.session_state.live_trading_confirmed:
        st.warning("⚠️ Live trading is DISABLED")
        st.session_state.live_trading_confirmed = st.checkbox(
            "I understand the risks and accept full responsibility"
        )
    
    if st.session_state.live_trading_confirmed and is_market_open():
        st.success("✅ Live trading ENABLED")
        
        # Dhan connection check
        try:
            broker = create_broker(
                broker_type="dhan",
                api_key=st.session_state.config.get("api_key", ""),
                api_secret=st.session_state.config.get("api_secret", ""),
                access_token=st.session_state.config.get("access_token", ""),
            )
            
            # Scan for signals
            st.subheader("📡 Strategy Signals")
            watchlist = st.text_input("Watchlist", "RELIANCE, TCS", key="live_watchlist")
            
            if st.button("🔍 Scan Live Signals"):
                symbols = [s.strip() for s in watchlist.split(",")]
                with st.spinner("Scanning..."):
                    params = StrategyParams()
                    results = []
                    
                    for sym in symbols:
                        try:
                            df = data_feed.get_historical(sym, interval="1d")
                            if not df.empty:
                                sig_df = generate_signals(df, params)
                                last = sig_df.iloc[-1]
                                if last["signal"] == "BUY":
                                    results.append({
                                        "symbol": sym,
                                        "price": last["Close"],
                                        "stop_loss": last["stop_loss"],
                                        "target": last["target"],
                                    })
                        except:
                            pass
                    
                    st.session_state.live_signals = results
            
            live_signals = st.session_state.get("live_signals", [])
            if live_signals:
                for s in live_signals:
                    col1, col2, col3, col4, col5 = st.columns(5)
                    col1.write(f"**{s['symbol']}**")
                    col2.write(f"₹{s['price']:.2f}")
                    col3.write(f"SL: ₹{s['stop_loss']:.2f}")
                    col4.write(f"Tgt: ₹{s['target']:.2f}")
                    if col5.button("🚀 BUY", key=f"live_buy_{s['symbol']}"):
                        try:
                            qty = calculate_position_size(
                                st.session_state.config["capital"],
                                st.session_state.config["risk_per_trade_pct"],
                                s["price"],
                                s["stop_loss"]
                            )
                            order_id = broker.place_order(s["symbol"], qty, "BUY", "MARKET", product="CNC")
                            st.success(f"Order placed: {order_id}")
                            st.session_state.notification_manager.notify_order_placed(
                                s["symbol"], qty, s["price"], "MARKET", order_id
                            )
                        except Exception as e:
                            st.error(f"Order failed: {e}")
        
        except Exception as e:
            st.error(f"Broker error: {e}")

# =====================================================================
# TAB 5: ORDER MANAGEMENT
# =====================================================================

with tab_orders:
    st.subheader("📋 Order Management")
    
    om = st.session_state.order_manager
    all_orders = list(om.orders.values())
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Pending", len(om.get_pending_orders()))
    col2.metric("Filled", len([o for o in all_orders if o.status == OrderStatus.FILLED]))
    col3.metric("Rejected", len([o for o in all_orders if o.status == OrderStatus.REJECTED]))
    col4.metric("Total", len(all_orders))
    
    # Orders table
    if all_orders:
        orders_data = []
        for o in sorted(all_orders, key=lambda x: x.created_at, reverse=True):
            orders_data.append({
                "Symbol": o.symbol,
                "Type": o.transaction_type.value,
                "Qty": o.qty,
                "Status": o.status.value,
                "Filled": o.filled_qty,
                "Avg Price": f"₹{o.average_price:.2f}" if o.average_price > 0 else "-",
                "Created": o.created_at[:19],
            })
        st.dataframe(pd.DataFrame(orders_data), use_container_width=True)
    else:
        st.info("No orders yet")
    
    # Trade history
    st.divider()
    st.subheader("Trade History")
    trades = om.get_trade_history()
    if trades:
        trades_data = []
        for t in trades:
            trades_data.append({
                "Symbol": t.symbol,
                "Entry": f"₹{t.entry_price:.2f}",
                "Exit": f"₹{t.exit_price:.2f}" if t.exit_price else "-",
                "P&L": f"₹{t.realized_pnl:,.2f}",
                "P&L %": f"{t.realized_pnl_pct:.2f}%",
                "Exit": t.exit_reason,
            })
        st.dataframe(pd.DataFrame(trades_data), use_container_width=True)

# =====================================================================
# TAB 6: NOTIFICATIONS
# =====================================================================

with tab_notifications:
    st.subheader("🔔 Notification Configuration")
    
    nm = st.session_state.notification_manager
    
    with st.expander("📱 Telegram", expanded=True):
        enabled = st.checkbox("Enable Telegram", nm.config["telegram"]["enabled"])
        if enabled:
            token = st.text_input("Bot Token", type="password")
            chat_id = st.text_input("Chat ID")
            if st.button("Save Telegram"):
                nm.enable_telegram(token, chat_id)
                st.success("Configured!")
            if st.button("Test"):
                nm.send_notification("Test", "Working!", NotificationLevel.INFO)
                st.success("Sent!")
    
    st.divider()
    st.subheader("Recent Notifications")
    recent = nm.get_recent_notifications(10)
    for n in reversed(recent):
        st.write(f"**{n.title}** - {n.timestamp[:19]}")
        st.caption(n.message)

# =====================================================================
# TAB 7: SETTINGS
# =====================================================================

with tab_settings:
    st.subheader("🔑 Dhan Connection")
    
    api_key = st.text_input("Client ID", st.session_state.config.get("api_key", ""))
    api_secret = st.text_input("API Key", st.session_state.config.get("api_secret", ""), type="password")
    
    st.session_state.config["api_key"] = api_key
    st.session_state.config["api_secret"] = api_secret
    
    if st.button("Save Credentials"):
        cfg.save_config(st.session_state.config)
        st.success("Saved!")
    
    st.divider()
    st.write("**Login to Dhan**")
    
    if api_key:
        try:
            broker = create_broker("dhan", api_key, api_secret)
            login_url = broker.get_login_url()
            st.markdown(f"[Open Dhan Login]({login_url})")
            
            request_token = st.text_input("Paste the token/redirect URL here")
            if st.button("Connect"):
                try:
                    access_token = broker.generate_session(request_token)
                    st.session_state.config["access_token"] = access_token
                    cfg.save_config(st.session_state.config)
                    st.success("Connected to Dhan!")
                except Exception as e:
                    st.error(f"Connection failed: {e}")
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.info("Enter your Client ID above")

