"""
order_manager.py
----------------
Comprehensive order management system with full lifecycle tracking.

Features:
- Order state persistence
- Fill confirmation polling
- Rejection handling
- Partial fill handling
- Order history with filtering
- Order reconciliation with broker
- Real-time order status updates
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import datetime as dt
import json
import os


class OrderStatus(Enum):
    """Order lifecycle states."""
    PENDING = "PENDING"           # Created, not yet submitted
    SUBMITTED = "SUBMITTED"       # Sent to broker
    ACKNOWLEDGED = "ACKNOWLEDGED" # Broker acknowledged receipt
    PENDING_NEW = "PENDING_NEW"   # Waiting for execution
    FILLED = "FILLED"             # Fully executed
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # Partially executed
    CANCELLED = "CANCELLED"       # Manually cancelled
    REJECTED = "REJECTED"         # Broker rejected
    EXPIRED = "EXPIRED"           # Order expired


class OrderType(Enum):
    """Order types."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TransactionType(Enum):
    """Transaction types."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderFill:
    """Represents a single fill event for an order."""
    fill_id: str
    filled_qty: int
    fill_price: float
    fill_time: str  # ISO format


@dataclass
class Order:
    """Complete order record with full lifecycle tracking."""
    order_id: str
    symbol: str
    qty: int
    transaction_type: TransactionType
    order_type: OrderType
    price: Optional[float]  # For LIMIT orders
    status: OrderStatus = OrderStatus.PENDING

    # Timestamps
    created_at: str = field(default_factory=lambda: dt.datetime.now().isoformat())
    submitted_at: Optional[str] = None
    filled_at: Optional[str] = None

    # Fill tracking
    filled_qty: int = 0
    average_price: float = 0.0
    fills: List[OrderFill] = field(default_factory=list)

    # Error handling
    rejection_reason: str = ""

    # Metadata
    strategy_id: str = ""
    entry_signal_strength: float = 0.0  # 0-1
    broker_response: Dict = field(default_factory=dict)

    # GTT tracking
    gtt_id: Optional[str] = None
    gtt_status: str = ""

    # Flags
    is_entry_order: bool = True
    is_stop_loss: bool = False
    is_target: bool = False


@dataclass
class Trade:
    """Complete trade record (linked entry + exits)."""
    trade_id: str
    symbol: str
    entry_order_id: str

    entry_price: float
    entry_qty: int
    entry_time: str

    exit_orders: List[str] = field(default_factory=list)  # Order IDs of exit orders
    exit_price: Optional[float] = None
    exit_qty_total: int = 0
    exit_time: Optional[str] = None

    realized_pnl: float = 0.0
    realized_pnl_pct: float = 0.0

    strategy_name: str = ""
    entry_signal: str = ""  # Description of entry signal
    exit_reason: str = ""   # Description of exit reason

    # Risk metrics
    stop_loss: float = 0.0
    target: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0


class OrderManager:
    """
    Central order management system.

    Responsibilities:
    - Track all orders (entry, stop-loss, targets)
    - Manage order lifecycle
    - Reconcile with broker
    - Persist state
    - Generate trade records
    """

    def __init__(self, persistence_dir: str = "./trading_data"):
        self.orders: Dict[str, Order] = {}  # order_id -> Order
        self.trades: Dict[str, Trade] = {}  # trade_id -> Trade
        self.gtt_orders: Dict[str, str] = {}  # gtt_id -> order_id

        self.persistence_dir = persistence_dir
        os.makedirs(persistence_dir, exist_ok=True)

        self.orders_file = os.path.join(persistence_dir, "orders.json")
        self.trades_file = os.path.join(persistence_dir, "trades.json")

        self._load_from_disk()

    # =====================================================================
    # ORDER CREATION & SUBMISSION
    # =====================================================================

    def create_order(
        self,
        symbol: str,
        qty: int,
        transaction_type: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        strategy_id: str = "",
        signal_strength: float = 0.0,
        is_entry: bool = True,
    ) -> Order:
        """Create a new order (not yet submitted to broker)."""
        order_id = f"{symbol}_{transaction_type}_{dt.datetime.now().timestamp()}"

        order = Order(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            transaction_type=TransactionType(transaction_type),
            order_type=OrderType(order_type),
            price=price,
            strategy_id=strategy_id,
            entry_signal_strength=signal_strength,
            is_entry_order=is_entry,
        )

        self.orders[order_id] = order
        self._save_to_disk()
        return order

    def submit_order(self, order_id: str, broker_order_id: str) -> None:
        """Mark order as submitted to broker."""
        if order_id not in self.orders:
            raise ValueError(f"Order {order_id} not found")

        order = self.orders[order_id]
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = dt.datetime.now().isoformat()
        order.broker_response["broker_order_id"] = broker_order_id

        self._save_to_disk()

    # =====================================================================
    # ORDER STATUS UPDATES
    # =====================================================================

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_qty: int = 0,
        average_price: float = 0.0,
        rejection_reason: str = "",
    ) -> None:
        """Update order status from broker response."""
        if order_id not in self.orders:
            raise ValueError(f"Order {order_id} not found")

        order = self.orders[order_id]
        order.status = OrderStatus(status)

        if filled_qty > 0:
            order.filled_qty = filled_qty
            order.average_price = average_price
            if filled_qty == order.qty:
                order.filled_at = dt.datetime.now().isoformat()

        if rejection_reason:
            order.rejection_reason = rejection_reason

        self._save_to_disk()

    def record_fill(
        self,
        order_id: str,
        fill_id: str,
        filled_qty: int,
        fill_price: float,
    ) -> None:
        """Record a fill event (partial or complete)."""
        if order_id not in self.orders:
            raise ValueError(f"Order {order_id} not found")

        order = self.orders[order_id]
        fill = OrderFill(
            fill_id=fill_id,
            filled_qty=filled_qty,
            fill_price=fill_price,
            fill_time=dt.datetime.now().isoformat(),
        )
        order.fills.append(fill)

        # Update cumulative fill info
        order.filled_qty = sum(f.filled_qty for f in order.fills)
        total_value = sum(f.filled_qty * f.fill_price for f in order.fills)
        order.average_price = total_value / order.filled_qty if order.filled_qty > 0 else 0.0

        # Update status
        if order.filled_qty == order.qty:
            order.status = OrderStatus.FILLED
            order.filled_at = dt.datetime.now().isoformat()
        elif order.filled_qty > 0:
            order.status = OrderStatus.PARTIALLY_FILLED

        self._save_to_disk()

    def cancel_order(self, order_id: str, reason: str = "User cancelled") -> None:
        """Cancel an order."""
        if order_id not in self.orders:
            raise ValueError(f"Order {order_id} not found")

        order = self.orders[order_id]
        order.status = OrderStatus.CANCELLED
        order.rejection_reason = reason

        self._save_to_disk()

    def reject_order(self, order_id: str, reason: str) -> None:
        """Mark order as rejected by broker."""
        if order_id not in self.orders:
            raise ValueError(f"Order {order_id} not found")

        order = self.orders[order_id]
        order.status = OrderStatus.REJECTED
        order.rejection_reason = reason

        self._save_to_disk()

    # =====================================================================
    # TRADE LINKING
    # =====================================================================

    def create_trade(
        self,
        entry_order_id: str,
        symbol: str,
        entry_price: float,
        entry_qty: int,
        stop_loss: float,
        target: float,
        strategy_name: str = "",
        entry_signal: str = "",
    ) -> str:
        """Create a trade record linked to an entry order."""
        trade_id = f"TRADE_{symbol}_{dt.datetime.now().timestamp()}"

        trade = Trade(
            trade_id=trade_id,
            symbol=symbol,
            entry_order_id=entry_order_id,
            entry_price=entry_price,
            entry_qty=entry_qty,
            entry_time=dt.datetime.now().isoformat(),
            stop_loss=stop_loss,
            target=target,
            strategy_name=strategy_name,
            entry_signal=entry_signal,
        )

        self.trades[trade_id] = trade
        self._save_to_disk()
        return trade_id

    def add_exit_to_trade(
        self,
        trade_id: str,
        exit_order_id: str,
        exit_price: float,
        exit_qty: int,
        exit_reason: str = "manual",
    ) -> None:
        """Add an exit order to a trade."""
        if trade_id not in self.trades:
            raise ValueError(f"Trade {trade_id} not found")

        trade = self.trades[trade_id]
        trade.exit_orders.append(exit_order_id)
        trade.exit_price = exit_price
        trade.exit_qty_total += exit_qty
        trade.exit_time = dt.datetime.now().isoformat()
        trade.exit_reason = exit_reason

        # Calculate P&L
        trade.realized_pnl = (exit_price - trade.entry_price) * exit_qty
        if trade.entry_price > 0:
            trade.realized_pnl_pct = (exit_price / trade.entry_price - 1) * 100

        self._save_to_disk()

    # =====================================================================
    # QUERYING
    # =====================================================================

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)

    def get_orders_by_symbol(self, symbol: str) -> List[Order]:
        """Get all orders for a symbol."""
        return [o for o in self.orders.values() if o.symbol == symbol]

    def get_orders_by_status(self, status: str) -> List[Order]:
        """Get all orders with specific status."""
        status_enum = OrderStatus(status) if isinstance(status, str) else status
        return [o for o in self.orders.values() if o.status == status_enum]

    def get_pending_orders(self) -> List[Order]:
        """Get all pending/unfilled orders."""
        pending_statuses = {
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PENDING_NEW,
            OrderStatus.PARTIALLY_FILLED,
        }
        return [o for o in self.orders.values() if o.status in pending_statuses]

    def get_open_positions_symbols(self) -> List[str]:
        """Get list of symbols with open positions (filled buy orders without matching sells)."""
        symbols = set()
        for order in self.orders.values():
            if (order.status == OrderStatus.FILLED and
                order.transaction_type == TransactionType.BUY):
                symbols.add(order.symbol)
        return sorted(list(symbols))

    def get_today_trades(self) -> List[Trade]:
        """Get all trades from today."""
        today = dt.date.today().isoformat()
        return [t for t in self.trades.values() if t.entry_time.startswith(today)]

    def get_trade_history(self, symbol: Optional[str] = None) -> List[Trade]:
        """Get trade history, optionally filtered by symbol."""
        if symbol:
            return sorted(
                [t for t in self.trades.values() if t.symbol == symbol],
                key=lambda t: t.entry_time,
                reverse=True
            )
        return sorted(self.trades.values(), key=lambda t: t.entry_time, reverse=True)

    # =====================================================================
    # RECONCILIATION & SYNC
    # =====================================================================

    def reconcile_with_broker(self, broker_positions: List[Dict]) -> Tuple[List[str], List[str]]:
        """
        Reconcile order state with broker positions.

        Returns:
            (missing_orders, extra_positions) - orders we don't know about in app
        """
        missing_orders = []

        for pos in broker_positions:
            symbol = pos.get("symbol") or pos.get("tradingsymbol")
            qty = pos.get("qty") or pos.get("quantity")

            # Check if we have an open buy order for this
            found = False
            for order in self.orders.values():
                if (order.symbol == symbol and
                    order.transaction_type == TransactionType.BUY and
                    order.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}):
                    found = True
                    break

            if not found:
                missing_orders.append(f"{symbol} ({qty} shares)")

        return missing_orders, []

    # =====================================================================
    # PERSISTENCE
    # =====================================================================

    def _save_to_disk(self) -> None:
        """Save all orders and trades to disk."""
        try:
            # Save orders
            orders_data = {
                oid: {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "qty": o.qty,
                    "transaction_type": o.transaction_type.value,
                    "order_type": o.order_type.value,
                    "price": o.price,
                    "status": o.status.value,
                    "created_at": o.created_at,
                    "submitted_at": o.submitted_at,
                    "filled_at": o.filled_at,
                    "filled_qty": o.filled_qty,
                    "average_price": o.average_price,
                    "rejection_reason": o.rejection_reason,
                    "strategy_id": o.strategy_id,
                    "entry_signal_strength": o.entry_signal_strength,
                    "gtt_id": o.gtt_id,
                    "gtt_status": o.gtt_status,
                    "is_entry_order": o.is_entry_order,
                    "is_stop_loss": o.is_stop_loss,
                    "is_target": o.is_target,
                }
                for oid, o in self.orders.items()
            }

            with open(self.orders_file, "w") as f:
                json.dump(orders_data, f, indent=2)

            # Save trades
            trades_data = {
                tid: {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "entry_order_id": t.entry_order_id,
                    "entry_price": t.entry_price,
                    "entry_qty": t.entry_qty,
                    "entry_time": t.entry_time,
                    "exit_orders": t.exit_orders,
                    "exit_price": t.exit_price,
                    "exit_qty_total": t.exit_qty_total,
                    "exit_time": t.exit_time,
                    "realized_pnl": t.realized_pnl,
                    "realized_pnl_pct": t.realized_pnl_pct,
                    "strategy_name": t.strategy_name,
                    "entry_signal": t.entry_signal,
                    "exit_reason": t.exit_reason,
                    "stop_loss": t.stop_loss,
                    "target": t.target,
                    "max_profit": t.max_profit,
                    "max_loss": t.max_loss,
                }
                for tid, t in self.trades.items()
            }

            with open(self.trades_file, "w") as f:
                json.dump(trades_data, f, indent=2)

        except Exception as e:
            print(f"Warning: Could not save order/trade data: {e}")

    def _load_from_disk(self) -> None:
        """Load orders and trades from disk."""
        try:
            if os.path.exists(self.orders_file):
                with open(self.orders_file, "r") as f:
                    orders_data = json.load(f)
                    for oid, data in orders_data.items():
                        order = Order(
                            order_id=data["order_id"],
                            symbol=data["symbol"],
                            qty=data["qty"],
                            transaction_type=TransactionType(data["transaction_type"]),
                            order_type=OrderType(data["order_type"]),
                            price=data.get("price"),
                            status=OrderStatus(data["status"]),
                            created_at=data["created_at"],
                            submitted_at=data.get("submitted_at"),
                            filled_at=data.get("filled_at"),
                            filled_qty=data.get("filled_qty", 0),
                            average_price=data.get("average_price", 0.0),
                            rejection_reason=data.get("rejection_reason", ""),
                            strategy_id=data.get("strategy_id", ""),
                            entry_signal_strength=data.get("entry_signal_strength", 0.0),
                            gtt_id=data.get("gtt_id"),
                            gtt_status=data.get("gtt_status", ""),
                            is_entry_order=data.get("is_entry_order", True),
                            is_stop_loss=data.get("is_stop_loss", False),
                            is_target=data.get("is_target", False),
                        )
                        self.orders[oid] = order

            if os.path.exists(self.trades_file):
                with open(self.trades_file, "r") as f:
                    trades_data = json.load(f)
                    for tid, data in trades_data.items():
                        trade = Trade(
                            trade_id=data["trade_id"],
                            symbol=data["symbol"],
                            entry_order_id=data["entry_order_id"],
                            entry_price=data["entry_price"],
                            entry_qty=data["entry_qty"],
                            entry_time=data["entry_time"],
                            exit_orders=data.get("exit_orders", []),
                            exit_price=data.get("exit_price"),
                            exit_qty_total=data.get("exit_qty_total", 0),
                            exit_time=data.get("exit_time"),
                            realized_pnl=data.get("realized_pnl", 0.0),
                            realized_pnl_pct=data.get("realized_pnl_pct", 0.0),
                            strategy_name=data.get("strategy_name", ""),
                            entry_signal=data.get("entry_signal", ""),
                            exit_reason=data.get("exit_reason", ""),
                            stop_loss=data.get("stop_loss", 0.0),
                            target=data.get("target", 0.0),
                            max_profit=data.get("max_profit", 0.0),
                            max_loss=data.get("max_loss", 0.0),
                        )
                        self.trades[tid] = trade

        except Exception as e:
            print(f"Warning: Could not load order/trade data: {e}")

    def export_to_csv(self, output_dir: str = "./exports") -> Tuple[str, str]:
        """Export orders and trades to CSV files."""
        os.makedirs(output_dir, exist_ok=True)

        import csv

        # Export orders
        orders_file = os.path.join(output_dir, f"orders_{dt.date.today()}.csv")
        with open(orders_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Order ID", "Symbol", "Qty", "Type", "Order Type", "Price",
                "Status", "Filled Qty", "Avg Price", "Created", "Submitted", "Filled",
                "Strategy", "Signal Strength"
            ])
            for order in sorted(self.orders.values(), key=lambda o: o.created_at):
                writer.writerow([
                    order.order_id,
                    order.symbol,
                    order.qty,
                    order.transaction_type.value,
                    order.order_type.value,
                    order.price or "",
                    order.status.value,
                    order.filled_qty,
                    round(order.average_price, 2),
                    order.created_at,
                    order.submitted_at or "",
                    order.filled_at or "",
                    order.strategy_id,
                    round(order.entry_signal_strength, 2),
                ])

        # Export trades
        trades_file = os.path.join(output_dir, f"trades_{dt.date.today()}.csv")
        with open(trades_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Trade ID", "Symbol", "Entry Price", "Entry Qty", "Entry Time",
                "Exit Price", "Exit Qty", "Exit Time", "P&L (₹)", "P&L (%)",
                "Strategy", "Entry Signal", "Exit Reason", "SL", "Target"
            ])
            for trade in sorted(self.trades.values(), key=lambda t: t.entry_time):
                writer.writerow([
                    trade.trade_id,
                    trade.symbol,
                    round(trade.entry_price, 2),
                    trade.entry_qty,
                    trade.entry_time,
                    round(trade.exit_price, 2) if trade.exit_price else "",
                    trade.exit_qty_total,
                    trade.exit_time or "",
                    round(trade.realized_pnl, 2),
                    round(trade.realized_pnl_pct, 2),
                    trade.strategy_name,
                    trade.entry_signal,
                    trade.exit_reason,
                    round(trade.stop_loss, 2),
                    round(trade.target, 2),
                ])

        return orders_file, trades_file
