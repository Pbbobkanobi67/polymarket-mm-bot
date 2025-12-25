"""
Configuration settings for the Polymarket Market Making Bot
"""
import os
from dataclasses import dataclass, field
from typing import Optional
from decimal import Decimal


@dataclass
class APIConfig:
    """Polymarket API configuration"""
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137  # Polygon mainnet
    
    # Load from environment variables for security
    private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PK", ""))
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_PASSPHRASE", ""))
    funder_address: str = field(default_factory=lambda: os.getenv("POLYMARKET_FUNDER", ""))
    
    # Signature type: 0 = EOA, 1 = Email/Magic, 2 = Browser Wallet Proxy
    signature_type: int = 0


@dataclass
class TradingConfig:
    """Trading parameters"""
    # Spread settings (in decimal, e.g., 0.02 = 2 cents)
    base_spread: Decimal = Decimal("0.02")
    min_spread: Decimal = Decimal("0.01")
    max_spread: Decimal = Decimal("0.10")
    
    # Order sizes (in USDC value)
    min_order_size: Decimal = Decimal("5.0")
    max_order_size: Decimal = Decimal("50.0")
    default_order_size: Decimal = Decimal("20.0")
    
    # How many levels deep to quote
    num_levels: int = 3
    level_spacing: Decimal = Decimal("0.01")  # Space between levels
    
    # Price bounds - don't quote outside these ranges
    min_price: Decimal = Decimal("0.05")
    max_price: Decimal = Decimal("0.95")


@dataclass
class RiskConfig:
    """Risk management parameters"""
    # Maximum position in any single market (in shares)
    max_position: int = 500
    
    # Maximum total exposure across all markets (in USDC)
    max_total_exposure: Decimal = Decimal("1000.0")
    
    # Inventory skew threshold - start adjusting quotes when inventory exceeds this
    inventory_skew_threshold: int = 100
    
    # Maximum inventory imbalance before halting trading
    max_inventory_imbalance: int = 400
    
    # Daily loss limit (in USDC) - stop trading if exceeded
    daily_loss_limit: Decimal = Decimal("100.0")
    
    # Spread multiplier when inventory is skewed
    inventory_spread_multiplier: Decimal = Decimal("1.5")
    
    # Time-based risk: hours before market resolution to stop trading
    hours_before_resolution_cutoff: int = 24
    
    # Volatility adjustment: multiply spread by this during high volatility
    volatility_spread_multiplier: Decimal = Decimal("2.0")


@dataclass
class StrategyConfig:
    """Strategy-specific parameters"""
    # Quote refresh interval in seconds
    quote_refresh_interval: float = 5.0
    
    # Order timeout - cancel orders older than this (seconds)
    order_timeout: int = 300
    
    # Minimum edge required to quote (expected profit per trade)
    min_edge: Decimal = Decimal("0.005")
    
    # Use mid-price or weighted mid for fair value calculation
    use_weighted_mid: bool = True
    
    # Weight for order book depth in fair value calculation
    depth_weight: Decimal = Decimal("0.3")
    
    # Whether to adjust for adverse selection
    adverse_selection_adjustment: bool = True
    
    # Adverse selection decay factor (recent trades weighted more)
    adverse_selection_decay: Decimal = Decimal("0.9")


@dataclass
class MonitoringConfig:
    """Monitoring and logging configuration"""
    log_level: str = "INFO"
    log_file: str = "logs/market_maker.log"
    
    # Performance metrics
    track_pnl: bool = True
    track_fills: bool = True
    track_inventory: bool = True
    
    # Alert thresholds
    alert_on_loss: Decimal = Decimal("50.0")
    alert_on_large_fill: Decimal = Decimal("100.0")
    
    # Console output
    print_quotes: bool = True
    print_fills: bool = True
    print_pnl_interval: int = 60  # Print PnL every N seconds


@dataclass
class BotConfig:
    """Main bot configuration combining all settings"""
    api: APIConfig = field(default_factory=APIConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Markets to trade (list of token IDs)
    target_markets: list = field(default_factory=list)
    
    # Paper trading mode
    paper_trading: bool = True
    
    @classmethod
    def load_from_env(cls) -> "BotConfig":
        """Load configuration with environment variable overrides"""
        config = cls()
        
        # Override paper trading from env
        if os.getenv("PAPER_TRADING", "true").lower() == "false":
            config.paper_trading = False
            
        return config


# Default configuration instance
DEFAULT_CONFIG = BotConfig()
