"""
FastAPI Backend for Polymarket Market Making Bot

Provides REST API for bot control and WebSocket for live data streaming.
"""
import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, List, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from src import PolymarketClient, MarketMakingBot
from src.client import OrderBook, Trade, Market

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== State ====================

class BotState:
    """Global bot state manager"""
    def __init__(self):
        self.client: Optional[PolymarketClient] = None
        self.bot: Optional[MarketMakingBot] = None
        self.bot_task: Optional[asyncio.Task] = None
        self.is_running: bool = False
        self.target_markets: List[str] = []
        self.config: Dict[str, Any] = {
            "paper_trading": os.getenv("PAPER_TRADING", "true").lower() == "true",
            "use_websocket": True,
            "base_spread": 0.02,
            "order_size": 20.0,
            "max_position": 500,
            "max_exposure": 1000.0,
            "refresh_interval": 5.0,
        }
        logger.info(f"Paper trading mode: {self.config['paper_trading']}")
        # WebSocket clients for broadcasting
        self.ws_clients: List[WebSocket] = []
        self.broadcast_task: Optional[asyncio.Task] = None

    async def start_bot(self, token_ids: List[str]):
        """Start the bot with given token IDs"""
        if self.is_running:
            raise ValueError("Bot is already running")

        self.target_markets = token_ids

        self.client = PolymarketClient(
            private_key=os.getenv("POLYMARKET_PK", ""),
            api_key=os.getenv("POLYMARKET_API_KEY", ""),
            api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
            passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
            funder_address=os.getenv("POLYMARKET_FUNDER", ""),
            paper_trading=self.config["paper_trading"],
            use_websocket=self.config["use_websocket"],
        )

        self.bot = MarketMakingBot(
            client=self.client,
            target_markets=token_ids,
            paper_trading=self.config["paper_trading"],
            use_websocket=self.config["use_websocket"],
            base_spread=Decimal(str(self.config["base_spread"])),
            default_order_size=Decimal(str(self.config["order_size"])),
            max_position_per_market=self.config["max_position"],
            max_total_exposure=Decimal(str(self.config["max_exposure"])),
            quote_refresh_interval=self.config["refresh_interval"],
        )

        self.is_running = True
        self.bot_task = asyncio.create_task(self._run_bot())
        self.broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def _run_bot(self):
        """Run bot in background"""
        try:
            await self.bot.start()
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            self.is_running = False

    async def stop_bot(self):
        """Stop the bot"""
        if not self.is_running:
            return

        if self.bot:
            await self.bot.stop()

        if self.bot_task:
            self.bot_task.cancel()
            try:
                await self.bot_task
            except asyncio.CancelledError:
                pass

        if self.broadcast_task:
            self.broadcast_task.cancel()
            try:
                await self.broadcast_task
            except asyncio.CancelledError:
                pass

        if self.client:
            await self.client.close()

        self.is_running = False
        self.bot = None
        self.client = None

    async def _broadcast_loop(self):
        """Broadcast bot state to WebSocket clients"""
        while self.is_running:
            try:
                if self.ws_clients:
                    data = self.get_state_snapshot()
                    message = json.dumps(data, default=str)

                    # Send to all connected clients
                    disconnected = []
                    for ws in self.ws_clients:
                        try:
                            await ws.send_text(message)
                        except:
                            disconnected.append(ws)

                    # Remove disconnected clients
                    for ws in disconnected:
                        self.ws_clients.remove(ws)

                await asyncio.sleep(1)  # Broadcast every second
            except Exception as e:
                logger.error(f"Broadcast error: {e}")
                await asyncio.sleep(1)

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Get current bot state for broadcasting"""
        if not self.bot:
            return {
                "status": "stopped",
                "timestamp": datetime.utcnow().isoformat(),
            }

        # Get positions
        positions = []
        for token_id, pos in self.bot.inventory_manager.get_all_positions().items():
            positions.append({
                "token_id": token_id,
                "quantity": pos.quantity,
                "avg_entry_price": float(pos.avg_entry_price),
                "realized_pnl": float(pos.realized_pnl),
                "unrealized_pnl": float(pos.unrealized_pnl),
            })

        # Get orderbooks
        orderbooks = {}
        for token_id, book in self.bot._orderbooks.items():
            orderbooks[token_id] = {
                "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in book.bids[:10]],
                "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in book.asks[:10]],
                "mid_price": float(book.mid_price) if book.mid_price else None,
                "spread": float(book.spread) if book.spread else None,
            }

        # Get live orders
        live_orders = []
        for managed in self.bot.order_manager.get_live_orders():
            live_orders.append({
                "order_id": managed.order.order_id,
                "token_id": managed.token_id,
                "side": managed.order.side,
                "price": float(managed.order.price),
                "size": float(managed.order.size),
                "status": managed.order.status,
            })

        # Get risk metrics
        metrics = self.bot.risk_manager.get_risk_metrics(self.bot.inventory_manager)

        # Get PnL history (snapshots are tuples: timestamp, realized, unrealized)
        pnl_history = []
        for snapshot in self.bot.pnl_tracker._snapshots[-100:]:  # Last 100 snapshots
            timestamp, realized, unrealized = snapshot
            pnl_history.append({
                "timestamp": timestamp.isoformat(),
                "realized": float(realized),
                "unrealized": float(unrealized),
                "total": float(realized + unrealized),
            })

        return {
            "status": "running" if self.is_running else "stopped",
            "timestamp": datetime.utcnow().isoformat(),
            "paper_trading": self.config["paper_trading"],
            "use_websocket": self.config["use_websocket"],
            "target_markets": self.target_markets,
            "positions": positions,
            "orderbooks": orderbooks,
            "live_orders": live_orders,
            "risk_metrics": {
                "total_exposure": float(metrics.total_exposure),
                "max_position_size": metrics.max_position_size,
                "current_max_position": metrics.current_max_position,
                "inventory_imbalance": float(metrics.inventory_imbalance),
                "realized_pnl": float(metrics.realized_pnl),
                "unrealized_pnl": float(metrics.unrealized_pnl),
                "is_halted": self.bot.risk_manager.is_halted,
            },
            "pnl_history": pnl_history,
            "fills_count": len(self.bot.pnl_tracker._fills),
            "recent_trades": self._get_recent_trades(50),
        }

    def _get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades for display"""
        if not self.bot or not self.client:
            return []

        trades = []
        for timestamp, trade in self.bot.pnl_tracker._fills[-limit:]:
            trades.append({
                "trade_id": trade.trade_id,
                "token_id": trade.token_id,
                "side": trade.side,
                "price": float(trade.price),
                "size": float(trade.size),
                "timestamp": timestamp.isoformat(),
            })
        return list(reversed(trades))  # Most recent first


