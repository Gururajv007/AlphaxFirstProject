import unittest

from risk_manager import DailyLossTracker


class DailyLossTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = DailyLossTracker(capital=100000.0, max_daily_loss_pct=2.5)

    def test_not_tripped_initially(self):
        self.assertFalse(self.tracker.is_tripped)

    def test_trips_on_daily_loss_limit(self):
        self.tracker.record_trade_pnl(-3000.0)
        self.assertTrue(self.tracker.is_tripped)

    def test_latches_after_recovery_profit(self):
        """Once tripped, a later profit must not re-open entries for the day."""
        self.tracker.record_trade_pnl(-3000.0)
        self.assertTrue(self.tracker.is_tripped)
        self.tracker.record_trade_pnl(5000.0)
        self.assertTrue(self.tracker.is_tripped)

    def test_reset_clears_latch(self):
        self.tracker.record_trade_pnl(-3000.0)
        self.assertTrue(self.tracker.is_tripped)
        self.tracker.reset()
        self.assertFalse(self.tracker.is_tripped)
        self.assertEqual(self.tracker.realized_pnl_today, 0.0)


if __name__ == "__main__":
    unittest.main()
