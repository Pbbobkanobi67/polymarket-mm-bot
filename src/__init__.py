"""
Polymarket Market Making Bot

A market making bot for Polymarket prediction markets.
"""

from .client import PolymarketClient, OrderBook, Market, Order, Trade
from .quote_engine import QuoteEngine, SmartQuoteEngine, Quote, QuoteSet
from .order_manager import OrderManager
from .risk_manager import InventoryManager, RiskManager, PnLTracker
from .bot import MarketMakingBot, run_bot

# Optional WebSocket imports (may not be available if websockets not installed)
try:
    from .websocket import PolymarketWebSocket, BookSnapshot, PriceChange, LastTradePrice
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False

__all__ = [
    # Client
    "PolymarketClient",
    "OrderBook",
    "Market",
    "Order",
    "Trade",
    # Quote Engine
    "QuoteEngine",
    "SmartQuoteEngine",
    "Quote",
    "QuoteSet",
    # Order Management
    "OrderManager",
    # Risk Management
    "InventoryManager",
    "RiskManager",
    "PnLTracker",
    # Bot
    "MarketMakingBot",
    "run_bot",
    # WebSocket (optional)
    "PolymarketWebSocket",
    "BookSnapshot",
    "PriceChange",
    "LastTradePrice",
    "WEBSOCKET_AVAILABLE",
]

__version__ = "1.0.0"
