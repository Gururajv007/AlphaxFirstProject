"""
broker_dhan.py
--------------
Dhan implementation of the BrokerInterface.

Dhan is an Indian broker offering zero-commission trading with advanced APIs.
Docs: https://api.dhan.co/
SDK: pip install dhanhq
"""

import base64
import json
import os
from typing import Optional, Dict, List

import requests

from broker_interface import BrokerInterface, Position, GTTOrder

try:
    from dhanhq import DhanContext, DhanLogin, MarketFeed, Order, Portfolio, Funds
except ImportError:  # pragma: no cover - depends on installed SDK
    DhanContext = None
    DhanLogin = None
    MarketFeed = None
    Order = None
    Portfolio = None
    Funds = None


class BrokerNotConfigured(Exception):
    pass


def extract_dhan_client_id(value: str) -> str:
    """Normalize Dhan credentials into a usable client id.

    The app may receive the Dhan client ID from a JWT-style token payload, a
    plain numeric client id, or a pre-existing config entry. This helper keeps
    the UI and broker setup working with all of those shapes.
    """
    if not value:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    if text.isdigit():
        return text

    def _extract_from_payload(payload: object) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        for key in ("dhanClientId", "client_id", "clientId", "dhan_client_id"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return str(int(candidate))
        return None

    for candidate_text in (text, *([part for part in text.split(".")[1:] if part])):
        if not candidate_text:
            continue
        try:
            decoded_bytes = base64.urlsafe_b64decode(candidate_text + "=" * (-len(candidate_text) % 4))
            decoded_text = decoded_bytes.decode("utf-8")
        except Exception:
            continue
        if not decoded_text:
            continue
        if decoded_text.startswith("{"):
            try:
                payload = json.loads(decoded_text)
            except Exception:
                payload = None
            extracted = _extract_from_payload(payload)
            if extracted:
                return extracted
        elif "." in decoded_text:
            try:
                nested_payload = json.loads(decoded_text)
            except Exception:
                nested_payload = None
            extracted = _extract_from_payload(nested_payload)
            if extracted:
                return extracted

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        extracted = _extract_from_payload(payload)
        if extracted:
            return extracted

    return text


def looks_like_dhan_access_token(value: str) -> bool:
    """Return True when a string looks like a Dhan access token or callback token."""
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    if text.startswith("http") and "access_token" in text:
        return True
    if text.count(".") >= 1:
        return True
    return len(text) >= 20


def extract_balance_value(payload) -> Optional[float]:
    """Extract a numeric balance from common Dhan fund-response payloads."""
    if payload is None:
        return None

    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return float(payload)

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    if isinstance(payload, dict):
        for key in (
            "availableBalance",
            "availabelBalance",
            "available",
            "balance",
            "withdrawableBalance",
            "collateralAmount",
            "sodLimit",
            "netBalance",
            "marginAvailable",
            "cash",
        ):
            if key in payload:
                value = extract_balance_value(payload[key])
                if value is not None:
                    return value

        for value in payload.values():
            extracted = extract_balance_value(value)
            if extracted is not None:
                return extracted

    elif isinstance(payload, (list, tuple)):
        for item in payload:
            extracted = extract_balance_value(item)
            if extracted is not None:
                return extracted

    return None


class DhanBroker(BrokerInterface):
    """Dhan broker implementation backed by the official dhanhq SDK."""

    SECURITY_LIST_CACHE: Dict[tuple, object] = {}

    def __init__(self, api_key: str = "", access_token: str = "", api_secret: str = ""):
        if DhanContext is None or DhanLogin is None or Order is None or Portfolio is None or Funds is None:
            raise ImportError("dhanhq is not installed. Run: pip install dhanhq")
        if not api_key:
            raise BrokerNotConfigured("Dhan API key is required")

        self.api_key = extract_dhan_client_id(api_key)
        self.api_secret = api_secret
        self.access_token = access_token or ""
        self.dhan_context = DhanContext(client_id=api_key, access_token=self.access_token)
        self._order_client = None
        self._portfolio_client = None
        self._funds_client = None
        self._market_feed = None

    def _require_access_token(self) -> None:
        if not self.access_token:
            raise BrokerNotConfigured(
                "No Dhan access token is available. Complete the broker login flow in Settings first."
            )

    def _get_order_client(self):
        self._require_access_token()
        if self._order_client is None:
            self._order_client = Order(self.dhan_context)
        return self._order_client

    def _get_portfolio_client(self):
        self._require_access_token()
        if self._portfolio_client is None:
            self._portfolio_client = Portfolio(self.dhan_context)
        return self._portfolio_client

    def _get_funds_client(self):
        self._require_access_token()
        if self._funds_client is None:
            self._funds_client = Funds(self.dhan_context)
        return self._funds_client

    def _get_market_feed(self):
        self._require_access_token()
        if self._market_feed is None:
            self._market_feed = MarketFeed(self.dhan_context)
        return self._market_feed

    def _to_exchange_segment(self, exchange: str) -> str:
        exchange = (exchange or "NSE").upper()
        mapping = {
            "NSE": "NSE_EQ",
            "BSE": "BSE_EQ",
            "NFO": "NSE_FNO",
            "CDS": "NSE_CDS",
            "MCX": "MCX",
        }
        return mapping.get(exchange, exchange)

    def _download_security_list(self):
        cache_key = ("security_list", self.api_key)
        if cache_key in self.SECURITY_LIST_CACHE:
            return self.SECURITY_LIST_CACHE[cache_key]

        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        import pandas as pd

        df = pd.read_csv(response.content.decode("utf-8"), low_memory=False)
        self.SECURITY_LIST_CACHE[cache_key] = df
        return df

    def _resolve_security_id(self, symbol: str, exchange: str = "NSE") -> int:
        exchange_segment = self._to_exchange_segment(exchange)
        cache_key = (symbol.upper(), exchange_segment)
        if cache_key in self.SECURITY_LIST_CACHE:
            return int(self.SECURITY_LIST_CACHE[cache_key])

        try:
            df = self._download_security_list()
        except Exception as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"Unable to resolve Dhan securityId for {symbol}: {exc}") from exc

        symbol_norm = str(symbol).upper()
        columns = {c.upper(): c for c in df.columns}
        trading_symbol_col = columns.get("SEM_TRADING_SYMBOL")
        segment_col = columns.get("SEM_SEGMENT")
        security_id_col = columns.get("SEM_SMST_SECURITY_ID") or columns.get("SECURITY_ID") or columns.get("SEM_SECURITY_ID")

        if not trading_symbol_col or not segment_col or not security_id_col:
            raise RuntimeError("Dhan security-list CSV format was not recognized")

        mask = df[trading_symbol_col].astype(str).str.upper() == symbol_norm
        candidates = df.loc[mask & (df[segment_col].astype(str).str.upper() == exchange_segment)]
        if candidates.empty:
            raise RuntimeError(f"No Dhan security found for {symbol} on {exchange_segment}")

        security_id = int(candidates.iloc[0][security_id_col])
        self.SECURITY_LIST_CACHE[cache_key] = security_id
        return security_id

    def _extract_price(self, payload) -> Optional[float]:
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            return float(payload)
        if isinstance(payload, dict):
            for key in ("ltp", "LTP", "lastTradedPrice", "last_price", "lastPrice", "price"):
                if key in payload:
                    try:
                        return float(payload[key])
                    except (TypeError, ValueError):
                        continue
            for value in payload.values():
                price = self._extract_price(value)
                if price is not None:
                    return price
        elif isinstance(payload, list):
            for item in payload:
                price = self._extract_price(item)
                if price is not None:
                    return price
        return None

    def get_login_url(self) -> str:
        """Returns a Dhan consent login URL or a help page when the SDK cannot build it."""
        if not self.api_key:
            raise BrokerNotConfigured("Dhan API key is required")
        if not self.api_secret:
            return "https://login.dhan.co/?location=DH_WEB"

        try:
            login_client = DhanLogin(self.api_key)
            consent_app_id = login_client.generate_login_session(self.api_key, self.api_secret)
            return f"https://auth.dhan.co/login/consentApp-login?consentAppId={consent_app_id}"
        except Exception:
            return "https://login.dhan.co/?location=DH_WEB"

    def generate_session(self, request_token: str) -> str:
        """Accepts either a direct Dhan access token or a callback token/request value."""
        cleaned = str(request_token or "").strip()
        if not cleaned:
            raise BrokerNotConfigured("A Dhan token value is required")

        if looks_like_dhan_access_token(cleaned):
            access_token = cleaned
            if "access_token=" in access_token and "http" in access_token:
                import urllib.parse as urlparse
                parsed = urlparse.urlparse(access_token)
                query = urlparse.parse_qs(parsed.query)
                access_token = query.get("access_token", [access_token])[0]
            self.access_token = access_token
            self.dhan_context = DhanContext(client_id=self.api_key, access_token=self.access_token)
            return self.access_token

        if not self.api_secret:
            raise BrokerNotConfigured("Dhan API secret is required to complete login")

        try:
            login_client = DhanLogin(self.api_key)
            response = login_client.consume_token_id(cleaned, self.api_key, self.api_secret)
            access_token = (
                response.get("accessToken")
                or response.get("access_token")
                or response.get("data", {}).get("accessToken")
                or response.get("data", {}).get("access_token")
            )
        except Exception as exc:
            raise RuntimeError(f"Could not complete Dhan token exchange: {exc}") from exc

        if not access_token:
            raise RuntimeError(f"Could not extract Dhan access token from response: {response}")

        self.access_token = access_token
        self.dhan_context = DhanContext(client_id=self.api_key, access_token=self.access_token)
        return access_token

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetches last traded price using the Dhan marketfeed endpoint."""
        self._require_access_token()
        security_id = self._resolve_security_id(symbol, exchange)
        exchange_segment = self._to_exchange_segment(exchange)
        response = self._get_market_feed().ticker_data({exchange_segment: [security_id]})
        price = self._extract_price(response)
        if price is None:
            raise RuntimeError(f"Unable to read Dhan LTP for {symbol}")
        return float(price)

    def get_margins(self) -> Dict:
        """Returns account margin details from Dhan fund limits."""
        raw = self._get_funds_client().get_fund_limits()
        if isinstance(raw, dict):
            payload = raw.get("data", raw)
            if isinstance(payload, dict):
                return payload
        return raw

    def get_positions(self) -> List[Position]:
        """Returns list of open positions normalized for the app."""
        raw_positions = self._get_portfolio_client().get_positions()

        if isinstance(raw_positions, dict):
            payload = raw_positions.get("data", raw_positions)
        else:
            payload = raw_positions

        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("positions") or [payload]
        else:
            items = payload

        positions: List[Position] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            qty = int(item.get("quantity") or item.get("netQty") or item.get("dayQty") or 0)
            if qty == 0:
                continue

            symbol = item.get("tradingSymbol") or item.get("symbol") or item.get("securityName") or ""
            buy_price = item.get("averagePrice") or item.get("avgPrice") or item.get("buyPrice") or item.get("buy_average_price") or 0.0
            current_price = item.get("ltp") or item.get("lastPrice") or item.get("currentPrice") or 0.0
            pnl = item.get("pnl") or item.get("unrealizedPnl") or item.get("unrealizedProfit") or 0.0

            try:
                buy_price = float(buy_price)
                current_price = float(current_price) if current_price not in (None, "", 0) else self.get_ltp(symbol)
                pnl = float(pnl)
            except Exception:
                buy_price = float(buy_price or 0.0)
                current_price = float(current_price or 0.0)
                pnl = float(pnl or 0.0)

            pnl_pct = (pnl / (buy_price * qty) * 100) if buy_price and qty else 0.0
            positions.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    buy_price=buy_price,
                    current_price=current_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    broker_raw=item,
                )
            )

        return positions

    def place_order(
        self,
        symbol: str,
        qty: int,
        transaction_type: str = "BUY",
        order_type: str = "MARKET",
        product: str = "MIS",
        price: Optional[float] = None,
        exchange: str = "NSE",
        variety: str = "regular",
    ) -> str:
        """Places an order through the Dhan order API."""
        self._require_access_token()
        security_id = self._resolve_security_id(symbol, exchange)
        exchange_segment = self._to_exchange_segment(exchange)
        transaction_type = transaction_type.upper()
        order_type = order_type.upper()
        product_type = "INTRA" if product.upper() == "MIS" else product.upper()
        price_value = float(price or 0.0)
        trigger_price = 0

        if order_type == "LIMIT" and price_value <= 0:
            raise ValueError("A limit order requires a price")

        response = self._get_order_client().place_order(
            security_id,
            exchange_segment,
            transaction_type,
            int(qty),
            order_type,
            product_type,
            price_value,
            trigger_price=trigger_price,
            tag=f"app-{symbol}-{transaction_type.lower()}",
        )

        order_id = None
        if isinstance(response, dict):
            order_id = response.get("orderId") or response.get("order_id") or response.get("data", {}).get("orderId")
        if not order_id:
            order_id = str(response)
        return order_id

    def place_gtt_stop_loss(
        self,
        symbol: str,
        qty: int,
        trigger_price: float,
        exchange: str = "NSE",
    ) -> GTTOrder:
        """Falls back to a SELL limit order because Dhan GTT support is not exposed here."""
        order_id = self.place_order(
            symbol=symbol,
            qty=qty,
            transaction_type="SELL",
            order_type="LIMIT",
            product="CNC",
            price=trigger_price,
            exchange=exchange,
        )
        return GTTOrder(
            gtt_id=order_id,
            symbol=symbol,
            trigger_price=float(trigger_price),
            status="PENDING",
            broker_raw={"orderId": order_id},
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order."""
        try:
            self._get_order_client().cancel_order(order_id)
            return True
        except Exception:
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Fetches order status."""
        try:
            return self._get_order_client().get_order_by_id(order_id)
        except Exception:
            return None
