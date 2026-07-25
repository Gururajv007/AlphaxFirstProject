"""
screener.py
-----------
Stock screening engine with real-time scanning, signal ranking, and watchlist management.

Features:
- NSE stock universe loader (Nifty 50, Nifty 100, F&O, Custom)
- Background data refresh with caching
- Multi-timeframe scanning
- Signal strength scoring and ranking
- Filter presets (aggressive, conservative, momentum, value)
- Watchlist persistence
- Scan history and performance tracking
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import datetime as dt
import json
import os
import pandas as pd
import numpy as np
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import data_feed
from strategy import StrategyParams, generate_signals


# =====================================================================
# STOCK UNIVERSE DEFINITIONS
# =====================================================================

NIFTY_50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BAJFINANCE",
    "BHARTIARTL", "HINDUNILVR", "ITC", "LTC", "KOTAKBANK", "AXISBANK",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "M&M",
    "NESTLEIND", "POWERGRID", "NTPC", "ONGC", "COALINDIA", "GRASIM",
    "HDFCLIFE", "SBILIFE", "BAJAJFINSV", "ADANIPORTS", "JSWSTEEL",
    "TATASTEEL", "WIPRO", "CIPLA", "DRREDDY", "HEROMOTOCO", "BPCL",
    "EICHERMOT", "INDUSINDBK", "HCLTECH", "UPL", "ADANIENT", "DIVISLAB",
    "APOLLOHOSP", "TATACONSUM", "BRITANNIA", "TECHM", "GRANULES",
]

NIFTY_100_SYMBOLS = NIFTY_50_SYMBOLS + [
    "ADANIGREEN", "ADANIPOWER", "ATUL", "AUBANK", "BANDHANBNK", "BANKBARODA",
    "BEL", "BHEL", "BIOCON", "BOSCHLTD", "CANBK", "CHAMBER", "CONCOR",
    "COROMANDEL", "CROMPTON", "CUMMINSIND", "DABUR", "DALMIABHA", "DEEPAKNTR",
    "DELHIVERY", "ESCORTS", "EXIDEIND", "FEDERALBNK", "GAIL", "GLENMARK",
    "GODREJCP", "GODREJPROP", "GSPL", "GUJGASLTD", "HAL", "HAVELLS", "HINDZINC",
    "ICICIGI", "ICICIPRULI", "IDFCFIRSTB", "IGL", "INDIGO", "IPCALAB",
    "JINDALSTEL", "JUBLFOOD", "KARURVYSYA", "L&TFH", "LAURUSLABS", "LICI",
    "LUPIN", "M&MFIN", "MANKIND", "MARICO", "MEDPLUS", "MFSL", "MUTHOOTFIN",
    "NAMURA", "NATALIE", "NAUKRI", "OBEROIRLTY", "OFSS", "OIL", "PAGEIND",
    "PATANJALI", "PCBL", "PEL", "PERSISTENT", "PETRONET", "PFIZER", "PIDILITIND",
    "PIIND", "PNB", "PRESTIGE", "QUESS", "RBLBANK", "RECLTD", "SANDHYA",
    "SHREECEM", "SHRIRAMFIN", "SIEMENS", "SPA", "SRF", "STAR", "SUNTV",
    "SYMBIOSIS", "TATACHEM", "TATAMOTORS", "TATAPOWER", "TATAELXSI", "TCS",
    "TORNTPOWER", "TRENT", "TRIVENI", "TVSMOTOR", "UBL", "VEDL", "VOLTAS",
    "WHIRLPOOL", "WONDERLA", "ZEEL", "ZYDUSLIFE",
]

# Common F&O stocks (subset of Nifty 100 plus others)
FNO_SYMBOLS = NIFTY_100_SYMBOLS + [
    "AARTIDRUGS", "ABB", "ABFRL", "ACC", "ALKEM", "AMARAJABAT", "AMBER",
    "ANUP", "APLLTD", "ASHOKLEY", "ASTRAL", "ATGL", "AUROPHARMA", "AVANTIFEED",
    "AXISBANK", "BAJAJ-AUTO", "BAJAJCON", "BALAMINES", "BALKRISHNA", "BALRAMCHIN",
    "BATAINDIA", "BBTC", "BCCL", "BEL", "BFUTILITIES", "BHARATFORG", "BHARATPETROL",
    "BHARTIARTL", "BIRLACORPN", "BSOFT", "CAMPUS", "CANFINHOME", "CAPLIPOINT",
    "CASTROLIND", "CCL", "CDSL", "CENTRALBK", "CERA", "CHALET", "CHAMBS",
    "CHEMPLASTS", "CHENNPETRO", "CLEAN", "CLNINDIA", "COFORGE", "CRAFTSMAN",
    "CREATIVE", "CRISIL", "CYIENT", "DAAWAT", "DALMIABHA", "DATAPATTNS",
    "DEEPAKFERT", "DELHIVERY", "DHANI", "DHANUKA", "DIAMONDYD", "DINOX",
    "DIXON", "DOLLAR", "DRL", "DTIL", "DWARIKESH", "DYL", "EASTSILK",
    "EIDPARRY", "EIHOTEL", "ELECON", "EMAMILTD", "ENDURANCE", "ENGINERSIN",
    "EPL", "EQUITAS", "ERIS", "ESABINDIA", "EXIDEIND", "FACT", "FCL",
    "FINCABLES", "FINEORG", "FINPIPE", "FMGOETZE", "FORTIS", "FOSPH",
    "FRETAIL", "GABRIEL", "GALAXYSURF", "GALLANTT", "GANDHITUBE", "GANESHBE",
    "GATEWAY", "GEPIL", "GICRE", "GILLETTE", "GIR", "GLAND", "GLAXO",
    "GLENMARK", "GLOBUSSPR", "GLS", "GMMPFAUDLR", "GOCOLORS", "GODFRYPHLP",
    "GODREJAGRO", "GODREJIND", "GODREJMIC", "GOKEX", "GOLDBEES", "GOLDTECH",
    "GPPL", "GRINDWELL", "GROBTEA", "GRPL", "GSFC", "GSPL", "GUJALKALI",
    "GUJCOTEXP", "GUJFLUORO", "GULFOILLUB", "GULFPETRO", "HAL", "HAPPYFORGE",
    "HAVELLS", "HDIL", "HGS", "HIKAL", "HINDALCO", "HINDCOPPER", "HINDPETRO",
    "HINDZINC", "HOMEFIRST", "HONAUT", "HUDCO", "HUHTAMAKI", "IBULHSGFIN",
    "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDBI", "IDFC", "IDFCFIRSTB",
    "IEX", "IFBIND", "IGL", "IIFL", "IMFA", "INCREDIBLE", "INDHOTEL",
    "INDIACEM", "INDIAMART", "INDIANB", "INDIGO", "INDOBORAX", "INDOCO",
    "INDORAMA", "INDOSTAR", "INDUSINDBK", "INEOSSTYRO", "INFIBEAM", "INFY",
    "INGERRAND", "IOB", "IPAPPM", "IRB", "IRCON", "ISEC", "ITC",
    "JAGUARCARS", "JAMNAAUTO", "JAYBARMARU", "JCHAC", "JINDALHOTEL", "JINDALPAINT",
    "JINDALPOLY", "JINDALSTEL", "JKCEMENT", "JKLAKSHMI", "JKPAPER", "JKTYRE",
    "JMA", "JMF", "JOC", "JPASSOCIAT", "JPowers", "JSL", "JSLHISAR",
    "JSWENERGY", "JSWHL", "JTEKT", "JUBLFOOD", "JUBLING", "JUBLPHARMA",
    "JVS", "JYOTHYLABS", "KAJARIACER", "KALPATPOWR", "KALYANKJIL",
    "KANSAINER", "KARURVYSYA", "KAYNES", "KCP", "KCR", "KEI", "KNRCON",
    "KOLTEPATIL", "KOTAKBANK", "KPITTECH", "KRBL", "KRSNAA", "KSB",
    "KSHIRAMA", "KTKBANK", "KURLON", "L&TFH", "LALPATHLAB", "LAOPALA",
    "LAURUSLABS", "LEEL", "LEMONTREE", "LICHSGFIN", "LINDEINDIA", "LODHA",
    "LOKESHMACH", "LT", "LTTS", "LUPIN", "LYKALABS", "M&M", "M&MFIN",
    "MAFANG", "MAHABANK", "MAHINDCIE", "MAHLIFE", "MAHSCOOTER", "MAHSEAMLES",
    "MANAKALU", "MANAL", "MANAPPuram", "MANGLIN", "MANINFRA", "MANKIND",
    "MAPMYINDIA", "MARICO", "MARUTI", "MAS", "MATRIMONY", "MAWANAGAS",
    "MAXHEALTH", "MAXIND", "MCDOWELL-N", "MCHL", "MCX", "MEDANTA", "MEGH",
    "MELSTAR", "MENONBRA", "METROBRAND", "MFL", "MFSL", "MGL", "MHRIL",
    "MINDACORP", "MINDTREE", "MMFL", "MODIRUBBER", "MOIL", "MONDIS",
    "MOREPENLAB", "MOTILALOFS", "MPHASIS", "MRBL", "MRF", "MRL",
    "MRPL", "MSUMI", "MUTHOOTFIN", "NACLIND", "NANDAN", "NATCOPHARM",
    "NATHBIOGEN", "NAUKRI", "NAVINFLUOR", "NAVNET", "NCC", "NCLIND",
    "NDGL", "NEOGEN", "NESTLEIND", "NETWORK18", "NEWGEN", "NEXT",
    "NFL", "NGLFINE", "NH", "NIBL", "NIITLTD", "NILKAMAL",
    "NIPPO", "NIRAJ", "NIRLON", "NMDC", "NOCIL", "NOIDATOLL",
    "NORTHLAND", "NPCC", "NRA", "NSLNISP", "NTPC", "NUCLEUS",
    "OBEROIRLTY", "OFSS", "OIL", "OLECTRA", "ORIENTCEM", "ORIENTELEC",
    "ORIENTREF", "ORTEL", "PAEL", "PAGEIND", "PAISALO", "PALRED",
    "PANCARBON", "PAPERPROD", "PARADEEP", "PARAGMILK", "PARASDEF",
    "PCJEWELLER", "PCL", "PDMJ", "PDPL", "PEARLPOLY", "PERSISTENT",
    "PETRONET", "PFC", "PFIZER", "PFS", "PGHH", "PGHL", "PHOENIXLTD",
    "PIDILITIND", "PIIND", "PILANI", "Piramal", "PLASTIBLES", "PNB",
    "PNCINFRA", "POONAWALLA", "POWERGRID", "PPA", "PRAJIND", "PRSMJOHNSON",
    "PSUBNK", "PTC", "PVRINOX", "QUESS", "QUICKHEAL", "RBLBANK",
    "RCF", "RDEL", "REALREG", "REDINGTON", "RELAXO", "RELCHEM",
    "RELCAPITAL", "RELIANCE", "RELIGARE", "RENUKA", "REPCOHOME", "REPL",
    "RETAIL", "RGL", "RHFL", "RIIL", "RITES", "RKFORGE", "RMG",
    "ROHLTD", "ROMA", "ROML", "RPOWER", "RRKABEL", "RSYSTEMS",
    "RTNINDIA", "RUBYMILLS", "RUCHIRA", "RUPA", "RUSHIL", "SABAR",
    "SADBHAV", "SAFARI", "SAIL", "SAMI", "SANDESH", "SANGHIIND",
    "SANGHVIMOV", "SANOFI", "SAPPHIRE", "SARDAEN", "SAREGAMA", "SATIN",
    "SAURAS", "SBICARD", "SBIN", "SCC", "SCHAND", "SCHNEIDER",
    "SCI", "SCRL", "SDBL", "SEAMEC", "SELAN", "SERVOTECH",
    "SESHACOP", "SGBL", "SHANKARA", "SHANTIGEAR", "SHAW", "SHIL",
    "SHIVALIK", "SHRADHA", "SHREECEM", "SHREMETAL", "SHRIRAM", "SHRIRAMFIN",
    "SHS", "SIDDL", "SIGMA", "SILGO", "SILVER", "SIMBHAL",
    "SIMPLEX", "SIRCA", "SIS", "SKFINDIA", "SMLISUZU", "SMLT",
    "SNOWMAN", "SOUTHBANK", "SPANDANA", "SPICEJET", "SPLIL", "SPML",
    "SRE", "SREINFRA", "SRF", "SRG", "STAR", "STARPAPER",
    "STCINDIA", "STEELCAS", "STEL", "STERTOOLS", "STLTECH", "STOVEKRAFT",
    "SUCHITRA", "SUDARSCHEM", "SUKHJIT", "SUMICHEM", "SUMITDB", "SUNFLAG",
    "SUNPHARMA", "SUNTECK", "SUNTV", "SUPERHOUSE", "SUPERSPIN", "SUPRAJIT",
    "SUSW", "SUULD", "SUVEN", "SUYOG", "SYNGENE", "SYRMA",
    "TATACHEM", "TATACOFFEE", "TATACONSUM", "TATAELXSI", "TATAGALLIUM",
    "TATAINVEST", "TATAMETALI", "TATAMOTORS", "TATAMUL", "TATAPOWER",
    "TATASTEEL", "TATVA", "TCS", "TDPOWERSYS", "TEAMLEASE", "TECHM",
    "TEJASNET", "THERMAX", "THOMASCOOK", "THYROCARE", "TI", "TIDEWATER",
    "TINPLATE", "TITAN", "TORNTPHARM", "TORNTPOWER", "TRENT", "TRF",
    "TRID", "TRIGYN", "TRIJ", "TRIVENI", "TTKHL", "TTL",
    "TTML", "TV18BRDCST", "TVSMOTOR", "TVSS", "UBL", "UCHEMA",
    "UJJIVAN", "UJJIVANSFB", "ULTRACEMCO", "UNICHEMLAB", "UNIONBANK",
    "UNIPARTS", "UNITECH", "UPL", "URJA", "USHAMART", "UTIBANK",
    "UTTARA", "VAKRANGEE", "VAL", "VARDHACRL", "VARIM", "VASWANI",
    "VEDL", "VENKEYS", "VENUSREM", "VERTOZ", "VETO", "VGUARD",
    "VINATIORGA", "VIPCLOTH", "VJ", "VL", "VLL", "VMART",
    "VOLTAS", "VRLLOG", "VSTIND", "VSTTILLERS", "WABCOINDIA", "WALCHANDNAG",
    "WANBURY", "WATERBASE", "WEATHER", "WELCORP", "WELENT", "WELINV",
    "WELSPUNL", "WENDT", "WHIRLPOOL", "WINDLAS", "WINGREENS", "WIPRO",
    "WOCKPHARMA", "WONDERLA", "WSI", "WSTCSTPAPR", "XCHG", "XELPMOC",
    "YESBANK", "ZEEL", "ZENSARTECH", "ZFCVINDIA", "ZIMLAB", "ZODIAC",
    "ZOMATO", "ZOTA", "ZYDUSWELL", "ZYDUSLIFE",
]


# =====================================================================
# FILTER PRESETS
# =====================================================================

FILTER_PRESETS = {
    "conservative": {
        "min_risk_reward": 2.0,
        "min_adx": 30,
        "min_volume_ratio": 1.5,
        "min_rel_strength": 2.0,
        "min_liquidity": 10_00_00_000,  # ₹10 crore
    },
    "moderate": {
        "min_risk_reward": 1.5,
        "min_adx": 25,
        "min_volume_ratio": 1.2,
        "min_rel_strength": 0,
        "min_liquidity": 5_00_00_000,  # ₹5 crore
    },
    "aggressive": {
        "min_risk_reward": 1.2,
        "min_adx": 20,
        "min_volume_ratio": 1.0,
        "min_rel_strength": -2.0,
        "min_liquidity": 2_00_00_000,  # ₹2 crore
    },
    "momentum": {
        "min_risk_reward": 2.0,
        "min_adx": 35,
        "min_volume_ratio": 1.8,
        "min_rel_strength": 3.0,
        "min_liquidity": 10_00_00_000,
        "rsi_buy_max": 50,  # Only buy RSI < 50 (early momentum)
    },
}


@dataclass
class ScanResult:
    """Result of a stock scan."""
    symbol: str
    price: float
    signal: str
    signal_strength: float  # 0-1
    filters_passed: int
    filters_total: int

    # Technical indicators
    rsi: float
    adx: float
    atr_pct: float
    volume_ratio: float

    # Trade parameters
    stop_loss: float
    target: float
    risk_reward: float

    # Additional data
    change_pct: float
    volume: float
    avg_turnover: float


@dataclass
class ScanConfig:
    """Configuration for a stock scan."""
    symbols: List[str]
    timeframe: str = "1d"
    preset: str = "moderate"  # conservative, moderate, aggressive, momentum
    custom_params: Optional[StrategyParams] = None


class StockUniverse:
    """Manages stock universes and watchlists."""

    def __init__(self, data_dir: str = "./screener_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.watchlists: Dict[str, List[str]] = {}
        self.load_watchlists()

    def get_nifty_50(self) -> List[str]:
        return NIFTY_50_SYMBOLS.copy()

    def get_nifty_100(self) -> List[str]:
        return NIFTY_100_SYMBOLS.copy()

    def get_fno_stocks(self) -> List[str]:
        return FNO_SYMBOLS.copy()

    def get_all(self) -> List[str]:
        return FNO_SYMBOLS.copy()

    # =====================================================================
    # WATCHLIST MANAGEMENT
    # =====================================================================

    def create_watchlist(self, name: str, symbols: List[str]) -> None:
        """Create a new watchlist."""
        self.watchlists[name] = [s.upper().strip() for s in symbols]
        self.save_watchlists()

    def add_to_watchlist(self, name: str, symbol: str) -> bool:
        """Add a symbol to an existing watchlist."""
        if name not in self.watchlists:
            return False

        symbol = symbol.upper().strip()
        if symbol not in self.watchlists[name]:
            self.watchlists[name].append(symbol)
            self.save_watchlists()
        return True

    def remove_from_watchlist(self, name: str, symbol: str) -> bool:
        """Remove a symbol from a watchlist."""
        if name not in self.watchlists:
            return False

        symbol = symbol.upper().strip()
        if symbol in self.watchlists[name]:
            self.watchlists[name].remove(symbol)
            self.save_watchlists()
        return True

    def get_watchlist(self, name: str) -> List[str]:
        """Get symbols in a watchlist."""
        return self.watchlists.get(name, [])

    def get_all_watchlists(self) -> Dict[str, List[str]]:
        """Get all watchlists."""
        return self.watchlists.copy()

    def delete_watchlist(self, name: str) -> bool:
        """Delete a watchlist."""
        if name in self.watchlists:
            del self.watchlists[name]
            self.save_watchlists()
            return True
        return False

    def save_watchlists(self) -> None:
        """Save watchlists to disk."""
        watchlist_file = os.path.join(self.data_dir, "watchlists.json")
        with open(watchlist_file, "w") as f:
            json.dump(self.watchlists, f, indent=2)

    def load_watchlists(self) -> None:
        """Load watchlists from disk."""
        watchlist_file = os.path.join(self.data_dir, "watchlists.json")
        if os.path.exists(watchlist_file):
            with open(watchlist_file, "r") as f:
                self.watchlists = json.load(f)


class StockScreener:
    """
    Stock screening engine with multi-timeframe scanning and signal ranking.
    """

    def __init__(
        self,
        universe: Optional[StockUniverse] = None,
        data_cache_dir: str = "./screener_data/cache",
    ):
        self.universe = universe or StockUniverse(data_cache_dir)

        # Data cache
        self.data_cache_dir = data_cache_dir
        os.makedirs(data_cache_dir, exist_ok=True)
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.cache_ttl_seconds = 300  # 5 minutes

        # Scan history
        self.scan_history: List[Dict] = []
        self.history_file = os.path.join(data_cache_dir, "scan_history.json")

        # Background scanning
        self.is_scanning = False
        self.scan_thread: Optional[threading.Thread] = None
        self.latest_results: List[ScanResult] = []

    # =====================================================================
    # SCREENING
    # =====================================================================

    def scan(
        self,
        symbols: List[str],
        timeframe: str = "1d",
        preset: str = "moderate",
        use_relative_strength: bool = True,
        max_workers: int = 5,
    ) -> List[ScanResult]:
        """
        Scan a list of symbols for trading signals.

        Args:
            symbols: List of NSE symbols to scan
            timeframe: Timeframe for analysis (15m, 1h, 1d, 1wk)
            preset: Filter preset (conservative, moderate, aggressive, momentum)
            use_relative_strength: Whether to use relative strength filter
            max_workers: Number of parallel workers for scanning

        Returns:
            List of ScanResult objects sorted by signal strength
        """
        if self.is_scanning:
            return self.latest_results

        self.is_scanning = True
        start_time = dt.datetime.now()

        # Get parameters based on preset
        params = self._get_params_from_preset(preset)
        params.use_relative_strength = use_relative_strength

        # Get index data for relative strength
        index_df = None
        if use_relative_strength:
            try:
                index_df = data_feed.get_historical(data_feed.NIFTY50_SYMBOL, interval=timeframe)
            except Exception as e:
                print(f"Warning: Could not fetch index data: {e}")

        results: List[ScanResult] = []
        failed_symbols = []

        # Process symbols in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(
                    self._scan_single_symbol,
                    symbol,
                    timeframe,
                    params,
                    index_df,
                ): symbol
                for symbol in symbols
            }

            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    failed_symbols.append((symbol, str(e)))

        # Sort by signal strength
        results.sort(key=lambda x: x.signal_strength, reverse=True)

        # Store results
        self.latest_results = results
        self.is_scanning = False

        # Record scan in history
        scan_record = {
            "timestamp": start_time.isoformat(),
            "duration_seconds": (dt.datetime.now() - start_time).total_seconds(),
            "symbols_scanned": len(symbols),
            "signals_found": sum(1 for r in results if r.signal == "BUY"),
            "failed_symbols": len(failed_symbols),
            "preset": preset,
            "timeframe": timeframe,
        }
        self.scan_history.append(scan_record)
        self._save_history()

        return results

    def _scan_single_symbol(
        self,
        symbol: str,
        timeframe: str,
        params: StrategyParams,
        index_df: Optional[pd.DataFrame],
    ) -> Optional[ScanResult]:
        """Scan a single symbol."""
        try:
            # Get cached or fresh data
            df = self._get_cached_data(symbol, timeframe)

            if df is None or df.empty:
                return None

            # Generate signals
            sig_df = generate_signals(df, params, index_df)

            if sig_df.empty:
                return None

            # Get latest signal
            latest = sig_df.iloc[-1]

            # Check if there's a BUY signal
            if latest.get("signal") != "BUY":
                return None

            # Count filters passed
            filters_passed = 0
            filters_total = 9

            if latest.get("ema_fast", 0) > latest.get("ema_slow", 0):
                filters_passed += 1
            if latest.get("adx", 0) >= params.adx_threshold:
                filters_passed += 1
            if latest.get("volume_ratio", 0) >= params.volume_ratio_min:
                filters_passed += 1

            # Calculate signal strength (0-1)
            signal_strength = filters_passed / filters_total

            # Apply preset-specific adjustments
            if params.use_adx_filter:
                adx_factor = min(1.0, latest.get("adx", 0) / 40)
                signal_strength *= (0.5 + 0.5 * adx_factor)

            # Get latest price data
            change_pct = ((latest["Close"] - latest["Open"]) / latest["Open"]) * 100

            return ScanResult(
                symbol=symbol,
                price=float(latest["Close"]),
                signal="BUY",
                signal_strength=signal_strength,
                filters_passed=filters_passed,
                filters_total=filters_total,
                rsi=float(latest.get("rsi", 0)),
                adx=float(latest.get("adx", 0)),
                atr_pct=float(latest.get("atr_pct", 0)) * 100,
                volume_ratio=float(latest.get("vol_ratio", 0)),
                stop_loss=float(latest.get("stop_loss", 0)),
                target=float(latest.get("target", 0)),
                risk_reward=float(latest.get("risk_reward", 0)),
                change_pct=change_pct,
                volume=float(latest.get("Volume", 0)),
                avg_turnover=float(latest.get("avg_turnover", 0)),
            )

        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
            return None

    def _get_cached_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Get data from cache or fetch fresh."""
        cache_key = f"{symbol}_{timeframe}"
        cache_file = os.path.join(self.data_cache_dir, f"{cache_key}.parquet")

        # Check file cache
        if os.path.exists(cache_file):
            file_time = dt.datetime.fromtimestamp(os.path.getmtime(cache_file))
            if (dt.datetime.now() - file_time).total_seconds() < self.cache_ttl_seconds:
                try:
                    return pd.read_parquet(cache_file)
                except Exception:
                    pass

        # Fetch fresh data
        try:
            df = data_feed.get_historical(symbol, interval=timeframe)
            if df is not None and not df.empty:
                # Save to cache
                try:
                    df.to_parquet(cache_file)
                except Exception:
                    pass
                return df
        except Exception:
            pass

        return None

    def _get_params_from_preset(self, preset: str) -> StrategyParams:
        """Get strategy parameters from preset."""
        params = StrategyParams()

        if preset in FILTER_PRESETS:
            preset_config = FILTER_PRESETS[preset]
            params.min_risk_reward = preset_config.get("min_risk_reward", 1.5)
            params.adx_threshold = preset_config.get("min_adx", 25)
            params.volume_ratio_min = preset_config.get("min_volume_ratio", 1.1)
            params.min_avg_turnover = preset_config.get("min_liquidity", 5_00_00_000)

        return params

    # =====================================================================
    # BACKGROUND SCANNING
    # =====================================================================

    def start_background_scan(
        self,
        symbols: List[str],
        interval_seconds: int = 300,  # 5 minutes
        preset: str = "moderate",
    ) -> None:
        """Start background scanning."""
        if self.scan_thread and self.scan_thread.is_alive():
            return

        def scan_loop():
            while self.is_scanning:
                self.scan(symbols, preset=preset)
                time.sleep(interval_seconds)

        self.is_scanning = True
        self.scan_thread = threading.Thread(target=scan_loop, daemon=True)
        self.scan_thread.start()

    def stop_background_scan(self) -> None:
        """Stop background scanning."""
        self.is_scanning = False
        if self.scan_thread:
            self.scan_thread.join(timeout=5)

    # =====================================================================
    # HISTORY & ANALYTICS
    # =====================================================================

    def get_scan_history(self, limit: int = 20) -> List[Dict]:
        """Get recent scan history."""
        return self.scan_history[-limit:]

    def _save_history(self) -> None:
        """Save scan history to disk."""
        try:
            with open(self.history_file, "w") as f:
                json.dump(self.scan_history[-100:], f, indent=2)  # Keep last 100
        except Exception as e:
            print(f"Error saving scan history: {e}")

    # =====================================================================
    # RESULT FORMATTING
    # =====================================================================

    def results_to_dataframe(self, results: List[ScanResult]) -> pd.DataFrame:
        """Convert scan results to DataFrame for display."""
        if not results:
            return pd.DataFrame()

        rows = []
        for r in results:
            rows.append({
                "Symbol": r.symbol,
                "Price": f"₹{r.price:.2f}",
                "Change %": f"{r.change_pct:+.2f}%",
                "Signal": r.signal,
                "Strength": f"{r.signal_strength*100:.0f}%",
                "RSI": f"{r.rsi:.1f}",
                "ADX": f"{r.adx:.1f}",
                "ATR %": f"{r.atr_pct:.2f}%",
                "Volume": f"{r.volume_ratio:.1f}x",
                "Stop Loss": f"₹{r.stop_loss:.2f}",
                "Target": f"₹{r.target:.2f}",
                "R:R": f"{r.risk_reward:.2f}",
                "Filters": f"{r.filters_passed}/{r.filters_total}",
            })

        return pd.DataFrame(rows)

    def get_top_signals(self, results: List[ScanResult], top_n: int = 10) -> List[ScanResult]:
        """Get top N signals by strength."""
        return sorted(results, key=lambda x: x.signal_strength, reverse=True)[:top_n]