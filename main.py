#!/usr/bin/env python3
"""
Polymarket Market Making Bot - Main Entry Point

Usage:
    python main.py                      # Run with interactive market selection
    python main.py --discover           # Discover available markets
    python main.py --token-id <id>      # Run on specific token
    python main.py --live               # Run in live mode (real money!)
    python main.py --websocket          # Use WebSocket for real-time data
"""
import asyncio
import argparse
import logging
import os
import sys
from decimal import Decimal

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import PolymarketClient, MarketMakingBot, run_bot


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Reduce noise from aiohttp
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def discover_markets(client: PolymarketClient, min_liquidity: float = 1000):
    """
    Discover available markets and display them.
    
    Shows markets sorted by liquidity, with key metrics.
    """
    print("\nDiscovering markets...")
    print("=" * 80)
    
    markets = await client.get_markets(active_only=True)
    
    if not markets:
        print("No markets found. API might be unavailable.")
        return []
    
    # Filter by minimum liquidity
    markets = [m for m in markets if float(m.liquidity) >= min_liquidity]
    
    # Sort by liquidity
    markets = sorted(markets, key=lambda m: m.liquidity, reverse=True)
    
    print(f"\nFound {len(markets)} markets with liquidity >= ${min_liquidity:,.0f}")
    print("-" * 80)
    print(f"{'#':<4} {'Question':<50} {'Liquidity':>12} {'Volume':>12}")
    print("-" * 80)
    
    for i, market in enumerate(markets[:30], 1):
        question = market.question[:47] + "..." if len(market.question) > 50 else market.question
        print(
            f"{i:<4} {question:<50} "
            f"${market.liquidity:>10,.0f} "
            f"${market.volume:>10,.0f}"
        )
    
    print("-" * 80)
    print(f"\nShowing top 30 of {len(markets)} markets")
    
    return markets


async def select_market_interactive(client: PolymarketClient) -> list:
    """Interactive market selection"""
    markets = await discover_markets(client)
    
    if not markets:
        return []
    
    print("\nEnter market number(s) to trade (comma-separated), or 'q' to quit:")
    
    while True:
        try:
            choice = input("> ").strip()
            
            if choice.lower() == 'q':
                return []
            
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            
            selected_tokens = []
            for idx in indices:
                if 0 <= idx < len(markets):
                    market = markets[idx]
                    # Return the YES token ID
                    selected_tokens.append(market.yes_token_id)
                    print(f"Selected: {market.question}")
                else:
                    print(f"Invalid index: {idx + 1}")
            
            if selected_tokens:
                return selected_tokens
                
        except ValueError:
            print("Invalid input. Enter numbers separated by commas.")
        except KeyboardInterrupt:
            return []


async def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Market Making Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                          # Interactive mode
    python main.py --discover               # List available markets
    python main.py --token-id <id>          # Trade specific token
    python main.py --live                   # LIVE TRADING (real money!)
    
Environment Variables:
    POLYMARKET_PK           Private key for signing
    POLYMARKET_API_KEY      API key
    POLYMARKET_API_SECRET   API secret
    POLYMARKET_PASSPHRASE   API passphrase
    POLYMARKET_FUNDER       Funder address
        """
    )
    
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Discover and list available markets"
    )
    
    parser.add_argument(
        "--token-id",
        type=str,
        nargs="+",
        help="Token ID(s) to trade"
    )
    
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mode (real money!)"
    )
    
    parser.add_argument(
        "--spread",
        type=float,
        default=0.02,
        help="Base spread (default: 0.02 = 2 cents)"
    )
    
    parser.add_argument(
        "--size",
        type=float,
        default=20.0,
        help="Default order size in USDC (default: 20)"
    )
    
    parser.add_argument(
        "--max-position",
        type=int,
        default=500,
        help="Maximum position per market (default: 500)"
    )
    
    parser.add_argument(
        "--max-exposure",
        type=float,
        default=1000.0,
        help="Maximum total exposure (default: 1000)"
    )
    
    parser.add_argument(
        "--refresh",
        type=float,
        default=5.0,
        help="Quote refresh interval in seconds (default: 5)"
    )
    
    parser.add_argument(
        "--min-liquidity",
        type=float,
        default=1000.0,
        help="Minimum liquidity for market discovery (default: 1000)"
    )

    parser.add_argument(
        "--websocket",
        action="store_true",
        help="Use WebSocket for real-time orderbook updates (instead of REST polling)"
    )

    args = parser.parse_args()
    
    # Determine trading mode
    paper_trading = not args.live
    
    if args.live:
        print("\n" + "!" * 60)
        print("WARNING: LIVE TRADING MODE")
        print("You will be trading with REAL MONEY!")
        print("!" * 60)
        
        confirm = input("\nType 'YES' to confirm live trading: ")
        if confirm != "YES":
            print("Cancelled.")
            return
    
    # Create client
    client = PolymarketClient(
        private_key=os.getenv("POLYMARKET_PK", ""),
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
        passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
        funder_address=os.getenv("POLYMARKET_FUNDER", ""),
        paper_trading=paper_trading,
        use_websocket=args.websocket,
    )
    
    try:
        # Discovery mode
        if args.discover:
            await discover_markets(client, args.min_liquidity)
            return
        
        # Get target markets
        if args.token_id:
            target_markets = args.token_id
        else:
            target_markets = await select_market_interactive(client)
        
        if not target_markets:
            print("No markets selected. Exiting.")
            return
        
        print(f"\nStarting bot with {len(target_markets)} market(s)...")
        print(f"Mode: {'PAPER TRADING' if paper_trading else 'LIVE TRADING'}")
        print(f"Data: {'WEBSOCKET (real-time)' if args.websocket else 'REST POLLING'}")

        # Create and run bot
        bot = MarketMakingBot(
            client=client,
            target_markets=target_markets,
            paper_trading=paper_trading,
            use_websocket=args.websocket,
            base_spread=Decimal(str(args.spread)),
            default_order_size=Decimal(str(args.size)),
            max_position_per_market=args.max_position,
            max_total_exposure=Decimal(str(args.max_exposure)),
            quote_refresh_interval=args.refresh,
        )

        await bot.start()
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
