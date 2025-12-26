"""
Polymarket API Client Wrapper

Handles all communication with the Polymarket CLOB API.
Supports both REST polling and WebSocket real-time updates.
"""
import asyncio
import aiohttp
import logging
from typing import Optional, Dict, List, Any, Callable
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
import json
import hashlib
import hmac
import time
import os

logger = logging.getLogger(__name__)

# Import WebSocket client (optional - graceful fallback if not available)
try:
    from .websocket import (
        PolymarketWebSocket,
        BookSnapshot,
        PriceChange,
        LastTradePrice,
        UserTrade,
        UserOrder,
    )
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    PolymarketWebSocket = None

# Import realistic paper trading simulator
try:
    from .paper_simulator import PaperTradingSimulator, QueuedOrder, SimulatedTrade
    SIMULATOR_AVAILABLE = True
except ImportError:
    SIMULATOR_AVAILABLE = False
    PaperTradingSimulator = None


@dataclass
class OrderBook:
    """Represents an order book snapshot"""
    token_id: str
    timestamp: datetime
    bids: List[Dict[str, Decimal]]  # [{"price": Decimal, "size": Decimal}, ...]
    asks: List[Dict[str, Decimal]]
    market_id: str = ""
    
    @property
    def best_bid(self) -> Optional[Decimal]:
        """Get best bid price"""
        if self.bids:
            return self.bids[0]["price"]
        return None
    
    @property
    def best_ask(self) -> Optional[Decimal]:
        """Get best ask price"""
        if self.asks:
            return self.asks[0]["price"]
        return None
    
    @property
    def mid_price(self) -> Optional[Decimal]:
        """Calculate mid price"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None
    
    @property
    def spread(self) -> Optional[Decimal]:
        """Calculate spread"""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None
    
    def weighted_mid(self, depth: int = 3) -> Optional[Decimal]:
        """Calculate volume-weighted mid price"""
        if not self.bids or not self.asks:
            return None
            
        bid_value = sum(
            b["price"] * b["size"] 
            for b in self.bids[:depth]
        )
        bid_size = sum(b["size"] for b in self.bids[:depth])
        
        ask_value = sum(
            a["price"] * a["size"] 
            for a in self.asks[:depth]
        )
        ask_size = sum(a["size"] for a in self.asks[:depth])
        
        if bid_size == 0 or ask_size == 0:
            return self.mid_price
            
        weighted_bid = bid_value / bid_size
        weighted_ask = ask_value / ask_size
        
        return (weighted_bid + weighted_ask) / 2


@dataclass
class Market:
    """Represents a Polymarket market"""
    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str
    end_date: Optional[datetime]
    active: bool
    volume: Decimal
    liquidity: Decimal
    
    @property
    def is_near_expiry(self) -> bool:
        """Check if market is within 24 hours of expiry"""
        if not self.end_date:
            return False
        hours_remaining = (self.end_date - datetime.utcnow()).total_seconds() / 3600
        return hours_remaining < 24


@dataclass
class Order:
    """Represents an order"""
    order_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: Decimal
    size: Decimal
    size_matched: Decimal
    status: str  # "LIVE", "MATCHED", "CANCELLED"
    created_at: datetime
    order_type: str = "GTC"  # GTC, FOK, FAK


@dataclass
class Trade:
    """Represents a trade/fill"""
    trade_id: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    fee: Decimal
    timestamp: datetime
    order_id: str


class PolymarketClient:
    """
    Async client for Polymarket CLOB API

    Supports both paper trading (simulation) and live trading.
    Supports both REST polling and WebSocket real-time updates.
    """

    BASE_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        funder_address: str = "",
        paper_trading: bool = True,
        use_websocket: bool = False,
        realistic_simulation: bool = True,  # Use advanced paper trading simulator
    ):
        self.private_key = private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.funder_address = funder_address
        self.paper_trading = paper_trading
        self.use_websocket = use_websocket and WEBSOCKET_AVAILABLE
        self.realistic_simulation = realistic_simulation and SIMULATOR_AVAILABLE

        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket client
        self._ws: Optional[PolymarketWebSocket] = None
        self._ws_orderbooks: Dict[str, OrderBook] = {}  # Cached from WebSocket
        self._ws_connected = False

        # WebSocket callbacks (set by bot)
        self._on_orderbook_update: Optional[Callable[[OrderBook], None]] = None
        self._on_trade: Optional[Callable[[Trade], None]] = None
        self._on_fill: Optional[Callable[[Trade], None]] = None

        # Realistic paper trading simulator
        self._simulator: Optional[PaperTradingSimulator] = None
        if self.paper_trading and self.realistic_simulation:
            self._simulator = PaperTradingSimulator(
                starting_balance=Decimal("1000.0"),
                enable_latency=True,
                enable_adverse_selection=True,
                enable_partial_fills=True,
            )
            logger.info("[CLIENT] Using realistic paper trading simulator")

        # Simple paper trading state (fallback if simulator not available)
        self._paper_orders: Dict[str, Order] = {}
        self._paper_trades: List[Trade] = []
        self._paper_balance: Decimal = Decimal("1000.0")  # Starting balance
        self._paper_positions: Dict[str, int] = {}  # token_id -> shares
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        """Close the client session and WebSocket"""
        # Stop simulator
        if self._simulator:
            await self._simulator.stop()

        if self._ws:
            await self._ws.disconnect()
            self._ws = None
            self._ws_connected = False

        if self._session and not self._session.closed:
            await self._session.close()

    async def start_paper_simulator(self):
        """Start the paper trading simulator (call after setting callbacks)"""
        if self._simulator:
            # Wire up fill callback
            if self._on_fill:
                self._simulator.set_fill_callback(self._on_fill)
            await self._simulator.start()

    # ==================== WebSocket Methods ====================

    async def connect_websocket(
        self,
        assets: List[str],
        markets: Optional[List[str]] = None,
    ):
        """
        Connect to WebSocket for real-time updates.

        Args:
            assets: List of token/asset IDs to subscribe to
            markets: List of market condition IDs (for user channel)
        """
        if not WEBSOCKET_AVAILABLE:
            logger.warning("WebSocket not available - websockets library not installed")
            return

        if self._ws_connected:
            logger.warning("WebSocket already connected")
            return

        self._ws = PolymarketWebSocket(
            api_key=self.api_key,
            api_secret=self.api_secret,
            passphrase=self.passphrase,
        )

        # Register internal handlers
        self._ws.on_book(self._handle_ws_book)
        self._ws.on_price_change(self._handle_ws_price_change)
        self._ws.on_trade(self._handle_ws_trade)
        self._ws.on_user_trade(self._handle_ws_user_trade)
        self._ws.on_user_order(self._handle_ws_user_order)

        await self._ws.connect(assets, markets)
        self._ws_connected = True
        logger.info(f"WebSocket connected for {len(assets)} assets")

    async def disconnect_websocket(self):
        """Disconnect from WebSocket"""
        if self._ws:
            await self._ws.disconnect()
            self._ws = None
            self._ws_connected = False
            logger.info("WebSocket disconnected")

    async def subscribe_assets(self, assets: List[str]):
        """Subscribe to additional assets via WebSocket"""
        if self._ws and self._ws_connected:
            await self._ws.subscribe(assets)

    async def unsubscribe_assets(self, assets: List[str]):
        """Unsubscribe from assets via WebSocket"""
        if self._ws and self._ws_connected:
            await self._ws.unsubscribe(assets)

    def set_orderbook_callback(self, callback: Callable[[OrderBook], None]):
        """Set callback for orderbook updates (from WebSocket)"""
        self._on_orderbook_update = callback

    def set_trade_callback(self, callback: Callable[[Trade], None]):
        """Set callback for trade notifications (market trades)"""
        self._on_trade = callback

    def set_fill_callback(self, callback: Callable[[Trade], None]):
        """Set callback for fill notifications (our orders filled)"""
        self._on_fill = callback

    def _handle_ws_book(self, snapshot: "BookSnapshot"):
        """Handle orderbook snapshot from WebSocket"""
        # Convert to OrderBook format
        bids = [
            {"price": level.price, "size": level.size}
            for level in snapshot.bids
        ]
        asks = [
            {"price": level.price, "size": level.size}
            for level in snapshot.asks
        ]

        orderbook = OrderBook(
            token_id=snapshot.asset_id,
            timestamp=snapshot.timestamp,
            bids=bids,
            asks=asks,
            market_id=snapshot.market_id,
        )

        self._ws_orderbooks[snapshot.asset_id] = orderbook

        # Feed simulator with orderbook data for queue position calculation
        if self._simulator:
            self._simulator.update_orderbook(snapshot.asset_id, bids, asks)

        if self._on_orderbook_update:
            try:
                if asyncio.iscoroutinefunction(self._on_orderbook_update):
                    asyncio.create_task(self._on_orderbook_update(orderbook))
                else:
                    self._on_orderbook_update(orderbook)
            except Exception as e:
                logger.error(f"Error in orderbook callback: {e}")

    def _handle_ws_price_change(self, change: "PriceChange"):
        """Handle incremental price update from WebSocket"""
        book = self._ws_orderbooks.get(change.asset_id)
        if not book:
            return

        # Update the appropriate side
        levels = book.bids if change.side == "BUY" else book.asks

        # Find and update/remove the level
        found = False
        for i, level in enumerate(levels):
            if level["price"] == change.price:
                if change.size == Decimal("0"):
                    levels.pop(i)
                else:
                    level["size"] = change.size
                found = True
                break

        # Add new level if not found and size > 0
        if not found and change.size > Decimal("0"):
            levels.append({"price": change.price, "size": change.size})
            # Re-sort
            if change.side == "BUY":
                levels.sort(key=lambda x: x["price"], reverse=True)
            else:
                levels.sort(key=lambda x: x["price"])

        book.timestamp = datetime.utcnow()

        # Update simulator with new book state
        if self._simulator:
            self._simulator.update_orderbook(
                change.asset_id,
                book.bids,
                book.asks
            )

        # Notify callback
        if self._on_orderbook_update:
            try:
                if asyncio.iscoroutinefunction(self._on_orderbook_update):
                    asyncio.create_task(self._on_orderbook_update(book))
                else:
                    self._on_orderbook_update(book)
            except Exception as e:
                logger.error(f"Error in orderbook callback: {e}")

    def _handle_ws_trade(self, trade: "LastTradePrice"):
        """Handle market trade from WebSocket"""
        trade_obj = Trade(
            trade_id=f"ws_{trade.asset_id}_{int(time.time()*1000)}",
            token_id=trade.asset_id,
            side=trade.side,
            price=trade.price,
            size=trade.size,
            fee=Decimal("0"),
            timestamp=trade.timestamp,
            order_id="",
        )

        # Record market trade for simulator volume estimation
        if self._simulator:
            self._simulator.record_market_trade(trade.asset_id, trade.size)

        if self._on_trade:
            try:
                if asyncio.iscoroutinefunction(self._on_trade):
                    asyncio.create_task(self._on_trade(trade_obj))
                else:
                    self._on_trade(trade_obj)
            except Exception as e:
                logger.error(f"Error in trade callback: {e}")

    def _handle_ws_user_trade(self, user_trade: "UserTrade"):
        """Handle user fill notification from WebSocket"""
        trade = Trade(
            trade_id=user_trade.trade_id,
            token_id=user_trade.asset_id,
            side=user_trade.side,
            price=user_trade.price,
            size=user_trade.size,
            fee=Decimal("0"),
            timestamp=user_trade.timestamp,
            order_id=user_trade.taker_order_id,
        )

        # For paper trading, record the fill
        if self.paper_trading:
            self._paper_trades.append(trade)

            # Update positions
            if trade.side == "BUY":
                self._paper_positions[trade.token_id] = (
                    self._paper_positions.get(trade.token_id, 0) + int(trade.size)
                )
                self._paper_balance -= trade.price * trade.size
            else:
                self._paper_positions[trade.token_id] = (
                    self._paper_positions.get(trade.token_id, 0) - int(trade.size)
                )
                self._paper_balance += trade.price * trade.size

        if self._on_fill:
            try:
                if asyncio.iscoroutinefunction(self._on_fill):
                    asyncio.create_task(self._on_fill(trade))
                else:
                    self._on_fill(trade)
            except Exception as e:
                logger.error(f"Error in fill callback: {e}")

    def _handle_ws_user_order(self, order: "UserOrder"):
        """Handle user order update from WebSocket"""
        # Update paper trading order state if applicable
        if self.paper_trading and order.order_id in self._paper_orders:
            paper_order = self._paper_orders[order.order_id]
            paper_order.size_matched = order.size_matched

            if order.event_type == "CANCELLATION":
                paper_order.status = "CANCELLED"
            elif order.size_matched >= order.original_size:
                paper_order.status = "MATCHED"

    def get_ws_orderbook(self, token_id: str) -> Optional[OrderBook]:
        """Get cached orderbook from WebSocket (if available)"""
        return self._ws_orderbooks.get(token_id)

    @property
    def websocket_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._ws_connected and self._ws is not None and self._ws.is_connected

    def _generate_l1_headers(self) -> Dict[str, str]:
        """Generate L1 authentication headers (for basic auth)"""
        timestamp = str(int(time.time() * 1000))
        return {
            "POLY_ADDRESS": self.funder_address,
            "POLY_TIMESTAMP": timestamp,
        }
    
    def _generate_l2_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Generate L2 authentication headers (for trading)"""
        timestamp = str(int(time.time() * 1000))
        
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "POLY_API_KEY": self.api_key,
            "POLY_TIMESTAMP": timestamp,
            "POLY_SIGNATURE": signature,
            "POLY_PASSPHRASE": self.passphrase,
        }
    
    # ==================== Market Data ====================
    
    async def get_markets(self, active_only: bool = True) -> List[Market]:
        """
        Fetch available markets.

        Uses the CLOB sampling-markets endpoint for active markets with orderbooks.
        Falls back to Gamma API for historical/complete market data.
        """
        session = await self._get_session()

        # Try CLOB sampling-markets first (has active markets with orderbooks)
        try:
            async with session.get(f"{self.BASE_URL}/sampling-markets") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    clob_markets = data.get("data", [])

                    markets = []
                    for m in clob_markets:
                        if active_only and not m.get("accepting_orders"):
                            continue

                        try:
                            tokens = m.get("tokens", [])
                            # Find Yes/No tokens or use first two
                            yes_token = next(
                                (t for t in tokens if t.get("outcome", "").lower() == "yes"),
                                tokens[0] if tokens else {}
                            )
                            no_token = next(
                                (t for t in tokens if t.get("outcome", "").lower() == "no"),
                                tokens[1] if len(tokens) > 1 else {}
                            )

                            end_date = None
                            if m.get("end_date_iso"):
                                try:
                                    end_date = datetime.fromisoformat(
                                        m["end_date_iso"].replace("Z", "+00:00")
                                    )
                                except:
                                    pass

                            market = Market(
                                condition_id=m.get("condition_id", ""),
                                question=m.get("question", ""),
                                slug=m.get("market_slug", ""),
                                yes_token_id=yes_token.get("token_id", ""),
                                no_token_id=no_token.get("token_id", ""),
                                end_date=end_date,
                                active=m.get("active", False),
                                volume=Decimal("0"),  # Not available in this endpoint
                                liquidity=Decimal("0"),
                            )
                            markets.append(market)
                        except Exception as e:
                            logger.warning(f"Failed to parse CLOB market: {e}")
                            continue

                    if markets:
                        return markets

        except Exception as e:
            logger.warning(f"CLOB sampling-markets failed: {e}, trying Gamma API")

        # Fallback to Gamma API
        params = {"active": "true"} if active_only else {}

        try:
            async with session.get(
                f"{self.GAMMA_URL}/markets",
                params=params
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch markets: {resp.status}")
                    return []

                data = await resp.json()
                markets = []

                for m in data:
                    try:
                        tokens = m.get("tokens", [])
                        yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), {})
                        no_token = next((t for t in tokens if t.get("outcome") == "No"), {})

                        market = Market(
                            condition_id=m.get("conditionId", ""),
                            question=m.get("question", ""),
                            slug=m.get("slug", ""),
                            yes_token_id=yes_token.get("token_id", ""),
                            no_token_id=no_token.get("token_id", ""),
                            end_date=datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")) if m.get("endDate") else None,
                            active=m.get("active", False),
                            volume=Decimal(str(m.get("volume", 0))),
                            liquidity=Decimal(str(m.get("liquidity", 0))),
                        )
                        markets.append(market)
                    except Exception as e:
                        logger.warning(f"Failed to parse market: {e}")
                        continue

                return markets

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []
    
    async def get_orderbook(self, token_id: str, force_rest: bool = False) -> Optional[OrderBook]:
        """
        Fetch order book for a specific token.

        When WebSocket is connected, returns cached data from WebSocket.
        Falls back to REST API if WebSocket not available or force_rest=True.
        """
        # Use WebSocket cache if available and not forcing REST
        if not force_rest and self._ws_connected:
            ws_book = self._ws_orderbooks.get(token_id)
            if ws_book:
                return ws_book

        # Fall back to REST API
        session = await self._get_session()

        try:
            async with session.get(
                f"{self.BASE_URL}/book",
                params={"token_id": token_id}
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch orderbook: {resp.status}")
                    return None

                data = await resp.json()

                bids = [
                    {"price": Decimal(str(b["price"])), "size": Decimal(str(b["size"]))}
                    for b in data.get("bids", [])
                ]
                asks = [
                    {"price": Decimal(str(a["price"])), "size": Decimal(str(a["size"]))}
                    for a in data.get("asks", [])
                ]

                orderbook = OrderBook(
                    token_id=token_id,
                    timestamp=datetime.utcnow(),
                    bids=sorted(bids, key=lambda x: x["price"], reverse=True),
                    asks=sorted(asks, key=lambda x: x["price"]),
                    market_id=data.get("market", ""),
                )

                # Feed simulator with orderbook data
                if self._simulator:
                    self._simulator.update_orderbook(token_id, orderbook.bids, orderbook.asks)

                return orderbook

        except Exception as e:
            logger.error(f"Error fetching orderbook: {e}")
            return None
    
    async def get_price(self, token_id: str, side: str = "BUY") -> Optional[Decimal]:
        """Get current price for a token"""
        session = await self._get_session()
        
        try:
            async with session.get(
                f"{self.BASE_URL}/price",
                params={"token_id": token_id, "side": side}
            ) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                return Decimal(str(data.get("price", 0)))
                
        except Exception as e:
            logger.error(f"Error fetching price: {e}")
            return None
    
    # ==================== Order Management ====================
    
    async def place_order(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        size: Decimal,
        order_type: str = "GTC",
    ) -> Optional[Order]:
        """
        Place an order
        
        Args:
            token_id: The token to trade
            side: "BUY" or "SELL"
            price: Limit price (0.01 to 0.99)
            size: Number of shares
            order_type: GTC, FOK, or FAK
        """
        if self.paper_trading:
            # Use realistic simulator if available
            if self._simulator:
                order = await self._simulator.place_order(token_id, side, price, size, order_type)
                if order:
                    # Convert to standard Order format
                    return Order(
                        order_id=order.order_id,
                        token_id=order.token_id,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        size_matched=order.size_matched,
                        status=order.status,
                        created_at=order.created_at,
                        order_type=order.order_type,
                    )
                return None
            return await self._paper_place_order(token_id, side, price, size, order_type)
        
        # Live trading
        session = await self._get_session()
        
        order_data = {
            "tokenID": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "type": order_type,
        }
        
        body = json.dumps(order_data)
        headers = self._generate_l2_headers("POST", "/order", body)
        headers["Content-Type"] = "application/json"
        
        try:
            async with session.post(
                f"{self.BASE_URL}/order",
                headers=headers,
                data=body,
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Failed to place order: {error}")
                    return None
                
                data = await resp.json()
                
                return Order(
                    order_id=data.get("orderID", ""),
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    size_matched=Decimal("0"),
                    status="LIVE",
                    created_at=datetime.utcnow(),
                    order_type=order_type,
                )
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if self.paper_trading:
            if self._simulator:
                return await self._simulator.cancel_order(order_id)
            return await self._paper_cancel_order(order_id)
        
        session = await self._get_session()
        
        body = json.dumps({"orderID": order_id})
        headers = self._generate_l2_headers("DELETE", "/order", body)
        headers["Content-Type"] = "application/json"
        
        try:
            async with session.delete(
                f"{self.BASE_URL}/order",
                headers=headers,
                data=body,
            ) as resp:
                return resp.status == 200
                
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
    
    async def cancel_all_orders(self, token_id: Optional[str] = None) -> int:
        """Cancel all orders, optionally filtered by token"""
        if self.paper_trading:
            if self._simulator:
                return await self._simulator.cancel_all_orders(token_id)
            return await self._paper_cancel_all_orders(token_id)
        
        session = await self._get_session()
        
        params = {}
        if token_id:
            params["token_id"] = token_id
        
        headers = self._generate_l2_headers("DELETE", "/orders")
        
        try:
            async with session.delete(
                f"{self.BASE_URL}/orders",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("cancelled", 0)
                return 0
                
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
            return 0
    
    async def get_orders(
        self,
        token_id: Optional[str] = None,
        status: str = "LIVE"
    ) -> List[Order]:
        """Get orders for the authenticated user"""
        if self.paper_trading:
            if self._simulator:
                sim_orders = self._simulator.get_orders(token_id, status)
                return [
                    Order(
                        order_id=o.order_id,
                        token_id=o.token_id,
                        side=o.side,
                        price=o.price,
                        size=o.size,
                        size_matched=o.size_matched,
                        status=o.status,
                        created_at=o.created_at,
                        order_type=o.order_type,
                    )
                    for o in sim_orders
                ]
            return await self._paper_get_orders(token_id, status)
        
        session = await self._get_session()
        
        params = {"state": status}
        if token_id:
            params["asset_id"] = token_id
        
        headers = self._generate_l2_headers("GET", "/orders")
        
        try:
            async with session.get(
                f"{self.BASE_URL}/orders",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                orders = []
                
                for o in data:
                    orders.append(Order(
                        order_id=o.get("id", ""),
                        token_id=o.get("asset_id", ""),
                        side=o.get("side", ""),
                        price=Decimal(str(o.get("price", 0))),
                        size=Decimal(str(o.get("original_size", 0))),
                        size_matched=Decimal(str(o.get("size_matched", 0))),
                        status=o.get("status", ""),
                        created_at=datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00")),
                        order_type=o.get("type", "GTC"),
                    ))
                
                return orders
                
        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            return []
    
    async def get_trades(
        self,
        token_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Trade]:
        """Get trade history"""
        if self.paper_trading:
            if self._simulator:
                sim_trades = self._simulator.get_trades(limit)
                return [
                    Trade(
                        trade_id=t.trade_id,
                        token_id=t.token_id,
                        side=t.side,
                        price=t.price,
                        size=t.size,
                        fee=t.fee,
                        timestamp=t.timestamp,
                        order_id=t.order_id,
                    )
                    for t in sim_trades
                ]
            return self._paper_trades[-limit:]
        
        session = await self._get_session()
        
        params = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        
        headers = self._generate_l2_headers("GET", "/trades")
        
        try:
            async with session.get(
                f"{self.BASE_URL}/trades",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                trades = []
                
                for t in data:
                    trades.append(Trade(
                        trade_id=t.get("id", ""),
                        token_id=t.get("asset_id", ""),
                        side=t.get("side", ""),
                        price=Decimal(str(t.get("price", 0))),
                        size=Decimal(str(t.get("size", 0))),
                        fee=Decimal(str(t.get("fee", 0))),
                        timestamp=datetime.fromisoformat(t.get("created_at", "").replace("Z", "+00:00")),
                        order_id=t.get("order_id", ""),
                    ))
                
                return trades
                
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []
    
    # ==================== Paper Trading ====================
    
    async def _paper_place_order(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        size: Decimal,
        order_type: str,
    ) -> Optional[Order]:
        """Simulate order placement"""
        import uuid
        
        order_id = f"paper_{uuid.uuid4().hex[:16]}"
        
        order = Order(
            order_id=order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            size_matched=Decimal("0"),
            status="LIVE",
            created_at=datetime.utcnow(),
            order_type=order_type,
        )
        
        self._paper_orders[order_id] = order
        logger.info(f"[PAPER] Placed {side} order: {size} @ {price}")
        
        # Simulate potential immediate fill
        await self._paper_check_fills(order)
        
        return order
    
    async def _paper_cancel_order(self, order_id: str) -> bool:
        """Simulate order cancellation"""
        if order_id in self._paper_orders:
            self._paper_orders[order_id].status = "CANCELLED"
            logger.info(f"[PAPER] Cancelled order {order_id}")
            return True
        return False
    
    async def _paper_cancel_all_orders(self, token_id: Optional[str] = None) -> int:
        """Simulate cancelling all orders"""
        count = 0
        for order in self._paper_orders.values():
            if order.status == "LIVE":
                if token_id is None or order.token_id == token_id:
                    order.status = "CANCELLED"
                    count += 1
        logger.info(f"[PAPER] Cancelled {count} orders")
        return count
    
    async def _paper_get_orders(
        self,
        token_id: Optional[str] = None,
        status: str = "LIVE"
    ) -> List[Order]:
        """Get paper trading orders"""
        orders = []
        for order in self._paper_orders.values():
            if order.status == status:
                if token_id is None or order.token_id == token_id:
                    orders.append(order)
        return orders
    
    async def _paper_check_fills(self, order: Order):
        """
        Simulate order fills based on current market data.
        In paper trading, we assume some random fill probability.
        """
        import random
        
        # Get current orderbook
        orderbook = await self.get_orderbook(order.token_id)
        if not orderbook:
            return
        
        # Check if order would fill
        filled = False
        
        if order.side == "BUY":
            if orderbook.best_ask and order.price >= orderbook.best_ask:
                filled = True
                fill_price = orderbook.best_ask
        else:
            if orderbook.best_bid and order.price <= orderbook.best_bid:
                filled = True
                fill_price = orderbook.best_bid
        
        # Simulate partial fill with some probability
        if filled or random.random() < 0.1:  # 10% chance of fill per check
            fill_price = fill_price if filled else order.price
            fill_size = order.size - order.size_matched
            
            # Create trade record
            trade = Trade(
                trade_id=f"paper_trade_{len(self._paper_trades)}",
                token_id=order.token_id,
                side=order.side,
                price=fill_price,
                size=fill_size,
                fee=Decimal("0"),  # No fees in paper trading
                timestamp=datetime.utcnow(),
                order_id=order.order_id,
            )
            
            self._paper_trades.append(trade)
            order.size_matched = order.size
            order.status = "MATCHED"
            
            # Update paper positions
            if order.side == "BUY":
                self._paper_positions[order.token_id] = (
                    self._paper_positions.get(order.token_id, 0) + int(fill_size)
                )
                self._paper_balance -= fill_price * fill_size
            else:
                self._paper_positions[order.token_id] = (
                    self._paper_positions.get(order.token_id, 0) - int(fill_size)
                )
                self._paper_balance += fill_price * fill_size
            
            logger.info(f"[PAPER] Fill: {order.side} {fill_size} @ {fill_price}")

            # Call the fill callback if registered
            if self._on_fill:
                try:
                    if asyncio.iscoroutinefunction(self._on_fill):
                        asyncio.create_task(self._on_fill(trade))
                    else:
                        self._on_fill(trade)
                except Exception as e:
                    logger.error(f"Error in paper fill callback: {e}")

    def get_paper_balance(self) -> Decimal:
        """Get paper trading balance"""
        if self._simulator:
            return self._simulator.get_balance()
        return self._paper_balance

    def get_paper_positions(self) -> Dict[str, int]:
        """Get paper trading positions"""
        if self._simulator:
            positions = self._simulator.get_all_positions()
            return {k: int(v) for k, v in positions.items()}
        return self._paper_positions.copy()

    def get_simulation_stats(self) -> Optional[Dict]:
        """Get detailed simulation statistics (only available with realistic simulator)"""
        if self._simulator:
            return self._simulator.get_stats()
        return None
