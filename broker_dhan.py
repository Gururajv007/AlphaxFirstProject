"""
broker_dhan.py
--------------
Dhan implementation of the BrokerInterface.

Dhan is an Indian broker offering zero-commission trading with advanced APIs.
Docs: https://api.dhan.co/
SDK: pip install dhanhq
"""

import base64
import datetime as dt
import json
import os
import re
import time
from typing import Optional, Dict, List

import pandas as pd
import requests

from broker_interface import BrokerInterface, Position, GTTOrder

try:
    from dhanhq import DhanContext, DhanLogin, MarketFeed, Order, Portfolio, Funds, ForeverOrder, HistoricalData
except ImportError:  # pragma: no cover - depends on installed SDK
    DhanContext = None
    DhanLogin = None
    MarketFeed = None
    Order = None
    Portfolio = None
    Funds = None
    ForeverOrder = None
    HistoricalData = None


class BrokerNotConfigured(Exception):
    pass


class DhanAPIError(Exception):
    """Typed error raised when the Dhan API reports a failure.

    Attributes:
        code (str): Dhan error code, e.g. "DH-901" (bad auth), "DH-904" (rate
            limit), "DH-905" (input exception), or "EXPIRED" for an expired
            local access token.
        error_type (str): Dhan error type label when provided.
        context (str): Which operation failed (e.g. "order placement").
        retryable (bool): True when the error is transient (rate-limit/network).
    """

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        error_type: Optional[str] = None,
        context: str = "",
        retryable: bool = False,
    ):
        self.code = code
        self.error_type = error_type
        self.context = context
        self.retryable = retryable
        super().__init__(message)


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


def decode_jwt_expiry(token: str) -> Optional[dt.datetime]:
    """Return the UTC expiry datetime embedded in a Dhan JWT access token.

    Dhan access tokens are JWTs carrying a 24-hour ``exp`` claim. Returns None
    when the token is not a JWT or the claim is unreadable.
    """
    if not token or "." not in token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        exp = payload.get("exp")
        if exp:
            return dt.datetime.fromtimestamp(float(exp), tz=dt.timezone.utc)
    except Exception:
        return None
    return None


# Mapping from normalized exchange/segment to (scrip-master exchange id, segment code).
# Segment codes come from the Dhan scrip master (C=currency, E=equity, F=F&O, M=MCX).
_SCRIP_MASTER_FILTERS = {
    "NSE_EQ": ("NSE", "E"),
    "BSE_EQ": ("BSE", "E"),
    "NSE_CURRENCY": ("NSE", "C"),
    "BSE_CURRENCY": ("BSE", "C"),
    "MCX_COMM": ("MCX", "M"),
}

# Dhan F&O scrips live in a separate scrip-master file.
_SCRIP_MASTER_FNO_FILTERS = {
    "NSE_FNO": ("NSE", "F"),
    "BSE_FNO": ("BSE", "F"),
}


