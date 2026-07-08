"""
strategy.py
-----------
Adaptive Trend-Pullback Swing Strategy  —  FINAL VERSION
=========================================================

ENTRY FILTERS (9 total — all individually toggleable):
  1. Trend filter          — EMA 50 above EMA 200, price above EMA 50
  2. ADX filter (NEW)      — only trade when a real trend exists (ADX > 25)
  3. Pullback entry        — price dips to EMA 20, RSI cools then turns up
  4. Momentum filter       — MACD histogram rising, RSI not overbought
  5. Volatility filter     — ATR% ranked between 20th–90th percentile
  6. Volume filter         — today's volume at least 1.1× its 20-bar average
  7. Relative strength     — stock beating Nifty 50 over the lookback period
  8. Liquidity filter      — average daily turnover above ₹5 crore floor
  9. Risk-reward filter    — reward:risk at least 1.5:1 after transaction costs

EXIT METHODS (4 total — all individually toggleable):
  1. Trailing stop         — stop ratchets up as price rises, never drops
  2. Partial exit at 1:1  — sell 50% at breakeven reward, trail the rest
  3. Time-based exit       — exit if price goes nowhere after N bars
  4. Trend breakdown exit  — exit early if EMA structure reverses

Long-only (NSE cash segment — multi-day shorting not permitted).
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from indicators import ema, rsi, atr, macd, rolling_percentile_rank


# ============================================================
#  ADX INDICATOR  (new — self-contained, no extra dependency)
# ============================================================

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Computes the Average Directional Index (ADX) from OHLC data.

    ADX measures TREND STRENGTH, not direction:
      ADX < 20  →  no real trend, market is ranging/choppy  →  SKIP trade
      ADX 20-25 →  weak trend developing
      ADX > 25  →  trend is strong enough to trade           →  ALLOW trade
      ADX > 40  →  very strong trend (rare, but great for trailing)

    Parameters
    ----------
    df     : DataFrame with columns High, Low, Close
    period : smoothing period (default 14 — standard across all timeframes)

    Returns
    -------
    pd.Series of ADX values (0–100 scale)
    """
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder smoothing (equivalent to EMA with alpha = 1/period)
    def wilder_smooth(series, n):
        result = pd.Series(np.nan, index=series.index)
        result.iloc[n] = series.iloc[:n].sum()
        for i in range(n + 1, len(series)):
            result.iloc[i] = result.iloc[i - 1] - (result.iloc[i - 1] / n) + series.iloc[i]
        return result

    tr_smooth      = wilder_smooth(tr,                        period)
    plus_dm_smooth = wilder_smooth(pd.Series(plus_dm,  index=df.index), period)
    minus_dm_smooth= wilder_smooth(pd.Series(minus_dm, index=df.index), period)

    plus_di  = 100 * plus_dm_smooth  / tr_smooth
    minus_di = 100 * minus_dm_smooth / tr_smooth

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)

    adx_series = wilder_smooth(dx.fillna(0), period)
    return adx_series


# ============================================================
#  STRATEGY PARAMETERS
# ============================================================

