"""
config.py
---------
Handles loading/saving local app configuration (broker API credentials,
risk settings) to a local JSON file. This file never leaves your machine
and is NOT committed to source control (see .gitignore).

This is intentionally simple (plain JSON on disk) so a beginner can open
config.json and see exactly what is stored. For real production use you
may want to encrypt this file or use OS keychain storage instead.
"""

import json
import os
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG = {
    "broker": "zerodha_kite",
    "api_key": "",
    "api_secret": "",
    "access_token": "",
    "capital": 100000.0,
    "risk_per_trade_pct": 1.0,      # % of capital risked per trade
    "max_daily_loss_pct": 3.0,      # kill-switch trigger
    "min_risk_reward": 1.5,
    "paper_trading_only": True,     # safety default: app starts in paper mode
    "trading_mode": "paper",       # paper or live
}


def load_config() -> dict:
    """Load config from disk, falling back to defaults for any missing keys."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}

    # merge with defaults so new fields don't break old config files
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config: dict) -> None:
    """Persist config to disk."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def normalize_trading_mode(mode: Optional[str], default: str = "paper") -> str:
    """Normalize a runtime or persisted trading mode string to paper/live."""
    value = (mode or default).strip().lower().replace("-", " ").replace("_", " ")
    value = " ".join(value.split())
    if value in {"paper", "paper trade", "papertrade"}:
        return "paper"
    if value in {"live", "live trade", "livetrade"}:
        return "live"
    return default


def get_trading_mode_label(mode: Optional[str]) -> str:
    """Return a display label for the trading mode."""
    return "Paper Trade" if normalize_trading_mode(mode) == "paper" else "Live Trade"


def is_broker_connected(config: dict, runtime_config: Optional[dict] = None) -> bool:
    """Return True once the app has a usable broker API key and access token."""
    effective_config = dict(config or {})
    if runtime_config:
        effective_config.update({k: v for k, v in runtime_config.items() if v is not None})

    api_key = effective_config.get("api_key", "")
    access_token = effective_config.get("access_token", "")
    return bool(api_key) and bool(access_token)
