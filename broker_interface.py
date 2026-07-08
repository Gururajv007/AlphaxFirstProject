"""
broker_interface.py
-------------------
Abstract base class defining the common interface all broker implementations must follow.
This allows the app to work with any broker (Zerodha, Upstox, Angel One, Fyers, Dhan)
without changing core logic.

Each broker's SDK has a different API shape, but we normalize them here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, List


@dataclass
class Order:
    """Normalized order response across all brokers."""
    order_id: str
    symbol: str
    qty: int
    transaction_type: str  # BUY or SELL
    order_type: str  # MARKET, LIMIT, etc.
    status: str  # PENDING, FILLED, REJECTED, CANCELLED
    filled_qty: int
    price: Optional[float] = None
    average_price: Optional[float] = None


@dataclass
class Position:
    """Normalized position across all brokers."""
    symbol: str
    qty: int
    buy_price: float
    current_price: float
    pnl: float
    pnl_pct: float
    broker_raw: Dict = None  # broker-specific data


@dataclass
class GTTOrder:
    """Normalized GTT (Good Till Triggered) stop-loss order response."""
    gtt_id: str
    symbol: str
    trigger_price: float
    status: str
    broker_raw: Dict = None


class BrokerInterface(ABC):
    """Abstract base class for all broker implementations."""

    @abstractmethod
    def get_login_url(self) -> str:
        """
        Returns a login URL for user authentication.
        User opens this in browser, logs in, and receives a request_token.
        """
        pass

    @abstractmethod
    def generate_session(self, request_token: str) -> str:
        """
        Exchanges a one-time request_token for a day-valid access_token.
        Returns the access_token.
        """
        pass

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """
        Fetches the last traded price for a symbol.
        """
        pass

    @abstractmethod
    def get_margins(self) -> Dict:
        """
        Returns account margin details (available cash, used margin, etc).
        Format varies by broker but should include 'available', 'used', 'total'.
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        Returns list of current open positions.
        """
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: int,
        transaction_type: str = "BUY",
        order_type: str = "MARKET",
        product: str = "CNC",
        price: Optional[float] = None,
        exchange: str = "NSE",
        variety: str = "regular",
    ) -> str:
        """
        Places a REAL order. Returns the broker order_id.
        
        Args:
            symbol: Trading symbol (e.g., 'RELIANCE')
            qty: Quantity to trade
            transaction_type: 'BUY' or 'SELL'
            order_type: 'MARKET', 'LIMIT'
            product: 'CNC' (swing/delivery), 'MIS' (intraday), etc. (varies by broker)
            price: For LIMIT orders
            exchange: 'NSE', 'BSE', etc.
            variety: 'regular', 'oco', etc.
        """
        pass

    @abstractmethod
    def place_gtt_stop_loss(
        self,
        symbol: str,
        qty: int,
        trigger_price: float,
        exchange: str = "NSE",
    ) -> GTTOrder:
        """
        Places a server-side GTT (Good Till Triggered) stop-loss SELL order.
        This is critical for risk management - the stop-loss works even if
        your app/internet goes down.
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancels a pending order. Returns True if successful.
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """
        Fetches the status of a specific order.
        """
        pass