class DhanBroker(BrokerInterface):
    """Dhan broker implementation backed by the official dhanhq SDK."""

    SECURITY_LIST_CACHE: Dict[tuple, object] = {}
    SECURITY_META_CACHE: Dict[tuple, dict] = {}
    SECURITY_LIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhan_security_list.csv")
    SECURITY_LIST_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
    SECURITY_LIST_FNO_URL = "https://images.dhan.co/api-data/api-scrip-master-fno.csv"
    SECURITY_LIST_TTL_SECONDS = 7 * 24 * 3600  # refresh the local master weekly

    def __init__(self, api_key: str = "", access_token: str = "", api_secret: str = ""):
        if DhanContext is None or DhanLogin is None or Order is None or Portfolio is None or Funds is None:
            raise ImportError("dhanhq is not installed. Run: pip install dhanhq")
        if not api_key:
            raise BrokerNotConfigured("Dhan API key is required")

        self.api_key = extract_dhan_client_id(api_key)
        self.api_secret = api_secret
        self.access_token = access_token or ""
        self.dhan_context = DhanContext(client_id=self.api_key, access_token=self.access_token)
        self._order_client = None
        self._portfolio_client = None
        self._funds_client = None
        self._market_feed = None
        self._historical_client = None
        self._forever_client = None
        self._security_list_stale = False

    # =====================================================================
    # AUTH / TOKEN HELPERS
    # =====================================================================

    @property
    def token_expiry(self) -> Optional[dt.datetime]:
        return decode_jwt_expiry(self.access_token)

    @property
    def token_expired(self) -> bool:
        expiry = self.token_expiry
        if expiry is None:
            return False
        return dt.datetime.now(dt.timezone.utc) >= expiry

    def _require_access_token(self) -> None:
        if not self.access_token:
            raise BrokerNotConfigured(
                "No Dhan access token is available. Complete the broker login flow in Settings first."
            )
        if self.token_expired:
            raise DhanAPIError(
                "Dhan access token expired. Re-generate it in Settings / API -> Step 3: Daily login.",
                code="EXPIRED",
                error_type="Authentication",
                context="authentication",
            )

    def renew_token(self) -> str:
        """Refresh the current Dhan access token via the RenewToken API."""
        self._require_access_token()
        login_client = DhanLogin(self.api_key)
        response = login_client.renew_token(self.access_token)
        access_token = (
            response.get("accessToken")
            or response.get("access_token")
            or response.get("data", {}).get("accessToken")
            or response.get("data", {}).get("access_token")
        )
        if not access_token:
            raise DhanAPIError(f"Could not extract renewed Dhan access token: {response}", context="token renewal")
        self.access_token = access_token
        self.dhan_context = DhanContext(client_id=self.api_key, access_token=self.access_token)
        return access_token

    @staticmethod
    def _make_correlation_id(raw: str, max_len: int = 30) -> str:
        """Sanitize an arbitrary tag into a valid Dhan correlationId (<=30 chars)."""
        sanitized = re.sub(r"[^a-zA-Z0-9 _-]", "-", str(raw or ""))
        return (sanitized[:max_len] or "app").strip()

    # =====================================================================
    # SDK CLIENT HELPERS
    # =====================================================================

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

    def _get_historical_client(self):
        self._require_access_token()
        if self._historical_client is None:
            self._historical_client = HistoricalData(self.dhan_context)
        return self._historical_client

    def _get_forever_client(self):
        self._require_access_token()
        if self._forever_client is None:
            self._forever_client = ForeverOrder(self.dhan_context)
        return self._forever_client

    @staticmethod
    def _check_response(response, context: str):
        """Raise a typed DhanAPIError on API failure; otherwise return the data payload.

        The dhanhq SDK wraps every response as
        ``{"status": "success"|"failure", "remarks": ..., "data": ...}``.
        """
        if not isinstance(response, dict):
            return response
        if response.get("status") == "success":
            return response.get("data", response)
        remarks = response.get("remarks")
        if isinstance(remarks, dict):
            code = remarks.get("error_code")
            error_type = remarks.get("error_type")
            message = remarks.get("error_message") or str(remarks)
            retryable = code in ("DH-904", "DH-909")
            raise DhanAPIError(
                f"Dhan {context} failed ({code}): {message}",
                code=code,
                error_type=error_type,
                context=context,
                retryable=retryable,
            )
        raise DhanAPIError(
            f"Dhan {context} failed: {remarks or response}",
            context=context,
            retryable=True,
        )

    # =====================================================================
    # EXCHANGE / SECURITY MASTER
    # =====================================================================

    def _to_exchange_segment(self, exchange: str) -> str:
        exchange = (exchange or "NSE").upper()
        mapping = {
            "NSE": "NSE_EQ",
            "BSE": "BSE_EQ",
            "NFO": "NSE_FNO",
            "CDS": "NSE_CURRENCY",
            "MCX": "MCX_COMM",
            "BSE_FNO": "BSE_FNO",
            "BSE_CURRENCY": "BSE_CURRENCY",
        }
        return mapping.get(exchange, exchange)

    def is_security_list_stale(self) -> bool:
        """True when the local scrip master is old and was used as a fallback."""
        return self._security_list_stale

    def _load_security_master(self, url: str, local_file: str, exchange_segment: str) -> pd.DataFrame:
        """Return the scrip master for a segment, preferring a fresh local cache."""
        cache_key = ("security_list", exchange_segment)
        if cache_key in self.SECURITY_LIST_CACHE:
            return self.SECURITY_LIST_CACHE[cache_key]

        df = None
        source = None

        if os.path.exists(local_file):
            try:
                df = pd.read_csv(local_file, low_memory=False)
                if df.empty or "SEM_SMST_SECURITY_ID" not in {c.upper() for c in df.columns}:
                    df = None
            except Exception:
                df = None

        fresh = False
        if df is not None:
            try:
                age = time.time() - os.path.getmtime(local_file)
                fresh = age < self.SECURITY_LIST_TTL_SECONDS
            except Exception:
                fresh = False
            source = f"cache:{local_file}"

        if df is None or not fresh:
            try:
                response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                response.raise_for_status()
                downloaded = pd.read_csv(response.content.decode("utf-8"), low_memory=False)
                expected = {"SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_TRADING_SYMBOL"}
                if expected.issubset({c.upper() for c in downloaded.columns}):
                    df = downloaded
                    fresh = True
                    source = f"network:{url}"
                    if os.path.isdir(os.path.dirname(local_file)):
                        try:
                            downloaded.to_csv(local_file, index=False)
                        except Exception:
                            pass
                else:
                    # Network returned something that is not a valid master (e.g. a
                    # signed-URL error body). Fall back to a stale local copy if any.
                    if df is None:
                        raise RuntimeError("Dhan scrip-master download returned unexpected content")
            except Exception as exc:
                if df is None:
                    raise RuntimeError(f"Unable to load Dhan scrip master for {exchange_segment}: {exc}") from exc

        if df is None:
            raise RuntimeError(f"Unable to load Dhan scrip master for {exchange_segment}")

        self._security_list_stale = not fresh
        self.SECURITY_LIST_CACHE[cache_key] = df
        return df

    def _get_security_meta(self, symbol: str, exchange: str = "NSE") -> dict:
        """Resolve a symbol to Dhan security metadata (id, tick size, lot units).

        Raises RuntimeError when the symbol cannot be resolved on the exchange.
        """
        symbol_norm = str(symbol or "").upper()
        if not symbol_norm:
            raise RuntimeError("An NSE symbol is required")
        exchange_segment = self._to_exchange_segment(exchange)
        cache_key = (symbol_norm, exchange_segment)

        if cache_key in self.SECURITY_META_CACHE:
            return self.SECURITY_META_CACHE[cache_key]

        if exchange_segment in _SCRIP_MASTER_FNO_FILTERS:
            exch_id, segment = _SCRIP_MASTER_FNO_FILTERS[exchange_segment]
            df = self._load_security_master(
                self.SECURITY_LIST_FNO_URL,
                os.path.splitext(self.SECURITY_LIST_FILE)[0] + "-fno.csv",
                exchange_segment,
            )
        elif exchange_segment in _SCRIP_MASTER_FILTERS:
            exch_id, segment = _SCRIP_MASTER_FILTERS[exchange_segment]
            df = self._load_security_master(
                self.SECURITY_LIST_URL,
                self.SECURITY_LIST_FILE,
                exchange_segment,
            )
        else:
            raise RuntimeError(f"Dhan segment {exchange_segment} is not supported")

        columns = {c.upper(): c for c in df.columns}
        trading_symbol_col = columns.get("SEM_TRADING_SYMBOL")
        security_id_col = columns.get("SEM_SMST_SECURITY_ID") or columns.get("SECURITY_ID") or columns.get("SEM_SECURITY_ID")
        exch_id_col = columns.get("SEM_EXM_EXCH_ID")
        segment_col = columns.get("SEM_SEGMENT")
        tick_col = columns.get("SEM_TICK_SIZE")
        lot_col = columns.get("SEM_LOT_UNITS")
        series_col = columns.get("SEM_SERIES")

        if not trading_symbol_col or not security_id_col or not exch_id_col or not segment_col:
            raise RuntimeError("Dhan security-list CSV format was not recognized")

        mask = df[trading_symbol_col].astype(str).str.upper() == symbol_norm
        mask &= df[exch_id_col].astype(str).str.upper() == exch_id
        mask &= df[segment_col].astype(str).str.upper() == segment

        candidates = df.loc[mask]
        if candidates.empty:
            raise RuntimeError(
                f"No Dhan security found for {symbol_norm} on {exchange_segment}. "
                "Check the symbol spelling and that the segment is enabled on the account."
            )

        if series_col is not None and "EQ" in set(candidates[series_col].astype(str).str.upper()):
            candidates = candidates[candidates[series_col].astype(str).str.upper() == "EQ"]

        row = candidates.iloc[0]
        meta = {
            "security_id": int(row[security_id_col]),
            "tick_size": self._safe_float(row.get(tick_col)),
            "lot_units": self._safe_float(row.get(lot_col)) or 1.0,
            "exchange_segment": exchange_segment,
            "symbol": symbol_norm,
        }
        self.SECURITY_META_CACHE[cache_key] = meta
        self.SECURITY_LIST_CACHE[cache_key] = meta["security_id"]
        return meta

    def _resolve_security_id(self, symbol: str, exchange: str = "NSE") -> int:
        return int(self._get_security_meta(symbol, exchange)["security_id"])

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _round_to_tick(price: float, tick_size: Optional[float]) -> float:
        """Round a limit/trigger price down to the scrip's tick size."""
        if not tick_size or tick_size <= 0:
            return float(price)
        return float(round(float(price) / tick_size) * tick_size)

    # =====================================================================
    # PRICE / EXTRACT HELPERS
    # =====================================================================

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

    # =====================================================================
    # AUTH FLOW
    # =====================================================================

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

    # =====================================================================
    # MARKET DATA
    # =====================================================================

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetches last traded price using the Dhan marketfeed endpoint."""
        self._require_access_token()
        security_id = self._resolve_security_id(symbol, exchange)
        exchange_segment = self._to_exchange_segment(exchange)
        response = self._get_market_feed().ticker_data({exchange_segment: [security_id]})
        data = self._check_response(response, "LTP fetch")
        price = self._extract_price(data)
        if price is None:
            raise RuntimeError(f"Unable to read Dhan LTP for {symbol}")
        return float(price)

    def get_historical(
        self,
        symbol: str,
        interval: str = "1d",
        period: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV from Dhan's Historical / Intraday Data APIs.

        Returns a DataFrame with columns Open/High/Low/Close/Volume and a
        DatetimeIndex (Asia/Kolkata) named 'Date', matching
        ``data_feed.get_historical()`` so the strategy layer is unchanged.

        Requires a valid access token and a Dhan Data-API subscription
        (otherwise Dhan returns DH-902).
        """
        self._require_access_token()
        meta = self._get_security_meta(symbol, "NSE")
        security_id = meta["security_id"]
        exchange_segment = self._to_exchange_segment("NSE")
        instrument_type = "EQUITY"

        today = dt.date.today()
        to_date = to_date or today.isoformat()
        if from_date is None:
            lookback_days = {"15m": 90, "1h": 730, "1d": 1825, "1wk": 3650}.get(interval, 365)
            from_date = (today - dt.timedelta(days=lookback_days)).isoformat()

        hist = self._get_historical_client()

        if interval in ("1d", "1wk"):
            # Dhan treats toDate as non-inclusive; push it one day forward.
            to_daily = (dt.date.fromisoformat(to_date) + dt.timedelta(days=1)).isoformat()
            response = hist.historical_daily_data(
                security_id,
                exchange_segment,
                instrument_type,
                from_date,
                to_daily,
                expiry_code=0,
                oi=False,
            )
            data = self._check_response(response, "historical data fetch")
            df = self._candles_to_dataframe(data)
            if interval == "1wk":
                df = (
                    df.resample("W-FRI")
                    .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
                    .dropna()
                )
            return df

        interval_map = {"15m": 15, "1h": 60}
        minutes = interval_map.get(interval)
        if minutes is None:
            raise ValueError(f"Unsupported interval '{interval}' for Dhan historical data")

        from_dt = dt.datetime.combine(dt.date.fromisoformat(from_date), dt.time(9, 15))
        to_dt = dt.datetime.combine(dt.date.fromisoformat(to_date), dt.time(15, 30))
        response = hist.intraday_minute_data(
            security_id,
            exchange_segment,
            instrument_type,
            from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            to_dt.strftime("%Y-%m-%d %H:%M:%S"),
            interval=minutes,
            oi=False,
        )
        data = self._check_response(response, "intraday data fetch")
        return self._candles_to_dataframe(data)

    @staticmethod
    def _candles_to_dataframe(data: dict) -> pd.DataFrame:
        if not isinstance(data, dict) or "timestamp" not in data:
            raise DhanAPIError(
                f"Unexpected Dhan historical response shape: {str(data)[:200]}",
                context="historical data",
            )
        index = pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert("Asia/Kolkata")
        df = pd.DataFrame(
            {
                "Open": data.get("open", []),
                "High": data.get("high", []),
                "Low": data.get("low", []),
                "Close": data.get("close", []),
                "Volume": data.get("volume", []),
            },
            index=index,
        )
        df.index.name = "Date"
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df.dropna()

    # =====================================================================
    # ACCOUNT / PORTFOLIO
    # =====================================================================

    def get_margins(self) -> Dict:
        """Returns account margin details from Dhan fund limits."""
        raw = self._get_funds_client().get_fund_limits()
        if isinstance(raw, dict):
            payload = self._check_response(raw, "funds fetch")
            if isinstance(payload, dict):
                return payload
            return raw
        return raw

    def get_positions(self) -> List[Position]:
        """Returns list of open positions normalized for the app."""
        raw_positions = self._get_portfolio_client().get_positions()

        if isinstance(raw_positions, dict):
            payload = self._check_response(raw_positions, "positions fetch")
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
            buy_price = (
                item.get("buyAvg")
                or item.get("averagePrice")
                or item.get("avgPrice")
                or item.get("buyPrice")
                or item.get("costPrice")
                or 0.0
            )
            current_price = item.get("ltp") or item.get("lastPrice") or item.get("currentPrice") or 0.0
            pnl = item.get("unrealizedProfit") or item.get("unrealizedPnl") or item.get("pnl") or 0.0

            try:
                buy_price = float(buy_price)
                if current_price not in (None, "", 0):
                    current_price = float(current_price)
                elif symbol:
                    try:
                        current_price = self.get_ltp(symbol)
                    except Exception:
                        current_price = float(buy_price)
                else:
                    current_price = 0.0
                pnl = float(pnl)
            except Exception:
                buy_price = float(buy_price or 0.0)
                current_price = float(current_price or 0.0)
                pnl = float(pnl or 0.0)

            pnl_pct = (pnl / (buy_price * qty) * 100) if buy_price and qty else 0.0
            positions.append(
                Position(
                    symbol=symbol or "?",
                    qty=qty,
                    buy_price=buy_price,
                    current_price=current_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    broker_raw=item,
                )
            )

        return positions

    # =====================================================================
    # ORDER PLACEMENT
    # =====================================================================

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
        """Places an order through the Dhan order API and returns the Dhan orderId.

        Raises DhanAPIError (with Dhan error code) on any non-success response so
        the app never mistakes a rejected order for a filled one.
        """
        self._require_access_token()
        meta = self._get_security_meta(symbol, exchange)
        security_id = meta["security_id"]
        exchange_segment = meta["exchange_segment"]

        transaction_type = transaction_type.upper()
        if transaction_type not in ("BUY", "SELL"):
            raise ValueError(f"Invalid Dhan transaction type '{transaction_type}'")

        order_type = order_type.upper()
        if order_type not in ("LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"):
            raise ValueError(f"Invalid Dhan order type '{order_type}'")

        product_type = product.upper()
        if product_type in ("MIS", "INTRADAY", "INTRA"):
            product_type = "INTRADAY"
        if product_type not in ("CNC", "INTRADAY", "MARGIN", "MTF", "CO", "BO"):
            raise ValueError(f"Invalid Dhan product type '{product}'")

        lot_units = int(meta.get("lot_units") or 1)
        if int(qty) <= 0 or int(qty) % lot_units != 0:
            raise ValueError(f"Quantity {qty} is not a valid multiple of the lot size ({lot_units})")

        price_value = 0.0
        if order_type in ("LIMIT", "STOP_LOSS") and price is not None:
            price_value = self._round_to_tick(float(price), meta.get("tick_size"))
        if order_type in ("LIMIT", "STOP_LOSS") and price_value <= 0:
            raise ValueError("A limit/stop-loss order requires a price")

        trigger_price = 0
        if order_type in ("STOP_LOSS", "STOP_LOSS_MARKET"):
            if price is None:
                raise ValueError("A stop-loss order requires a trigger price")
            trigger_price = self._round_to_tick(float(price), meta.get("tick_size"))

        correlation_id = self._make_correlation_id(f"app-{symbol}-{transaction_type.lower()}")

        response = self._get_order_client().place_order(
            security_id,
            exchange_segment,
            transaction_type,
            int(qty),
            order_type,
            product_type,
            price_value,
            trigger_price=trigger_price,
            tag=correlation_id,
        )

        data = self._check_response(response, "order placement")
        order_id = data.get("orderId") or data.get("order_id")
        if not order_id:
            raise DhanAPIError(f"Dhan order placement returned no orderId: {data}", context="order placement")
        return str(order_id)

    def place_gtt_stop_loss(
        self,
        symbol: str,
        qty: int,
        trigger_price: float,
        exchange: str = "NSE",
    ) -> GTTOrder:
        """Places a server-side GTT stop-loss SELL via the Dhan Forever Order API.

        Unlike a plain SELL LIMIT order, a Forever Order stays on Dhan's server
        and only fires when the trigger price is hit, so the stop works even if
        this app or the internet goes down.
        """
        self._require_access_token()
        meta = self._get_security_meta(symbol, exchange)
        security_id = meta["security_id"]
        exchange_segment = meta["exchange_segment"]

        if int(qty) <= 0:
            raise ValueError("GTT stop-loss quantity must be positive")
        if float(trigger_price) <= 0:
            raise ValueError("GTT stop-loss trigger price must be positive")

        trigger_price = self._round_to_tick(float(trigger_price), meta.get("tick_size"))

        response = self._get_forever_client().place_forever(
            security_id=security_id,
            exchange_segment=exchange_segment,
            transaction_type="SELL",
            product_type="CNC",
            order_type="LIMIT",
            quantity=int(qty),
            price=trigger_price,
            trigger_Price=trigger_price,
            order_flag="SINGLE",
            tag=self._make_correlation_id(f"gtt-{symbol}"),
        )

        data = self._check_response(response, "GTT stop-loss placement")
        gtt_id = data.get("orderId") or data.get("order_id")
        if not gtt_id:
            raise DhanAPIError(f"Dhan GTT placement returned no orderId: {data}", context="GTT stop-loss placement")
        status = str(data.get("orderStatus") or "PENDING").upper()
        return GTTOrder(
            gtt_id=str(gtt_id),
            symbol=str(symbol).upper(),
            trigger_price=float(trigger_price),
            status=status,
            broker_raw=data,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order. Returns True if the cancel was accepted."""
        try:
            response = self._get_order_client().cancel_order(order_id)
            self._check_response(response, "order cancellation")
            return True
        except Exception:
            return False

    def cancel_gtt(self, gtt_id: str) -> bool:
        """Cancels a pending Forever (GTT) order."""
        try:
            response = self._get_forever_client().cancel_forever(gtt_id)
            self._check_response(response, "GTT cancellation")
            return True
        except Exception:
            return False

    def get_gtt_orders(self) -> List[Dict]:
        """Lists all active Forever (GTT) orders on the account."""
        self._require_access_token()
        response = self._get_forever_client().get_forever()
        data = self._check_response(response, "GTT list fetch")
        if isinstance(data, dict):
            return data.get("data") or data.get("foreverOrders") or []
        if isinstance(data, list):
            return data
        return []

    def exit_all_positions(self) -> dict:
        """Exit all open positions and cancel all pending orders for the day.

        Uses Dhan's ``DELETE /positions`` (Exit All Positions) endpoint. This is
        the account-level kill switch for the emergency controls.
        """
        self._require_access_token()
        http = self.dhan_context.get_dhan_http()
        response = http.delete("/positions")
        return self._check_response(response, "exit all positions")

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Fetches order status (raw Dhan payload on success, None on failure)."""
        try:
            response = self._get_order_client().get_order_by_id(order_id)
            data = self._check_response(response, "order status fetch")
            return data if isinstance(data, dict) else None
        except Exception:
            return None
