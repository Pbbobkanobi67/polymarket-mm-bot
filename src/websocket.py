"""
Polymarket WebSocket Client

Provides real-time market data and user event streaming via WebSocket.
"""
import asyncio
import json
import logging
import hashlib
import hmac
import time
from typing import Optional, Dict, List, Callable, Any, Set
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None
    WebSocketClientProtocol = None

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    """WebSocket channel types"""
    MARKET = "market"
    USER = "user"


class EventType(Enum):
    """WebSocket event types"""
    BOOK = "book"
    PRICE_CHANGE = "price_change"
    TICK_SIZE_CHANGE = "tick_size_change"
    LAST_TRADE_PRICE = "last_trade_price"
    TRADE = "trade"
    ORDER = "order"


@dataclass
class BookLevel:
    """Represents a single price level in the order book"""
    price: Decimal
    size: Decimal


@dataclass
class BookSnapshot:
    """Full orderbook snapshot from WebSocket"""
    asset_id: str
    market_id: str
    timestamp: datetime
    bids: List[BookLevel]
    asks: List[BookLevel]
    hash: str = ""


@dataclass
class PriceChange:
    """Incremental price level update"""
    asset_id: str
    market_id: str
    side: str  # "BUY" or "SELL"
    price: Decimal
    size: Decimal  # New size at this level (0 = removed)
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None


@dataclass
class LastTradePrice:
    """Trade notification from market channel"""
    asset_id: str
    market_id: str
    side: str
    price: Decimal
    size: Decimal
    timestamp: datetime
    fee_rate_bps: int = 0


@dataclass
class UserTrade:
    """Trade notification from user channel"""
    trade_id: str
    asset_id: str
    market_id: str
    side: str
    price: Decimal
    size: Decimal
    status: str  # MATCHED, MINED, CONFIRMED, RETRYING, FAILED
    timestamp: datetime
    taker_order_id: str = ""
    maker_orders: List[str] = field(default_factory=list)


@dataclass
class UserOrder:
    """Order update from user channel"""
    order_id: str
    asset_id: str
    market_id: str
    side: str
    price: Decimal
    original_size: Decimal
    size_matched: Decimal
    event_type: str  # PLACEMENT, UPDATE, CANCELLATION
    timestamp: datetime


# Type aliases for callbacks
BookCallback = Callable[[BookSnapshot], None]
PriceChangeCallback = Callable[[PriceChange], None]
TradeCallback = Callable[[LastTradePrice], None]
UserTradeCallback = Callable[[UserTrade], None]
UserOrderCallback = Callable[[UserOrder], None]


