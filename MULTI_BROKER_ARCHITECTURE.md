# Multi-Broker Architecture

## Goal
Support multiple brokers (Zerodha, Upstox, Angel One, Fyers, Dhan) through a unified interface, enabling users to switch brokers without changing core app logic.

## Supported Brokers

| Broker | Code | Status | Location |
|--------|------|--------|----------|
| 🦓 Zerodha (Kite Connect) | `zerodha_kite` | ✅ Implemented | [broker_kite.py](broker_kite.py) |
| 📈 Upstox | `upstox` | 🔄 Stub | [broker_upstox.py](broker_upstox.py) |
| 😇 Angel One | `angel_one` | 🔄 Stub | [broker_angel.py](broker_angel.py) |
| ⚡ Fyers | `fyers` | 🔄 Stub | [broker_fyers.py](broker_fyers.py) |
| 💰 Dhan | `dhan` | 🔄 Stub | [broker_dhan.py](broker_dhan.py) |

## Architecture

### 1. **BrokerInterface** (`broker_interface.py`)
Abstract base class defining the common interface all brokers must implement.

**Core Methods:**
- `get_login_url()` — Returns broker login URL for authentication
- `generate_session(request_token)` — Exchanges temporary token for access token
- `get_ltp(symbol, exchange)` — Fetches last traded price
- `get_margins()` — Returns account margin details
- `get_positions()` — Returns list of open positions (normalized)
- `place_order(symbol, qty, ...)` — Places a buy/sell order
- `place_gtt_stop_loss(symbol, qty, trigger_price)` — Places server-side stop-loss
- `cancel_order(order_id)` — Cancels a pending order
- `get_order_status(order_id)` — Fetches order status

**Normalized Data Classes:**
- `Position` — Standardized position data across all brokers
- `Order` — Standardized order response
- `GTTOrder` — Standardized GTT stop-loss response

### 2. **BrokerFactory** (`broker_factory.py`)
Factory function that creates the correct broker instance based on configuration.

```python
from broker_factory import create_broker

broker = create_broker(
    broker_type="zerodha_kite",  # or "upstox", "angel_one", "fyers", "dhan"
    api_key="your_api_key",
    api_secret="your_api_secret",  # only needed for some brokers
    access_token="your_access_token"
)

# Now use broker methods consistently
ltp = broker.get_ltp("RELIANCE")
order_id = broker.place_order("TCS", qty=1, transaction_type="BUY")
```

**Supported Brokers List:**
```python
from broker_factory import SUPPORTED_BROKERS, get_broker_list

print(get_broker_list())  # ['zerodha_kite', 'upstox', 'angel_one', 'fyers', 'dhan']
```

### 3. **Broker Implementations**

#### Zerodha (KiteBroker)
- **Status**: ✅ Full implementation with all methods
- **SDK**: `kiteconnect` (pip install kiteconnect)
- **Authentication**: OAuth flow with daily access tokens
- **Products Supported**: CNC (swing), MIS (intraday)
- **GTT Support**: ✅ Yes (server-side stop-loss)

#### Upstox, Angel One, Fyers, Dhan
- **Status**: 🔄 Stub implementations (all methods raise NotImplementedError)
- **How to Complete**: Replace `raise NotImplementedError()` with actual SDK calls
- **Each broker has**:
  - Constructor with API credentials
  - Placeholder for each interface method
  - TODO comments indicating what needs implementation

## Configuration

### `config.py` Updates
```python
DEFAULT_CONFIG = {
    "broker": "zerodha_kite",  # ← Broker selection saved here
    "api_key": "",             # ← API credentials
    "api_secret": "",
    "access_token": "",
    # ... other settings
}
```

### Settings Tab UI
1. **Broker Dropdown** — User selects broker from list of 5
2. **API Key/Secret Input** — Broker-specific credentials
3. **Login Flow** — Calls broker.get_login_url() and broker.generate_session()
4. **Status Display** — Shows connection status

## File Structure

```
trading_app/
├── broker_interface.py          # Abstract base class + data classes
├── broker_factory.py            # Factory for creating brokers
├── broker_kite.py               # ✅ Zerodha (Kite Connect)
├── broker_upstox.py             # 🔄 Upstox stub
├── broker_angel.py              # 🔄 Angel One stub
├── broker_fyers.py              # 🔄 Fyers stub
├── broker_dhan.py               # 🔄 Dhan stub
├── app.py                       # Updated to use factory
├── config.py                    # Stores broker selection
└── ...
```

## Usage in App

### Before (Hardcoded Zerodha)
```python
from broker_kite import KiteBroker
broker = KiteBroker(api_key=api_key, api_secret=api_secret, access_token=access_token)
```

### After (Multi-Broker)
```python
from broker_factory import create_broker

broker = create_broker(
    broker_type=config.get("broker", "zerodha_kite"),
    api_key=config["api_key"],
    api_secret=config["api_secret"],
    access_token=config["access_token"]
)
```

**Key Benefit:** `app.py` doesn't change — same code works with any broker!

## Adding a New Broker

