"""
paper_trader.py
----------------
A virtual portfolio used by the "Paper Trading" tab. No real orders are
ever placed here - it just tracks pretend cash and positions so you can
watch how the strategy would have performed in (near-)real time before
risking real money.

Streamlit re-runs the whole script on every interaction, so this
portfolio is designed to be stored in st.session_state by app.py rather
than recreated each time.
"""

from dataclasses import dataclass, field
from typing import Dict, List
import pandas as pd


@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float
    stop_loss: float
    target: float
    entry_date: str


@dataclass
class PaperPortfolio:
    starting_cash: float
    cash: float = None
    positions: Dict[str, Position] = field(default_factory=dict)
    trade_log: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.cash is None:
            self.cash = self.starting_cash

    def buy(self, symbol: str, qty: int, price: float, stop_loss: float, target: float, date: str) -> bool:
        cost = qty * price
        if cost > self.cash:
            return False
        if symbol in self.positions:
            return False  # one open position per symbol, kept simple on purpose

        self.cash -= cost
        self.positions[symbol] = Position(symbol, qty, price, stop_loss, target, date)
        self.trade_log.append(
            {"date": date, "symbol": symbol, "action": "BUY", "qty": qty, "price": price}
        )
        return True

    def sell(self, symbol: str, price: float, date: str, reason: str = "manual") -> float:
        if symbol not in self.positions:
            return 0.0
        pos = self.positions.pop(symbol)
        proceeds = pos.qty * price
        self.cash += proceeds
        pnl = (price - pos.entry_price) * pos.qty
        self.trade_log.append(
            {
                "date": date,
                "symbol": symbol,
                "action": "SELL",
                "qty": pos.qty,
                "price": price,
                "pnl": round(pnl, 2),
                "reason": reason,
            }
        )
        return pnl

    def check_stops_and_targets(self, current_prices: Dict[str, float], date: str) -> List[str]:
        """Call this on each refresh with latest prices; auto-exits any position
        that has hit its stop-loss or target. Returns list of symbols closed."""
        closed = []
        for symbol in list(self.positions.keys()):
            price = current_prices.get(symbol)
            if price is None:
                continue
            pos = self.positions[symbol]
            if price <= pos.stop_loss:
                self.sell(symbol, pos.stop_loss, date, reason="stop_loss")
                closed.append(symbol)
            elif price >= pos.target:
                self.sell(symbol, pos.target, date, reason="target")
                closed.append(symbol)
        return closed

    def mark_to_market(self, current_prices: Dict[str, float]) -> float:
        unrealized = 0.0
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol, pos.entry_price)
            unrealized += (price - pos.entry_price) * pos.qty
        return unrealized

    def total_equity(self, current_prices: Dict[str, float]) -> float:
        return self.cash + sum(
            current_prices.get(s, p.entry_price) * p.qty for s, p in self.positions.items()
        )

    def realized_pnl_today(self, today: str) -> float:
        return sum(
            t.get("pnl", 0) for t in self.trade_log if t["action"] == "SELL" and t["date"] == today
        )

    def positions_df(self, current_prices: Dict[str, float] = None) -> pd.DataFrame:
        if not self.positions:
            return pd.DataFrame()
        rows = []
        for s, p in self.positions.items():
            cur = (current_prices or {}).get(s, p.entry_price)
            rows.append(
                {
                    "Symbol": s,
                    "Qty": p.qty,
                    "Entry Price": round(p.entry_price, 2),
                    "Current Price": round(cur, 2),
                    "Stop Loss": round(p.stop_loss, 2),
                    "Target": round(p.target, 2),
                    "Unrealized P&L": round((cur - p.entry_price) * p.qty, 2),
                }
            )
        return pd.DataFrame(rows)

    def trade_log_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.trade_log)
