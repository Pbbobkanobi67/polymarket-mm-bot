"""
Order Manager

Handles order lifecycle: placing, tracking, cancelling orders.
"""
import logging
import asyncio
from decimal import Decimal
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .client import PolymarketClient, Order
from .quote_engine import QuoteSet, Quote

logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    """An order being managed by the bot"""
    order: Order
    quote: Quote
    placed_at: datetime
    token_id: str
    is_stale: bool = False


class OrderManager:
    """
    Manages the order lifecycle.
    
    Responsibilities:
    - Place new orders
    - Cancel stale orders
    - Track order state
    - Ensure quote consistency
    """
    
    def __init__(
        self,
        client: PolymarketClient,
        order_timeout_seconds: int = 300,
        max_orders_per_side: int = 5,
    ):
        self.client = client
        self.order_timeout = timedelta(seconds=order_timeout_seconds)
        self.max_orders_per_side = max_orders_per_side
        
        # Track managed orders by token_id and side
        self._orders: Dict[str, Dict[str, List[ManagedOrder]]] = {}
        # token_id -> {"BUY": [orders], "SELL": [orders]}
        
        # Orders pending cancellation
        self._pending_cancels: Set[str] = set()
    
    def _get_orders_for_token(self, token_id: str) -> Dict[str, List[ManagedOrder]]:
        """Get orders structure for a token"""
        if token_id not in self._orders:
            self._orders[token_id] = {"BUY": [], "SELL": []}
        return self._orders[token_id]
    
    async def update_quotes(
        self,
        token_id: str,
        quote_set: QuoteSet,
    ) -> int:
        """
        Update quotes for a token.
        
        This will:
        1. Cancel orders that no longer match desired quotes
        2. Place new orders for the new quotes
        
        Returns number of orders placed.
        """
        orders_placed = 0
        
        current_orders = self._get_orders_for_token(token_id)
        
        # Process bids
        orders_placed += await self._update_side(
            token_id, "BUY", quote_set.bids, current_orders["BUY"]
        )
        
        # Process asks
        orders_placed += await self._update_side(
            token_id, "SELL", quote_set.asks, current_orders["SELL"]
        )
        
        return orders_placed
    
    async def _update_side(
        self,
        token_id: str,
        side: str,
        new_quotes: List[Quote],
        current_orders: List[ManagedOrder],
    ) -> int:
        """Update orders for one side of the book"""
        orders_placed = 0
        
        # Build set of desired (price, size) tuples
        desired_quotes = {(q.price, q.size) for q in new_quotes}
        
        # Check existing orders
        orders_to_cancel = []
        orders_to_keep = []
        
        for managed in current_orders:
            if managed.order.status != "LIVE":
                continue
                
            key = (managed.order.price, managed.order.size)
            
            if key in desired_quotes:
                # Order matches desired quote, keep it
                orders_to_keep.append(managed)
                desired_quotes.remove(key)
            else:
                # Order doesn't match, cancel it
                orders_to_cancel.append(managed)
        
        # Cancel non-matching orders
        for managed in orders_to_cancel:
            await self._cancel_order(managed)
        
        # Place new orders for remaining desired quotes
        for quote in new_quotes:
            key = (quote.price, quote.size)
            if key in desired_quotes:
                order = await self.client.place_order(
                    token_id=token_id,
                    side=side,
                    price=quote.price,
                    size=quote.size,
                )
                
                if order:
                    managed = ManagedOrder(
                        order=order,
                        quote=quote,
                        placed_at=datetime.utcnow(),
                        token_id=token_id,
                    )
                    orders_to_keep.append(managed)
                    orders_placed += 1
                    
                    logger.debug(
                        f"Placed {side} order: {quote.size} @ {quote.price}"
                    )
        
        # Update tracked orders
        self._orders[token_id][side] = orders_to_keep
        
        return orders_placed
    
    async def _cancel_order(self, managed: ManagedOrder):
        """Cancel a managed order"""
        if managed.order.order_id in self._pending_cancels:
            return
        
        self._pending_cancels.add(managed.order.order_id)
        
        try:
            success = await self.client.cancel_order(managed.order.order_id)
            if success:
                managed.order.status = "CANCELLED"
                logger.debug(f"Cancelled order {managed.order.order_id}")
            else:
                logger.warning(f"Failed to cancel order {managed.order.order_id}")
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
        finally:
            self._pending_cancels.discard(managed.order.order_id)
    
    async def cancel_all_orders(self, token_id: Optional[str] = None):
        """Cancel all orders, optionally for a specific token"""
        if token_id:
            tokens = [token_id]
        else:
            tokens = list(self._orders.keys())
        
        for tid in tokens:
            orders = self._get_orders_for_token(tid)
            
            for side in ["BUY", "SELL"]:
                for managed in orders[side]:
                    if managed.order.status == "LIVE":
                        await self._cancel_order(managed)
            
            self._orders[tid] = {"BUY": [], "SELL": []}
        
        # Also call the API to cancel all (belt and suspenders)
        await self.client.cancel_all_orders(token_id)
    
    async def cancel_stale_orders(self) -> int:
        """Cancel orders that have been live too long"""
        cancelled = 0
        now = datetime.utcnow()
        
        for token_id, orders in self._orders.items():
            for side in ["BUY", "SELL"]:
                for managed in orders[side]:
                    if managed.order.status == "LIVE":
                        age = now - managed.placed_at
                        if age > self.order_timeout:
                            await self._cancel_order(managed)
                            cancelled += 1
        
        return cancelled
    
    def get_live_orders(
        self,
        token_id: Optional[str] = None,
        side: Optional[str] = None,
    ) -> List[ManagedOrder]:
        """Get all live orders"""
        result = []
        
        tokens = [token_id] if token_id else list(self._orders.keys())
        sides = [side] if side else ["BUY", "SELL"]
        
        for tid in tokens:
            if tid not in self._orders:
                continue
            orders = self._orders[tid]
            
            for s in sides:
                for managed in orders.get(s, []):
                    if managed.order.status == "LIVE":
                        result.append(managed)
        
        return result
    
    def get_order_count(self, token_id: str) -> Dict[str, int]:
        """Get count of live orders by side"""
        orders = self._get_orders_for_token(token_id)
        return {
            "BUY": sum(1 for m in orders["BUY"] if m.order.status == "LIVE"),
            "SELL": sum(1 for m in orders["SELL"] if m.order.status == "LIVE"),
        }
    
    async def sync_with_exchange(self, token_id: str):
        """
        Sync local order state with exchange.
        
        Fetches orders from exchange and reconciles with local state.
        """
        try:
            exchange_orders = await self.client.get_orders(token_id, status="LIVE")
            
            # Build set of known order IDs
            exchange_ids = {o.order_id for o in exchange_orders}
            
            orders = self._get_orders_for_token(token_id)
            
            for side in ["BUY", "SELL"]:
                updated = []
                for managed in orders[side]:
                    if managed.order.order_id in exchange_ids:
                        # Order still live on exchange
                        updated.append(managed)
                    else:
                        # Order no longer on exchange (filled or cancelled)
                        if managed.order.status == "LIVE":
                            logger.info(
                                f"Order {managed.order.order_id} no longer live on exchange"
                            )
                            managed.order.status = "UNKNOWN"
                
                orders[side] = updated
                
        except Exception as e:
            logger.error(f"Error syncing orders: {e}")
    
    def print_orders(self, token_id: Optional[str] = None):
        """Print current orders"""
        orders = self.get_live_orders(token_id)
        
        if not orders:
            print("No live orders")
            return
        
        print("\nLive Orders:")
        print("-" * 60)
        
        for managed in sorted(orders, key=lambda m: (m.token_id, m.order.side, -m.order.price)):
            print(
                f"{managed.token_id[:8]}... "
                f"{managed.order.side:4} "
                f"{managed.order.size:>8.2f} @ ${managed.order.price:.2f}"
            )
        
        print("-" * 60)
