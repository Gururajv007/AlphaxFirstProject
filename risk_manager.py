"""
risk_manager.py
----------------
Keeps risk math in ONE place so the same rules apply identically in
backtesting, paper trading, and live trading - that consistency is the
whole point of testing a strategy before going live.

Includes:
- Position Sizing: risk a fixed % of capital per trade
- DailyLossTracker: kill-switch when daily loss limit is hit
- DrawdownTracker: auto-pause when drawdown exceeds limit (BUG FIXED)
- ConsecutiveLossTracker: pause after N losses in a row (LOGIC FIXED)
- OpenPositionTracker: prevent duplicate orders on same symbol (NEW)
- Market Hours Validator: NSE 9:15 AM - 3:30 PM only + holiday calendar (NEW)
- TradeAuditLog: full audit trail of every action
"""

from dataclasses import dataclass, field
import datetime as dt
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# NSE HOLIDAY CALENDAR
# Update this list every year from the official NSE website:
# https://www.nseindia.com/resources/exchange-communication-holidays
# ---------------------------------------------------------------------------
NSE_HOLIDAYS: Set[dt.date] = {
    # --- 2025 ---
    dt.date(2025, 2, 26),   # Mahashivratri
    dt.date(2025, 3, 14),   # Holi
    dt.date(2025, 3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    dt.date(2025, 4, 10),   # Shri Ram Navami
    dt.date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    dt.date(2025, 4, 18),   # Good Friday
    dt.date(2025, 5, 1),    # Maharashtra Day
    dt.date(2025, 8, 15),   # Independence Day
    dt.date(2025, 8, 27),   # Ganesh Chaturthi
    dt.date(2025, 10, 2),   # Mahatma Gandhi Jayanti
    dt.date(2025, 10, 20),  # Diwali Laxmi Pujan (Muhurat trading day - special)
    dt.date(2025, 10, 21),  # Diwali Balipratipada
    dt.date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev Ji
    dt.date(2025, 12, 25),  # Christmas

    # --- 2026 ---
    dt.date(2026, 1, 26),   # Republic Day
    dt.date(2026, 3, 19),   # Holi (verify exact date on NSE website)
    dt.date(2026, 4, 3),    # Good Friday (verify exact date)
    dt.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    dt.date(2026, 5, 1),    # Maharashtra Day
    dt.date(2026, 8, 15),   # Independence Day
    dt.date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    dt.date(2026, 11, 9),   # Diwali (verify exact date on NSE website)
    dt.date(2026, 12, 25),  # Christmas
    # IMPORTANT: Always verify and update this list from NSE website before
    # each new trading year. NSE announces holidays in advance.
}


# ---------------------------------------------------------------------------
# 1. POSITION SIZING
# ---------------------------------------------------------------------------

def calculate_position_size(
    capital: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss_price: float,
    transaction_cost_pct: float = 0.05,
) -> int:
    """
    Returns number of shares to buy such that, if the stop-loss is hit,
    the total loss (including brokerage + STT + slippage) equals
    risk_per_trade_pct% of capital - not a rupee more.

    transaction_cost_pct: estimated round-trip cost as % of trade value.
                          Default 0.05% covers typical Zerodha/Upstox costs.
                          Increase to 0.1% if you want to be conservative.
    """
    risk_amount = capital * (risk_per_trade_pct / 100.0)

    # Deduct estimated transaction costs from the risk budget
    # so we don't accidentally risk more than intended after costs
    transaction_cost = entry_price * (transaction_cost_pct / 100.0)
    per_share_risk = (entry_price - stop_loss_price) + transaction_cost

    if per_share_risk <= 0:
        return 0

    qty = int(risk_amount // per_share_risk)
    return max(qty, 0)


# ---------------------------------------------------------------------------
# 2. DAILY LOSS TRACKER (Kill-Switch)
# ---------------------------------------------------------------------------

@dataclass
class DailyLossTracker:
    """
    Kill-switch: once today's realized loss crosses the configured
    percentage of capital, is_tripped becomes True and the app should
    refuse to place any further new entries for the rest of the day.

    Reset this once per trading day (e.g. at market open each morning).
    """
    capital: float
    max_daily_loss_pct: float
    realized_pnl_today: float = 0.0

    @property
    def max_loss_amount(self) -> float:
        return self.capital * (self.max_daily_loss_pct / 100.0)

    @property
    def is_tripped(self) -> bool:
        return self.realized_pnl_today <= -abs(self.max_loss_amount)

    def record_trade_pnl(self, pnl: float) -> None:
        """Call this every time a trade closes with its profit or loss."""
        self.realized_pnl_today += pnl

    def reset(self) -> None:
        """Call this at the start of each new trading day."""
        self.realized_pnl_today = 0.0

    def status_text(self) -> str:
        if self.is_tripped:
            return (
                f"🔴 KILL-SWITCH ACTIVE — daily loss limit of "
                f"₹{self.max_loss_amount:,.0f} reached. No new entries today."
            )
        remaining = self.max_loss_amount + self.realized_pnl_today
        return f"🟢 OK — ₹{remaining:,.0f} of daily loss budget remaining."


# ---------------------------------------------------------------------------
# 3. DRAWDOWN TRACKER  (BUG FIXED: get_status now accepts current_equity)
# ---------------------------------------------------------------------------

@dataclass
class DrawdownTracker:
    """
    Monitors equity drawdown from the highest point seen so far.
    Auto-pauses trading when drawdown exceeds max_drawdown_pct.

    HOW TO USE:
      - Call update(current_equity) after every trade closes.
      - Check is_paused before placing any new order.
      - Call reset_peak() at the start of each new trading day if desired.
    """
    capital: float
    max_drawdown_pct: float = 10.0        # pause trading at this drawdown %
    warning_threshold_pct: float = 5.0   # show a warning at this drawdown %
    auto_pause_enabled: bool = True
    peak_equity: Optional[float] = None
    is_paused: bool = False
    pause_reason: str = ""

    def __post_init__(self):
        if self.peak_equity is None:
            self.peak_equity = self.capital

    def update(self, current_equity: float) -> str:
        """
        Call this after every trade with the updated total account value.
        Returns a human-readable status string.
        """
        # New all-time high — reset pause
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self.is_paused = False
            self.pause_reason = ""
            return f"✅ New peak equity: ₹{self.peak_equity:,.0f}"

        drawdown = self.peak_equity - current_equity
        drawdown_pct = (drawdown / self.peak_equity) * 100.0

        if drawdown_pct >= self.max_drawdown_pct:
            if self.auto_pause_enabled and not self.is_paused:
                self.is_paused = True
                self.pause_reason = (
                    f"Max drawdown ({drawdown_pct:.2f}%) reached. "
                    f"Limit is {self.max_drawdown_pct}%."
                )
            return (
                f"🔴 MAX DRAWDOWN HIT: {drawdown_pct:.2f}% "
                f"(limit: {self.max_drawdown_pct}%) — Trading PAUSED"
            )

        if drawdown_pct >= self.warning_threshold_pct:
            return (
                f"⚠️  Drawdown Warning: {drawdown_pct:.2f}% "
                f"(limit: {self.max_drawdown_pct}%)"
            )

        return f"🟢 Drawdown: {drawdown_pct:.2f}% — within limits."

    def reset_peak(self) -> None:
        """Reset peak equity — call at the start of each new trading day."""
        self.peak_equity = self.capital
        self.is_paused = False
        self.pause_reason = ""

    def get_status(self, current_equity: float) -> Dict:
        """
        FIXED: now correctly takes current_equity as parameter
        instead of using stale self.capital.
        """
        if self.peak_equity == 0:
            return {"drawdown_pct": 0.0, "is_paused": False, "pause_reason": ""}

        drawdown_pct = (
            (self.peak_equity - current_equity) / self.peak_equity
        ) * 100.0

        return {
            "drawdown_pct": round(drawdown_pct, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_equity": round(current_equity, 2),
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
        }


# ---------------------------------------------------------------------------
# 4. CONSECUTIVE LOSS TRACKER  (LOGIC FIXED: "next_day" now means next market open)
# ---------------------------------------------------------------------------

@dataclass
class ConsecutiveLossTracker:
    """
    Counts consecutive losing trades. Pauses trading after hitting the limit.

    pause_duration options:
      "1_hour"   — resume 1 hour after the last loss
      "next_day" — resume at 9:15 AM the NEXT trading day  (FIXED)
      "manual"   — requires you to manually reset via reset()
    """
    max_consecutive_losses: int = 3
    pause_duration: str = "1_hour"   # "1_hour", "next_day", "manual"
    consecutive_loss_count: int = 0
    is_paused: bool = False
    pause_until: Optional[dt.datetime] = None

    def _next_market_open(self) -> dt.datetime:
        """
        Returns the datetime of the next NSE market open (9:15 AM),
        skipping weekends and NSE holidays.
        """
        candidate = dt.datetime.now() + dt.timedelta(days=1)
        candidate = candidate.replace(hour=9, minute=15, second=0, microsecond=0)

        # Keep advancing until we land on a valid trading day
        while candidate.weekday() >= 5 or candidate.date() in NSE_HOLIDAYS:
            candidate += dt.timedelta(days=1)
            candidate = candidate.replace(hour=9, minute=15, second=0, microsecond=0)

        return candidate

    def record_trade_result(self, pnl: float) -> str:
        """
        Call this every time a trade closes.
        pnl > 0 = profit, pnl < 0 = loss.
        Returns a status message.
        """
        if pnl >= 0:
            # Winning trade — reset the streak
            self.consecutive_loss_count = 0
            self.is_paused = False
            self.pause_until = None
            return "✅ Win — consecutive loss counter reset."
        else:
            # Losing trade — increment streak
            self.consecutive_loss_count += 1
            msg = (
                f"❌ Loss — {self.consecutive_loss_count} of "
                f"{self.max_consecutive_losses} consecutive losses."
            )

            if self.consecutive_loss_count >= self.max_consecutive_losses:
                self.is_paused = True

                if self.pause_duration == "1_hour":
                    self.pause_until = dt.datetime.now() + dt.timedelta(hours=1)
                    msg += f" — PAUSED until {self.pause_until.strftime('%I:%M %p')}"

                elif self.pause_duration == "next_day":
                    # FIXED: pause until 9:15 AM the next actual trading day
                    self.pause_until = self._next_market_open()
                    msg += (
                        f" — PAUSED until next market open: "
                        f"{self.pause_until.strftime('%d %b %Y, %I:%M %p')}"
                    )

                else:  # "manual"
                    self.pause_until = None
                    msg += " — PAUSED (manual restart needed — call reset() to resume)"

            return msg

    def check_pause_expiry(self) -> bool:
        """
        Call this before placing any order to check if pause has expired.
        Returns True if we just unpaused, False otherwise.
        """
        if not self.is_paused or self.pause_until is None:
            return False

        if dt.datetime.now() >= self.pause_until:
            self.is_paused = False
            self.pause_until = None
            self.consecutive_loss_count = 0
            return True

        return False

    def reset(self) -> None:
        """Manually reset all state — use after 'manual' pause mode."""
        self.consecutive_loss_count = 0
        self.is_paused = False
        self.pause_until = None

    def get_status(self) -> Dict:
        """Return current status as a dictionary."""
        self.check_pause_expiry()
        return {
            "consecutive_losses": self.consecutive_loss_count,
            "max_allowed": self.max_consecutive_losses,
            "is_paused": self.is_paused,
            "pause_until": (
                self.pause_until.strftime("%d %b %Y, %I:%M %p")
                if self.pause_until else None
            ),
        }


# ---------------------------------------------------------------------------
# 5. OPEN POSITION TRACKER  (NEW — prevents duplicate orders on same symbol)
# ---------------------------------------------------------------------------

@dataclass
class OpenPositionTracker:
    """
    Tracks which symbols currently have an open position.
    Prevents the bot from placing a second order on a symbol
    it already holds — a common and costly bug in automated systems.

    HOW TO USE:
      - Before placing a BUY: check can_enter(symbol)
      - After order confirmed: call mark_entered(symbol)
      - After position closed: call mark_exited(symbol)
    """
    open_symbols: Set[str] = field(default_factory=set)

    def can_enter(self, symbol: str) -> bool:
        """Returns True if we do NOT already hold this symbol."""
        return symbol not in self.open_symbols

    def mark_entered(self, symbol: str) -> None:
        """Call this after a BUY order is confirmed by the broker."""
        self.open_symbols.add(symbol.upper())

    def mark_exited(self, symbol: str) -> None:
        """Call this after a position is fully closed/exited."""
        self.open_symbols.discard(symbol.upper())

    def get_open_positions(self) -> List[str]:
        """Returns a sorted list of all currently open symbols."""
        return sorted(self.open_symbols)

    def reset(self) -> None:
        """Clear all tracked positions — use at the start of each day."""
        self.open_symbols.clear()

    def status_text(self) -> str:
        if not self.open_symbols:
            return "No open positions."
        return f"Open positions ({len(self.open_symbols)}): {', '.join(sorted(self.open_symbols))}"


# ---------------------------------------------------------------------------
# 6. MARKET HOURS VALIDATOR  (FIXED: now checks NSE holiday calendar too)
# ---------------------------------------------------------------------------

def is_market_open() -> bool:
    """
    Returns True only if NSE market is currently open.
    Checks: weekdays, NSE holidays, and trading window (9:15 AM - 3:30 PM IST).

    Note: This uses your local system clock. Make sure your machine's
    timezone is set to IST (Asia/Kolkata) or adjust accordingly.
    """
    now = dt.datetime.now()

    # Weekend check
    if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False

    # NSE holiday check
    if now.date() in NSE_HOLIDAYS:
        return False

    # Trading window check
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_open <= now <= market_close


def get_market_hours_status() -> str:
    """Returns a human-readable string of the current market status."""
    now = dt.datetime.now()

    if now.weekday() >= 5:
        return "🔴 Market Closed (Weekend)"

    if now.date() in NSE_HOLIDAYS:
        return "🔴 Market Closed (NSE Holiday)"

    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < market_open:
        mins = int((market_open - now).total_seconds() / 60)
        return f"🔴 Market Closed — Opens in {mins} minutes (9:15 AM)"
    elif now > market_close:
        return "🔴 Market Closed — Closed at 3:30 PM. Opens tomorrow at 9:15 AM."
    else:
        mins_left = int((market_close - now).total_seconds() / 60)
        return f"🟢 Market Open — Closes in {mins_left} minutes (3:30 PM)"


# ---------------------------------------------------------------------------
# 7. TRADE AUDIT LOG
# ---------------------------------------------------------------------------

@dataclass
class TradeAuditLog:
    """
    Records every trading action for compliance, debugging, and review.
    Every single order attempt — successful or failed — should be logged here.
    """
    trades: List[Dict] = field(default_factory=list)

    def log_action(
        self,
        action: str,
        symbol: str = "",
        qty: int = 0,
        price: float = 0.0,
        reason: str = "",
        confidence: float = 0.0,
        broker_response: str = "",
    ) -> None:
        """
        Log any trading action.

        action examples: "BUY", "SELL", "SCAN", "PAUSE", "RESUME",
                         "REJECTED_DUPLICATE", "REJECTED_MARKET_CLOSED",
                         "REJECTED_KILL_SWITCH", "REJECTED_DRAWDOWN",
                         "REJECTED_CONSECUTIVE_LOSS"
        """
        entry = {
            "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "symbol": symbol.upper() if symbol else "",
            "qty": qty,
            "price": round(price, 2) if price else 0.0,
            "reason": reason,
            "confidence": round(confidence, 2) if confidence else 0.0,
            "broker_response": broker_response,
        }
        self.trades.append(entry)

    def get_recent_logs(self, limit: int = 20) -> List[Dict]:
        """Get the last N log entries."""
        return self.trades[-limit:]

    def filter_by_symbol(self, symbol: str) -> List[Dict]:
        """Get all log entries for a specific symbol."""
        return [t for t in self.trades if t["symbol"] == symbol.upper()]

    def filter_by_action(self, action: str) -> List[Dict]:
        """Get all log entries for a specific action type (e.g. 'BUY')."""
        return [t for t in self.trades if t["action"] == action.upper()]

    def clear(self) -> None:
        """Clear all logs — use with caution."""
        self.trades.clear()


# ---------------------------------------------------------------------------
# 8. MASTER GATE — single function to check ALL guards before placing an order
# ---------------------------------------------------------------------------

def can_place_order(
    symbol: str,
    daily_loss_tracker: DailyLossTracker,
    drawdown_tracker: DrawdownTracker,
    consecutive_loss_tracker: ConsecutiveLossTracker,
    open_position_tracker: OpenPositionTracker,
    current_equity: float,
    audit_log: Optional[TradeAuditLog] = None,
) -> tuple[bool, str]:
    """
    Master safety gate. Call this BEFORE placing any new BUY order.
    Returns (True, "OK") if all checks pass, or (False, reason) if blocked.

    Example usage in your broker file:
        allowed, reason = can_place_order(symbol, daily_loss, drawdown, ...)
        if not allowed:
            print(f"Order blocked: {reason}")
            return
        # place order here
    """

    # Check 1: Market must be open
    if not is_market_open():
        reason = f"Market is closed. Status: {get_market_hours_status()}"
        if audit_log:
            audit_log.log_action("REJECTED_MARKET_CLOSED", symbol=symbol, reason=reason)
        return False, reason

    # Check 2: Daily loss kill-switch
    if daily_loss_tracker.is_tripped:
        reason = daily_loss_tracker.status_text()
        if audit_log:
            audit_log.log_action("REJECTED_KILL_SWITCH", symbol=symbol, reason=reason)
        return False, reason

    # Check 3: Drawdown auto-pause
    dd_status = drawdown_tracker.get_status(current_equity)
    if dd_status["is_paused"]:
        reason = f"Drawdown limit hit: {dd_status['pause_reason']}"
        if audit_log:
            audit_log.log_action("REJECTED_DRAWDOWN", symbol=symbol, reason=reason)
        return False, reason

    # Check 4: Consecutive loss pause
    cl_status = consecutive_loss_tracker.get_status()
    if cl_status["is_paused"]:
        reason = (
            f"Consecutive loss limit hit. Paused until: "
            f"{cl_status['pause_until'] or 'manual reset required'}"
        )
        if audit_log:
            audit_log.log_action("REJECTED_CONSECUTIVE_LOSS", symbol=symbol, reason=reason)
        return False, reason

    # Check 5: Duplicate position check
    if not open_position_tracker.can_enter(symbol):
        reason = f"Already holding an open position in {symbol}. Skipping."
        if audit_log:
            audit_log.log_action("REJECTED_DUPLICATE", symbol=symbol, reason=reason)
        return False, reason

    return True, "OK"