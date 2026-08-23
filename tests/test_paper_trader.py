import unittest

from paper_trader import PaperPortfolio


class PaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.pf = PaperPortfolio(starting_cash=100000.0)

    def test_buy_debits_cash_and_opens_position(self):
        ok = self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        self.assertTrue(ok)
        self.assertAlmostEqual(self.pf.cash, 100000.0 - 25000.0)
        self.assertIn("RELIANCE", self.pf.positions)
        self.assertEqual(self.pf.positions["RELIANCE"].qty, 10)

    def test_buy_rejects_duplicate_symbol(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        ok = self.pf.buy("RELIANCE", 5, 2500.0, 2400.0, 2800.0, "2026-08-24")
        self.assertFalse(ok)

    def test_buy_rejects_insufficient_cash(self):
        ok = self.pf.buy("RELIANCE", 1000, 2500.0, 2400.0, 2800.0, "2026-08-24")
        self.assertFalse(ok)

    def test_sell_returns_realized_pnl(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        pnl = self.pf.sell("RELIANCE", 2600.0, "2026-08-25", reason="manual")
        self.assertEqual(pnl, 1000.0)
        self.assertNotIn("RELIANCE", self.pf.positions)

    def test_check_stops_and_targets_auto_exits(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        self.pf.buy("TCS", 10, 3500.0, 3400.0, 4000.0, "2026-08-24")
        closed = self.pf.check_stops_and_targets({"RELIANCE": 2390.0, "TCS": 4010.0}, "2026-08-25")
        self.assertEqual(sorted(closed), ["RELIANCE", "TCS"])
        self.assertNotIn("RELIANCE", self.pf.positions)
        self.assertNotIn("TCS", self.pf.positions)

    def test_total_equity_marks_to_market(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        equity = self.pf.total_equity({"RELIANCE": 2550.0})
        self.assertAlmostEqual(equity, 100000.0 + 500.0)

    def test_realized_pnl_today_filters_by_date(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        self.pf.sell("RELIANCE", 2600.0, "2026-08-25", reason="manual")
        self.assertEqual(self.pf.realized_pnl_today("2026-08-25"), 1000.0)
        self.assertEqual(self.pf.realized_pnl_today("2026-08-26"), 0.0)

    def test_positions_df_shape(self):
        self.pf.buy("RELIANCE", 10, 2500.0, 2400.0, 2800.0, "2026-08-24")
        df = self.pf.positions_df({"RELIANCE": 2550.0})
        self.assertEqual(len(df), 1)
        self.assertIn("Unrealized P&L", df.columns)
        self.assertAlmostEqual(float(df.iloc[0]["Unrealized P&L"]), 500.0)


if __name__ == "__main__":
    unittest.main()
