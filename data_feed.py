"""
data_feed.py
------------
Market data access layer.

- Historical OHLCV (for backtesting and for computing indicators in
  paper/live trading) comes from Yahoo Finance via yfinance. It's free,
  needs no API key, and covers NSE symbols using the ".NS" suffix
  (e.g. RELIANCE.NS, TCS.NS, ^NSEI for the Nifty 50 index).

- IMPORTANT: yfinance data is delayed and NOT suitable as the source of
  truth for live order execution. Once a broker is connected (see
  broker_dhan.py), live trading uses the broker's own LTP/quote API for
  the actual traded price. yfinance is still used in live mode only to
  compute indicators (trend/momentum/etc.) off recent history.
"""

import pandas as pd
import yfinance as yf

# Yahoo Finance interval -> max lookback period it will actually return
INTERVAL_PERIOD_MAP = {
    "15m": "60d",
    "1h": "730d",
    "1d": "5y",
    "1wk": "10y",
}

NIFTY50_SYMBOL = "^NSEI"


def to_yahoo_symbol(nse_symbol: str) -> str:
    """Convert a plain NSE trading symbol (RELIANCE) to Yahoo format (RELIANCE.NS)."""
    nse_symbol = nse_symbol.strip().upper()
    if nse_symbol.startswith("^") or nse_symbol.endswith(".NS"):
        return nse_symbol
    return f"{nse_symbol}.NS"


def get_historical(
    symbol: str,
    interval: str = "1d",
    period: str = None,
    broker=None,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV data.

    symbol:   plain NSE symbol, e.g. "RELIANCE" (auto-converted to RELIANCE.NS)
    interval: one of "15m", "1h", "1d", "1wk"
    period:   yfinance period string (e.g. "1y"); defaults to the max
              sensible lookback for the given interval
    broker:   optional connected BrokerInterface (e.g. a DhanBroker). When
              provided, its real-time Historical Data API is tried first and
              yfinance is used only as a fallback.
    """
    if broker is not None and hasattr(broker, "get_historical"):
        try:
            df = broker.get_historical(symbol, interval=interval, period=period)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    yahoo_symbol = to_yahoo_symbol(symbol)
    if period is None:
        period = INTERVAL_PERIOD_MAP.get(interval, "1y")

    df = yf.download(
        yahoo_symbol,
        interval=interval,
        period=period,
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        return df

    # yfinance sometimes returns a MultiIndex column header for single tickers
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        }
    )
    df.index.name = "Date"
    return df.dropna()


def get_latest_price(symbol: str, broker=None) -> float:
    """
    Best-effort 'current' price. When a broker is provided, its live LTP feed
    is used (real-time); otherwise falls back to yfinance (delayed, paper/
    demo use only).
    """
    if broker is not None and hasattr(broker, "get_ltp"):
        try:
            return float(broker.get_ltp(symbol))
        except Exception:
            pass

    yahoo_symbol = to_yahoo_symbol(symbol)
    data = yf.Ticker(yahoo_symbol).history(period="1d", interval="1m")
    if data.empty:
        data = yf.Ticker(yahoo_symbol).history(period="5d")
    if data.empty:
        raise ValueError(f"Could not fetch price data for {symbol}")
    return float(data["Close"].iloc[-1])
