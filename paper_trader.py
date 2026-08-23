"""
paper_trader.py
---------------
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
    cash: float = field(init=False)
    positions: Dict[str, Position] = field(default_factory=dict)
    trade_log: List[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    def buy(self, symbol: str, qty: int, price: float, stop_loss: float, target: float, date: str) -> bool:
        """
        Simulated buy. Returns True on success, False if the symbol is
        already held or there is not enough cash.
        """
        symbol = symbol.upper()
        if symbol in self.positions:
            return False

        cost = price * qty
        if cost > self.cash:
            return False

        self.cash -= cost
        self.positions[symbol] = Position(
            symbol=symbol,
            qty=qty,
            entry_price=price,
            stop_loss=stop_loss,
            target=target,
            entry_date=date,
        )
        self.trade_log.append(
            {"date": date, "action": "BUY", "symbol": symbol, "qty": qty, "price": price, "pnl": 0.0}
        )
        return True

    def sell(self, symbol: str, price: float, date: str, reason: str = "manual") -> float:
        """
        Simulated sell. Returns the realized P&L for the position (0 if none held).
        """
        symbol = symbol.upper()
        if symbol not in self.positions:
            return 0.0

        pos = self.positions.pop(symbol)
        proceeds = pos.qty * price
        self.cash += proceeds
        pnl = round((price - pos.entry_price) * pos.qty, 2)
        self.trade_log.append(
            {
                "date": date,
                "action": "SELL",
                "symbol": symbol,
                "qty": pos.qty,
                "price": price,
                "pnl": pnl,
                "reason": reason,
            }
        )
        return pnl

    def check_stops_and_targets(self, current_prices: Dict[str, float], date: str) -> List[str]:
        """
        Call this on each refresh with latest prices; auto-exits any position
        that has hit its stop-loss or target. Returns list of symbols closed.
        """
        closed: List[str] = []
        for symbol in list(self.positions.keys()):
            price = current_prices.get(symbol)
            if price is None:
                continue
            pos = self.positions[symbol]
            if pos.stop_loss and price <= pos.stop_loss:
                self.sell(symbol, pos.stop_loss, date, reason="stop_loss")
                closed.append(symbol)
            elif pos.target and price >= pos.target:
                self.sell(symbol, pos.target, date, reason="target")
                closed.append(symbol)
        return closed

    def mark_to_market(self, current_prices: Dict[str, float]) -> Dict[str, float]:
        """Return dict of symbol -> unrealized P&L at the given prices."""
        unrealized: Dict[str, float] = {}
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol, pos.entry_price)
            unrealized[symbol] = (price - pos.entry_price) * pos.qty
        return unrealized

    def total_equity(self, current_prices: Dict[str, float]) -> float:
        """Cash plus the mark-to-market value of all open positions."""
        return self.cash + sum(
            pos.qty * current_prices.get(symbol, pos.entry_price)
            for symbol, pos in self.positions.items()
        )

    def realized_pnl_today(self, today: str) -> float:
        """Sum of realized P&L from SELL trades recorded on the given date."""
        return sum(
            entry.get("pnl", 0.0)
            for entry in self.trade_log
            if entry.get("action") == "SELL" and entry.get("date") == today
        )

    def positions_df(self, current_prices: Dict[str, float]) -> pd.DataFrame:
        """DataFrame of open positions for display."""
        if not self.positions:
            return pd.DataFrame()
        rows = []
        for symbol, pos in self.positions.items():
            cur = current_prices.get(symbol, pos.entry_price)
            rows.append(
                {
                    "Symbol": symbol,
                    "Qty": pos.qty,
                    "Entry Price": round(pos.entry_price, 2),
                    "Current Price": round(cur, 2),
                    "Stop Loss": round(pos.stop_loss, 2),
                    "Target": round(pos.target, 2),
                    "Unrealized P&L": round((cur - pos.entry_price) * pos.qty, 2),
                }
            )
        return pd.DataFrame(rows)

    def trade_log_df(self) -> pd.DataFrame:
        """DataFrame of the full paper trade log for display."""
        if not self.trade_log:
            return pd.DataFrame()
        return pd.DataFrame(self.trade_log)
