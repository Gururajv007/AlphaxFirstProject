"""
broker_kite.py
--------------
Zerodha Kite Connect implementation of the BrokerInterface.

Thin wrapper around the official `kiteconnect` Python SDK for Zerodha's
Kite Connect API. This is the ONLY module that talks to a real broker /
places real orders - keeping it isolated makes it easy to audit and easy
to swap for a different broker later (Upstox, Fyers, Angel One, etc. all
have similarly-shaped SDKs).

You need a Kite Connect developer subscription (paid, separate from your
normal Zerodha trading account) to get an api_key/api_secret:
https://developers.kite.trade

LOGIN FLOW (Kite Connect uses a browser-redirect login, not a plain
username/password the app can hold):
  1. App calls get_login_url() -> you open that URL in your browser
  2. You log in with your normal Zerodha credentials + 2FA
  3. Zerodha redirects to your configured redirect URL with a
     `request_token` in the query string
  4. You paste that request_token back into the app's Settings tab
  5. App calls generate_session(request_token) -> gets an access_token
  6. access_token is valid until ~6am the next trading day; you repeat
     steps 1-5 once per day.
"""

from dataclasses import dataclass
from typing import Optional, Dict, List

from broker_interface import BrokerInterface, Position, GTTOrder

try:
    from kiteconnect import KiteConnect
except ImportError:  # lets the rest of the app run even before this is installed
    KiteConnect = None


class BrokerNotConfigured(Exception):
    pass


class KiteBroker(BrokerInterface):
    def __init__(self, api_key: str, api_secret: str = "", access_token: str = ""):
        if KiteConnect is None:
            raise ImportError(
                "kiteconnect is not installed. Run: pip install kiteconnect"
            )
        if not api_key:
            raise BrokerNotConfigured("No API key configured. Add it in the Settings tab.")

        self.api_key = api_key
        self.api_secret = api_secret
        self.kite = KiteConnect(api_key=api_key)
        if access_token:
            self.kite.set_access_token(access_token)

    def _require_access_token(self) -> None:
        if not getattr(self.kite, "access_token", None):
            raise BrokerNotConfigured(
                "No access token is available. Complete the broker login flow in Settings first."
            )

    def get_login_url(self) -> str:
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> str:
        """Exchanges a one-time request_token for a day-valid access_token."""
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        self.kite.set_access_token(access_token)
        return access_token

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        self._require_access_token()
        key = f"{exchange}:{symbol}"
        quote = self.kite.ltp([key])
        return float(quote[key]["last_price"])

    def get_margins(self) -> dict:
        self._require_access_token()
        return self.kite.margins()

    def get_positions(self) -> List[Position]:
        """Returns list of current open positions (normalized)."""
        self._require_access_token()
        raw_positions = self.kite.positions()
        
        # Zerodha returns net and day positions; we care about net (multi-day holdings)
        net_positions = raw_positions.get("net", [])
        positions = []
        
        for pos in net_positions:
            # Skip if no quantity
            if pos.get("quantity", 0) == 0:
                continue
                
            symbol = pos.get("tradingsymbol", "")
            qty = int(pos.get("quantity", 0))
            buy_price = float(pos.get("buy_price", 0)) or float(pos.get("average_price", 0))
            current_price = float(pos.get("last_price", 0))
            pnl = float(pos.get("pnl", 0))
            pnl_pct = (pnl / (buy_price * qty * 100)) * 100 if buy_price and qty else 0
            
            positions.append(Position(
                symbol=symbol,
                qty=qty,
                buy_price=buy_price,
                current_price=current_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                broker_raw=pos
            ))
        
        return positions

    def place_order(
        self,
        symbol: str,
        qty: int,
        transaction_type: str = "BUY",
        order_type: str = "MARKET",
        product: str = "CNC",  # CNC = delivery/swing; use MIS for intraday
        price: float = None,
        exchange: str = "NSE",
        variety: str = "regular",
    ) -> str:
        """
        Places a REAL order. Returns the broker order_id.

        product="CNC" (Cash and Carry) is correct for multi-day swing
        positions in the cash segment. Do not use MIS (intraday) for
        swing trades - MIS positions get auto-squared-off same day.
        """
        self._require_access_token()
        params = dict(
            variety=variety,
            exchange=exchange,
            tradingsymbol=symbol,
            transaction_type=transaction_type,
            quantity=qty,
            order_type=order_type,
            product=product,
        )
        if order_type == "LIMIT" and price is not None:
            params["price"] = price

        order_response = self.kite.place_order(**params)
        if isinstance(order_response, dict):
            return order_response.get("order_id") or order_response.get("data", {}).get("order_id") or str(order_response)
        return str(order_response)

    def place_gtt_stop_loss(self, symbol: str, qty: int, trigger_price: float, exchange: str = "NSE") -> GTTOrder:
        """
        Places a server-side GTT (Good Till Triggered) stop-loss SELL order.
        This is important: a server-side stop means your position is still
        protected even if your laptop/internet/the app itself goes down.
        """
        self._require_access_token()
        raw_response = self.kite.place_gtt(
            trigger_type=self.kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[trigger_price],
            last_price=trigger_price,
            orders=[
                {
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "order_type": "LIMIT",
                    "product": "CNC",
                    "price": trigger_price,
                }
            ],
        )
        
        gtt_id = raw_response.get("trigger_id", raw_response.get("id", ""))
        return GTTOrder(
            gtt_id=gtt_id,
            symbol=symbol,
            trigger_price=trigger_price,
            status="ACTIVE",
            broker_raw=raw_response
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order. Returns True if successful."""
        self._require_access_token()
        try:
            self.kite.cancel_order(variety="regular", order_id=order_id)
            return True
        except Exception:
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Fetches the status of a specific order."""
        self._require_access_token()
        try:
            orders = self.kite.orders()
            for order in orders:
                if order.get("order_id") == order_id or order.get("order_id") == str(order_id):
                    return order
            return None
        except Exception:
            return None
