"""
broker_factory.py
-----------------
Dhan-only broker factory for the NSE AI Trading Platform.

This module is streamlined to support only Dhan broker,
providing the most comprehensive API for NSE trading with:
- Real-time market data
- Order management
- Position tracking
- GTT (Good Till Triggered) orders
- Margin management
"""

from typing import Optional
from broker_interface import BrokerInterface


def create_broker(
    broker_type: str = "dhan",
    api_key: str = "",
    api_secret: str = "",
    access_token: str = "",
) -> BrokerInterface:
    """
    Factory function to create broker instance.

    Currently only supports Dhan broker.

    Args:
        broker_type: Broker identifier (currently only 'dhan' supported)
        api_key: Dhan Client ID
        api_secret: Dhan API Secret
        access_token: Dhan Access Token

    Returns:
        DhanBroker instance

    Raises:
        ValueError: If broker_type is not 'dhan'
    """

    # Force Dhan only - ignore other broker types
    broker_type = "dhan"

    if broker_type == "dhan":
        from broker_dhan import DhanBroker
        return DhanBroker(
            api_key=api_key,
            access_token=access_token,
            api_secret=api_secret
        )
    else:
        raise ValueError(
            f"Only Dhan broker is supported. "
            f"Please configure your Dhan credentials in Settings."
        )


# Dhan-only broker configuration
SUPPORTED_BROKERS = [
    ("dhan", "💰 Dhan (India's Fastest Growing Broker)"),
]


def get_broker_display_names() -> dict:
    """Returns a mapping of broker_type to display name."""
    return {code: name for code, name in SUPPORTED_BROKERS}


def get_broker_list() -> list:
    """Returns list of supported broker types."""
    return [code for code, _ in SUPPORTED_BROKERS]


def is_dhan_configured(api_key: str, access_token: str) -> bool:
    """Check if Dhan credentials are properly configured."""
    return bool(api_key and access_token)