# Global state
state = BotState()


# ==================== Pydantic Models ====================

class StartBotRequest(BaseModel):
    token_ids: List[str]

class ConfigUpdate(BaseModel):
    paper_trading: Optional[bool] = None
    use_websocket: Optional[bool] = None
    base_spread: Optional[float] = None
    order_size: Optional[float] = None
    max_position: Optional[int] = None
    max_exposure: Optional[float] = None
    refresh_interval: Optional[float] = None

class MarketInfo(BaseModel):
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    active: bool


# ==================== Lifespan ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("API server starting...")
    yield
    logger.info("API server shutting down...")
    await state.stop_bot()


# ==================== FastAPI App ====================

app = FastAPI(
    title="Polymarket MM Bot API",
    description="API for controlling the Polymarket Market Making Bot",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS origins - includes Vercel production domain
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "https://polymarket-mm-bot.vercel.app",
]
# Add custom origins from environment
if os.getenv("CORS_ORIGINS"):
    CORS_ORIGINS.extend(os.getenv("CORS_ORIGINS").split(","))

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== REST Endpoints ====================

@app.get("/")
async def root():
    """Health check"""
    return {"status": "ok", "bot_running": state.is_running}


@app.get("/api/status")
async def get_status():
    """Get current bot status"""
    return state.get_state_snapshot()


@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    return state.config


@app.put("/api/config")
async def update_config(config: ConfigUpdate):
    """Update configuration (only when bot is stopped)"""
    if state.is_running:
        raise HTTPException(400, "Cannot update config while bot is running")

    for key, value in config.model_dump(exclude_none=True).items():
        if key in state.config:
            state.config[key] = value

    return state.config


@app.get("/api/markets")
async def get_markets(limit: int = Query(50, le=200)):
    """Get available markets"""
    client = PolymarketClient(paper_trading=True)
    try:
        markets = await client.get_markets(active_only=True)
        result = []
        for m in markets[:limit]:
            result.append({
                "condition_id": m.condition_id,
                "question": m.question,
                "slug": m.slug,
                "yes_token_id": m.yes_token_id,
                "no_token_id": m.no_token_id,
                "active": m.active,
            })
        return result
    finally:
        await client.close()


@app.get("/api/orderbook/{token_id}")
async def get_orderbook(token_id: str):
    """Get orderbook for a token"""
    client = PolymarketClient(paper_trading=True)
    try:
        book = await client.get_orderbook(token_id)
        if not book:
            raise HTTPException(404, "Orderbook not found")

        return {
            "token_id": token_id,
            "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in book.bids],
            "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in book.asks],
            "mid_price": float(book.mid_price) if book.mid_price else None,
            "spread": float(book.spread) if book.spread else None,
        }
    finally:
        await client.close()


