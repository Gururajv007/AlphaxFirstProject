import base64
import datetime as dt
import json
import unittest

from broker_dhan import DhanAPIError, DhanBroker


def _make_jwt(exp_epoch: float) -> str:
    """Build a fake Dhan-style JWT access token with the given exp claim."""
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"exp": exp_epoch}).encode())
    return f"{header}.{payload}.fakesig"


class FakeOrderClient:
    def __init__(self):
        self.calls = []

    def place_order(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"status": "success", "data": {"orderId": "D12345"}}


class FakeForeverClient:
    def __init__(self):
        self.calls = []

    def place_forever(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "success", "data": {"orderId": "GTT678", "orderStatus": "PENDING"}}

    def cancel_forever(self, gtt_id):
        return {"status": "success", "data": {"orderId": gtt_id}}


class FakePortfolioClient:
    def __init__(self, payload):
        self.payload = payload

    def get_positions(self):
        return self.payload


def _make_broker(meta=None, token="demo-token"):
    """Construct a DhanBroker instance without hitting the network."""
    broker = DhanBroker.__new__(DhanBroker)
    broker.api_key = "demo-client"
    broker.api_secret = "demo-secret"
    broker.access_token = token
    broker.dhan_context = object()
    broker._security_list_stale = False
    broker._order_client = None
    broker._portfolio_client = None
    broker._funds_client = None
    broker._market_feed = None
    broker._historical_client = None
    broker._forever_client = None
    if meta is None:
        meta = {
            "security_id": 11536,
            "tick_size": 1.0,
            "lot_units": 1,
            "exchange_segment": "NSE_EQ",
            "symbol": "TCS",
        }
    broker._get_security_meta = lambda symbol, exchange="NSE": meta
    return broker


class DhanBrokerTests(unittest.TestCase):
    def test_place_order_maps_legacy_product_to_intraday(self):
        """Legacy 'MIS'/'INTRA' product must map to Dhan's 'INTRADAY' value."""
        broker = _make_broker()
        broker._order_client = FakeOrderClient()

        order_id = broker.place_order(
            symbol="TCS",
            qty=10,
            transaction_type="BUY",
            order_type="MARKET",
            product="MIS",
            exchange="NSE",
        )

        self.assertEqual(order_id, "D12345")
        args, kwargs = broker._order_client.calls[0]
        self.assertEqual(args[0], 11536)
        self.assertEqual(args[1], "NSE_EQ")
        self.assertEqual(args[2], "BUY")
        self.assertEqual(args[3], 10)
        self.assertEqual(args[4], "MARKET")
        self.assertEqual(args[5], "INTRADAY")
        self.assertEqual(args[6], 0.0)
        self.assertEqual(kwargs["trigger_price"], 0)
        self.assertTrue(str(kwargs["tag"]).startswith("app-TCS-buy"))

    def test_place_order_rounds_limit_price_to_tick(self):
        broker = _make_broker(meta={"security_id": 2885, "tick_size": 0.05, "lot_units": 1, "exchange_segment": "NSE_EQ"})
        broker._order_client = FakeOrderClient()

        broker.place_order(
            symbol="TCS", qty=1, transaction_type="BUY",
            order_type="LIMIT", product="CNC", price=100.04,
        )
        args, _ = broker._order_client.calls[0]
        self.assertEqual(args[5], "CNC")
        self.assertAlmostEqual(args[6], 100.05)

    def test_place_order_rejects_quantity_not_multiple_of_lot(self):
        broker = _make_broker(meta={"security_id": 1, "tick_size": 1.0, "lot_units": 2, "exchange_segment": "NSE_EQ"})
        with self.assertRaises(ValueError):
            broker.place_order(symbol="TCS", qty=5, transaction_type="BUY", order_type="MARKET")

    def test_place_gtt_stop_loss_returns_gtt_order(self):
        broker = _make_broker()
        broker._forever_client = FakeForeverClient()

        gtt = broker.place_gtt_stop_loss(symbol="TCS", qty=10, trigger_price=2500.0)

        self.assertEqual(gtt.gtt_id, "GTT678")
        self.assertEqual(gtt.symbol, "TCS")
        self.assertEqual(gtt.trigger_price, 2500.0)
        self.assertEqual(gtt.status, "PENDING")
        kwargs = broker._forever_client.calls[0]
        self.assertEqual(kwargs["transaction_type"], "SELL")
        self.assertEqual(kwargs["order_type"], "LIMIT")
        self.assertEqual(kwargs["trigger_Price"], 2500.0)

    def test_check_response_raises_typed_error_on_failure(self):
        with self.assertRaises(DhanAPIError) as ctx:
            DhanBroker._check_response(
                {
                    "status": "failure",
                    "remarks": {
                        "error_code": "DH-901",
                        "error_type": "Invalid_Authentication",
                        "error_message": "Client ID or user generated access token is invalid or expired.",
                    },
                },
                "order placement",
            )
        self.assertEqual(ctx.exception.code, "DH-901")
        self.assertEqual(ctx.exception.error_type, "Invalid_Authentication")
        self.assertFalse(ctx.exception.retryable)

    def test_check_response_marks_rate_limit_retryable(self):
        with self.assertRaises(DhanAPIError) as ctx:
            DhanBroker._check_response(
                {"status": "failure", "remarks": {"error_code": "DH-904", "error_message": "Too many requests"}},
                "order placement",
            )
        self.assertEqual(ctx.exception.code, "DH-904")
        self.assertTrue(ctx.exception.retryable)

    def test_expired_token_raises_typed_error(self):
        expired = _make_jwt((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).timestamp())
        broker = _make_broker(token=expired)
        with self.assertRaises(DhanAPIError) as ctx:
            broker._require_access_token()
        self.assertEqual(ctx.exception.code, "EXPIRED")

    def test_valid_token_passes_auth_check(self):
        valid = _make_jwt((dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=23)).timestamp())
        broker = _make_broker(token=valid)
        broker._require_access_token()  # should not raise

    def test_get_positions_normalizes_dhan_payload(self):
        broker = _make_broker()
        broker._portfolio_client = FakePortfolioClient(
            {
                "status": "success",
                "data": [
                    {
                        "tradingSymbol": "RELIANCE",
                        "netQty": 10,
                        "buyAvg": 2500.0,
                        "ltp": 2510.0,
                        "unrealizedProfit": 100.0,
                    }
                ],
            }
        )
        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "RELIANCE")
        self.assertEqual(positions[0].qty, 10)
        self.assertEqual(positions[0].buy_price, 2500.0)
        self.assertEqual(positions[0].pnl, 100.0)


class SecurityResolutionTests(unittest.TestCase):
    def test_resolves_reliance_against_real_security_list(self):
        """Uses the on-disk dhan_security_list.csv (regenerated master)."""
        broker = DhanBroker.__new__(DhanBroker)
        broker._security_list_stale = False
        broker.dhan_context = object()
        meta = broker._get_security_meta("RELIANCE", "NSE")
        self.assertEqual(meta["security_id"], 2885)
        self.assertEqual(meta["exchange_segment"], "NSE_EQ")
        self.assertEqual(meta["lot_units"], 1)
        self.assertFalse(broker.is_security_list_stale())

    def test_unknown_symbol_raises(self):
        broker = DhanBroker.__new__(DhanBroker)
        broker._security_list_stale = False
        broker.dhan_context = object()
        with self.assertRaises(RuntimeError):
            broker._get_security_meta("NOT_A_REAL_SYMBOL_XYZ", "NSE")


if __name__ == "__main__":
    unittest.main()