@dataclass
class StrategyParams:

    # ── Entry: Trend ──────────────────────────────────────────
    trend_fast_ema: int   = 50
    trend_slow_ema: int   = 200

    # ── Entry: ADX Filter (NEW) ───────────────────────────────
    use_adx_filter: bool  = True     # toggle on/off from Streamlit UI
    adx_period:     int   = 14       # standard period — works on all timeframes
    adx_threshold:  float = 25.0     # minimum ADX to allow a trade
                                     # raise to 30 in very choppy markets
                                     # lower to 20 in trending bull markets

    # ── Entry: Pullback ───────────────────────────────────────
    pullback_ema:       int   = 20
    rsi_period:         int   = 14
    rsi_pullback_low:   float = 38.0
    rsi_pullback_high:  float = 55.0
    rsi_overbought:     float = 70.0

    # ── Entry: ATR / Volatility ───────────────────────────────
    atr_period:             int   = 14
    volatility_pctile_low:  float = 0.20
    volatility_pctile_high: float = 0.90
    volatility_lookback:    int   = 100

    # ── Entry: Volume ─────────────────────────────────────────
    volume_avg_period: int   = 20
    volume_ratio_min:  float = 1.1

    # ── Entry: Relative Strength ──────────────────────────────
    rel_strength_lookback:  int  = 20
    use_relative_strength:  bool = True

    # ── Entry: Liquidity ──────────────────────────────────────
    min_avg_turnover_lookback: int   = 20
    min_avg_turnover:          float = 5_00_00_000   # ₹5 crore/day (raised from ₹1 cr)
    use_liquidity_filter:      bool  = True

    # ── Entry: Risk / Reward ──────────────────────────────────
    atr_stop_multiplier:   float = 1.5
    atr_target_multiplier: float = 3.0
    min_risk_reward:        float = 1.5
    transaction_cost_pct:   float = 0.05   # round-trip brokerage + STT estimate

    # ── Exit 1: Trailing Stop ─────────────────────────────────
    use_trailing_stop:        bool  = True
    trailing_atr_multiplier:  float = 2.0   # wider than entry stop — gives room

    # ── Exit 2: Partial Exit ──────────────────────────────────
    use_partial_exit:       bool  = True
    partial_exit_fraction:  float = 0.50   # sell 50% at 1:1 RR

    # ── Exit 3: Time-based Exit ───────────────────────────────
    use_time_exit:           bool  = True
    time_exit_bars:          int   = 10    # see note below — tune per timeframe
    time_exit_min_move_pct:  float = 1.0   # % move to be considered "alive"

    # ── Exit 4: Trend Breakdown Exit ─────────────────────────
    use_trend_breakdown_exit:   bool = True
    breakdown_bars_below_ema:   int  = 2   # closes below pullback EMA = exit


# ============================================================
#  INDICATOR COMPUTATION
# ============================================================

def compute_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"]     = ema(out["Close"], p.trend_fast_ema)
    out["ema_slow"]     = ema(out["Close"], p.trend_slow_ema)
    out["ema_pullback"] = ema(out["Close"], p.pullback_ema)
    out["rsi"]          = rsi(out["Close"], p.rsi_period)
    out["atr"]          = atr(out, p.atr_period)
    out["atr_pct"]      = out["atr"] / out["Close"]
    out["atr_pct_rank"] = rolling_percentile_rank(out["atr_pct"], p.volatility_lookback)
    out["vol_avg"]      = out["Volume"].rolling(p.volume_avg_period).mean()
    out["vol_ratio"]    = out["Volume"] / out["vol_avg"]
    macd_line, signal_line, hist = macd(out["Close"])
    out["macd_hist"]    = hist
    out["turnover"]     = out["Close"] * out["Volume"]
    out["avg_turnover"] = out["turnover"].rolling(p.min_avg_turnover_lookback).mean()

    # ADX — computed here so it's available in generate_signals and the UI
    out["adx"] = adx(out, p.adx_period)

    return out


def add_relative_strength(
    df: pd.DataFrame, index_df: pd.DataFrame, p: StrategyParams
) -> pd.DataFrame:
    out = df.copy()
    stock_ret = out["Close"].pct_change(p.rel_strength_lookback)
    idx = index_df["Close"].reindex(out.index, method="ffill")
    idx_ret = idx.pct_change(p.rel_strength_lookback)
    out["rel_strength"]        = stock_ret - idx_ret
    out["outperforming_index"] = out["rel_strength"] > 0
    return out


# ============================================================
#  EXIT SIGNAL HELPERS
# ============================================================

def _compute_trailing_stop(df: pd.DataFrame, p: StrategyParams) -> pd.Series:
    """
    Trailing stop = rolling highest close minus (multiplier × ATR).
    The cummax() ensures the stop only ever moves UP — never down.
    """
    trail_distance = p.trailing_atr_multiplier * df["atr"]
    candidate      = df["Close"] - trail_distance
    return candidate.cummax()


def _bars_below_pullback_ema(df: pd.DataFrame) -> pd.Series:
    """Consecutive count of bars where Close is below the pullback EMA."""
    below       = (df["Close"] < df["ema_pullback"]).astype(int)
    consecutive = below * (
        below.groupby((below != below.shift()).cumsum()).cumcount() + 1
    )
    return consecutive