### 1. Create `broker_[name].py`
```python
from broker_interface import BrokerInterface

class YourBroker(BrokerInterface):
    def __init__(self, api_key: str, access_token: str = ""):
        self.api_key = api_key
        self.access_token = access_token
        # Initialize your broker's SDK
    
    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        # Use your broker's SDK to fetch LTP
        pass
    
    # ... implement all other methods
```

### 2. Update `broker_factory.py`
```python
def create_broker(broker_type: str, ...):
    if broker_type == "your_broker":
        from broker_your_broker import YourBroker
        return YourBroker(api_key=api_key, access_token=access_token)
    # ...
    
SUPPORTED_BROKERS = [
    # ...
    ("your_broker", "🎯 Your Broker Name"),
]
```

### 3. Test Broker-Agnostic App
```
The app automatically works without any changes!
```

## Current Implementation Status

### ✅ Completed
- Abstract interface definition (`BrokerInterface`)
- Normalized data classes (Position, Order, GTTOrder)
- Broker factory with validation
- Zerodha implementation with all 9 methods
- App.py integration with broker factory
- Settings tab broker selection dropdown
- Config system for broker persistence
- All 5 stubs created with TODO placeholders

### 🔄 In Progress / TODO
- Implement Upstox methods
- Implement Angel One methods
- Implement Fyers methods
- Implement Dhan methods
- Test broker switching in live trading
- Add broker-specific error handling
- Document broker-specific quirks (e.g., product types, order types)

### 📋 Implementation Roadmap

**Phase 1: Core Architecture** ✅
- [x] BrokerInterface abstract class
- [x] BrokerFactory for instantiation
- [x] Zerodha full implementation
- [x] Stub implementations for 4 brokers
- [x] Config system integration
- [x] App.py factory integration

**Phase 2: Broker Implementation** (Choose order based on demand)
1. Upstox (popular zero-commission broker)
2. Angel One (mobile-first trading)
3. Fyers (advanced retail segment)
4. Dhan (emerging platform)

**Phase 3: Testing & Hardening**
- Multi-broker integration tests
- Error handling & edge cases
- Broker-specific position calculations
- Daily P&L consistency across brokers

**Phase 4: Advanced Features**
- Portfolio sync across multiple brokers
- Broker comparison dashboard
- Multi-account trading

## Broker-Specific Notes

### Zerodha (Kite Connect)
- **Unique Identifiers**: Uses `exchange:symbol` format (e.g., "NSE:RELIANCE")
- **Products**: CNC (delivery), MIS (intraday), NRML (futures)
- **GTT Availability**: ✅ Supported
- **Margin Call**: Real-time via WebSocket

### Upstox
- **Unique Identifiers**: Might use different format
- **Products**: TBD during implementation
- **GTT Availability**: Check API documentation
- **Notes**: Zero-commission broker, popular among retail traders

### Angel One
- **Unique Identifiers**: Uses `exchange-symbol` format (check SmartAPI docs)
- **Products**: MIS, NRML, CNC
- **GTT Availability**: Check if available
- **Notes**: Mobile-first, has iOS/Android apps

### Fyers
- **Unique Identifiers**: TBD
- **Products**: TBD
- **GTT Availability**: Check if available
- **Notes**: Advanced options trading available

### Dhan
- **Unique Identifiers**: TBD
- **Products**: TBD
- **GTT Availability**: Check if available
- **Notes**: Growing platform with good API support

## Example: Trading with Different Brokers

```python
# User selects broker in Settings tab
broker = create_broker(
    broker_type=config["broker"],  # "upstox" or "fyers" etc.
    api_key=config["api_key"],
    access_token=config["access_token"]
)

# Same trading code works for ANY broker
symbol = "RELIANCE"
ltp = broker.get_ltp(symbol)  # Returns float
positions = broker.get_positions()  # Returns List[Position]
order_id = broker.place_order(
    symbol=symbol,
    qty=1,
    transaction_type="BUY"
)
broker.place_gtt_stop_loss(symbol, qty=1, trigger_price=ltp*0.97)
```

## Safety & Best Practices

1. **Separate Broker Logic** — Keep broker-specific code isolated in broker_* files
2. **Normalize Data** — Always return Position/Order/GTTOrder, never raw broker responses
3. **Error Handling** — Each broker raises different exceptions; catch and re-raise as needed
4. **API Rate Limits** — Document rate limits per broker in their respective modules
5. **Testing** — Test order placement and position tracking on each broker

## Future Enhancements

1. **Multi-Account Trading** — Trade simultaneously on Zerodha + Upstox
2. **Broker Comparison** — Show brokerage costs, margin rates, order fill speeds
3. **Portfolio Aggregation** — View combined positions across multiple brokers
4. **Smart Router** — Route orders to broker with best execution
5. **Circuit Breaker** — Detect broker downtime, auto-failover to backup

---

**Last Updated**: 2026-06-21  
**Architecture Status**: Production-ready for Zerodha, stubs ready for other brokers  
**Next Priority**: Implement Upstox methods