class PolymarketWebSocket:
    """
    WebSocket client for Polymarket real-time data.

    Supports two channels:
    - Market channel (public): Orderbook snapshots, price changes, trades
    - User channel (authenticated): Order updates, fill notifications
    """

    # WebSocket URLs - channel type is in the path
    WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 10,
    ):
        if websockets is None:
            raise ImportError(
                "websockets library required. Install with: pip install websockets"
            )

        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts

        # Connection state
        self._market_ws: Optional[WebSocketClientProtocol] = None
        self._user_ws: Optional[WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0

        # Subscriptions
        self._subscribed_assets: Set[str] = set()
        self._subscribed_markets: Set[str] = set()  # For user channel

        # Callbacks
        self._book_callbacks: List[BookCallback] = []
        self._price_change_callbacks: List[PriceChangeCallback] = []
        self._trade_callbacks: List[TradeCallback] = []
        self._user_trade_callbacks: List[UserTradeCallback] = []
        self._user_order_callbacks: List[UserOrderCallback] = []

        # Tasks
        self._market_task: Optional[asyncio.Task] = None
        self._user_task: Optional[asyncio.Task] = None

        # Local orderbook cache (built from snapshots + incremental updates)
        self._orderbooks: Dict[str, BookSnapshot] = {}

    # ==================== Callback Registration ====================

    def on_book(self, callback: BookCallback):
        """Register callback for orderbook snapshots"""
        self._book_callbacks.append(callback)

    def on_price_change(self, callback: PriceChangeCallback):
        """Register callback for price level changes"""
        self._price_change_callbacks.append(callback)

    def on_trade(self, callback: TradeCallback):
        """Register callback for trade notifications (market channel)"""
        self._trade_callbacks.append(callback)

    def on_user_trade(self, callback: UserTradeCallback):
        """Register callback for user trade notifications"""
        self._user_trade_callbacks.append(callback)

    def on_user_order(self, callback: UserOrderCallback):
        """Register callback for user order updates"""
        self._user_order_callbacks.append(callback)

    # ==================== Connection Management ====================

    async def connect(self, assets: List[str], markets: Optional[List[str]] = None):
        """
        Connect to WebSocket channels.

        Args:
            assets: List of token/asset IDs to subscribe to (market channel)
            markets: List of market condition IDs for user channel (optional)
        """
        self._running = True
        self._subscribed_assets = set(assets)
        if markets:
            self._subscribed_markets = set(markets)

        # Start market channel (always)
        self._market_task = asyncio.create_task(
            self._run_market_channel()
        )

        # Start user channel if authenticated
        if self.api_key and self.api_secret:
            self._user_task = asyncio.create_task(
                self._run_user_channel()
            )

        logger.info(f"WebSocket connecting to {len(assets)} assets")

    async def disconnect(self):
        """Disconnect from all WebSocket channels"""
        self._running = False

        if self._market_ws:
            await self._market_ws.close()
        if self._user_ws:
            await self._user_ws.close()

        if self._market_task:
            self._market_task.cancel()
            try:
                await self._market_task
            except asyncio.CancelledError:
                pass

        if self._user_task:
            self._user_task.cancel()
            try:
                await self._user_task
            except asyncio.CancelledError:
                pass

        logger.info("WebSocket disconnected")

    async def subscribe(self, assets: List[str]):
        """Subscribe to additional assets"""
        new_assets = set(assets) - self._subscribed_assets
        if not new_assets:
            return

        self._subscribed_assets.update(new_assets)

        if self._market_ws:
            msg = {
                "assets_ids": list(new_assets),
                "type": "market",
            }
            await self._market_ws.send(json.dumps(msg))
            logger.info(f"Subscribed to {len(new_assets)} additional assets")

    async def unsubscribe(self, assets: List[str]):
        """Unsubscribe from assets"""
        to_remove = set(assets) & self._subscribed_assets
        if not to_remove:
            return

        self._subscribed_assets -= to_remove

        # Remove from local cache
        for asset_id in to_remove:
            self._orderbooks.pop(asset_id, None)

        logger.info(f"Unsubscribed from {len(to_remove)} assets")

    # ==================== Channel Runners ====================

    async def _run_market_channel(self):
        """Run market channel with reconnection logic"""
        while self._running:
            try:
                await self._connect_market_channel()
            except Exception as e:
                if not self._running:
                    break

                self._reconnect_count += 1
                if self._reconnect_count > self.max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached for market channel")
                    break

                wait_time = min(
                    self.reconnect_interval * (2 ** self._reconnect_count),
                    60.0
                )
                logger.warning(
                    f"Market channel disconnected: {e}. "
                    f"Reconnecting in {wait_time:.1f}s..."
                )
                await asyncio.sleep(wait_time)

    async def _connect_market_channel(self):
        """Connect and listen to market channel"""
        async with websockets.connect(
            self.WS_MARKET_URL,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            self._market_ws = ws
            self._reconnect_count = 0

            # Send initial subscription
            subscribe_msg = {
                "assets_ids": list(self._subscribed_assets),
                "type": "market",
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info(f"Market channel connected, subscribed to {len(self._subscribed_assets)} assets")

            # Listen for messages
            async for message in ws:
                await self._handle_market_message(message)

    async def _run_user_channel(self):
        """Run user channel with reconnection logic"""
        while self._running:
            try:
                await self._connect_user_channel()
            except Exception as e:
                if not self._running:
                    break

                wait_time = min(
                    self.reconnect_interval * (2 ** self._reconnect_count),
                    60.0
                )
                logger.warning(
                    f"User channel disconnected: {e}. "
                    f"Reconnecting in {wait_time:.1f}s..."
                )
                await asyncio.sleep(wait_time)

    async def _connect_user_channel(self):
        """Connect and listen to user channel (authenticated)"""
        async with websockets.connect(
            self.WS_USER_URL,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            self._user_ws = ws

            # Send authenticated subscription
            subscribe_msg = {
                "auth": {
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.passphrase,
                },
                "markets": list(self._subscribed_markets) if self._subscribed_markets else [],
                "assets_ids": list(self._subscribed_assets),
                "type": "user",
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info("User channel connected and authenticated")

            # Listen for messages
            async for message in ws:
                await self._handle_user_message(message)

    # ==================== Message Handlers ====================

    async def _handle_market_message(self, raw_message: str):
        """Handle messages from market channel"""
        try:
            data = json.loads(raw_message)

            # Handle array of events
            events = data if isinstance(data, list) else [data]

            for event in events:
                event_type = event.get("event_type", "")

                # Handle price_changes format (can have event_type="price_change" and price_changes array)
                if "price_changes" in event:
                    for change in event.get("price_changes", []):
                        change["market"] = event.get("market", "")
                        await self._handle_price_change_event({"changes": [change]})
                    continue

                if event_type == "book":
                    await self._handle_book_event(event)
                elif event_type == "price_change":
                    # Single price change event
                    await self._handle_price_change_event(event)
                elif event_type == "last_trade_price":
                    await self._handle_trade_event(event)
                elif event_type == "tick_size_change":
                    logger.debug(f"Tick size change: {event}")
                elif not event_type:
                    # Empty event or unknown format
                    if event:
                        logger.debug(f"Unknown market event keys: {list(event.keys())}")
                else:
                    logger.debug(f"Unknown event_type: {event_type}")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse market message: {e}")
        except Exception as e:
            logger.error(f"Error handling market message: {e}", exc_info=True)

    async def _handle_user_message(self, raw_message: str):
        """Handle messages from user channel"""
        try:
            data = json.loads(raw_message)

            events = data if isinstance(data, list) else [data]

            for event in events:
                event_type = event.get("event_type", "")

                if event_type == "trade":
                    await self._handle_user_trade_event(event)
                elif event_type == "order":
                    await self._handle_user_order_event(event)
                else:
                    logger.debug(f"Unknown user event type: {event_type}")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse user message: {e}")
        except Exception as e:
            logger.error(f"Error handling user message: {e}")

    async def _handle_book_event(self, event: Dict[str, Any]):
        """Handle orderbook snapshot"""
        try:
            asset_id = event.get("asset_id", "")
            market_id = event.get("market", "")

            bids = [
                BookLevel(
                    price=Decimal(str(level.get("price", 0))),
                    size=Decimal(str(level.get("size", 0))),
                )
                for level in event.get("bids", [])
            ]

            asks = [
                BookLevel(
                    price=Decimal(str(level.get("price", 0))),
                    size=Decimal(str(level.get("size", 0))),
                )
                for level in event.get("asks", [])
            ]

            # Sort: bids descending, asks ascending
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            snapshot = BookSnapshot(
                asset_id=asset_id,
                market_id=market_id,
                timestamp=datetime.utcnow(),
                bids=bids,
                asks=asks,
                hash=event.get("hash", ""),
            )

            # Cache locally
            self._orderbooks[asset_id] = snapshot

            # Notify callbacks
            for callback in self._book_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(snapshot)
                    else:
                        callback(snapshot)
                except Exception as e:
                    logger.error(f"Error in book callback: {e}")

        except Exception as e:
            logger.error(f"Error parsing book event: {e}")

    async def _handle_price_change_event(self, event: Dict[str, Any]):
        """Handle incremental price level update"""
        try:
            # Price change events may contain multiple changes
            changes = event.get("changes", [event])

            for change in changes:
                price_change = PriceChange(
                    asset_id=change.get("asset_id", ""),
                    market_id=change.get("market", ""),
                    side=change.get("side", ""),
                    price=Decimal(str(change.get("price", 0))),
                    size=Decimal(str(change.get("size", 0))),
                    best_bid=Decimal(str(change["best_bid"])) if change.get("best_bid") else None,
                    best_ask=Decimal(str(change["best_ask"])) if change.get("best_ask") else None,
                )

                # Update local orderbook cache
                self._apply_price_change(price_change)

                # Notify callbacks
                for callback in self._price_change_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(price_change)
                        else:
                            callback(price_change)
                    except Exception as e:
                        logger.error(f"Error in price change callback: {e}")

        except Exception as e:
            logger.error(f"Error parsing price change event: {e}")

    async def _handle_trade_event(self, event: Dict[str, Any]):
        """Handle last trade price notification"""
        try:
            trade = LastTradePrice(
                asset_id=event.get("asset_id", ""),
                market_id=event.get("market", ""),
                side=event.get("side", ""),
                price=Decimal(str(event.get("price", 0))),
                size=Decimal(str(event.get("size", 0))),
                timestamp=datetime.utcnow(),
                fee_rate_bps=int(event.get("fee_rate_bps", 0)),
            )

            # Notify callbacks
            for callback in self._trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(trade)
                    else:
                        callback(trade)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")

        except Exception as e:
            logger.error(f"Error parsing trade event: {e}")

    async def _handle_user_trade_event(self, event: Dict[str, Any]):
        """Handle user trade notification (fill)"""
        try:
            timestamp_str = event.get("last_update", "") or event.get("matchtime", "")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()

            trade = UserTrade(
                trade_id=event.get("id", ""),
                asset_id=event.get("asset_id", ""),
                market_id=event.get("market", ""),
                side=event.get("side", ""),
                price=Decimal(str(event.get("price", 0))),
                size=Decimal(str(event.get("size", 0))),
                status=event.get("status", ""),
                timestamp=timestamp,
                taker_order_id=event.get("taker_order_id", ""),
                maker_orders=event.get("maker_orders", []),
            )

            logger.info(
                f"[WS] Fill: {trade.side} {trade.size} @ {trade.price} "
                f"(status: {trade.status})"
            )

            # Notify callbacks
            for callback in self._user_trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(trade)
                    else:
                        callback(trade)
                except Exception as e:
                    logger.error(f"Error in user trade callback: {e}")

        except Exception as e:
            logger.error(f"Error parsing user trade event: {e}")

    async def _handle_user_order_event(self, event: Dict[str, Any]):
        """Handle user order update"""
        try:
            timestamp_str = event.get("timestamp", "")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()

            order = UserOrder(
                order_id=event.get("id", ""),
                asset_id=event.get("asset_id", ""),
                market_id=event.get("market", ""),
                side=event.get("side", ""),
                price=Decimal(str(event.get("price", 0))),
                original_size=Decimal(str(event.get("original_size", 0))),
                size_matched=Decimal(str(event.get("size_matched", 0))),
                event_type=event.get("type", ""),
                timestamp=timestamp,
            )

            logger.debug(
                f"[WS] Order {order.event_type}: {order.side} "
                f"{order.original_size} @ {order.price}"
            )

            # Notify callbacks
            for callback in self._user_order_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(order)
                    else:
                        callback(order)
                except Exception as e:
                    logger.error(f"Error in user order callback: {e}")

        except Exception as e:
            logger.error(f"Error parsing user order event: {e}")

    # ==================== Local Orderbook Management ====================

    def _apply_price_change(self, change: PriceChange):
        """Apply incremental update to local orderbook cache"""
        book = self._orderbooks.get(change.asset_id)
        if not book:
            return

        levels = book.bids if change.side == "BUY" else book.asks

        # Find and update/remove the level
        found = False
        for i, level in enumerate(levels):
            if level.price == change.price:
                if change.size == Decimal("0"):
                    # Remove level
                    levels.pop(i)
                else:
                    # Update size
                    level.size = change.size
                found = True
                break

        # Add new level if not found and size > 0
        if not found and change.size > Decimal("0"):
            levels.append(BookLevel(price=change.price, size=change.size))

            # Re-sort
            if change.side == "BUY":
                levels.sort(key=lambda x: x.price, reverse=True)
            else:
                levels.sort(key=lambda x: x.price)

        book.timestamp = datetime.utcnow()

    def get_orderbook(self, asset_id: str) -> Optional[BookSnapshot]:
        """Get cached orderbook for an asset"""
        return self._orderbooks.get(asset_id)

    def get_best_bid(self, asset_id: str) -> Optional[Decimal]:
        """Get best bid price for an asset"""
        book = self._orderbooks.get(asset_id)
        if book and book.bids:
            return book.bids[0].price
        return None

    def get_best_ask(self, asset_id: str) -> Optional[Decimal]:
        """Get best ask price for an asset"""
        book = self._orderbooks.get(asset_id)
        if book and book.asks:
            return book.asks[0].price
        return None

    def get_mid_price(self, asset_id: str) -> Optional[Decimal]:
        """Get mid price for an asset"""
        bid = self.get_best_bid(asset_id)
        ask = self.get_best_ask(asset_id)
        if bid and ask:
            return (bid + ask) / 2
        return None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        if self._market_ws is None:
            return False
        try:
            # websockets library uses 'open' property
            return self._market_ws.open
        except AttributeError:
            # Fallback for different websockets versions
            return self._running and self._market_ws is not None
