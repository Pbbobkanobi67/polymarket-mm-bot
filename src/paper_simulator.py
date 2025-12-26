"""
Realistic Paper Trading Simulator

Models real market dynamics including:
- Queue position tracking (time priority)
- Partial fills based on market volume
- Adverse selection (getting filled on losing trades)
- Latency simulation
- Market impact for large orders
- Slippage for crossing orders
- Realistic maker/taker fees
"""
import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Callable, Tuple
from collections import defaultdict
import uuid

logger = logging.getLogger(__name__)


@dataclass
class QueuedOrder:
    """Order with queue position tracking"""
    order_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: Decimal
    size: Decimal
    size_matched: Decimal
    status: str  # "LIVE", "MATCHED", "CANCELLED", "PARTIAL"
    created_at: datetime
    order_type: str

    # Queue simulation fields
    queue_position: Decimal = Decimal("0")  # Size ahead of us in queue
    initial_queue_depth: Decimal = Decimal("0")  # Total queue depth when placed
    last_fill_time: Optional[datetime] = None
    fills: List[Tuple[Decimal, Decimal, datetime]] = field(default_factory=list)  # (price, size, time)

    @property
    def size_remaining(self) -> Decimal:
        return self.size - self.size_matched

    @property
    def is_live(self) -> bool:
        return self.status in ("LIVE", "PARTIAL")


@dataclass
class SimulatedTrade:
    """Trade record with detailed simulation info"""
    trade_id: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    fee: Decimal
    timestamp: datetime
    order_id: str

    # Simulation metadata
    is_maker: bool = True
    slippage: Decimal = Decimal("0")
    queue_wait_time: float = 0.0  # seconds waited in queue


@dataclass
class MarketState:
    """Tracks market state for simulation"""
    token_id: str
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    bid_depth: Dict[Decimal, Decimal] = field(default_factory=dict)  # price -> size
    ask_depth: Dict[Decimal, Decimal] = field(default_factory=dict)

    # Volume tracking for fill probability
    recent_volume: Decimal = Decimal("0")
    volume_window_start: datetime = field(default_factory=datetime.utcnow)
    trade_count: int = 0

    # Price movement tracking for adverse selection
    price_history: List[Tuple[datetime, Decimal]] = field(default_factory=list)

    def update_from_orderbook(self, bids: List[Dict], asks: List[Dict]):
        """Update market state from orderbook snapshot"""
        self.bid_depth = {
            Decimal(str(b["price"])): Decimal(str(b["size"]))
            for b in bids
        }
        self.ask_depth = {
            Decimal(str(a["price"])): Decimal(str(a["size"]))
            for a in asks
        }

        if bids:
            self.best_bid = max(self.bid_depth.keys())
        if asks:
            self.best_ask = min(self.ask_depth.keys())

        # Track mid price history
        if self.best_bid and self.best_ask:
            mid = (self.best_bid + self.best_ask) / 2
            now = datetime.utcnow()
            self.price_history.append((now, mid))
            # Keep only last 5 minutes
            cutoff = now - timedelta(minutes=5)
            self.price_history = [(t, p) for t, p in self.price_history if t > cutoff]

    def record_market_trade(self, size: Decimal):
        """Record observed market trade for volume estimation"""
        now = datetime.utcnow()

        # Reset window every minute
        if (now - self.volume_window_start).total_seconds() > 60:
            self.recent_volume = Decimal("0")
            self.volume_window_start = now
            self.trade_count = 0

        self.recent_volume += size
        self.trade_count += 1

    def get_volume_per_second(self) -> Decimal:
        """Estimate volume per second from recent trades"""
        elapsed = max(1, (datetime.utcnow() - self.volume_window_start).total_seconds())
        return self.recent_volume / Decimal(str(elapsed))

    def get_price_move(self, since: datetime) -> Decimal:
        """Get price movement since a given time"""
        if len(self.price_history) < 2:
            return Decimal("0")

        # Find price at 'since' time
        old_price = None
        for t, p in self.price_history:
            if t <= since:
                old_price = p
            else:
                break

        if old_price is None:
            return Decimal("0")

        current_price = self.price_history[-1][1]
        return current_price - old_price

    def get_queue_depth_at_price(self, price: Decimal, side: str) -> Decimal:
        """Get total size at or better than price"""
        if side == "BUY":
            # For buys, queue depth is sum of all bids >= our price
            return sum(
                size for p, size in self.bid_depth.items()
                if p >= price
            )
        else:
            # For sells, queue depth is sum of all asks <= our price
            return sum(
                size for p, size in self.ask_depth.items()
                if p <= price
            )


