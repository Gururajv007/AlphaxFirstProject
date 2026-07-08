import unittest

from broker_dhan import DhanBroker


class FakeOrderClient:
    def __init__(self):
        self.calls = []

    def place_order(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"orderId": "D12345"}


class DhanBrokerTests(unittest.TestCase):
    def test_place_order_uses_dhan_payload_mapping(self):
        broker = DhanBroker.__new__(DhanBroker)
        broker.api_key = "demo-key"
        broker.access_token = "demo-token"
        broker.dhan_context = object()
        broker._order_client = FakeOrderClient()
        broker._resolve_security_id = lambda symbol, exchange: 11536

        order_id = broker.place_order(
            symbol="RELIANCE",
            qty=10,
            transaction_type="BUY",
            order_type="MARKET",
            product="MIS",
            exchange="NSE",
        )

        self.assertEqual(order_id, "D12345")
        self.assertEqual(len(broker._order_client.calls), 1)
        args, kwargs = broker._order_client.calls[0]
        self.assertEqual(args[0], 11536)
        self.assertEqual(args[1], "NSE_EQ")
        self.assertEqual(args[2], "BUY")
        self.assertEqual(args[3], 10)
        self.assertEqual(args[4], "MARKET")
        self.assertEqual(args[5], "INTRA")
        self.assertEqual(args[6], 0.0)
        self.assertEqual(kwargs["trigger_price"], 0)


if __name__ == "__main__":
    unittest.main()
