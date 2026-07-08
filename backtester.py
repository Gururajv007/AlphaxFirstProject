"""
backtester.py
-------------
A simple, readable (not micro-optimized) event-style backtest: walks
bar-by-bar, opens a position on a BUY signal (if not already in one),
and exits on stop-loss hit, target hit, or a configurable max holding
period. Readability is prioritized over speed - correctness matters far
more than runtime for a beginner-facing tool.
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

from risk_manager import calculate_position_size


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float
    target: float
    qty: int
    exit_date: pd.Timestamp = None
    exit_price: float = None
    exit_reason: str = None
    pnl: float = None
    pnl_pct: float = None


def run_backtest(
    df: pd.DataFrame,
    capital: float = 100_000.0,
    risk_per_trade_pct: float = 1.0,
    max_holding_bars: int = 20,
) -> dict:
    """
    df must already have signal/stop_loss/target columns from strategy.generate_signals().
    Returns a dict with: trades (list[Trade]), equity_curve (pd.Series), metrics (dict).
    """
    trades: List[Trade] = []
    open_trade: Trade = None
    cash = capital
    equity_curve = []

    for date, row in df.iterrows():
        price = row["Close"]

        # --- manage open trade ---
        if open_trade is not None:
            bars_held = (df.index.get_loc(date) - df.index.get_loc(open_trade.entry_date))
            hit_stop = row["Low"] <= open_trade.stop_loss
            hit_target = row["High"] >= open_trade.target
            time_exit = bars_held >= max_holding_bars

            exit_price = None
            exit_reason = None
            if hit_stop:
                exit_price = open_trade.stop_loss
                exit_reason = "stop_loss"
            elif hit_target:
                exit_price = open_trade.target
                exit_reason = "target"
            elif time_exit:
                exit_price = price
                exit_reason = "max_holding_period"

            if exit_price is not None:
                open_trade.exit_date = date
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                open_trade.pnl = (exit_price - open_trade.entry_price) * open_trade.qty
                open_trade.pnl_pct = (exit_price / open_trade.entry_price - 1) * 100
                cash += open_trade.pnl
                trades.append(open_trade)
                open_trade = None

        # --- check for new entry ---
        if open_trade is None and row.get("signal") == "BUY":
            qty = calculate_position_size(cash, risk_per_trade_pct, price, row["stop_loss"])
            if qty > 0:
                open_trade = Trade(
                    entry_date=date,
                    entry_price=price,
                    stop_loss=row["stop_loss"],
                    target=row["target"],
                    qty=qty,
                )

        # mark-to-market equity for the curve
        unrealized = 0.0
        if open_trade is not None:
            unrealized = (price - open_trade.entry_price) * open_trade.qty
        equity_curve.append(cash + unrealized)

    equity_series = pd.Series(equity_curve, index=df.index, name="equity")
    metrics = compute_metrics(trades, equity_series, capital)
    return {"trades": trades, "equity_curve": equity_series, "metrics": metrics}


def compute_metrics(trades: List[Trade], equity_curve: pd.Series, initial_capital: float) -> dict:
    if not trades:
        return {
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "avg_risk_reward": 0.0,
        }

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_return_pct = (equity_curve.iloc[-1] / initial_capital - 1) * 100

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    return {
        "num_trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else float("inf"),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
    }


def trades_to_dataframe(trades: List[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = [
        {
            "Entry Date": t.entry_date,
            "Entry Price": round(t.entry_price, 2),
            "Exit Date": t.exit_date,
            "Exit Price": round(t.exit_price, 2) if t.exit_price else None,
            "Qty": t.qty,
            "Exit Reason": t.exit_reason,
            "P&L (₹)": round(t.pnl, 2) if t.pnl is not None else None,
            "P&L (%)": round(t.pnl_pct, 2) if t.pnl_pct is not None else None,
        }
        for t in trades
    ]
    return pd.DataFrame(rows)