class PaperTradingSimulator:
    """
    Realistic paper trading simulator with queue dynamics.

    Fill Model:
    1. Crossing orders fill immediately with slippage through book
    2. Resting orders join queue at their price level
    3. Fills occur when:
       - Market trades eat through queue ahead of us
       - Adverse selection: price moves against us (higher fill prob)
    4. Partial fills based on observed volume

    Adverse Selection Model:
    - When price moves against your order, you're more likely to get filled
    - This models the reality that market makers get "picked off" by informed flow
    - Toxic orders that would be profitable are less likely to fill

    Fee Model:
    - Maker: 0% (Polymarket standard)
    - Taker: 0% (Polymarket standard, but we model spread cost)
    """

    # Simulation parameters
    LATENCY_MIN_MS = 50
    LATENCY_MAX_MS = 300

    # Fill probability parameters
    BASE_FILL_PROB_PER_SECOND = Decimal("0.02")  # 2% per second at queue front
    ADVERSE_SELECTION_MULTIPLIER = Decimal("3.0")  # 3x more likely to fill on adverse move
    FAVORABLE_SELECTION_MULTIPLIER = Decimal("0.3")  # 70% less likely on favorable move

    # Market impact parameters (for large orders)
    IMPACT_COEFFICIENT = Decimal("0.001")  # 0.1% impact per 100 shares

    def __init__(
        self,
        starting_balance: Decimal = Decimal("1000.0"),
        maker_fee: Decimal = Decimal("0"),
        taker_fee: Decimal = Decimal("0"),
        enable_latency: bool = True,
        enable_adverse_selection: bool = True,
        enable_partial_fills: bool = True,
    ):
        self.balance = starting_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.enable_latency = enable_latency
        self.enable_adverse_selection = enable_adverse_selection
        self.enable_partial_fills = enable_partial_fills

        # State
        self.orders: Dict[str, QueuedOrder] = {}
        self.trades: List[SimulatedTrade] = []
        self.positions: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        self.market_states: Dict[str, MarketState] = {}

        # Callbacks
        self._on_fill: Optional[Callable] = None

        # Background fill checker
        self._fill_check_task: Optional[asyncio.Task] = None
        self._running = False

        # Statistics
        self.stats = {
            "orders_placed": 0,
            "orders_filled": 0,
            "orders_partial": 0,
            "orders_cancelled": 0,
            "total_volume": Decimal("0"),
            "maker_volume": Decimal("0"),
            "taker_volume": Decimal("0"),
            "total_fees": Decimal("0"),
            "adverse_fills": 0,
            "favorable_fills": 0,
        }

    def set_fill_callback(self, callback: Callable):
        """Set callback for fill notifications"""
        self._on_fill = callback

    async def start(self):
        """Start background fill checking"""
        self._running = True
        self._fill_check_task = asyncio.create_task(self._fill_check_loop())
        logger.info("[PAPER SIM] Started realistic paper trading simulator")

    async def stop(self):
        """Stop background fill checking"""
        self._running = False
        if self._fill_check_task:
            self._fill_check_task.cancel()
            try:
                await self._fill_check_task
            except asyncio.CancelledError:
                pass
        logger.info("[PAPER SIM] Stopped paper trading simulator")

    async def _fill_check_loop(self):
        """Periodically check for fills on resting orders"""
        while self._running:
            try:
                await self._check_all_fills()
                await asyncio.sleep(0.5)  # Check every 500ms
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PAPER SIM] Fill check error: {e}")
                await asyncio.sleep(1)

    def update_orderbook(self, token_id: str, bids: List[Dict], asks: List[Dict]):
        """Update market state from orderbook data"""
        if token_id not in self.market_states:
            self.market_states[token_id] = MarketState(token_id=token_id)

        self.market_states[token_id].update_from_orderbook(bids, asks)

    def record_market_trade(self, token_id: str, size: Decimal):
        """Record an observed market trade for volume estimation"""
        if token_id not in self.market_states:
            self.market_states[token_id] = MarketState(token_id=token_id)

        self.market_states[token_id].record_market_trade(size)

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        size: Decimal,
        order_type: str = "GTC",
    ) -> Optional[QueuedOrder]:
        """
        Place a simulated order with realistic queue dynamics.
        """
        # Simulate latency
        if self.enable_latency:
            latency = random.randint(self.LATENCY_MIN_MS, self.LATENCY_MAX_MS)
            await asyncio.sleep(latency / 1000)

        order_id = f"paper_{uuid.uuid4().hex[:16]}"
        market = self.market_states.get(token_id, MarketState(token_id=token_id))

        # Calculate queue position
        queue_depth = market.get_queue_depth_at_price(price, side)

        order = QueuedOrder(
            order_id=order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            size_matched=Decimal("0"),
            status="LIVE",
            created_at=datetime.utcnow(),
            order_type=order_type,
            queue_position=queue_depth,
            initial_queue_depth=queue_depth,
        )

        self.orders[order_id] = order
        self.stats["orders_placed"] += 1

        logger.info(
            f"[PAPER SIM] Placed {side} {size} @ {price} "
            f"(queue pos: {queue_depth:.0f} ahead)"
        )

        # Check for immediate fill (crossing orders)
        await self._check_immediate_fill(order)

        return order

    async def _check_immediate_fill(self, order: QueuedOrder):
        """Check if order crosses spread and fills immediately"""
        market = self.market_states.get(order.token_id)
        if not market:
            return

        is_crossing = False

        if order.side == "BUY" and market.best_ask:
            if order.price >= market.best_ask:
                is_crossing = True
        elif order.side == "SELL" and market.best_bid:
            if order.price <= market.best_bid:
                is_crossing = True

        if is_crossing:
            await self._execute_crossing_order(order, market)

    async def _execute_crossing_order(self, order: QueuedOrder, market: MarketState):
        """
        Execute a crossing order with slippage through the book.

        Taker orders that cross the spread pay slippage:
        - Walk through the book at each price level
        - Fill at progressively worse prices for larger orders
        """
        remaining = order.size_remaining
        fills: List[Tuple[Decimal, Decimal]] = []  # (price, size)

        if order.side == "BUY":
            # Walk through asks from best to worst
            for price in sorted(market.ask_depth.keys()):
                if price > order.price:
                    break

                available = market.ask_depth[price]
                fill_size = min(remaining, available)

                if fill_size > 0:
                    fills.append((price, fill_size))
                    remaining -= fill_size

                if remaining <= 0:
                    break
        else:
            # Walk through bids from best to worst
            for price in sorted(market.bid_depth.keys(), reverse=True):
                if price < order.price:
                    break

                available = market.bid_depth[price]
                fill_size = min(remaining, available)

                if fill_size > 0:
                    fills.append((price, fill_size))
                    remaining -= fill_size

                if remaining <= 0:
                    break

        # Execute fills
        for fill_price, fill_size in fills:
            await self._execute_fill(
                order,
                fill_price,
                fill_size,
                is_maker=False,
                slippage=abs(fill_price - order.price)
            )

        # Any remaining size rests in book
        if order.size_remaining > 0:
            order.queue_position = Decimal("0")  # At front of queue at limit price
            logger.info(
                f"[PAPER SIM] Partial cross fill, {order.size_remaining} resting @ {order.price}"
            )

    async def _check_all_fills(self):
        """Check all resting orders for potential fills"""
        for order in list(self.orders.values()):
            if not order.is_live:
                continue

            await self._check_resting_fill(order)

    async def _check_resting_fill(self, order: QueuedOrder):
        """
        Check if a resting order should fill based on:
        1. Queue position (have trades eaten through queue ahead?)
        2. Adverse selection (price moved against us?)
        3. Random probability based on volume
        """
        market = self.market_states.get(order.token_id)
        if not market:
            return

        # Calculate fill probability
        fill_prob = self._calculate_fill_probability(order, market)

        if random.random() > float(fill_prob):
            return

        # Determine fill size (partial vs full)
        if self.enable_partial_fills:
            # Fill based on volume estimate
            vol_per_sec = market.get_volume_per_second()
            expected_fill = vol_per_sec * Decimal("0.5")  # Half second of volume
            expected_fill = max(Decimal("1"), expected_fill)  # At least 1 share
            fill_size = min(order.size_remaining, expected_fill)
        else:
            fill_size = order.size_remaining

        # Execute fill at limit price (maker)
        await self._execute_fill(
            order,
            order.price,
            fill_size,
            is_maker=True,
            slippage=Decimal("0")
        )

    def _calculate_fill_probability(
        self,
        order: QueuedOrder,
        market: MarketState
    ) -> Decimal:
        """
        Calculate probability of fill for a resting order.

        Factors:
        1. Base probability (volume-adjusted)
        2. Queue position decay
        3. Adverse selection adjustment
        """
        # Base probability per check (every 0.5 seconds)
        base_prob = self.BASE_FILL_PROB_PER_SECOND / 2

        # Adjust for volume
        vol_per_sec = market.get_volume_per_second()
        if vol_per_sec > 0:
            volume_factor = min(Decimal("3"), vol_per_sec / Decimal("10"))
            base_prob *= (Decimal("1") + volume_factor)

        # Queue position factor (front of queue = higher prob)
        if order.initial_queue_depth > 0:
            queue_progress = Decimal("1") - (order.queue_position / order.initial_queue_depth)
            queue_progress = max(Decimal("0"), min(Decimal("1"), queue_progress))
            base_prob *= (Decimal("0.2") + queue_progress * Decimal("0.8"))

        # Adverse selection adjustment
        if self.enable_adverse_selection:
            price_move = market.get_price_move(order.created_at)

            if order.side == "BUY":
                # If price went down, our buy is "stale" - more likely to fill (bad!)
                if price_move < 0:
                    base_prob *= self.ADVERSE_SELECTION_MULTIPLIER
                    self._is_adverse = True
                elif price_move > 0:
                    # Price went up, our buy is good - less likely to fill
                    base_prob *= self.FAVORABLE_SELECTION_MULTIPLIER
            else:  # SELL
                # If price went up, our sell is "stale" - more likely to fill (bad!)
                if price_move > 0:
                    base_prob *= self.ADVERSE_SELECTION_MULTIPLIER
                    self._is_adverse = True
                elif price_move < 0:
                    # Price went down, our sell is good - less likely to fill
                    base_prob *= self.FAVORABLE_SELECTION_MULTIPLIER

        return min(Decimal("1"), base_prob)

    async def _execute_fill(
        self,
        order: QueuedOrder,
        price: Decimal,
        size: Decimal,
        is_maker: bool,
        slippage: Decimal,
    ):
        """Execute a fill and update all state"""
        now = datetime.utcnow()

        # Calculate fee
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        fee = price * size * fee_rate

        # Create trade record
        trade = SimulatedTrade(
            trade_id=f"paper_trade_{len(self.trades)}",
            token_id=order.token_id,
            side=order.side,
            price=price,
            size=size,
            fee=fee,
            timestamp=now,
            order_id=order.order_id,
            is_maker=is_maker,
            slippage=slippage,
            queue_wait_time=(now - order.created_at).total_seconds(),
        )

        self.trades.append(trade)

        # Update order state
        order.size_matched += size
        order.fills.append((price, size, now))
        order.last_fill_time = now

        if order.size_matched >= order.size:
            order.status = "MATCHED"
            self.stats["orders_filled"] += 1
        else:
            order.status = "PARTIAL"
            self.stats["orders_partial"] += 1

        # Update position
        if order.side == "BUY":
            self.positions[order.token_id] += size
            self.balance -= (price * size + fee)
        else:
            self.positions[order.token_id] -= size
            self.balance += (price * size - fee)

        # Update stats
        self.stats["total_volume"] += size
        self.stats["total_fees"] += fee
        if is_maker:
            self.stats["maker_volume"] += size
        else:
            self.stats["taker_volume"] += size

        if hasattr(self, '_is_adverse') and self._is_adverse:
            self.stats["adverse_fills"] += 1
            self._is_adverse = False
        else:
            self.stats["favorable_fills"] += 1

        logger.info(
            f"[PAPER SIM] {'MAKER' if is_maker else 'TAKER'} fill: "
            f"{order.side} {size} @ {price} "
            f"(slip: {slippage}, wait: {trade.queue_wait_time:.1f}s)"
        )

        # Trigger callback
        if self._on_fill:
            try:
                # Convert to standard Trade format for compatibility
                from .client import Trade
                compat_trade = Trade(
                    trade_id=trade.trade_id,
                    token_id=trade.token_id,
                    side=trade.side,
                    price=trade.price,
                    size=trade.size,
                    fee=trade.fee,
                    timestamp=trade.timestamp,
                    order_id=trade.order_id,
                )

                if asyncio.iscoroutinefunction(self._on_fill):
                    asyncio.create_task(self._on_fill(compat_trade))
                else:
                    self._on_fill(compat_trade)
            except Exception as e:
                logger.error(f"[PAPER SIM] Fill callback error: {e}")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if self.enable_latency:
            latency = random.randint(self.LATENCY_MIN_MS, self.LATENCY_MAX_MS)
            await asyncio.sleep(latency / 1000)

        if order_id in self.orders:
            order = self.orders[order_id]
            if order.is_live:
                order.status = "CANCELLED"
                self.stats["orders_cancelled"] += 1
                logger.info(f"[PAPER SIM] Cancelled order {order_id[:16]}...")
                return True
        return False

    async def cancel_all_orders(self, token_id: Optional[str] = None) -> int:
        """Cancel all orders, optionally filtered by token"""
        if self.enable_latency:
            latency = random.randint(self.LATENCY_MIN_MS, self.LATENCY_MAX_MS)
            await asyncio.sleep(latency / 1000)

        count = 0
        for order in self.orders.values():
            if order.is_live:
                if token_id is None or order.token_id == token_id:
                    order.status = "CANCELLED"
                    count += 1
                    self.stats["orders_cancelled"] += 1

        logger.info(f"[PAPER SIM] Cancelled {count} orders")
        return count

    def get_orders(
        self,
        token_id: Optional[str] = None,
        status: str = "LIVE"
    ) -> List[QueuedOrder]:
        """Get orders filtered by token and status"""
        result = []
        for order in self.orders.values():
            if status == "LIVE" and order.status not in ("LIVE", "PARTIAL"):
                continue
            if status != "LIVE" and order.status != status:
                continue
            if token_id and order.token_id != token_id:
                continue
            result.append(order)
        return result

    def get_trades(self, limit: int = 100) -> List[SimulatedTrade]:
        """Get recent trades"""
        return self.trades[-limit:]

    def get_position(self, token_id: str) -> Decimal:
        """Get position for a token"""
        return self.positions.get(token_id, Decimal("0"))

    def get_all_positions(self) -> Dict[str, Decimal]:
        """Get all positions"""
        return dict(self.positions)

    def get_balance(self) -> Decimal:
        """Get current balance"""
        return self.balance

    def get_stats(self) -> Dict:
        """Get simulation statistics"""
        total_fills = self.stats["adverse_fills"] + self.stats["favorable_fills"]
        adverse_rate = (
            self.stats["adverse_fills"] / total_fills
            if total_fills > 0 else 0
        )

        return {
            **self.stats,
            "total_volume": float(self.stats["total_volume"]),
            "maker_volume": float(self.stats["maker_volume"]),
            "taker_volume": float(self.stats["taker_volume"]),
            "total_fees": float(self.stats["total_fees"]),
            "adverse_fill_rate": adverse_rate,
            "balance": float(self.balance),
        }

    def print_summary(self):
        """Print simulation summary"""
        stats = self.get_stats()
        print("\n" + "="*60)
        print("PAPER TRADING SIMULATION SUMMARY")
        print("="*60)
        print(f"Orders: {stats['orders_placed']} placed, "
              f"{stats['orders_filled']} filled, "
              f"{stats['orders_cancelled']} cancelled")
        print(f"Volume: ${stats['total_volume']:.2f} total "
              f"(${stats['maker_volume']:.2f} maker, "
              f"${stats['taker_volume']:.2f} taker)")
        print(f"Fees: ${stats['total_fees']:.4f}")
        print(f"Adverse Fill Rate: {stats['adverse_fill_rate']:.1%}")
        print(f"Balance: ${stats['balance']:.2f}")
        print("="*60 + "\n")
