"""
Inventory and Risk Management

Tracks positions, calculates exposure, and enforces risk limits.
"""
import logging
from decimal import Decimal
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from .client import Trade, Order

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a position in a single token"""
    token_id: str
    quantity: int  # Positive = long, Negative = short
    avg_entry_price: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def market_value(self) -> Decimal:
        """Calculate current market value (requires current price)"""
        return self.avg_entry_price * Decimal(str(abs(self.quantity)))
    
    def update_unrealized(self, current_price: Decimal):
        """Update unrealized PnL based on current price"""
        if self.quantity == 0:
            self.unrealized_pnl = Decimal("0")
            return
            
        if self.quantity > 0:  # Long position
            self.unrealized_pnl = (current_price - self.avg_entry_price) * Decimal(str(self.quantity))
        else:  # Short position
            self.unrealized_pnl = (self.avg_entry_price - current_price) * Decimal(str(abs(self.quantity)))


@dataclass
class RiskMetrics:
    """Current risk metrics"""
    total_exposure: Decimal
    max_position_size: int
    current_max_position: int
    daily_pnl: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    num_positions: int
    inventory_imbalance: Decimal  # -1 to 1, 0 = balanced


class InventoryManager:
    """
    Manages positions and calculates inventory metrics.
    """
    
    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._trade_history: List[Trade] = []
        
    def get_position(self, token_id: str) -> Position:
        """Get position for a token (creates if doesn't exist)"""
        if token_id not in self._positions:
            self._positions[token_id] = Position(
                token_id=token_id,
                quantity=0,
                avg_entry_price=Decimal("0"),
            )
        return self._positions[token_id]
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all non-zero positions"""
        return {
            k: v for k, v in self._positions.items() 
            if v.quantity != 0
        }
    
    def update_position(self, trade: Trade):
        """Update position based on a trade fill"""
        position = self.get_position(trade.token_id)
        
        old_quantity = position.quantity
        
        if trade.side == "BUY":
            new_quantity = old_quantity + int(trade.size)
            
            # Update average entry price
            if new_quantity != 0:
                if old_quantity >= 0:
                    # Adding to or starting long position
                    old_cost = position.avg_entry_price * Decimal(str(max(0, old_quantity)))
                    new_cost = trade.price * trade.size
                    position.avg_entry_price = (old_cost + new_cost) / Decimal(str(new_quantity))
                else:
                    # Closing short position
                    closed_qty = min(int(trade.size), abs(old_quantity))
                    pnl = (position.avg_entry_price - trade.price) * Decimal(str(closed_qty))
                    position.realized_pnl += pnl
                    
                    if new_quantity > 0:
                        # Flipped to long
                        position.avg_entry_price = trade.price
        else:  # SELL
            new_quantity = old_quantity - int(trade.size)
            
            if new_quantity != 0:
                if old_quantity <= 0:
                    # Adding to or starting short position
                    old_cost = position.avg_entry_price * Decimal(str(abs(min(0, old_quantity))))
                    new_cost = trade.price * trade.size
                    position.avg_entry_price = (old_cost + new_cost) / Decimal(str(abs(new_quantity)))
                else:
                    # Closing long position
                    closed_qty = min(int(trade.size), old_quantity)
                    pnl = (trade.price - position.avg_entry_price) * Decimal(str(closed_qty))
                    position.realized_pnl += pnl
                    
                    if new_quantity < 0:
                        # Flipped to short
                        position.avg_entry_price = trade.price
        
        position.quantity = new_quantity
        position.last_updated = datetime.utcnow()
        
        self._trade_history.append(trade)
        
        logger.info(
            f"Position updated: {trade.token_id} "
            f"{old_quantity} -> {new_quantity} @ {trade.price}"
        )
    
    def get_total_long_exposure(self) -> Decimal:
        """Get total long exposure in USDC terms"""
        return sum(
            p.avg_entry_price * Decimal(str(p.quantity))
            for p in self._positions.values()
            if p.quantity > 0
        )
    
    def get_total_short_exposure(self) -> Decimal:
        """Get total short exposure in USDC terms"""
        return sum(
            p.avg_entry_price * Decimal(str(abs(p.quantity)))
            for p in self._positions.values()
            if p.quantity < 0
        )
    
    def get_net_exposure(self) -> Decimal:
        """Get net exposure (long - short)"""
        return self.get_total_long_exposure() - self.get_total_short_exposure()
    
    def get_gross_exposure(self) -> Decimal:
        """Get gross exposure (long + short)"""
        return self.get_total_long_exposure() + self.get_total_short_exposure()
    
    def get_total_realized_pnl(self) -> Decimal:
        """Get total realized PnL across all positions"""
        return sum(p.realized_pnl for p in self._positions.values())
    
    def get_total_unrealized_pnl(self) -> Decimal:
        """Get total unrealized PnL across all positions"""
        return sum(p.unrealized_pnl for p in self._positions.values())
    
    def update_all_unrealized(self, prices: Dict[str, Decimal]):
        """Update unrealized PnL for all positions given current prices"""
        for token_id, position in self._positions.items():
            if token_id in prices:
                position.update_unrealized(prices[token_id])


class RiskManager:
    """
    Enforces risk limits and calculates risk metrics.
    """
    
    def __init__(
        self,
        max_position_per_market: int = 500,
        max_total_exposure: Decimal = Decimal("1000.0"),
        max_inventory_imbalance: int = 400,
        daily_loss_limit: Decimal = Decimal("100.0"),
    ):
        self.max_position_per_market = max_position_per_market
        self.max_total_exposure = max_total_exposure
        self.max_inventory_imbalance = max_inventory_imbalance
        self.daily_loss_limit = daily_loss_limit
        
        self._daily_pnl: Decimal = Decimal("0")
        self._daily_pnl_reset_time: datetime = datetime.utcnow()
        self._halted: bool = False
        self._halt_reason: str = ""
    
    def check_order_allowed(
        self,
        inventory_manager: InventoryManager,
        token_id: str,
        side: str,
        size: Decimal,
        price: Decimal,
    ) -> Tuple[bool, str]:
        """
        Check if an order is allowed given current risk limits.
        
        Returns:
            (allowed, reason)
        """
        if self._halted:
            return (False, f"Trading halted: {self._halt_reason}")
        
        position = inventory_manager.get_position(token_id)
        current_qty = position.quantity
        
        # Calculate new position after trade
        if side == "BUY":
            new_qty = current_qty + int(size)
        else:
            new_qty = current_qty - int(size)
        
        # Check position limit
        if abs(new_qty) > self.max_position_per_market:
            return (
                False, 
                f"Would exceed position limit: {new_qty} > {self.max_position_per_market}"
            )
        
        # Check total exposure
        current_exposure = inventory_manager.get_gross_exposure()
        additional_exposure = price * size
        
        if current_exposure + additional_exposure > self.max_total_exposure:
            return (
                False,
                f"Would exceed total exposure: {current_exposure + additional_exposure} > {self.max_total_exposure}"
            )
        
        # Check inventory imbalance
        net_exposure = inventory_manager.get_net_exposure()
        if side == "BUY":
            new_net = net_exposure + (price * size)
        else:
            new_net = net_exposure - (price * size)
        
        if abs(new_net) > self.max_inventory_imbalance:
            return (
                False,
                f"Would exceed inventory imbalance: {new_net}"
            )
        
        return (True, "OK")
    
    def check_daily_loss(self, inventory_manager: InventoryManager) -> bool:
        """Check if daily loss limit has been hit"""
        # Reset daily PnL at midnight UTC
        now = datetime.utcnow()
        if now.date() > self._daily_pnl_reset_time.date():
            self._daily_pnl = Decimal("0")
            self._daily_pnl_reset_time = now
        
        total_pnl = (
            inventory_manager.get_total_realized_pnl() +
            inventory_manager.get_total_unrealized_pnl()
        )
        
        if total_pnl < -self.daily_loss_limit:
            self._halted = True
            self._halt_reason = f"Daily loss limit hit: {total_pnl}"
            logger.warning(self._halt_reason)
            return True
        
        return False
    
    def get_risk_metrics(
        self,
        inventory_manager: InventoryManager,
    ) -> RiskMetrics:
        """Calculate current risk metrics"""
        positions = inventory_manager.get_all_positions()
        
        max_position = 0
        for p in positions.values():
            if abs(p.quantity) > max_position:
                max_position = abs(p.quantity)
        
        gross_exposure = inventory_manager.get_gross_exposure()
        net_exposure = inventory_manager.get_net_exposure()
        
        # Calculate inventory imbalance as ratio
        if gross_exposure > 0:
            imbalance = net_exposure / gross_exposure
        else:
            imbalance = Decimal("0")
        
        return RiskMetrics(
            total_exposure=gross_exposure,
            max_position_size=self.max_position_per_market,
            current_max_position=max_position,
            daily_pnl=self._daily_pnl,
            unrealized_pnl=inventory_manager.get_total_unrealized_pnl(),
            realized_pnl=inventory_manager.get_total_realized_pnl(),
            num_positions=len(positions),
            inventory_imbalance=imbalance,
        )
    
    def calculate_size_adjustment(
        self,
        inventory_manager: InventoryManager,
        token_id: str,
        side: str,
        base_size: Decimal,
    ) -> Decimal:
        """
        Adjust order size based on current inventory.
        
        Reduces size on the side that would increase imbalance.
        """
        position = inventory_manager.get_position(token_id)
        current_qty = position.quantity
        
        # If adding to position in same direction, reduce size
        if (side == "BUY" and current_qty > self.max_inventory_imbalance / 2):
            reduction = min(
                Decimal("0.5"),
                Decimal(str(current_qty)) / Decimal(str(self.max_inventory_imbalance))
            )
            return base_size * (Decimal("1.0") - reduction)
        
        if (side == "SELL" and current_qty < -self.max_inventory_imbalance / 2):
            reduction = min(
                Decimal("0.5"),
                Decimal(str(abs(current_qty))) / Decimal(str(self.max_inventory_imbalance))
            )
            return base_size * (Decimal("1.0") - reduction)
        
        return base_size
    
    def halt_trading(self, reason: str):
        """Halt trading with reason"""
        self._halted = True
        self._halt_reason = reason
        logger.warning(f"Trading HALTED: {reason}")
    
    def resume_trading(self):
        """Resume trading"""
        self._halted = False
        self._halt_reason = ""
        logger.info("Trading RESUMED")
    
    @property
    def is_halted(self) -> bool:
        return self._halted


class PnLTracker:
    """
    Tracks profit and loss over time.
    """
    
    def __init__(self):
        self._snapshots: List[Tuple[datetime, Decimal, Decimal]] = []  # (time, realized, unrealized)
        self._fills: List[Tuple[datetime, Trade]] = []
    
    def record_snapshot(
        self,
        realized: Decimal,
        unrealized: Decimal,
    ):
        """Record a PnL snapshot"""
        self._snapshots.append((
            datetime.utcnow(),
            realized,
            unrealized,
        ))
        
        # Keep last 24 hours
        cutoff = datetime.utcnow() - timedelta(hours=24)
        self._snapshots = [s for s in self._snapshots if s[0] > cutoff]
    
    def record_fill(self, trade: Trade):
        """Record a fill"""
        self._fills.append((datetime.utcnow(), trade))
    
    def get_hourly_pnl(self) -> List[Tuple[datetime, Decimal]]:
        """Get PnL by hour"""
        if not self._snapshots:
            return []
        
        hourly = defaultdict(lambda: (Decimal("0"), Decimal("0")))
        
        for timestamp, realized, unrealized in self._snapshots:
            hour = timestamp.replace(minute=0, second=0, microsecond=0)
            hourly[hour] = (realized, unrealized)
        
        return [
            (hour, realized + unrealized) 
            for hour, (realized, unrealized) in sorted(hourly.items())
        ]
    
    def get_statistics(self) -> Dict:
        """Get PnL statistics"""
        if not self._snapshots:
            return {
                "total_pnl": Decimal("0"),
                "realized_pnl": Decimal("0"),
                "unrealized_pnl": Decimal("0"),
                "num_fills": 0,
            }
        
        latest = self._snapshots[-1]
        
        return {
            "total_pnl": latest[1] + latest[2],
            "realized_pnl": latest[1],
            "unrealized_pnl": latest[2],
            "num_fills": len(self._fills),
        }
    
    def print_summary(self):
        """Print PnL summary to console"""
        stats = self.get_statistics()
        
        print("\n" + "="*50)
        print("PnL SUMMARY")
        print("="*50)
        print(f"Total PnL:      ${stats['total_pnl']:>10.2f}")
        print(f"  Realized:     ${stats['realized_pnl']:>10.2f}")
        print(f"  Unrealized:   ${stats['unrealized_pnl']:>10.2f}")
        print(f"Total Fills:    {stats['num_fills']:>10}")
        print("="*50 + "\n")