def _compute_exit_signals(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()

    # Exit 1 — Trailing Stop
    if p.use_trailing_stop:
        out["trailing_stop_price"] = _compute_trailing_stop(out, p)
        trailing_stop_hit = out["Low"] < out["trailing_stop_price"]
    else:
        out["trailing_stop_price"] = np.nan
        trailing_stop_hit = pd.Series(False, index=out.index)

    # Exit 2 — Partial Exit at 1:1 RR
    if p.use_partial_exit and "stop_loss" in out.columns:
        risk_per_share             = out["Close"] - out["stop_loss"]
        out["partial_exit_target"] = out["Close"] + risk_per_share
        partial_exit_hit           = out["High"] >= out["partial_exit_target"]
    else:
        out["partial_exit_target"] = np.nan
        partial_exit_hit = pd.Series(False, index=out.index)

    # Exit 3 — Time-based Exit
    if p.use_time_exit:
        price_n_bars_ago        = out["Close"].shift(p.time_exit_bars)
        move_pct                = ((out["Close"] - price_n_bars_ago) / price_n_bars_ago) * 100
        time_exit_signal        = move_pct.abs() < p.time_exit_min_move_pct
        out["time_exit_signal"] = time_exit_signal
    else:
        time_exit_signal        = pd.Series(False, index=out.index)
        out["time_exit_signal"] = False

    # Exit 4 — Trend Breakdown Exit
    if p.use_trend_breakdown_exit:
        ema_cross_down         = out["ema_fast"] < out["ema_slow"]
        consec_below           = _bars_below_pullback_ema(out)
        breakdown_by_ema       = consec_below >= p.breakdown_bars_below_ema
        trend_breakdown        = ema_cross_down | breakdown_by_ema
        out["trend_breakdown"] = trend_breakdown
    else:
        trend_breakdown        = pd.Series(False, index=out.index)
        out["trend_breakdown"] = False

    # Combine — higher priority overwrites lower priority
    conditions = [
        trailing_stop_hit,
        trend_breakdown,
        partial_exit_hit,
        time_exit_signal,
    ]
    reasons = [
        "Trailing stop hit",
        "Trend breakdown — EMA structure reversed",
        f"Partial exit — 1:1 RR reached (sell {int(p.partial_exit_fraction*100)}%, trail rest, stop → breakeven)",
        f"Time exit — less than {p.time_exit_min_move_pct}% move in {p.time_exit_bars} bars",
    ]

    exit_signal = pd.Series("", index=out.index)
    exit_reason = pd.Series("", index=out.index)

    for cond, reason in zip(reversed(conditions), reversed(reasons)):
        mask = cond.fillna(False)
        exit_signal[mask] = "SELL"
        exit_reason[mask] = reason

    out["exit_signal"] = exit_signal
    out["exit_reason"] = exit_reason
    return out


# ============================================================
#  MAIN SIGNAL GENERATION
# ============================================================

def generate_signals(
    df: pd.DataFrame,
    p: StrategyParams = None,
    index_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    df       : OHLCV DataFrame for ONE symbol, ONE timeframe.
    index_df : optional Nifty 50 OHLCV (same timeframe) for RS filter.

    Returns df with all indicator and signal columns added:

    ENTRY columns  : signal, stop_loss, target, risk_reward
    ADX column     : adx  (always computed so Streamlit can display it)
    EXIT columns   : trailing_stop_price, partial_exit_target,
                     time_exit_signal, trend_breakdown,
                     exit_signal, exit_reason
    """
    if p is None:
        p = StrategyParams()

    out = compute_indicators(df, p)

    if p.use_relative_strength and index_df is not None and not index_df.empty:
        out = add_relative_strength(out, index_df, p)
    else:
        out["outperforming_index"] = True

    # ── Filter 1: Trend ───────────────────────────────────────────────────
    trend_up = (
        (out["ema_fast"]  > out["ema_slow"]) &
        (out["Close"]     > out["ema_fast"])
    )

    # ── Filter 2: ADX — is there actually a trend worth trading? (NEW) ────
    #
    #   This is the single most important addition to the strategy.
    #   Without this filter the strategy fires BUY signals in sideways/
    #   choppy markets and gets chopped to pieces by repeated stop-outs.
    #
    #   With ADX > 25, we only trade when the market has confirmed
    #   directional momentum — the environment where trend-pullback
    #   strategies actually work.
    #
    #   The filter is displayed on the Streamlit dashboard just like the
    #   Drawdown Protection panel — green when ADX is strong, red when
    #   the market is ranging and trades are blocked.
    #
    if p.use_adx_filter:
        adx_ok = out["adx"] >= p.adx_threshold
    else:
        adx_ok = pd.Series(True, index=out.index)   # filter disabled → always pass

    # ── Filter 3: Pullback entry trigger ─────────────────────────────────
    near_pullback_ema = (
        (out["Low"]   <= out["ema_pullback"] * 1.01) &
        (out["Close"] >= out["ema_pullback"] * 0.99)
    )
    rsi_cooled_off   = out["rsi"].between(p.rsi_pullback_low, p.rsi_pullback_high)
    rsi_turning_up   = out["rsi"] > out["rsi"].shift(1)
    bullish_candle   = out["Close"] > out["Open"]
    pullback_trigger = near_pullback_ema & rsi_cooled_off & rsi_turning_up & bullish_candle

    # ── Filter 4: Momentum ────────────────────────────────────────────────
    momentum_ok = (
        (out["macd_hist"] > out["macd_hist"].shift(1)) &
        (out["rsi"]       < p.rsi_overbought)
    )

    # ── Filter 5: Volatility (percentile-based — timeframe agnostic) ──────
    volatility_ok = out["atr_pct_rank"].between(
        p.volatility_pctile_low, p.volatility_pctile_high
    )

    # ── Filter 6: Volume ──────────────────────────────────────────────────
    volume_ok = out["vol_ratio"] >= p.volume_ratio_min

    # ── Filter 7: Relative Strength ───────────────────────────────────────
    rel_strength_ok = out["outperforming_index"] if p.use_relative_strength else True

    # ── Filter 8: Liquidity ───────────────────────────────────────────────
    liquidity_ok = (
        out["avg_turnover"] >= p.min_avg_turnover
        if p.use_liquidity_filter else True
    )

    # ── Combine all entry filters ─────────────────────────────────────────
    raw_signal = (
        trend_up        &
        adx_ok          &   # ← NEW ADX filter in the chain
        pullback_trigger &
        momentum_ok     &
        volatility_ok   &
        volume_ok       &
        rel_strength_ok &
        liquidity_ok
    )
    out["signal"] = np.where(raw_signal, "BUY", "")

    # ── Stop, Target, Risk-Reward (Filter 9) ─────────────────────────────
    stop       = out["Close"] - p.atr_stop_multiplier * out["atr"]
    swing_high = out["High"].rolling(20).max().shift(1)
    atr_target = out["Close"] + p.atr_target_multiplier * out["atr"]
    target     = np.maximum(swing_high.fillna(-np.inf), atr_target)

    risk              = out["Close"] - stop
    reward            = target - out["Close"]
    cost_per_share    = out["Close"] * (p.transaction_cost_pct / 100.0)
    reward_net        = reward - cost_per_share
    rr                = np.where(risk > 0, reward_net / risk, np.nan)

    out["stop_loss"]   = np.where(out["signal"] == "BUY", stop,   np.nan)
    out["target"]      = np.where(out["signal"] == "BUY", target, np.nan)
    out["risk_reward"] = np.where(out["signal"] == "BUY", rr,     np.nan)

    rr_ok          = out["risk_reward"] >= p.min_risk_reward
    out["signal"]  = np.where((out["signal"] == "BUY") & rr_ok, "BUY", "")
    out.loc[out["signal"] != "BUY", ["stop_loss", "target", "risk_reward"]] = np.nan

    # ── Exit signals ──────────────────────────────────────────────────────
    out = _compute_exit_signals(out, p)

    # Safety: never SELL on the same bar as BUY
    conflict = (out["signal"] == "BUY") & (out["exit_signal"] == "SELL")
    out.loc[conflict, ["exit_signal", "exit_reason"]] = ""

    return out


# ============================================================
#  POSITION STATE TRACKER  (for live / paper trading loop)
# ============================================================

@dataclass
class PositionState:
    """
    Tracks a single open position bar-by-bar and evaluates exits.

    HOW TO USE in your broker/paper-trading loop:
    ─────────────────────────────────────────────
        pos = None
        for bar, current_atr in live_bars:
            if pos is None:
                if bar["signal"] == "BUY":
                    pos = PositionState(
                        entry_price = bar["Close"],
                        stop_loss   = bar["stop_loss"],
                        target      = bar["target"],
                        qty         = calculated_qty,
                        params      = p,
                    )
            else:
                action, reason = pos.evaluate(bar, current_atr)
                if action == "SELL_PARTIAL":
                    broker.sell(symbol, pos.partial_qty)
                elif action == "SELL_ALL":
                    broker.sell(symbol, pos.remaining_qty)
                    pos = None
    """
    entry_price: float
    stop_loss:   float
    target:      float
    qty:         int
    params:      StrategyParams = field(default_factory=StrategyParams)

    bars_held:          int   = 0
    highest_price_seen: float = 0.0
    partial_exit_done:  bool  = False
    remaining_qty:      int   = 0

    def __post_init__(self):
        self.highest_price_seen = self.entry_price
        self.remaining_qty      = self.qty

    @property
    def partial_qty(self) -> int:
        return int(self.qty * self.params.partial_exit_fraction)

    def evaluate(self, bar: pd.Series, current_atr: float) -> tuple[str, str]:
        """
        Call once per new bar while position is open.
        Returns (action, reason).

        action values:
          "HOLD"          — no exit, keep holding
          "SELL_PARTIAL"  — sell partial_exit_fraction of position
          "SELL_ALL"      — close entire remaining position
        """
        p = self.params
        self.bars_held += 1

        if bar["High"] > self.highest_price_seen:
            self.highest_price_seen = bar["High"]

        live_trailing_stop = (
            self.highest_price_seen - p.trailing_atr_multiplier * current_atr
        )

        # Priority 1 — Hard stop loss
        if bar["Low"] <= self.stop_loss:
            return "SELL_ALL", f"Hard stop loss hit at ₹{self.stop_loss:.2f}"

        # Priority 2 — Trailing stop
        if p.use_trailing_stop and bar["Low"] <= live_trailing_stop:
            return "SELL_ALL", (
                f"Trailing stop hit at ₹{live_trailing_stop:.2f} "
                f"(peak: ₹{self.highest_price_seen:.2f})"
            )

        # Priority 3 — Trend breakdown
        if p.use_trend_breakdown_exit:
            ema_cross_down = bar["ema_fast"] < bar["ema_slow"]
            below_pullback = bar["Close"] < bar["ema_pullback"]
            if ema_cross_down or (below_pullback and self.bars_held >= p.breakdown_bars_below_ema):
                return "SELL_ALL", "Trend breakdown — EMA structure reversed"

        # Priority 4 — Partial exit at 1:1 RR
        if p.use_partial_exit and not self.partial_exit_done:
            partial_target = self.entry_price + (self.entry_price - self.stop_loss)
            if bar["High"] >= partial_target:
                self.partial_exit_done  = True
                self.remaining_qty     -= self.partial_qty
                self.stop_loss          = self.entry_price  # move stop to breakeven
                return "SELL_PARTIAL", (
                    f"Partial exit: sell {self.partial_qty} shares at "
                    f"₹{partial_target:.2f} (1:1 RR). Stop → breakeven."
                )

        # Priority 5 — Full target hit
        if bar["High"] >= self.target:
            return "SELL_ALL", f"Full target hit at ₹{self.target:.2f}"

        # Priority 6 — Time exit
        if p.use_time_exit and self.bars_held >= p.time_exit_bars:
            move_pct = abs((bar["Close"] - self.entry_price) / self.entry_price) * 100
            if move_pct < p.time_exit_min_move_pct:
                return "SELL_ALL", (
                    f"Time exit: {self.bars_held} bars held, "
                    f"only {move_pct:.2f}% move. Redeploying capital."
                )

        return "HOLD", ""


# ============================================================
#  ADX STATUS HELPER  (for Streamlit dashboard panel)
# ============================================================

def get_adx_status(adx_value: float, threshold: float = 25.0) -> dict:
    """
    Returns a dict for the Streamlit UI ADX panel — mirrors the style
    of the Drawdown Protection panel in risk_manager.py.

    Usage in app.py:
        status = get_adx_status(latest_bar["adx"], p.adx_threshold)
        st.metric("ADX Trend Strength", status["adx_value"], status["label"])
        # colour the panel green/orange/red based on status["colour"]

    Returns:
        {
          "adx_value"   : float  — rounded ADX reading
          "label"       : str    — e.g. "Strong Trend ✅"
          "colour"      : str    — "green" / "orange" / "red"
          "trade_allowed": bool  — True if ADX clears the threshold
          "message"     : str    — one-line explanation for the UI
        }
    """
    adx_rounded = round(float(adx_value), 1) if not np.isnan(adx_value) else 0.0

    if adx_rounded >= 40:
        return {
            "adx_value":    adx_rounded,
            "label":        "Very Strong Trend ✅",
            "colour":       "green",
            "trade_allowed": True,
            "message":      f"ADX {adx_rounded} — very strong trend. Trailing stops work well here.",
        }
    elif adx_rounded >= threshold:
        return {
            "adx_value":    adx_rounded,
            "label":        "Strong Trend ✅",
            "colour":       "green",
            "trade_allowed": True,
            "message":      f"ADX {adx_rounded} — trend is strong enough to trade (min: {threshold}).",
        }
    elif adx_rounded >= threshold * 0.8:
        return {
            "adx_value":    adx_rounded,
            "label":        "Weak Trend ⚠️",
            "colour":       "orange",
            "trade_allowed": False,
            "message":      (
                f"ADX {adx_rounded} — trend is developing but below threshold ({threshold}). "
                f"New entries blocked. Watch for ADX to cross {threshold}."
            ),
        }
    else:
        return {
            "adx_value":    adx_rounded,
            "label":        "No Trend 🔴",
            "colour":       "red",
            "trade_allowed": False,
            "message":      (
                f"ADX {adx_rounded} — market is ranging / choppy. "
                f"All entries blocked until ADX exceeds {threshold}."
            ),
        }


# ============================================================
#  FILTER DESCRIPTIONS  (for Streamlit UI tooltips / help text)
# ============================================================

FILTER_DESCRIPTIONS = {
    # ── Entry filters ──────────────────────────────────────────────────────
    "Trend filter":
        "EMA 50 must be above EMA 200, and price above EMA 50. "
        "Only trade in the direction of the prevailing trend.",

    "ADX filter (NEW)":
        "ADX (Average Directional Index) measures how STRONG the trend is — "
        "not which direction, just whether a real trend exists. "
        "ADX below 25 means the market is choppy/sideways — all entries are blocked. "
        "ADX above 25 means a genuine trend is in place — entries are allowed. "
        "This is the single most important fix to avoid losing money in flat markets.",

    "Pullback entry trigger":
        "Price has pulled back to the EMA 20 zone, RSI cooled to 38–55 and is "
        "turning back up, and the bar closed bullish. Buy the dip, not the chase.",

    "Momentum filter":
        "MACD histogram is rising and RSI is below 70. Confirms momentum is "
        "turning in the trend direction without being overbought.",

    "Volatility filter":
        "ATR% must rank between the 20th and 90th percentile of its own last "
        "100 bars. Avoids stocks that are dead (too quiet) or spiking on news "
        "(too wild). Adapts automatically to any timeframe.",

    "Volume filter":
        "Today's volume must be at least 1.1× its 20-bar average. "
        "Confirms that real buyers are participating.",

    "Relative strength filter":
        "Stock's return over the lookback period must beat Nifty 50's return "
        "over the same period. Only trade the strongest stocks.",

    "Liquidity filter":
        "Average daily turnover (price × volume) must be at least ₹5 crore/day. "
        "Ensures positions can be entered and exited safely without moving the price.",

    "Risk-reward filter":
        "Net reward (after brokerage + STT estimate) must be at least 1.5× the "
        "risk (distance to stop loss). Trades with poor math are skipped entirely.",

    # ── Exit filters ───────────────────────────────────────────────────────
    "Trailing stop exit":
        "Stop loss automatically ratchets upward as the stock climbs — it never "
        "moves down. Locks in profits without requiring a manual exit decision.",

    "Partial exit at 1:1 RR":
        "When your profit equals your original risk, half the position is sold "
        "automatically. Stop loss is then moved to your entry price (breakeven). "
        "Worst case after this point: zero loss on the trade.",

    "Time-based exit":
        "If the stock has moved less than 1% after holding for N bars, exit and "
        "redeploy capital. Dead-money trades are an opportunity cost.",

    "Trend breakdown exit":
        "If the fast EMA crosses below the slow EMA, or price closes below the "
        "pullback EMA for 2 consecutive bars, exit immediately. The reason for "
        "the entry no longer exists — no point holding.",
}