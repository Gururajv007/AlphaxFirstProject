import base64
import json
import unittest

import broker_dhan
import config as cfg


class ConfigTests(unittest.TestCase):
    def test_is_broker_connected_accepts_runtime_credentials(self):
        base_config = {"api_key": "", "api_secret": "", "access_token": ""}

        self.assertTrue(
            cfg.is_broker_connected(
                base_config,
                {"api_key": "demo-key", "access_token": "demo-token"},
            )
        )
        self.assertFalse(
            cfg.is_broker_connected(
                base_config,
                {"api_key": "demo-key", "access_token": ""},
            )
        )

    def test_trading_mode_normalization(self):
        self.assertEqual(cfg.normalize_trading_mode("Paper Trade"), "paper")
        self.assertEqual(cfg.normalize_trading_mode("Live Trade"), "live")
        self.assertEqual(cfg.normalize_trading_mode("unknown"), "paper")
        self.assertEqual(cfg.get_trading_mode_label("live"), "Live Trade")

    def test_dhan_client_id_is_extracted_from_token_payload(self):
        payload = {"dhanClientId": "1109176427"}
        token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        self.assertEqual(broker_dhan.extract_dhan_client_id(token), "1109176427")
        self.assertEqual(broker_dhan.extract_dhan_client_id("1109176427"), "1109176427")
        self.assertEqual(broker_dhan.extract_dhan_client_id(""), "")

    def test_dhan_access_token_detection(self):
        self.assertTrue(broker_dhan.looks_like_dhan_access_token("eyJhbGciOiJIUzI1NiJ9.abc"))
        self.assertTrue(broker_dhan.looks_like_dhan_access_token("https://example.com/callback?access_token=abc123"))
        self.assertFalse(broker_dhan.looks_like_dhan_access_token("1109176427"))


if __name__ == "__main__":
    unittest.main()
