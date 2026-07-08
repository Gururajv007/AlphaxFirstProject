"""
broker_factory.py
-----------------
Factory for creating broker instances based on configuration.
Manages broker selection and instantiation.
"""

from typing import Optional
from broker_interface import BrokerInterface


def create_broker(
    broker_type: str,
    api_key: str = "",
    api_secret: str = "",
    access_token: str = "",
) -> BrokerInterface:
    """
    Factory function to create the appropriate broker instance.
    
    Args:
        broker_type: Broker identifier ('zerodha_kite', 'upstox', 'angel_one', 'fyers', 'dhan')
        api_key: Broker API key
        api_secret: Broker API secret
        access_token: Broker access token (for authenticated calls)
    
    Returns:
        An instance of the requested broker implementation
    
    Raises:
        ValueError: If broker_type is not recognized
    """
    
    broker_type = broker_type.lower().strip()
    
    if broker_type == "zerodha_kite" or broker_type == "zerodha":
        from broker_kite import KiteBroker
        return KiteBroker(api_key=api_key, api_secret=api_secret, access_token=access_token)
    
    elif broker_type == "upstox":
        from broker_upstox import UpstoxBroker
        return UpstoxBroker(api_key=api_key, access_token=access_token)
    
    elif broker_type == "angel_one" or broker_type == "angel":
        from broker_angel import AngelOneBroker
        return AngelOneBroker(api_key=api_key, client_code=api_secret, access_token=access_token)
    
    elif broker_type == "fyers":
        from broker_fyers import FyersBroker
        return FyersBroker(api_key=api_key, access_token=access_token)
    
    elif broker_type == "dhan":
        from broker_dhan import DhanBroker
        return DhanBroker(api_key=api_key, access_token=access_token, api_secret=api_secret)
    
    else:
        raise ValueError(
            f"Unknown broker type: {broker_type}. "
            f"Supported: zerodha_kite, upstox, angel_one, fyers, dhan"
        )


# List of supported brokers for UI dropdown
SUPPORTED_BROKERS = [
    ("zerodha_kite", "🦓 Zerodha (Kite Connect)"),
    ("upstox", "📈 Upstox"),
    ("angel_one", "😇 Angel One"),
    ("fyers", "⚡ Fyers"),
    ("dhan", "💰 Dhan"),
]


def get_broker_display_names() -> dict:
    """Returns a mapping of broker_type to display name."""
    return {code: name for code, name in SUPPORTED_BROKERS}


def get_broker_list() -> list:
    """Returns list of supported broker types."""
    return [code for code, _ in SUPPORTED_BROKERS]
