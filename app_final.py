"""
app_final.py - NSE AI Trading Platform
Complete Dhan-Only Implementation (No External Dependencies)

Run with: streamlit run app_final.py
"""

import datetime as dt
import os
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st

# Core imports
import config as cfg
import data_feed
from strategy import StrategyParams, generate_signals, FILTER_DESCRIPTIONS, get_adx_status
from backtester import run_backtest, trades_to_dataframe
from risk_manager import (
    DailyLossTracker, calculate_position_size, DrawdownTracker,
    ConsecutiveLossTracker, TradeAuditLog, is_market_open, get_market_hours_status,
    OpenPositionTracker,
)

# Import new modules (without external dependencies)
try:
    from order_manager import OrderManager, OrderStatus
    from notifications import NotificationManager, NotificationLevel
    from screener import StockScreener, StockUniverse, FILTER_PRESETS
    from paper_trading_enhanced import PaperPortfolioEnhanced
    ENHANCED_MODULES = True
except ImportError as e:
    st.error(f"Enhanced modules not available: {e}")
    ENHANCED_MODULES = False

from broker_factory import create_broker

st.set_page_config(
    page_title="NSE AI Trading Platform - Dhan",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for better UX
st.markdown("""
<style>
    .stMetric {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
    }
    .metric-card {
        background: #16213e;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("📈 NSE AI Trading Platform")
st.caption("Professional Algorithmic Trading for Indian Markets | Powered by Dhan")

# Session state initialization
if "config" not in st.session_state:
    st.session_state.config = cfg.load_config()
    st.session_state.config["broker"] = "dhan"

if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "paper"

if "daily_loss_tracker" not in st.session_state:
    st.session_state.daily_loss_tracker = DailyLossTracker(
        capital=st.session_state.config["capital"],
        max_daily_loss_pct=st.session_state.config["max_daily_loss_pct"],
    )

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

# Initialize enhanced modules if available
if ENHANCED_MODULES:
    if "order_manager" not in st.session_state:
        st.session_state.order_manager = OrderManager()
    
    if "notification_manager" not in st.session_state:
        st.session_state.notification_manager = NotificationManager()
    
    if "screener" not in st.session_state:
        st.session_state.screener = StockScreener()
        st.session_state.stock_universe = StockUniverse()
    
    if "paper_portfolio_enhanced" not in st.session_state:
        st.session_state.paper_portfolio_enhanced = PaperPortfolioEnhanced(
            starting_cash=st.session_state.config["capital"]
        )
    
    if "latest_scan_results" not in st.session_state:
        st.session_state.latest_scan_results = []

# Manual refresh button
col1, col2 = st.columns([6, 1])
with col2:
    if st.button("🔄 Refresh"):
        st.rerun()

# SIDEBAR
with st.sidebar:
    st.header("⚙️ Risk & Strategy")
    
    st.session_state.config["capital"] = st.number_input(
        "Capital (₹)", min_value=1000.0,
        value=float(st.session_state.config["capital"]), step=1000.0,
    )
    
    st.session_state.config["risk_per_trade_pct"] = st.slider(
        "Risk/Trade (%)", 0.25, 5.0, 
        float(st.session_state.config["risk_per_trade_pct"]), 0.25
    )
    
    st.session_state.config["max_daily_loss_pct"] = st.slider(
        "Max Daily Loss (%)", 1.0, 10.0,
        float(st.session_state.config["max_daily_loss_pct"]), 0.5,
    )
    
    st.session_state.config["min_risk_reward"] = st.slider(
        "Min R:R", 1.0, 4.0,
        float(st.session_state.config["min_risk_reward"]), 0.25,
    )
    
    with st.expander("⚙️ Advanced", expanded=False):
        st.session_state.config["adx_threshold"] = st.slider(
            "ADX Threshold", 15.0, 40.0, 
            float(st.session_state.config.get("adx_threshold", 25.0)), 1.0
        )
        st.session_state.config["volume_ratio_min"] = st.slider(
            "Volume Ratio", 1.0, 3.0, 
            float(st.session_state.config.get("volume_ratio_min", 1.1)), 0.1
        )
    
    if st.button("💾 Save"):
        cfg.save_config(st.session_state.config)
        st.success("Saved!")
    
    st.divider()
    st.subheader("🧭 Mode")
    mode = st.radio("", ["Paper", "Live"], horizontal=True)
    st.session_state.trading_mode = mode.lower()
    
    st.divider()
    st.caption(st.session_state.daily_loss_tracker.status_text())

# TABS
if ENHANCED_MODULES:
    tabs = st.tabs(["📊 Backtest", "🔍 Screener", "📝 Paper", "💰 Live", "📋 Orders", "🔔 Alerts", "🔑 Settings"])
    tab_bt, tab_sc, tab_pp, tab_lv, tab_od, tab_nt, tab_st = tabs
else:
    tabs = st.tabs(["📊 Backtest", "📝 Paper", "💰 Live", "🔑 Settings"])
    tab_bt, tab_pp, tab_lv, tab_st = tabs

# TAB: BACKTEST
with tab_bt:
    st.subheader("📊 Backtest Strategy")
    
    col1, col2, col3 = st.columns(3)
    symbol = col1.text_input("Symbol", "RELIANCE", key="bt_sym")
    timeframe = col2.selectbox("Timeframe", ["15m", "1h", "1d", "1wk"], index=2, key="bt_tf")
    use_nifty = col3.checkbox("Use Nifty Filter", True)
    
    if st.button("▶️ Run Backtest", type="primary"):
        with st.spinner("Running backtest..."):
            try:
                df = data_feed.get_historical(symbol, interval=timeframe)
                if df.empty:
                    st.error("No data found")
                else:
                    index_df = None
                    if use_nifty:
                        index_df = data_feed.get_historical("^NSEI", interval=timeframe)
                    
                    params = StrategyParams()
                    params.min_risk_reward = st.session_state.config["min_risk_reward"]
                    signals_df = generate_signals(df, params, index_df)
                    
                    result = run_backtest(
                        signals_df,
                        capital=st.session_state.config["capital"],
                        risk_per_trade_pct=st.session_state.config["risk_per_trade_pct"]
                    )
                    
                    m = result["metrics"]
                    col1, col2, col3, col4, col5 = st.columns(5)
                    col1.metric("Trades", m["num_trades"])
                    col2.metric("Win Rate", f"{m.get('win_rate_pct', 0):.1f}%")
                    col3.metric("Return", f"{m.get('total_return_pct', 0):.2f}%")
                    col4.metric("Max DD", f"{m.get('max_drawdown_pct', 0):.2f}%")
                    col5.metric("Profit Factor", f"{m.get('profit_factor', 0):.2f}")
                    
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=result["equity_curve"].index,
                        y=result["equity_curve"],
                        name="Equity",
                        line=dict(color='#00ff88', width=2)
                    ))
                    fig.update_layout(
                        title="Equity Curve",
                        height=350,
                        template="plotly_dark",
                        margin=dict(t=40, b=20)
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.subheader("Trade Log")
                    st.dataframe(trades_to_dataframe(result["trades"]), use_container_width=True)
                    
            except Exception as e:
                st.error(f"Backtest failed: {e}")

# TAB: SCREENER (if available)
if ENHANCED_MODULES:
    with tab_sc:
        st.subheader("🔍 Stock Screener")
        
        col1, col2, col3 = st.columns(3)
        universe = col1.selectbox("Universe", ["Nifty 50", "Nifty 100", "F&O"])
        preset = col2.selectbox("Preset", ["moderate", "conservative", "aggressive"])
        tf = col3.selectbox("Timeframe", ["15m", "1h", "1d"], index=2)
        
        if universe == "Nifty 50":
            syms = st.session_state.stock_universe.get_nifty_50()
        elif universe == "Nifty 100":
            syms = st.session_state.stock_universe.get_nifty_100()
        else:
            syms = st.session_state.stock_universe.get_fno_stocks()
        
        if st.button("🔍 Scan Now", type="primary"):
            with st.spinner(f"Scanning {len(syms)} stocks..."):
                results = st.session_state.screener.scan(syms, tf, preset, max_workers=3)
                st.session_state.latest_scan_results = results
                st.success(f"Found {len(results)} signals")
        
        if st.session_state.latest_scan_results:
            df = st.session_state.screener.results_to_dataframe(st.session_state.latest_scan_results)
            st.dataframe(df, use_container_width=True, height=400)

# TAB: PAPER TRADING
with tab_pp:
    st.subheader("📝 Paper Trading")
    
    watchlist = st.text_input("Watchlist", "RELIANCE, TCS, HDFCBANK", key="pp_wl")
    
    if st.button("🔍 Scan"):
        with st.spinner("Scanning..."):
            symbols = [s.strip() for s in watchlist.split(",")]
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
                                "stop": last["stop_loss"],
                                "target": last["target"],
                                "rr": round(last["risk_reward"], 2),
                            })
                except:
                    pass
            
            st.session_state.paper_signals = results
    
    sigs = st.session_state.get("paper_signals", [])
    if sigs:
        st.write(f"**Found {len(sigs)} signals:**")
        for s in sigs:
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.write(f"**{s['symbol']}**")
            col2.write(f"₹{s['price']:.2f}")
            col3.write(f"SL: {s['stop']:.2f}")
            col4.write(f"Tgt: {s['target']:.2f}")
            col5.write(f"R:R {s['rr']}")
            if col6.button("Buy", key=f"pb_{s['symbol']}"):
                st.success(f"Paper bought {s['symbol']}")
    
    if ENHANCED_MODULES:
        st.divider()
        st.subheader("Portfolio")
        pf = st.session_state.paper_portfolio_enhanced
        summary = pf.get_portfolio_summary()
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Cash", f"₹{summary['current_cash']:,.0f}")
        col2.metric("Equity", f"₹{summary['total_equity']:,.0f}")
        col3.metric("Return", f"{summary['total_return_pct']:+.2f}%")
        col4.metric("Trades", summary['total_trades'])

# TAB: LIVE TRADING
with tab_lv:
    st.subheader("💰 Live Trading")
    st.error("⚠️ REAL MONEY - REAL ORDERS")
    
    col1, col2, col3, col4 = st.columns(4)
    status_icon = "🟢" if st.session_state.bot_status == "RUNNING" else "🔴"
    col1.metric("Bot", f"{status_icon} {st.session_state.bot_status}")
    col2.metric("Market", "🟢 Open" if is_market_open() else "🔴 Closed")
    col3.metric("Daily P&L", f"₹{st.session_state.daily_loss_tracker.realized_pnl_today:,.0f}")
    
    if ENHANCED_MODULES:
        col4.metric("Orders", len(st.session_state.order_manager.get_pending_orders()))
    
    st.divider()
    
    if not st.session_state.live_trading_confirmed:
        st.warning("⚠️ Live trading DISABLED")
        st.session_state.live_trading_confirmed = st.checkbox(
            "I understand risks and accept full responsibility"
        )
    
    if st.session_state.live_trading_confirmed:
        if not is_market_open():
            st.error(f"Market Closed - {get_market_hours_status()}")
        else:
            st.success("✅ Live trading ENABLED")
            
            try:
                broker = create_broker(
                    "dhan",
                    st.session_state.config.get("api_key", ""),
                    st.session_state.config.get("api_secret", ""),
                    st.session_state.config.get("access_token", "")
                )
                
                st.subheader("📡 Scan & Trade")
                wl = st.text_input("Watchlist", "RELIANCE, TCS", key="lv_wl")
                
                if st.button("🔍 Scan Live"):
                    with st.spinner("Scanning..."):
                        symbols = [s.strip() for s in wl.split(",")]
                        results = []
                        
                        for sym in symbols:
                            try:
                                df = data_feed.get_historical(sym, "1d")
                                if not df.empty:
                                    params = StrategyParams()
                                    sig_df = generate_signals(df, params)
                                    last = sig_df.iloc[-1]
                                    if last["signal"] == "BUY":
                                        results.append({
                                            "symbol": sym,
                                            "price": last["Close"],
                                            "stop": last["stop_loss"],
                                            "target": last["target"],
                                        })
                            except:
                                pass
                        
                        st.session_state.live_signals = results
                
                sigs = st.session_state.get("live_signals", [])
                if sigs:
                    for s in sigs:
                        col1, col2, col3, col4, col5 = st.columns(5)
                        col1.write(f"**{s['symbol']}**")
                        col2.write(f"₹{s['price']:.2f}")
                        col3.write(f"SL: {s['stop']:.2f}")
                        col4.write(f"Tgt: {s['target']:.2f}")
                        if col5.button("🚀 BUY", key=f"lb_{s['symbol']}"):
                            try:
                                qty = calculate_position_size(
                                    st.session_state.config["capital"],
                                    st.session_state.config["risk_per_trade_pct"],
                                    s["price"],
                                    s["stop"]
                                )
                                oid = broker.place_order(s["symbol"], qty, "BUY", "MARKET", "CNC")
                                st.success(f"✅ Order: {oid}")
                                
                                if ENHANCED_MODULES:
                                    st.session_state.notification_manager.notify_order_placed(
                                        s["symbol"], qty, s["price"], "MARKET", oid
                                    )
                            except Exception as e:
                                st.error(f"Failed: {e}")
            
            except Exception as e:
                st.error(f"Broker error: {e}")
                st.info("Configure Dhan credentials in Settings tab")

# TAB: ORDERS (if available)
if ENHANCED_MODULES:
    with tab_od:
        st.subheader("📋 Order Management")
        
        om = st.session_state.order_manager
        all_orders = list(om.orders.values())
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Pending", len(om.get_pending_orders()))
        col2.metric("Filled", len([o for o in all_orders if o.status == OrderStatus.FILLED]))
        col3.metric("Rejected", len([o for o in all_orders if o.status == OrderStatus.REJECTED]))
        col4.metric("Total", len(all_orders))
        
        if all_orders:
            data = []
            for o in sorted(all_orders, key=lambda x: x.created_at, reverse=True):
                data.append({
                    "Symbol": o.symbol,
                    "Type": o.transaction_type.value,
                    "Qty": o.qty,
                    "Status": o.status.value,
                    "Filled": o.filled_qty,
                    "Price": f"₹{o.average_price:.2f}" if o.average_price > 0 else "-",
                    "Time": o.created_at[:19],
                })
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            st.info("No orders yet")
        
        st.divider()
        st.subheader("Trade History")
        trades = om.get_trade_history()
        if trades:
            tdata = []
            for t in trades:
                tdata.append({
                    "Symbol": t.symbol,
                    "Entry": f"₹{t.entry_price:.2f}",
                    "Exit": f"₹{t.exit_price:.2f}" if t.exit_price else "-",
                    "P&L": f"₹{t.realized_pnl:,.2f}",
                    "P&L%": f"{t.realized_pnl_pct:.2f}%",
                })
            st.dataframe(pd.DataFrame(tdata), use_container_width=True)
        else:
            st.info("No trades yet")

# TAB: NOTIFICATIONS (if available)
if ENHANCED_MODULES:
    with tab_nt:
        st.subheader("🔔 Notifications")
        
        nm = st.session_state.notification_manager
        
        with st.expander("📱 Telegram", expanded=True):
            enabled = st.checkbox("Enable", nm.config["telegram"]["enabled"])
            if enabled:
                token = st.text_input("Bot Token", type="password", key="tg_token")
                chat = st.text_input("Chat ID", key="tg_chat")
                if st.button("Save"):
                    nm.enable_telegram(token, chat)
                    st.success("Saved!")
                if st.button("Test"):
                    nm.send_notification("Test", "Working!", NotificationLevel.INFO)
                    st.success("Sent!")
        
        st.divider()
        st.subheader("Recent")
        for n in reversed(nm.get_recent_notifications(10)):
            st.write(f"**{n.title}** - {n.timestamp[:19]}")
            st.caption(n.message)
            st.divider()

# TAB: SETTINGS
with tab_st:
    st.subheader("🔑 Dhan Connection")
    
    api_key = st.text_input("Client ID", st.session_state.config.get("api_key", ""), key="dhan_id")
    api_secret = st.text_input("API Key", st.session_state.config.get("api_secret", ""), type="password", key="dhan_key")
    
    st.session_state.config["api_key"] = api_key
    st.session_state.config["api_secret"] = api_secret
    
    if st.button("💾 Save Credentials"):
        cfg.save_config(st.session_state.config)
        st.success("Saved!")
    
    st.divider()
    st.write("**Login**")
    
    if api_key:
        try:
            broker = create_broker("dhan", api_key, api_secret)
            login_url = broker.get_login_url()
            st.markdown(f"[🔗 Open Dhan Login]({login_url})")
            
            token = st.text_input("Paste token/redirect URL", key="dhan_token")
            if st.button("Connect"):
                try:
                    access = broker.generate_session(token)
                    st.session_state.config["access_token"] = access
                    cfg.save_config(st.session_state.config)
                    st.success("✅ Connected!")
                except Exception as e:
                    st.error(f"Failed: {e}")
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.info("Enter Client ID above")
    
    st.divider()
    status = "🟢 Connected" if cfg.is_broker_connected(st.session_state.config) else "🔴 Not Connected"
    st.write(f"**Status:** {status}")

# Footer
st.divider()
st.caption("NSE AI Trading Platform v2.0 | Professional Grade | Dhan Integration")
st.caption("⚠️ Trading involves risk. Past performance does not guarantee future results.")