@app.post("/api/bot/start")
async def start_bot(request: StartBotRequest):
    """Start the bot"""
    if state.is_running:
        raise HTTPException(400, "Bot is already running")

    if not request.token_ids:
        raise HTTPException(400, "No token IDs provided")

    await state.start_bot(request.token_ids)
    return {"status": "started", "token_ids": request.token_ids}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the bot"""
    if not state.is_running:
        raise HTTPException(400, "Bot is not running")

    await state.stop_bot()
    return {"status": "stopped"}


@app.post("/api/bot/markets/add")
async def add_market(request: StartBotRequest):
    """Add markets to a running bot"""
    if not state.is_running:
        raise HTTPException(400, "Bot is not running")

    if not request.token_ids:
        raise HTTPException(400, "No token IDs provided")

    added = []
    for token_id in request.token_ids:
        if token_id not in state.target_markets:
            state.target_markets.append(token_id)
            added.append(token_id)
            # Subscribe to new market
            if state.bot and state.client:
                await state.client.subscribe_assets([token_id])
                state.bot.target_markets.append(token_id)

    return {"status": "added", "added": added, "all_markets": state.target_markets}


@app.post("/api/bot/markets/remove")
async def remove_market(request: StartBotRequest):
    """Remove markets from a running bot"""
    if not state.is_running:
        raise HTTPException(400, "Bot is not running")

    if not request.token_ids:
        raise HTTPException(400, "No token IDs provided")

    removed = []
    for token_id in request.token_ids:
        if token_id in state.target_markets:
            state.target_markets.remove(token_id)
            removed.append(token_id)
            # Cancel orders for this market and remove from bot
            if state.bot:
                await state.bot.order_manager.cancel_all_orders(token_id)
                if token_id in state.bot.target_markets:
                    state.bot.target_markets.remove(token_id)
                if token_id in state.bot._orderbooks:
                    del state.bot._orderbooks[token_id]

    return {"status": "removed", "removed": removed, "all_markets": state.target_markets}


@app.post("/api/bot/cashout")
async def cashout():
    """
    Emergency cashout - cancel all orders and close all positions.
    This will:
    1. Cancel all open orders
    2. Market sell all long positions
    3. Market buy to close all short positions
    4. Stop the bot
    """
    if not state.bot:
        raise HTTPException(400, "Bot is not running")

    logger.info("CASHOUT INITIATED - Closing all positions")

    results = {
        "orders_cancelled": 0,
        "positions_closed": [],
        "final_pnl": {
            "realized": 0,
            "unrealized": 0,
            "total": 0
        }
    }

    try:
        # Step 1: Cancel all open orders
        if state.bot.order_manager:
            await state.bot.order_manager.cancel_all_orders()
            results["orders_cancelled"] = len(state.bot.order_manager.get_live_orders())
            logger.info(f"Cancelled all orders")

        # Step 2: Close all positions
        positions = state.bot.inventory_manager.get_all_positions()
        for token_id, position in positions.items():
            if position.quantity != 0:
                # Get current orderbook to find best price
                book = state.bot._orderbooks.get(token_id)

                if position.quantity > 0:
                    # Long position - sell to close
                    side = "SELL"
                    # Use best bid or mid price
                    price = book.bids[0]["price"] if book and book.bids else position.avg_entry_price
                else:
                    # Short position - buy to close
                    side = "BUY"
                    # Use best ask or mid price
                    price = book.asks[0]["price"] if book and book.asks else position.avg_entry_price

                size = abs(position.quantity)

                # Place market order to close
                order = await state.client.place_order(
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=Decimal(str(size)),
                )

                results["positions_closed"].append({
                    "token_id": token_id,
                    "side": side,
                    "size": size,
                    "price": float(price),
                    "pnl": float(position.realized_pnl + position.unrealized_pnl)
                })

                logger.info(f"Closed position: {side} {size} @ {price} for {token_id[:16]}...")

        # Step 3: Get final PnL
        results["final_pnl"] = {
            "realized": float(state.bot.inventory_manager.get_total_realized_pnl()),
            "unrealized": float(state.bot.inventory_manager.get_total_unrealized_pnl()),
            "total": float(
                state.bot.inventory_manager.get_total_realized_pnl() +
                state.bot.inventory_manager.get_total_unrealized_pnl()
            )
        }

        # Step 4: Stop the bot
        await state.stop_bot()

        logger.info(f"CASHOUT COMPLETE - Final PnL: ${results['final_pnl']['total']:.2f}")

    except Exception as e:
        logger.error(f"Cashout error: {e}")
        raise HTTPException(500, f"Cashout failed: {str(e)}")

    return {
        "status": "cashout_complete",
        "results": results
    }


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for live data streaming"""
    await websocket.accept()
    state.ws_clients.append(websocket)
    logger.info(f"WebSocket client connected. Total: {len(state.ws_clients)}")

    try:
        # Send initial state
        await websocket.send_text(json.dumps(state.get_state_snapshot(), default=str))

        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Handle ping/pong or commands
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_text(json.dumps({"type": "keepalive"}))
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in state.ws_clients:
            state.ws_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total: {len(state.ws_clients)}")


# ==================== Main ====================

def run_server(host: str = None, port: int = None):
    """Run the API server"""
    host = host or os.getenv("HOST", "0.0.0.0")
    port = port or int(os.getenv("PORT", "8000"))
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
