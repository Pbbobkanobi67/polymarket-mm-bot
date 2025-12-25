#!/usr/bin/env python3
"""
Demo script showing how the market making bot works.

This runs a simulation with mock market data so you can see
the bot's behavior without connecting to the real Polymarket API.
"""
import asyncio
import sys
import os
from decimal import Decimal
from datetime import datetime
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import (
    PolymarketClient,
    SmartQuoteEngine,
    OrderManager,
    InventoryManager,
    RiskManager,
    PnLTracker,
    OrderBook,
    Trade,
)


def generate_mock_orderbook(
    token_id: str,
    mid_price: float = 0.50,
    spread: float = 0.04,
    depth: int = 5,
) -> OrderBook:
    """Generate a realistic mock orderbook"""
    bids = []
    asks = []
    
    for i in range(depth):
        offset = (i + 1) * 0.01
        size = random.uniform(50, 200)
        
        bids.append({
            "price": Decimal(str(round(mid_price - spread/2 - offset, 2))),
            "size": Decimal(str(round(size, 2))),
        })
        
        asks.append({
            "price": Decimal(str(round(mid_price + spread/2 + offset, 2))),
            "size": Decimal(str(round(size, 2))),
        })
    
    return OrderBook(
        token_id=token_id,
        timestamp=datetime.utcnow(),
        bids=sorted(bids, key=lambda x: x["price"], reverse=True),
        asks=sorted(asks, key=lambda x: x["price"]),
    )


def simulate_fill(side: str, price: Decimal, size: Decimal, token_id: str) -> Trade:
    """Simulate a trade fill"""
    return Trade(
        trade_id=f"sim_{random.randint(1000, 9999)}",
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        fee=Decimal("0"),
        timestamp=datetime.utcnow(),
        order_id=f"order_{random.randint(1000, 9999)}",
    )


