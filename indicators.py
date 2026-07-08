"""
indicators.py
-------------
Plain-pandas implementations of the technical indicators the strategy
needs. No TA-Lib / extra C-dependencies required, so this installs
cleanly with just `pip install -r requirements.txt`.

All functions take a pandas Series/DataFrame and return a pandas Series
of the same length (NaN for the initial warm-up period, as usual).
"""

import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)  # neutral default during warm-up / flat periods


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """df must have columns: High, Low, Close"""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(alpha=1 / period, adjust=False).mean()


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def rolling_percentile_rank(series: pd.Series, lookback: int = 100) -> pd.Series:
    """
    Returns, for each point, what percentile (0-1) the current value
    sits at relative to the trailing `lookback` window.

    This is the trick that makes filters like volatility (ATR%) work
    the same way on a 15-minute chart as on a weekly chart: instead of
    a hardcoded absolute threshold (e.g. "ATR% > 2"), we ask "is this
    reading high/low relative to this stock's own recent history?"
    """

    def _pct_rank(window):
        return (window.rank(pct=True).iloc[-1])

    return series.rolling(lookback, min_periods=max(10, lookback // 5)).apply(
        _pct_rank, raw=False
    )
