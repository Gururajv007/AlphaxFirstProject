"""
paper_trading_enhanced.py
-------------------------
Enhanced paper trading module with real-market simulation.

Features:
- Slippage simulation
- Spread simulation
- Margin requirement checking
- Order queue simulation
- Partial fill simulation
- State persistence across sessions
- Automatic background refresh
- Comprehensive trade analytics
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import datetime as dt
import json
import os
import pandas as pd
import numpy as np
from dataclasses_json import dataclass_json


class PaperOrderStatus(Enum):
    """Paper order states."""
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass_json
@dataclass
class PaperOrder:
    """Paper trading order with simulation features."""
    order_id: str
    symbol: str
    qty: int
    order_type: str  # MARKET, LIMIT
    price: Optional[float] = None  # For LIMIT orders
    status: PaperOrderStatus = PaperOrderStatus.PENDING
    created_at: str = field(default_factory=lambda: dt.datetime.now().isoformat())
    filled_at: Optional[str] = None
    filled_qty: int = 0
    average_price: float = 0.0
    slippage_pct: float = 0.0  # Simulated slippage percentage
    partial_fill_percentage: float = 0.0  # 0-1 for partial fills
    reject_reason: str = ""


@dataclass_json
@dataclass
class PaperPosition:
    """Paper trading position."""
    symbol: str
    qty: int
    entry_price: float
    current_price: float
    entry_time: str
    stop_loss: float
    target: float
    max_profit: float = 0.0
    max_loss: float = 0.0
    margin_required: float = 0.0
    margin_utilization: float = 0.0


@dataclass_json
@dataclass
class PaperTrade:
    """Completed paper trade."""
    trade_id: str
    symbol: str
    entry_price: float
    exit_price: float
    qty: int
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    exit_reason: str
    strategy: str = ""
    slippage_incurred: float = 0.0
    spread_cost: float = 0.0


class MarketSimulator:
    """
    Simulates real market conditions for paper trading.

    Includes:
    - Slippage based on volatility and liquidity
    - Bid-ask spread simulation
    - Order queue with partial fills
    - Market impact simulation
    """

    def __init__(self):
        self.slippage_model = {
            "low_volume": 0.25,  # % slippage for low volume stocks
            "medium_volume": 0.15,
            "high_volume": 0.05,
        }
        self.spread_model = {
            "low_volume": 0.20,  # % spread for low volume stocks
            "medium_volume": 0.10,
            "high_volume": 0.05,
        }

    def calculate_slippage(self, symbol: str, volume: float, qty: int) -> float:
        """Calculate realistic slippage based on stock liquidity."""
        # Simple model: more volume = less slippage
        if volume > 1000000:  # High volume
            base_slippage = self.slippage_model["high_volume"]
        elif volume > 500000:  # Medium volume
            base_slippage = self.slippage_model["medium_volume"]
        else:  # Low volume
            base_slippage = self.slippage_model["low_volume"]

        # Adjust for order size relative to volume
        volume_impact = min(1.0, qty / (volume * 0.01))  # 1% of volume
        adjusted_slippage = base_slippage * (1 + volume_impact)

        # Add randomness
        noise = np.random.normal(0, 0.02)  # ±2%
        return max(0, adjusted_slippage + noise)

    def calculate_spread(self, symbol: str, volume: float) -> float:
        """Calculate bid-ask spread."""
        if volume > 1000000:  # High volume
            return self.spread_model["high_volume"]
        elif volume > 500000:  # Medium volume
            return self.spread_model["medium_volume"]
        else:  # Low volume
            return self.spread_model["low_volume"]

    def simulate_order_execution(
        self,
        symbol: str,
        order_type: str,
        price: float,
        qty: int,
        volume: float,
        current_price: float,
    ) -> Tuple[float, float, int, float]:
        """
        Simulate order execution with slippage, spread, and partial fills.

        Returns:
            (executed_price, slippage_pct, executed_qty, spread_cost)
        """
        spread_pct = self.calculate_spread(symbol, volume)
        slippage_pct = self.calculate_slippage(symbol, volume, qty)

        if order_type == "MARKET":
            # Market orders get immediate execution at current price + spread
            spread_cost = current_price * (spread_pct / 100)
            slippage_cost = current_price * (slippage_pct / 100)
            executed_price = current_price + spread_cost + slippage_cost
        else:  # LIMIT order
            # Limit orders execute at specified price (if market reaches it)
            if price >= current_price:  # For BUY orders
                spread_cost = price * (spread_pct / 100)
                executed_price = price + spread_cost
            else:
                # Limit price not reached
                return current_price, slippage_pct, 0, spread_pct

        # Simulate partial fills (5% chance for small orders, 20% for large)
        fill_probability = min(0.2, 0.05 + 0.15 * (qty > 1000))
        if np.random.random() < fill_probability:
            # Partial fill (30-70% of order)
            fill_pct = np.random.uniform(0.3, 0.7)
            executed_qty = int(qty * fill_pct)
        else:
            executed_qty = qty

        return executed_price, slippage_pct, executed_qty, spread_pct


class PaperPortfolioEnhanced:
    """
    Enhanced paper portfolio with realistic market simulation.
    """

    def __init__(
        self,
        starting_cash: float = 100000.0,
        margin_multiplier: float = 1.0,  # 1.0 = no margin, >1.0 = margin trading
        data_dir: str = "./paper_trading_data",
    ):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.margin_multiplier = margin_multiplier
        self.margin_available = starting_cash * (margin_multiplier - 1) if margin_multiplier > 1 else 0
        self.margin_used = 0.0

        self.positions: Dict[str, PaperPosition] = {}
        self.orders: Dict[str, PaperOrder] = {}
        self.trades: Dict[str, PaperTrade] = {}
        self.trade_history: List[PaperTrade] = []

        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        self.market_simulator = MarketSimulator()

        # Load previous state if exists
        self.load_state()

    # =====================================================================
    # ORDER PLACEMENT & SIMULATION
    # =====================================================================

    def place_order(
        self,
        symbol: str,
        qty: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        slippage_model: str = "realistic",
    ) -> Tuple[Optional[str], str, Optional[float]]:
        """
        Place a paper order with realistic simulation.

        Returns:
            (order_id, status_message, executed_price)
        """
        # Validate
        if qty <= 0:
            return None, "Invalid quantity", None

        # Get current market price (simulated)
        try:
            import data_feed
            current_price = data_feed.get_latest_price(symbol)
        except Exception:
            # Fallback for simulation
            current_price = price or 100.0

        # Simulate volume for slippage calculation
        volume = self._get_simulated_volume(symbol)

        # Create order
        order_id = f"PAPER_{symbol}_{dt.datetime.now().timestamp()}"
        order = PaperOrder(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            order_type=order_type,
            price=price,
        )

        # Check margin requirements
        required_margin = self._calculate_margin_requirement(
            symbol, qty, current_price
        )
        if required_margin > self.get_available_buying_power():
            order.status = PaperOrderStatus.REJECTED
            order.reject_reason = "Insufficient buying power"
            self.orders[order_id] = order
            self.save_state()
            return order_id, "Order rejected: Insufficient buying power", None

        # Simulate execution
        executed_price, slippage_pct, executed_qty, spread_pct = (
            self.market_simulator.simulate_order_execution(
                symbol=symbol,
                order_type=order_type,
                price=price or current_price,
                qty=qty,
                volume=volume,
                current_price=current_price,
            )
        )

        if executed_qty == 0:
            order.status = PaperOrderStatus.CANCELLED
            order.reject_reason = "Limit price not reached"
        else:
            # Update order with fill details
            order.filled_qty = executed_qty
            order.average_price = executed_price
            order.slippage_pct = slippage_pct
            order.partial_fill_percentage = executed_qty / qty

            if executed_qty == qty:
                order.status = PaperOrderStatus.FILLED
                order.filled_at = dt.datetime.now().isoformat()
                status_msg = f"Order filled at ₹{executed_price:.2f}"
            else:
                order.status = PaperOrderStatus.PARTIALLY_FILLED
                status_msg = f"Partial fill: {executed_qty}/{qty} at ₹{executed_price:.2f}"

        # Store order
        self.orders[order_id] = order

        # Update portfolio if order executed
        if order.status in [PaperOrderStatus.FILLED, PaperOrderStatus.PARTIALLY_FILLED]:
            self._process_order_execution(
                order_id=order_id,
                symbol=symbol,
                executed_qty=executed_qty,
                executed_price=executed_price,
                order_type="BUY",  # Assuming BUY for now
                slippage_pct=slippage_pct,
                spread_pct=spread_pct,
            )

        self.save_state()
        return order_id, status_msg, executed_price

    def _process_order_execution(
        self,
        order_id: str,
        symbol: str,
        executed_qty: int,
        executed_price: float,
        order_type: str,
        slippage_pct: float,
        spread_pct: float,
    ) -> None:
        """Process executed order and update portfolio."""
        total_cost = executed_qty * executed_price

        if order_type == "BUY":
            # Deduct cash
            self.cash -= total_cost

            # Update or create position
            if symbol in self.positions:
                # Average position
                pos = self.positions[symbol]
                total_qty = pos.qty + executed_qty
                avg_price = ((pos.qty * pos.entry_price) + total_cost) / total_qty
                pos.qty = total_qty
                pos.entry_price = avg_price
            else:
                # New position
                self.positions[symbol] = PaperPosition(
                    symbol=symbol,
                    qty=executed_qty,
                    entry_price=executed_price,
                    current_price=executed_price,
                    entry_time=dt.datetime.now().isoformat(),
                    stop_loss=0.0,  # Will be set separately
                    target=0.0,
                )

            # Update margin
            margin_required = total_cost * 0.5  # 50% margin for equity delivery
            self.margin_used += margin_required

        else:  # SELL
            # Add cash
            self.cash += total_cost

            # Remove position if fully sold
            if symbol in self.positions:
                pos = self.positions[symbol]
                pos.qty -= executed_qty
                if pos.qty <= 0:
                    del self.positions[symbol]

                # Record trade
                trade_id = f"TRADE_{symbol}_{dt.datetime.now().timestamp()}"
                trade = PaperTrade(
                    trade_id=trade_id,
                    symbol=symbol,
                    entry_price=pos.entry_price,
                    exit_price=executed_price,
                    qty=executed_qty,
                    entry_time=pos.entry_time,
                    exit_time=dt.datetime.now().isoformat(),
                    pnl=(executed_price - pos.entry_price) * executed_qty,
                    pnl_pct=((executed_price / pos.entry_price) - 1) * 100,
                    exit_reason="manual_sell",
                    slippage_incurred=slippage_pct,
                    spread_cost=spread_pct,
                )
                self.trades[trade_id] = trade
                self.trade_history.append(trade)

                # Release margin
                margin_released = total_cost * 0.5
                self.margin_used = max(0, self.margin_used - margin_released)

    # =====================================================================
    # PORTFOLIO MANAGEMENT
    # =====================================================================

    def get_total_equity(self, current_prices: Dict[str, float] = None) -> float:
        """Calculate total portfolio equity."""
        equity = self.cash
        for symbol, pos in self.positions.items():
            current_price = current_prices.get(symbol, pos.current_price) if current_prices else pos.current_price
            equity += pos.qty * current_price
        return equity

    def get_unrealized_pnl(self, current_prices: Dict[str, float] = None) -> float:
        """Calculate unrealized P&L."""
        unrealized = 0.0
        for symbol, pos in self.positions.items():
            current_price = current_prices.get(symbol, pos.current_price) if current_prices else pos.current_price
            unrealized += (current_price - pos.entry_price) * pos.qty
        return unrealized

    def get_realized_pnl(self) -> float:
        """Calculate realized P&L from closed trades."""
        return sum(trade.pnl for trade in self.trade_history)

    def get_available_buying_power(self) -> float:
        """Calculate available buying power including margin."""
        if self.margin_multiplier > 1.0:
            return self.cash + self.margin_available - self.margin_used
        return self.cash

    def get_margin_utilization_pct(self) -> float:
        """Calculate margin utilization percentage."""
        if self.margin_available > 0:
            return (self.margin_used / self.margin_available) * 100
        return 0.0

    def check_stops_and_targets(
        self,
        current_prices: Dict[str, float],
        simulate_slippage: bool = True,
    ) -> List[str]:
        """Check and auto-execute stop losses and targets."""
        closed_positions = []

        for symbol in list(self.positions.keys()):
            if symbol not in current_prices:
                continue

            pos = self.positions[symbol]
            current_price = current_prices[symbol]

            # Update position current price
            pos.current_price = current_price

            # Update max profit/loss
            pnl = (current_price - pos.entry_price) * pos.qty
            if pnl > pos.max_profit:
                pos.max_profit = pnl
            if pnl < pos.max_loss:
                pos.max_loss = pnl

            # Check stop loss
            if pos.stop_loss > 0 and current_price <= pos.stop_loss:
                # Simulate stop loss execution with slippage
                if simulate_slippage:
                    volume = self._get_simulated_volume(symbol)
                    slip_pct = self.market_simulator.calculate_slippage(symbol, volume, pos.qty)
                    execution_price = current_price * (1 - slip_pct/100)
                else:
                    execution_price = current_price

                # Close position
                self._close_position(symbol, execution_price, "stop_loss_hit")
                closed_positions.append(symbol)

            # Check target
            elif pos.target > 0 and current_price >= pos.target:
                # Target hit - similar execution simulation
                execution_price = current_price
                if simulate_slippage:
                    volume = self._get_simulated_volume(symbol)
                    slip_pct = self.market_simulator.calculate_slippage(symbol, volume, pos.qty)
                    execution_price = current_price * (1 + slip_pct/100)

                self._close_position(symbol, execution_price, "target_hit")
                closed_positions.append(symbol)

        self.save_state()
        return closed_positions

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        """Close a position and record trade."""
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]

        # Calculate trade details
        trade_id = f"TRADE_{symbol}_{dt.datetime.now().timestamp()}"
        trade = PaperTrade(
            trade_id=trade_id,
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.qty,
            entry_time=pos.entry_time,
            exit_time=dt.datetime.now().isoformat(),
            pnl=(exit_price - pos.entry_price) * pos.qty,
            pnl_pct=((exit_price / pos.entry_price) - 1) * 100,
            exit_reason=exit_reason,
            strategy="auto_exit",
        )

        # Add cash from sale
        self.cash += pos.qty * exit_price

        # Release margin
        margin_released = (pos.qty * exit_price) * 0.5
        self.margin_used = max(0, self.margin_used - margin_released)

        # Remove position
        del self.positions[symbol]

        # Record trade
        self.trades[trade_id] = trade
        self.trade_history.append(trade)

    # =====================================================================
    # ANALYTICS & REPORTING
    # =====================================================================

    def get_portfolio_summary(self, current_prices: Dict[str, float] = None) -> Dict:
        """Get comprehensive portfolio summary."""
        total_equity = self.get_total_equity(current_prices)
        unrealized_pnl = self.get_unrealized_pnl(current_prices)
        realized_pnl = self.get_realized_pnl()

        return {
            "starting_cash": round(self.starting_cash, 2),
            "current_cash": round(self.cash, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_return_pct": round(((total_equity / self.starting_cash) - 1) * 100, 2),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
            "available_buying_power": round(self.get_available_buying_power(), 2),
            "margin_utilization_pct": round(self.get_margin_utilization_pct(), 2),
        }

    def get_performance_metrics(self) -> Dict:
        """Calculate trading performance metrics."""
        if not self.trade_history:
            return {}

        trades = self.trade_history
        win_trades = [t for t in trades if t.pnl > 0]
        loss_trades = [t for t in trades if t.pnl <= 0]

        # P&L statistics
        total_profit = sum(t.pnl for t in win_trades)
        total_loss = abs(sum(t.pnl for t in loss_trades))

        # Average trade statistics
        avg_win = np.mean([t.pnl for t in win_trades]) if win_trades else 0
        avg_loss = np.mean([t.pnl for t in loss_trades]) if loss_trades else 0

        # Win rate
        win_rate = len(win_trades) / len(trades) * 100 if trades else 0

        # Profit factor
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        # Drawdown calculation (simplified)
        equity_curve = []
        equity = self.starting_cash
        for trade in sorted(trades, key=lambda t: t.exit_time):
            equity += trade.pnl
            equity_curve.append(equity)

        if equity_curve:
            running_max = np.maximum.accumulate(equity_curve)
            drawdowns = (equity_curve - running_max) / running_max * 100
            max_drawdown = np.min(drawdowns) if len(drawdowns) > 0 else 0
        else:
            max_drawdown = 0

        return {
            "total_trades": len(trades),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_win_loss_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
            "total_profit": round(total_profit, 2),
            "total_loss": round(total_loss, 2),
            "net_profit": round(total_profit - total_loss, 2),
            "largest_win": round(max([t.pnl for t in win_trades], default=0), 2),
            "largest_loss": round(min([t.pnl for t in loss_trades], default=0), 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "consecutive_wins": self._calculate_max_consecutive_wins(),
            "consecutive_losses": self._calculate_max_consecutive_losses(),
        }

    def _calculate_max_consecutive_wins(self) -> int:
        """Calculate maximum consecutive winning trades."""
        max_consecutive = 0
        current_consecutive = 0

        for trade in sorted(self.trade_history, key=lambda t: t.exit_time):
            if trade.pnl > 0:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0

        return max_consecutive

    def _calculate_max_consecutive_losses(self) -> int:
        """Calculate maximum consecutive losing trades."""
        max_consecutive = 0
        current_consecutive = 0

        for trade in sorted(self.trade_history, key=lambda t: t.exit_time):
            if trade.pnl <= 0:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0

        return max_consecutive

    # =====================================================================
    # HELPER METHODS
    # =====================================================================

    def _get_simulated_volume(self, symbol: str) -> float:
        """Get simulated volume for a symbol."""
        # In a real implementation, you'd fetch actual volume data
        # For simulation, use consistent values per symbol
        volume_map = {
            "RELIANCE": 1000000,
            "TCS": 800000,
            "HDFCBANK": 1500000,
            "INFY": 1200000,
        }
        return volume_map.get(symbol, 500000)

    def _calculate_margin_requirement(
        self,
        symbol: str,
        qty: int,
        price: float,
    ) -> float:
        """Calculate margin requirement for a trade."""
        total_value = qty * price
        # Simplified: 50% margin for delivery trades
        return total_value * 0.5

    # =====================================================================
    # PERSISTENCE
    # =====================================================================

    def save_state(self) -> None:
        """Save portfolio state to disk."""
        try:
            # Save positions
            positions_data = {
                symbol: pos.to_dict() for symbol, pos in self.positions.items()
            }
            positions_file = os.path.join(self.data_dir, "positions.json")
            with open(positions_file, "w") as f:
                json.dump(positions_data, f, indent=2)

            # Save orders
            orders_data = {
                order_id: order.to_dict() for order_id, order in self.orders.items()
            }
            orders_file = os.path.join(self.data_dir, "orders.json")
            with open(orders_file, "w") as f:
                json.dump(orders_data, f, indent=2)

            # Save trades
            trades_data = {
                trade_id: trade.to_dict() for trade_id, trade in self.trades.items()
            }
            trades_file = os.path.join(self.data_dir, "trades.json")
            with open(trades_file, "w") as f:
                json.dump(trades_data, f, indent=2)

            # Save portfolio metadata
            metadata = {
                "starting_cash": self.starting_cash,
                "cash": self.cash,
                "margin_multiplier": self.margin_multiplier,
                "margin_available": self.margin_available,
                "margin_used": self.margin_used,
                "last_saved": dt.datetime.now().isoformat(),
            }
            meta_file = os.path.join(self.data_dir, "metadata.json")
            with open(meta_file, "w") as f:
                json.dump(metadata, f, indent=2)

        except Exception as e:
            print(f"Error saving paper portfolio state: {e}")

    def load_state(self) -> None:
        """Load portfolio state from disk."""
        try:
            # Load positions
            positions_file = os.path.join(self.data_dir, "positions.json")
            if os.path.exists(positions_file):
                with open(positions_file, "r") as f:
                    positions_data = json.load(f)
                    for symbol, data in positions_data.items():
                        self.positions[symbol] = PaperPosition.from_dict(data)

            # Load orders
            orders_file = os.path.join(self.data_dir, "orders.json")
            if os.path.exists(orders_file):
                with open(orders_file, "r") as f:
                    orders_data = json.load(f)
                    for order_id, data in orders_data.items():
                        self.orders[order_id] = PaperOrder.from_dict(data)

            # Load trades
            trades_file = os.path.join(self.data_dir, "trades.json")
            if os.path.exists(trades_file):
                with open(trades_file, "r") as f:
                    trades_data = json.load(f)
                    for trade_id, data in trades_data.items():
                        trade = PaperTrade.from_dict(data)
                        self.trades[trade_id] = trade
                        self.trade_history.append(trade)

            # Load metadata
            meta_file = os.path.join(self.data_dir, "metadata.json")
            if os.path.exists(meta_file):
                with open(meta_file, "r") as f:
                    metadata = json.load(f)
                    self.starting_cash = metadata.get("starting_cash", self.starting_cash)
                    self.cash = metadata.get("cash", self.cash)
                    self.margin_multiplier = metadata.get("margin_multiplier", self.margin_multiplier)
                    self.margin_available = metadata.get("margin_available", self.margin_available)
                    self.margin_used = metadata.get("margin_used", self.margin_used)

        except Exception as e:
            print(f"Error loading paper portfolio state: {e}")

    # =====================================================================
    # DATA FRAMES FOR UI
    # =====================================================================

    def get_positions_dataframe(self, current_prices: Dict[str, float] = None) -> pd.DataFrame:
        """Get positions as pandas DataFrame."""
        if not self.positions:
            return pd.DataFrame()

        rows = []
        for symbol, pos in self.positions.items():
            current_price = current_prices.get(symbol, pos.current_price) if current_prices else pos.current_price
            rows.append({
                "Symbol": symbol,
                "Quantity": pos.qty,
                "Entry Price": round(pos.entry_price, 2),
                "Current Price": round(current_price, 2),
                "Entry Time": pos.entry_time,
                "Stop Loss": round(pos.stop_loss, 2) if pos.stop_loss > 0 else "-",
                "Target": round(pos.target, 2) if pos.target > 0 else "-",
                "Unrealized P&L (₹)": round((current_price - pos.entry_price) * pos.qty, 2),
                "Unrealized P&L (%)": round(((current_price / pos.entry_price) - 1) * 100, 2),
                "Max Profit (₹)": round(pos.max_profit, 2),
                "Max Loss (₹)": round(pos.max_loss, 2),
            })

        return pd.DataFrame(rows)

    def get_trades_dataframe(self) -> pd.DataFrame:
        """Get trade history as pandas DataFrame."""
        if not self.trade_history:
            return pd.DataFrame()

        rows = []
        for trade in sorted(self.trade_history, key=lambda t: t.exit_time, reverse=True):
            rows.append({
                "Trade ID": trade.trade_id,
                "Symbol": trade.symbol,
                "Entry Price": round(trade.entry_price, 2),
                "Exit Price": round(trade.exit_price, 2),
                "Quantity": trade.qty,
                "Entry Time": trade.entry_time,
                "Exit Time": trade.exit_time,
                "P&L (₹)": round(trade.pnl, 2),
                "P&L (%)": round(trade.pnl_pct, 2),
                "Exit Reason": trade.exit_reason,
                "Strategy": trade.strategy,
                "Slippage (%)": round(trade.slippage_incurred, 2) if hasattr(trade, "slippage_incurred") else "-",
            })

        return pd.DataFrame(rows)