async def run_demo():
    """Run a demo simulation of the market making bot"""
    
    print("\n" + "="*70)
    print("POLYMARKET MARKET MAKING BOT - DEMO")
    print("="*70)
    print("\nThis demo shows how the market making bot works.")
    print("We'll simulate 20 trading cycles with mock market data.\n")
    
    # Initialize components
    token_id = "demo_btc_100k_2025"
    
    quote_engine = SmartQuoteEngine(
        base_spread=Decimal("0.02"),
        min_spread=Decimal("0.01"),
        max_spread=Decimal("0.10"),
        default_size=Decimal("25.0"),
        num_levels=3,
    )
    
    inventory_manager = InventoryManager()
    risk_manager = RiskManager(
        max_position_per_market=500,
        max_total_exposure=Decimal("1000.0"),
        daily_loss_limit=Decimal("100.0"),
    )
    pnl_tracker = PnLTracker()
    
    # Simulation parameters
    initial_price = 0.50
    price_volatility = 0.02
    fill_probability = 0.3
    
    current_price = initial_price
    
    print(f"{'Cycle':<6} {'Mid':>7} {'Bid':>7} {'Ask':>7} {'Spread':>7} "
          f"{'Inv':>6} {'Fill':>12} {'PnL':>10}")
    print("-" * 80)
    
    for cycle in range(1, 21):
        # Simulate price movement
        price_change = random.gauss(0, price_volatility)
        current_price = max(0.10, min(0.90, current_price + price_change))
        
        # Generate mock orderbook
        orderbook = generate_mock_orderbook(
            token_id=token_id,
            mid_price=current_price,
            spread=0.04,
        )
        
        # Get current inventory
        position = inventory_manager.get_position(token_id)
        inventory = position.quantity
        
        # Calculate quotes
        quotes = quote_engine.calculate_quotes(
            token_id=token_id,
            orderbook=orderbook,
            inventory=inventory,
        )
        
        if not quotes:
            continue
        
        # Display quotes
        best_bid = quotes.bids[0].price if quotes.bids else Decimal("0")
        best_ask = quotes.asks[0].price if quotes.asks else Decimal("0")
        
        fill_info = ""
        
        # Simulate potential fills
        if random.random() < fill_probability:
            # Someone takes our quote
            if random.random() < 0.5 and quotes.asks:
                # Our ask got lifted (we sold)
                quote = quotes.asks[0]
                fill_size = min(quote.size, Decimal(str(random.randint(5, 20))))
                trade = simulate_fill("SELL", quote.price, fill_size, token_id)
                inventory_manager.update_position(trade)
                pnl_tracker.record_fill(trade)
                fill_info = f"SOLD {fill_size:.0f}@{quote.price:.2f}"
                
            elif quotes.bids:
                # Our bid got hit (we bought)
                quote = quotes.bids[0]
                fill_size = min(quote.size, Decimal(str(random.randint(5, 20))))
                trade = simulate_fill("BUY", quote.price, fill_size, token_id)
                inventory_manager.update_position(trade)
                pnl_tracker.record_fill(trade)
                fill_info = f"BOUGHT {fill_size:.0f}@{quote.price:.2f}"
        
        # Update unrealized PnL
        inventory_manager.update_all_unrealized({token_id: Decimal(str(current_price))})
        
        # Calculate total PnL
        total_pnl = (
            inventory_manager.get_total_realized_pnl() +
            inventory_manager.get_total_unrealized_pnl()
        )
        
        # Record snapshot
        pnl_tracker.record_snapshot(
            inventory_manager.get_total_realized_pnl(),
            inventory_manager.get_total_unrealized_pnl(),
        )
        
        # Get updated inventory
        position = inventory_manager.get_position(token_id)
        inventory = position.quantity
        
        print(f"{cycle:<6} ${current_price:>5.2f} ${best_bid:>5.2f} ${best_ask:>5.2f} "
              f"${quotes.spread:>5.2f} {inventory:>6} {fill_info:<12} ${total_pnl:>8.2f}")
        
        await asyncio.sleep(0.2)  # Small delay for visual effect
    
    # Final summary
    print("-" * 80)
    print("\nFINAL RESULTS")
    print("=" * 40)
    
    stats = pnl_tracker.get_statistics()
    position = inventory_manager.get_position(token_id)
    
    print(f"Total Fills:       {stats['num_fills']}")
    print(f"Final Position:    {position.quantity} shares")
    print(f"Avg Entry Price:   ${position.avg_entry_price:.3f}")
    print(f"Realized PnL:      ${stats['realized_pnl']:.2f}")
    print(f"Unrealized PnL:    ${stats['unrealized_pnl']:.2f}")
    print(f"Total PnL:         ${stats['total_pnl']:.2f}")
    print("=" * 40)
    
    # Explain results
    print("\nWHAT HAPPENED:")
    print("-" * 40)
    
    if stats['total_pnl'] > 0:
        print("✓ The bot was profitable in this simulation!")
        print("  Profits came from capturing the bid-ask spread.")
    else:
        print("✗ The bot lost money in this simulation.")
        print("  This can happen due to:")
        print("  - Adverse price movement against inventory")
        print("  - Accumulating inventory on the wrong side")
    
    if abs(position.quantity) > 100:
        print(f"\n⚠ Large inventory position ({position.quantity} shares)")
        print("  In real trading, you'd want to manage this risk by:")
        print("  - Skewing quotes to reduce position")
        print("  - Widening spread when inventory is high")
        print("  - Setting position limits")
    
    print("\nKEY TAKEAWAYS:")
    print("-" * 40)
    print("1. Market making profits come from the spread")
    print("2. Inventory management is critical")
    print("3. One-sided flow can hurt (adverse selection)")
    print("4. Risk management prevents catastrophic losses")
    print("5. Consistent small profits > occasional big gains")
    
    print("\n" + "="*70)
    print("Demo complete! Run 'python main.py' to try with real Polymarket data.")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_demo())
