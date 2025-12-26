"""
Polymarket Market Making Bot

Main bot class that orchestrates all components.
Supports both polling and WebSocket modes.
"""
import asyncio
import logging
import signal
from decimal import Decimal
from typing import Optional, List, Dict
from datetime import datetime, timedelta

from .client import PolymarketClient, OrderBook, Market, Trade
from .quote_engine import SmartQuoteEngine, QuoteSet
from .order_manager import OrderManager
from .risk_manager import InventoryManager, RiskManager, PnLTracker

logger = logging.getLogger(__name__)


class MarketMakingBot:
    """
    Main market making bot.

    Coordinates:
    - Market data fetching (polling or WebSocket)
    - Quote calculation
    - Order management
    - Risk management
    - Position tracking

    Supports two modes:
    - Polling mode: Fetches data at regular intervals (default)
    - WebSocket mode: Receives real-time updates via WebSocket
    """

    def __init__(
        self,
        client: PolymarketClient,
        target_markets: List[str],  # List of token IDs to trade
        paper_trading: bool = True,
        use_websocket: bool = False,
        # Quote engine settings
        base_spread: Decimal = Decimal("0.02"),
        min_spread: Decimal = Decimal("0.01"),
        max_spread: Decimal = Decimal("0.10"),
        default_order_size: Decimal = Decimal("20.0"),
        num_levels: int = 3,
        # Risk settings
        max_position_per_market: int = 500,
        max_total_exposure: Decimal = Decimal("1000.0"),
        daily_loss_limit: Decimal = Decimal("100.0"),
        # Timing settings
        quote_refresh_interval: float = 5.0,
        order_timeout_seconds: int = 300,
    ):
        self.client = client
        self.target_markets = target_markets
        self.paper_trading = paper_trading
        self.use_websocket = use_websocket
        self.quote_refresh_interval = quote_refresh_interval

        # Initialize components
        self.quote_engine = SmartQuoteEngine(
            base_spread=base_spread,
            min_spread=min_spread,
            max_spread=max_spread,
            default_size=default_order_size,
            num_levels=num_levels,
        )

        self.order_manager = OrderManager(
            client=client,
            order_timeout_seconds=order_timeout_seconds,
        )

        self.inventory_manager = InventoryManager()

        self.risk_manager = RiskManager(
            max_position_per_market=max_position_per_market,
            max_total_exposure=max_total_exposure,
            daily_loss_limit=daily_loss_limit,
        )

        self.pnl_tracker = PnLTracker()

        # State
        self._running = False
        self._orderbooks: Dict[str, OrderBook] = {}
        self._markets: Dict[str, Market] = {}
        self._last_quote_time: Dict[str, datetime] = {}

        # WebSocket state
        self._pending_quote_updates: Dict[str, bool] = {}  # token_id -> needs update
        self._quote_update_lock = asyncio.Lock()
    
    async def start(self):
        """Start the market making bot"""
        logger.info("="*60)
        logger.info("POLYMARKET MARKET MAKING BOT")
        logger.info("="*60)
        logger.info(f"Mode: {'PAPER TRADING' if self.paper_trading else 'LIVE TRADING'}")
        logger.info(f"Data: {'WEBSOCKET' if self.use_websocket else 'REST POLLING'}")
        logger.info(f"Target markets: {len(self.target_markets)}")
        logger.info(f"Quote refresh: {self.quote_refresh_interval}s")
        logger.info("="*60)

        self._running = True

        # Setup signal handlers (Unix only)
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_event_loop().add_signal_handler(
                    sig, lambda: asyncio.create_task(self.stop())
                )
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

        try:
            # Connect to WebSocket if enabled
            if self.use_websocket:
                await self._setup_websocket()

            # Start paper trading simulator if in paper mode
            if self.paper_trading:
                await self.client.start_paper_simulator()
                logger.info("Paper trading simulator started (realistic mode)")

            # Main loop
            await self._run_loop()
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        finally:
            await self._cleanup()

    async def _setup_websocket(self):
        """Setup WebSocket connection and callbacks"""
        logger.info("Connecting to WebSocket...")

        # Register callbacks
        self.client.set_orderbook_callback(self._on_orderbook_update)
        self.client.set_fill_callback(self._on_fill)
        self.client.set_trade_callback(self._on_market_trade)

        # Connect to WebSocket
        await self.client.connect_websocket(
            assets=self.target_markets,
            markets=None,  # Could add market condition IDs here for user channel
        )

        # Wait a moment for initial orderbook snapshots
        await asyncio.sleep(2)
        logger.info("WebSocket connected and subscribed")

    def _on_orderbook_update(self, orderbook: OrderBook):
        """Callback for orderbook updates from WebSocket"""
        self._orderbooks[orderbook.token_id] = orderbook

        # Update unrealized PnL
        if orderbook.mid_price:
            self.inventory_manager.update_all_unrealized({
                orderbook.token_id: orderbook.mid_price
            })

        # Mark this market for quote update
        self._pending_quote_updates[orderbook.token_id] = True

    def _on_fill(self, trade: Trade):
        """Callback for fill notifications from WebSocket"""
        logger.info(
            f"[WS FILL] {trade.side} {trade.size} @ {trade.price} "
            f"(token: {trade.token_id[:16]}...)"
        )

        # Update inventory
        self.inventory_manager.update_position(trade)

        # Record fill
        self.pnl_tracker.record_fill(trade)

        # Update adverse selection
        self.quote_engine.update_adverse_selection([trade])

        # Mark for quote update (need to adjust for new position)
        self._pending_quote_updates[trade.token_id] = True

    def _on_market_trade(self, trade: Trade):
        """Callback for market trade notifications (not our fills)"""
        # Can be used for adverse selection detection
        # or for tracking market activity
        self.quote_engine.update_adverse_selection([trade])
    
    async def stop(self):
        """Stop the bot gracefully"""
        logger.info("Stopping bot...")
        self._running = False
    
    async def _run_loop(self):
        """Main trading loop"""
        if self.use_websocket:
            await self._run_websocket_loop()
        else:
            await self._run_polling_loop()

    async def _run_polling_loop(self):
        """Main loop for REST polling mode"""
        iteration = 0
        pnl_print_interval = 60  # Print PnL every 60 seconds
        last_pnl_print = datetime.utcnow()

        while self._running:
            iteration += 1
            loop_start = datetime.utcnow()

            try:
                # 1. Fetch market data
                await self._update_market_data()

                # 2. Check for fills and update positions
                await self._check_fills()

                # 3. Check risk limits
                if self.risk_manager.check_daily_loss(self.inventory_manager):
                    logger.warning("Daily loss limit hit - pausing trading")
                    await asyncio.sleep(60)
                    continue

                # 4. Generate and update quotes
                await self._update_all_quotes()

                # 5. Cancel stale orders
                cancelled = await self.order_manager.cancel_stale_orders()
                if cancelled > 0:
                    logger.info(f"Cancelled {cancelled} stale orders")

                # 6. Record PnL snapshot
                self.pnl_tracker.record_snapshot(
                    self.inventory_manager.get_total_realized_pnl(),
                    self.inventory_manager.get_total_unrealized_pnl(),
                )

                # 7. Print status periodically
                if (datetime.utcnow() - last_pnl_print).seconds >= pnl_print_interval:
                    self._print_status()
                    last_pnl_print = datetime.utcnow()

            except Exception as e:
                logger.error(f"Error in main loop: {e}")

            # Sleep until next iteration
            elapsed = (datetime.utcnow() - loop_start).total_seconds()
            sleep_time = max(0, self.quote_refresh_interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def _run_websocket_loop(self):
        """
        Main loop for WebSocket mode.

        In WebSocket mode, orderbook updates and fills come via callbacks.
        This loop handles:
        - Processing pending quote updates
        - Periodic housekeeping (stale order cancellation)
        - PnL tracking and status printing
        """
        pnl_print_interval = 60
        last_pnl_print = datetime.utcnow()
        housekeeping_interval = 10  # Housekeeping every 10 seconds
        last_housekeeping = datetime.utcnow()

        # In WebSocket mode, we run a faster loop to process updates
        loop_interval = 0.5  # 500ms

        logger.info("Running in WebSocket mode - event-driven updates enabled")

        while self._running:
            loop_start = datetime.utcnow()

            try:
                # Check if WebSocket is still connected
                if not self.client.websocket_connected:
                    logger.warning("WebSocket disconnected - attempting reconnect...")
                    await self._setup_websocket()

                # Process pending quote updates
                await self._process_pending_quote_updates()

                # Periodic housekeeping
                housekeeping_elapsed = (datetime.utcnow() - last_housekeeping).total_seconds()
                if housekeeping_elapsed >= housekeeping_interval:
                    await self._websocket_housekeeping()
                    last_housekeeping = datetime.utcnow()

                # Record PnL snapshot
                self.pnl_tracker.record_snapshot(
                    self.inventory_manager.get_total_realized_pnl(),
                    self.inventory_manager.get_total_unrealized_pnl(),
                )

                # Print status periodically
                if (datetime.utcnow() - last_pnl_print).seconds >= pnl_print_interval:
                    self._print_status()
                    last_pnl_print = datetime.utcnow()

            except Exception as e:
                logger.error(f"Error in WebSocket loop: {e}")

            # Short sleep for responsive event handling
            elapsed = (datetime.utcnow() - loop_start).total_seconds()
            sleep_time = max(0, loop_interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def _process_pending_quote_updates(self):
        """Process markets that need quote updates"""
        async with self._quote_update_lock:
            # Get markets that need updates
            markets_to_update = [
                token_id for token_id, needs_update
                in self._pending_quote_updates.items()
                if needs_update
            ]

            # Clear pending flags
            for token_id in markets_to_update:
                self._pending_quote_updates[token_id] = False

        # Check risk limits first
        if self.risk_manager.check_daily_loss(self.inventory_manager):
            logger.warning("Daily loss limit hit - pausing trading")
            return

        # Update quotes for each market
        for token_id in markets_to_update:
            await self._update_quotes_for_market(token_id)

    async def _websocket_housekeeping(self):
        """Periodic housekeeping tasks in WebSocket mode"""
        # Cancel stale orders
        cancelled = await self.order_manager.cancel_stale_orders()
        if cancelled > 0:
            logger.info(f"Cancelled {cancelled} stale orders")

        # In WebSocket mode, we might occasionally want to verify
        # our orderbook cache is in sync (optional)
        # await self._verify_orderbooks()
    
    async def _update_market_data(self):
        """Fetch current orderbook data for all target markets"""
        for token_id in self.target_markets:
            try:
                orderbook = await self.client.get_orderbook(token_id)
                if orderbook:
                    self._orderbooks[token_id] = orderbook
                    
                    # Update unrealized PnL
                    if orderbook.mid_price:
                        self.inventory_manager.update_all_unrealized({
                            token_id: orderbook.mid_price
                        })
            except Exception as e:
                logger.warning(f"Failed to fetch orderbook for {token_id}: {e}")
    
    async def _check_fills(self):
        """Check for filled orders and update inventory"""
        try:
            trades = await self.client.get_trades(limit=20)
            
            for trade in trades:
                # Update inventory
                self.inventory_manager.update_position(trade)
                
                # Record fill
                self.pnl_tracker.record_fill(trade)
                
                # Update adverse selection
                self.quote_engine.update_adverse_selection([trade])
                
        except Exception as e:
            logger.warning(f"Error checking fills: {e}")
    
    async def _update_all_quotes(self):
        """Update quotes for all target markets"""
        for token_id in self.target_markets:
            await self._update_quotes_for_market(token_id)
    
    async def _update_quotes_for_market(self, token_id: str):
        """Update quotes for a single market"""
        orderbook = self._orderbooks.get(token_id)
        if not orderbook:
            return
        
        # Get current position
        position = self.inventory_manager.get_position(token_id)
        inventory = position.quantity
        
        # Check if we should be quoting
        should_quote, reason = self.quote_engine.should_quote(
            orderbook=orderbook,
            inventory=inventory,
            max_inventory=self.risk_manager.max_position_per_market,
        )
        
        if not should_quote:
            logger.debug(f"Not quoting {token_id}: {reason}")
            await self.order_manager.cancel_all_orders(token_id)
            return
        
        # Generate quotes
        quotes = self.quote_engine.calculate_quotes(
            token_id=token_id,
            orderbook=orderbook,
            inventory=inventory,
        )
        
        if not quotes:
            return
        
        # Filter quotes through risk manager
        filtered_bids = []
        filtered_asks = []
        
        for quote in quotes.bids:
            # Adjust size based on inventory
            adjusted_size = self.risk_manager.calculate_size_adjustment(
                self.inventory_manager, token_id, "BUY", quote.size
            )
            
            allowed, reason = self.risk_manager.check_order_allowed(
                self.inventory_manager,
                token_id,
                "BUY",
                adjusted_size,
                quote.price,
            )
            
            if allowed:
                quote.size = adjusted_size
                filtered_bids.append(quote)
        
        for quote in quotes.asks:
            adjusted_size = self.risk_manager.calculate_size_adjustment(
                self.inventory_manager, token_id, "SELL", quote.size
            )
            
            allowed, reason = self.risk_manager.check_order_allowed(
                self.inventory_manager,
                token_id,
                "SELL",
                adjusted_size,
                quote.price,
            )
            
            if allowed:
                quote.size = adjusted_size
                filtered_asks.append(quote)
        
        quotes.bids = filtered_bids
        quotes.asks = filtered_asks
        
        # Update orders
        if quotes.bids or quotes.asks:
            placed = await self.order_manager.update_quotes(token_id, quotes)
            
            if placed > 0:
                logger.debug(
                    f"Updated quotes for {token_id[:16]}...: "
                    f"FV={quotes.fair_value:.3f}, spread={quotes.spread:.3f}"
                )
    
    async def _cleanup(self):
        """Cleanup on shutdown"""
        logger.info("Cleaning up...")

        # Cancel all orders
        await self.order_manager.cancel_all_orders()

        # Print simulation stats if available
        sim_stats = self.client.get_simulation_stats()
        if sim_stats:
            print("\n" + "="*60)
            print("PAPER TRADING SIMULATION SUMMARY")
            print("="*60)
            print(f"Orders Placed:      {sim_stats.get('orders_placed', 0)}")
            print(f"Orders Filled:      {sim_stats.get('orders_filled', 0)}")
            print(f"Orders Cancelled:   {sim_stats.get('orders_cancelled', 0)}")
            print(f"Adverse Fills:      {sim_stats.get('adverse_fills', 0)}")
            print(f"Favorable Fills:    {sim_stats.get('favorable_fills', 0)}")
            print(f"Adverse Fill Rate:  {sim_stats.get('adverse_fill_rate', 0):.1%}")
            print(f"Total Volume:       ${sim_stats.get('total_volume', 0):.2f}")
            print(f"Final Balance:      ${sim_stats.get('balance', 0):.2f}")
            print("="*60 + "\n")

        # Close client
        await self.client.close()

        # Print final summary
        self.pnl_tracker.print_summary()

        logger.info("Bot stopped")
    
    def _print_status(self):
        """Print current bot status"""
        metrics = self.risk_manager.get_risk_metrics(self.inventory_manager)
        positions = self.inventory_manager.get_all_positions()
        
        print("\n" + "="*60)
        print(f"STATUS @ {datetime.utcnow().strftime('%H:%M:%S UTC')}")
        print("="*60)
        
        print(f"\n{'POSITIONS':^60}")
        print("-"*60)
        
        if positions:
            for token_id, pos in positions.items():
                print(
                    f"{token_id[:20]}...  "
                    f"Qty: {pos.quantity:>6}  "
                    f"Avg: ${pos.avg_entry_price:.2f}  "
                    f"PnL: ${pos.realized_pnl + pos.unrealized_pnl:.2f}"
                )
        else:
            print("No positions")
        
        print(f"\n{'RISK METRICS':^60}")
        print("-"*60)
        print(f"Total Exposure:     ${metrics.total_exposure:>10.2f}")
        print(f"Max Position:       {metrics.current_max_position:>10} / {metrics.max_position_size}")
        print(f"Inventory Imbalance:{metrics.inventory_imbalance:>10.2%}")
        print(f"Realized PnL:       ${metrics.realized_pnl:>10.2f}")
        print(f"Unrealized PnL:     ${metrics.unrealized_pnl:>10.2f}")
        print(f"Total PnL:          ${metrics.realized_pnl + metrics.unrealized_pnl:>10.2f}")
        
        print(f"\n{'ORDERS':^60}")
        print("-"*60)
        
        live_orders = self.order_manager.get_live_orders()
        print(f"Live orders: {len(live_orders)}")
        
        for token_id in self.target_markets:
            counts = self.order_manager.get_order_count(token_id)
            book = self._orderbooks.get(token_id)
            mid = book.mid_price if book else None
            print(
                f"{token_id[:20]}...  "
                f"Bids: {counts['BUY']:>2}  "
                f"Asks: {counts['SELL']:>2}  "
                f"Mid: ${mid:.3f}" if mid else ""
            )

        # Print simulation stats if using realistic simulator
        sim_stats = self.client.get_simulation_stats()
        if sim_stats:
            print(f"\n{'SIMULATION STATS':^60}")
            print("-"*60)
            print(f"Adverse Fill Rate:  {sim_stats.get('adverse_fill_rate', 0):>10.1%}")
            print(f"Maker Volume:       ${sim_stats.get('maker_volume', 0):>10.2f}")
            print(f"Taker Volume:       ${sim_stats.get('taker_volume', 0):>10.2f}")

        print("="*60 + "\n")


async def run_bot(
    target_markets: List[str],
    paper_trading: bool = True,
    use_websocket: bool = False,
    **kwargs
):
    """
    Convenience function to run the bot.

    Args:
        target_markets: List of token IDs to trade
        paper_trading: If True, simulate trades without real money
        use_websocket: If True, use WebSocket for real-time data
        **kwargs: Additional configuration options
    """
    import os

    # Create client
    client = PolymarketClient(
        private_key=os.getenv("POLYMARKET_PK", ""),
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
        passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
        funder_address=os.getenv("POLYMARKET_FUNDER", ""),
        paper_trading=paper_trading,
        use_websocket=use_websocket,
    )

    # Create and run bot
    bot = MarketMakingBot(
        client=client,
        target_markets=target_markets,
        paper_trading=paper_trading,
        use_websocket=use_websocket,
        **kwargs,
    )

    await bot.start()
