"""
Quote Engine

The brain of the market maker. Calculates fair value and optimal bid/ask quotes.
"""
import logging
from decimal import Decimal
from typing import Optional, Tuple, List
from dataclasses import dataclass
from datetime import datetime, timedelta

from .client import OrderBook, Trade

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    """A single bid or ask quote"""
    price: Decimal
    size: Decimal
    side: str  # "BUY" or "SELL"


@dataclass
class QuoteSet:
    """A set of quotes for a market"""
    token_id: str
    timestamp: datetime
    bids: List[Quote]
    asks: List[Quote]
    fair_value: Decimal
    spread: Decimal
    reason: str = ""  # Why these quotes were generated


class QuoteEngine:
    """
    Calculates optimal bid/ask quotes based on:
    - Current order book state
    - Inventory position
    - Recent trade flow
    - Market volatility
    - Time to expiry
    """
    
    def __init__(
        self,
        base_spread: Decimal = Decimal("0.02"),
        min_spread: Decimal = Decimal("0.01"),
        max_spread: Decimal = Decimal("0.10"),
        min_price: Decimal = Decimal("0.05"),
        max_price: Decimal = Decimal("0.95"),
        num_levels: int = 3,
        level_spacing: Decimal = Decimal("0.01"),
        default_size: Decimal = Decimal("20.0"),
        inventory_skew_threshold: int = 100,
        use_weighted_mid: bool = True,
    ):
        self.base_spread = base_spread
        self.min_spread = min_spread
        self.max_spread = max_spread
        self.min_price = min_price
        self.max_price = max_price
        self.num_levels = num_levels
        self.level_spacing = level_spacing
        self.default_size = default_size
        self.inventory_skew_threshold = inventory_skew_threshold
        self.use_weighted_mid = use_weighted_mid
        
        # State tracking
        self._recent_trades: List[Trade] = []
        self._volatility_window: timedelta = timedelta(minutes=5)
        self._adverse_selection_factor: Decimal = Decimal("1.0")
    
    def calculate_fair_value(
        self,
        orderbook: OrderBook,
        inventory: int = 0,
    ) -> Optional[Decimal]:
        """
        Calculate the fair value of the token.
        
        Uses a combination of:
        - Mid price
        - Volume-weighted mid price
        - Inventory adjustment
        """
        if not orderbook or not orderbook.mid_price:
            return None
        
        # Start with mid price or weighted mid
        if self.use_weighted_mid:
            fair_value = orderbook.weighted_mid(depth=3)
        else:
            fair_value = orderbook.mid_price
        
        if fair_value is None:
            fair_value = orderbook.mid_price
        
        if fair_value is None:
            return None
        
        # Adjust for inventory
        # If we're long, we want to sell, so lower the fair value slightly
        # If we're short, we want to buy, so raise the fair value slightly
        if abs(inventory) > self.inventory_skew_threshold:
            inventory_adjustment = Decimal(str(inventory)) * Decimal("0.0001")
            fair_value = fair_value - inventory_adjustment
        
        # Clamp to valid price range
        fair_value = max(self.min_price, min(self.max_price, fair_value))
        
        return fair_value
    
    def calculate_spread(
        self,
        orderbook: OrderBook,
        inventory: int = 0,
        volatility_factor: Decimal = Decimal("1.0"),
        hours_to_expiry: Optional[float] = None,
    ) -> Decimal:
        """
        Calculate optimal spread based on market conditions.
        
        Wider spread when:
        - High volatility
        - Large inventory
        - Near expiry
        - Thin orderbook
        """
        spread = self.base_spread
        
        # Volatility adjustment
        spread = spread * volatility_factor
        
        # Inventory adjustment
        if abs(inventory) > self.inventory_skew_threshold:
            inventory_factor = Decimal("1.0") + (
                Decimal(str(abs(inventory))) / 
                Decimal(str(self.inventory_skew_threshold * 4))
            )
            spread = spread * inventory_factor
        
        # Time to expiry adjustment
        if hours_to_expiry is not None and hours_to_expiry < 48:
            # Widen spread as we approach expiry
            expiry_factor = Decimal("1.0") + (
                Decimal("1.0") / max(Decimal("1.0"), Decimal(str(hours_to_expiry / 12)))
            )
            spread = spread * expiry_factor
        
        # Orderbook depth adjustment
        if orderbook:
            bid_depth = sum(b["size"] for b in orderbook.bids[:5])
            ask_depth = sum(a["size"] for a in orderbook.asks[:5])
            
            if bid_depth < 100 or ask_depth < 100:
                # Thin book - widen spread
                spread = spread * Decimal("1.5")
        
        # Adverse selection adjustment
        spread = spread * self._adverse_selection_factor
        
        # Clamp spread
        spread = max(self.min_spread, min(self.max_spread, spread))
        
        return spread
    
    def calculate_inventory_skew(
        self,
        inventory: int,
    ) -> Tuple[Decimal, Decimal]:
        """
        Calculate bid/ask skew based on inventory position.
        
        Returns:
            (bid_adjustment, ask_adjustment) - add to prices
        """
        if abs(inventory) <= self.inventory_skew_threshold:
            return (Decimal("0"), Decimal("0"))
        
        # How many thresholds over are we?
        skew_multiple = Decimal(str(inventory)) / Decimal(str(self.inventory_skew_threshold))
        
        # Adjustment per threshold
        adjustment_per_threshold = Decimal("0.005")
        
        adjustment = skew_multiple * adjustment_per_threshold
        
        # If long (positive inventory), lower bids and asks to encourage selling
        # If short (negative inventory), raise bids and asks to encourage buying
        return (-adjustment, -adjustment)
    
    def calculate_quotes(
        self,
        token_id: str,
        orderbook: OrderBook,
        inventory: int = 0,
        volatility_factor: Decimal = Decimal("1.0"),
        hours_to_expiry: Optional[float] = None,
        size_override: Optional[Decimal] = None,
    ) -> Optional[QuoteSet]:
        """
        Calculate a complete set of quotes (bids and asks) for a market.
        
        This is the main entry point for quote generation.
        """
        # Calculate fair value
        fair_value = self.calculate_fair_value(orderbook, inventory)
        if fair_value is None:
            logger.warning(f"Could not calculate fair value for {token_id}")
            return None
        
        # Calculate spread
        spread = self.calculate_spread(
            orderbook, inventory, volatility_factor, hours_to_expiry
        )
        
        # Calculate inventory skew
        bid_skew, ask_skew = self.calculate_inventory_skew(inventory)
        
        # Determine order size
        size = size_override or self.default_size
        
        # Generate multiple levels of quotes
        bids = []
        asks = []
        
        half_spread = spread / 2
        
        for level in range(self.num_levels):
            level_offset = Decimal(str(level)) * self.level_spacing
            
            # Calculate level size (smaller for outer levels)
            level_size = size * (Decimal("1.0") - Decimal(str(level)) * Decimal("0.2"))
            level_size = max(Decimal("5.0"), level_size)
            
            # Bid price
            bid_price = fair_value - half_spread - level_offset + bid_skew
            bid_price = self._round_price(bid_price)
            
            if bid_price >= self.min_price:
                bids.append(Quote(
                    price=bid_price,
                    size=level_size,
                    side="BUY",
                ))
            
            # Ask price
            ask_price = fair_value + half_spread + level_offset + ask_skew
            ask_price = self._round_price(ask_price)
            
            if ask_price <= self.max_price:
                asks.append(Quote(
                    price=ask_price,
                    size=level_size,
                    side="SELL",
                ))
        
        # Build reason string for logging
        reason = f"FV={fair_value:.3f}, spread={spread:.3f}, inv={inventory}"
        
        return QuoteSet(
            token_id=token_id,
            timestamp=datetime.utcnow(),
            bids=bids,
            asks=asks,
            fair_value=fair_value,
            spread=spread,
            reason=reason,
        )
    
    def should_quote(
        self,
        orderbook: OrderBook,
        inventory: int,
        max_inventory: int,
        hours_to_expiry: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Determine if we should be quoting in this market.
        
        Returns:
            (should_quote, reason)
        """
        # Check if market has liquidity
        if not orderbook or not orderbook.mid_price:
            return (False, "No orderbook data")
        
        # Check inventory limits
        if abs(inventory) >= max_inventory:
            side_blocked = "BUY" if inventory > 0 else "SELL"
            return (True, f"Inventory limit - blocking {side_blocked}")
        
        # Check time to expiry
        if hours_to_expiry is not None and hours_to_expiry < 1:
            return (False, "Too close to expiry")
        
        # Check for extreme prices (likely about to resolve)
        mid = orderbook.mid_price
        if mid < Decimal("0.02") or mid > Decimal("0.98"):
            return (False, "Price near resolution bounds")
        
        return (True, "OK")
    
    def update_adverse_selection(self, trades: List[Trade]):
        """
        Update adverse selection factor based on recent trade flow.
        
        If we're consistently getting picked off on one side,
        increase the spread.
        """
        if not trades:
            return
        
        # Add to recent trades
        self._recent_trades.extend(trades)
        
        # Remove old trades
        cutoff = datetime.utcnow() - self._volatility_window
        self._recent_trades = [
            t for t in self._recent_trades 
            if t.timestamp > cutoff
        ]
        
        if len(self._recent_trades) < 5:
            self._adverse_selection_factor = Decimal("1.0")
            return
        
        # Analyze trade direction
        buy_volume = sum(
            t.size for t in self._recent_trades 
            if t.side == "BUY"
        )
        sell_volume = sum(
            t.size for t in self._recent_trades 
            if t.side == "SELL"
        )
        
        # If flow is heavily one-sided, increase adverse selection factor
        total_volume = buy_volume + sell_volume
        if total_volume > 0:
            imbalance = abs(buy_volume - sell_volume) / total_volume
            self._adverse_selection_factor = Decimal("1.0") + imbalance * Decimal("0.5")
        
        logger.debug(f"Adverse selection factor: {self._adverse_selection_factor}")
    
    def _round_price(self, price: Decimal) -> Decimal:
        """Round price to valid tick size (0.01)"""
        return (price * 100).quantize(Decimal("1")) / 100
    
    def calculate_expected_pnl(
        self,
        quotes: QuoteSet,
        fill_probability: Decimal = Decimal("0.5"),
    ) -> Decimal:
        """
        Estimate expected PnL from a set of quotes.
        
        Assumes:
        - Equal probability of bid and ask filling
        - No adverse selection in this simple model
        """
        if not quotes.bids or not quotes.asks:
            return Decimal("0")
        
        # Expected profit is half the spread times fill probability
        expected_profit = (quotes.spread / 2) * fill_probability
        
        # Scale by size
        avg_size = (quotes.bids[0].size + quotes.asks[0].size) / 2
        
        return expected_profit * avg_size


class SmartQuoteEngine(QuoteEngine):
    """
    Enhanced quote engine with additional intelligence:
    - Order flow toxicity detection
    - Volatility regime detection
    - Mean reversion signals
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._price_history: List[Tuple[datetime, Decimal]] = []
        self._volatility_regime: str = "normal"  # low, normal, high
    
    def update_price_history(self, price: Decimal):
        """Track price history for volatility calculation"""
        self._price_history.append((datetime.utcnow(), price))
        
        # Keep last 100 prices
        if len(self._price_history) > 100:
            self._price_history = self._price_history[-100:]
    
    def calculate_realized_volatility(self) -> Decimal:
        """Calculate realized volatility from price history"""
        if len(self._price_history) < 10:
            return Decimal("1.0")  # Default
        
        prices = [p[1] for p in self._price_history[-20:]]
        returns = [
            (prices[i] - prices[i-1]) / prices[i-1] 
            for i in range(1, len(prices))
        ]
        
        if not returns:
            return Decimal("1.0")
        
        # Standard deviation of returns
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        
        # Scale to factor (1.0 = normal, >1 = high vol)
        vol = Decimal(str(variance ** Decimal("0.5"))) * Decimal("100")
        
        return max(Decimal("0.5"), min(Decimal("3.0"), vol + Decimal("1.0")))
    
    def detect_momentum(self) -> Decimal:
        """
        Detect short-term momentum.
        
        Returns:
            Positive = upward momentum, Negative = downward
        """
        if len(self._price_history) < 5:
            return Decimal("0")
        
        recent = [p[1] for p in self._price_history[-5:]]
        older = [p[1] for p in self._price_history[-10:-5]] if len(self._price_history) >= 10 else recent
        
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        
        return recent_avg - older_avg
    
    def calculate_quotes(
        self,
        token_id: str,
        orderbook: OrderBook,
        inventory: int = 0,
        volatility_factor: Decimal = Decimal("1.0"),
        hours_to_expiry: Optional[float] = None,
        size_override: Optional[Decimal] = None,
    ) -> Optional[QuoteSet]:
        """Enhanced quote calculation with volatility and momentum"""
        
        # Update price history
        if orderbook and orderbook.mid_price:
            self.update_price_history(orderbook.mid_price)
        
        # Calculate realized volatility
        realized_vol = self.calculate_realized_volatility()
        combined_vol = (volatility_factor + realized_vol) / 2
        
        # Detect momentum and adjust
        momentum = self.detect_momentum()
        
        # Get base quotes
        quotes = super().calculate_quotes(
            token_id=token_id,
            orderbook=orderbook,
            inventory=inventory,
            volatility_factor=combined_vol,
            hours_to_expiry=hours_to_expiry,
            size_override=size_override,
        )
        
        if quotes and abs(momentum) > Decimal("0.01"):
            # Adjust fair value slightly in direction of momentum
            # This helps avoid adverse selection
            adjustment = momentum * Decimal("0.1")
            quotes.fair_value += adjustment
            quotes.reason += f", mom={momentum:.4f}"
        
        return quotes
