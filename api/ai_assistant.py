"""
AI Trading Assistant for Polymarket Market Making Bot
Powered by Claude API (Anthropic)
"""

import os
import json
import uuid
from typing import AsyncGenerator, Dict, List, Optional, Any
from datetime import datetime
from anthropic import Anthropic

# System prompt for trading domain expertise
TRADING_ASSISTANT_PROMPT = """You are an AI trading assistant for a Polymarket market making bot. Your role is to help traders understand their bot's performance, analyze market conditions, and make informed decisions.

CAPABILITIES:
- Analyze market conditions and provide actionable insights
- Explain metrics, strategies, and bot behavior in clear terms
- Recommend markets to trade based on spread, volume, and liquidity analysis
- Answer questions about positions, risk exposure, and performance
- Suggest trading strategies based on current market conditions

IMPORTANT CONSTRAINTS:
- You provide INSIGHTS and RECOMMENDATIONS only - you cannot execute trades or modify bot settings
- Base all recommendations on the provided bot state data
- Be concise but thorough in explanations
- Use specific numbers from the data when available
- Acknowledge uncertainty when data is insufficient

DOMAIN KNOWLEDGE:
- Polymarket is a prediction market platform where users trade on event outcomes
- Market making involves quoting both bid (buy) and ask (sell) prices to earn the spread
- Key metrics to understand:
  - Spread: Difference between best ask and best bid - wider spread = more profit per trade but fewer fills
  - Mid price: Average of best bid and ask - represents fair value estimate
  - Inventory imbalance: Ratio of long vs short positions (-1 to 1, 0 = balanced)
  - Adverse selection: When fills happen because price moved against you (bad fills)
  - Exposure: Total capital at risk across all positions

MARKET ANALYSIS TIPS:
- Wider spreads indicate less competitive markets with more profit potential
- High volume markets have more opportunities but also more competition
- Inventory imbalance indicates directional risk - may need to adjust quotes
- Adverse fill rate > 30% suggests quotes are stale or strategy needs adjustment

When providing recommendations:
- Always explain the reasoning behind suggestions
- Consider risk factors and current exposure
- Prioritize capital preservation for new traders
- Suggest specific markets with data to support recommendations

Format responses with:
- Clear structure using bullet points when listing multiple items
- Bold text for key metrics or important warnings using **text**
- Specific numbers and percentages from the provided data
- Actionable next steps when appropriate"""


class TradingAssistant:
    """AI Trading Assistant powered by Claude API"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the assistant with Anthropic API key"""
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        self.client = Anthropic(api_key=self.api_key)
        self.model = os.getenv("AI_MODEL", "claude-3-5-haiku-20241022")
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "1024"))

        # Conversation history storage (in-memory, keyed by conversation_id)
        self.conversations: Dict[str, List[Dict[str, str]]] = {}

    def build_context(self, bot_state: Optional[Dict[str, Any]]) -> str:
        """Build context string from current bot state"""
        if not bot_state:
            return "BOT STATE: Not available (bot may not be running)"

        context_parts = []

        # Basic status
        context_parts.append(f"""CURRENT BOT STATE
=================
Status: {bot_state.get('status', 'unknown')}
Mode: {'Paper Trading (Simulation)' if bot_state.get('paper_trading') else 'LIVE Trading'}
Timestamp: {bot_state.get('timestamp', 'N/A')}
WebSocket: {'Enabled' if bot_state.get('use_websocket') else 'Polling mode'}""")

        # Target markets
        target_markets = bot_state.get('target_markets', [])
        if target_markets:
            context_parts.append(f"\nACTIVE MARKETS: {len(target_markets)} markets being traded")

        # Positions
        positions = bot_state.get('positions', [])
        if positions:
            pos_lines = ["POSITIONS", "---------"]
            for pos in positions:
                token_id = pos.get('token_id', 'unknown')[:8]
                qty = pos.get('quantity', 0)
                entry = pos.get('avg_entry_price', 0)
                realized = pos.get('realized_pnl', 0)
                unrealized = pos.get('unrealized_pnl', 0)
                total_pnl = realized + unrealized
                direction = "LONG" if qty > 0 else "SHORT" if qty < 0 else "FLAT"
                pos_lines.append(
                    f"- {token_id}... | {direction} {abs(qty)} @ ${entry:.3f} | PnL: ${total_pnl:+.2f}"
                )
            context_parts.append("\n".join(pos_lines))
        else:
            context_parts.append("\nPOSITIONS: None (no open positions)")

        # Risk metrics
        risk = bot_state.get('risk_metrics', {})
        if risk:
            context_parts.append(f"""
RISK METRICS
------------
- Total Exposure: ${risk.get('total_exposure', 0):.2f}
- Max Position Size: {risk.get('current_max_position', 0)} / {risk.get('max_position_size', 0)} limit
- Inventory Imbalance: {risk.get('inventory_imbalance', 0):.1%}
- Realized PnL: ${risk.get('realized_pnl', 0):+.2f}
- Unrealized PnL: ${risk.get('unrealized_pnl', 0):+.2f}
- Trading Status: {'HALTED' if risk.get('is_halted') else 'Normal'}""")

        # Orderbooks summary
        orderbooks = bot_state.get('orderbooks', {})
        if orderbooks:
            ob_lines = ["ORDERBOOK SUMMARY", "-----------------"]
            for token_id, ob in list(orderbooks.items())[:5]:  # Limit to 5
                mid = ob.get('mid_price', 0)
                spread = ob.get('spread', 0)
                bid_depth = sum(b.get('size', 0) for b in ob.get('bids', [])[:3])
                ask_depth = sum(a.get('size', 0) for a in ob.get('asks', [])[:3])
                ob_lines.append(
                    f"- {token_id[:8]}... | Mid: ${mid:.3f} | Spread: ${spread:.3f} ({spread/mid*100 if mid else 0:.1f}%) | Depth: {bid_depth:.0f}B / {ask_depth:.0f}A"
                )
            context_parts.append("\n".join(ob_lines))

        # Live orders
        live_orders = bot_state.get('live_orders', [])
        if live_orders:
            context_parts.append(f"\nLIVE ORDERS: {len(live_orders)} active orders in the book")

        # Recent trades
        recent_trades = bot_state.get('recent_trades', [])
        if recent_trades:
            trade_lines = ["RECENT TRADES (Last 5)", "----------------------"]
            for trade in recent_trades[:5]:
                side = trade.get('side', 'N/A')
                price = trade.get('price', 0)
                size = trade.get('size', 0)
                ts = trade.get('timestamp', '')[:19] if trade.get('timestamp') else 'N/A'
                trade_lines.append(f"- {side} {size} @ ${price:.3f} at {ts}")
            context_parts.append("\n".join(trade_lines))

        fills_count = bot_state.get('fills_count', 0)
        if fills_count:
            context_parts.append(f"\nTOTAL SESSION FILLS: {fills_count}")

        # Simulation stats (paper trading only)
        sim_stats = bot_state.get('simulation_stats')
        if sim_stats and bot_state.get('paper_trading'):
            context_parts.append(f"""
SIMULATION STATISTICS
---------------------
- Orders Placed: {sim_stats.get('orders_placed', 0)}
- Orders Filled: {sim_stats.get('orders_filled', 0)}
- Partial Fills: {sim_stats.get('orders_partial', 0)}
- Adverse Fill Rate: {sim_stats.get('adverse_fill_rate', 0):.1%}
- Maker Volume: ${sim_stats.get('maker_volume', 0):.2f}
- Taker Volume: ${sim_stats.get('taker_volume', 0):.2f}
- Paper Balance: ${sim_stats.get('balance', 0):.2f}""")

        # PnL trend
        pnl_history = bot_state.get('pnl_history', [])
        if len(pnl_history) >= 2:
            first_pnl = pnl_history[0].get('total', 0) if pnl_history else 0
            last_pnl = pnl_history[-1].get('total', 0) if pnl_history else 0
            trend = "UP" if last_pnl > first_pnl else "DOWN" if last_pnl < first_pnl else "FLAT"
            context_parts.append(f"\nPnL TREND: {trend} (from ${first_pnl:.2f} to ${last_pnl:.2f})")

        return "\n\n".join(context_parts)

    def get_or_create_conversation(self, conversation_id: Optional[str]) -> tuple[str, List[Dict[str, str]]]:
        """Get existing conversation or create new one"""
        if conversation_id and conversation_id in self.conversations:
            return conversation_id, self.conversations[conversation_id]

        new_id = conversation_id or str(uuid.uuid4())[:8]
        self.conversations[new_id] = []
        return new_id, self.conversations[new_id]

    async def chat_stream(
        self,
        message: str,
        bot_state: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream a chat response with bot state context"""

        # Get or create conversation
        conv_id, history = self.get_or_create_conversation(conversation_id)

        # Build context from bot state
        context = self.build_context(bot_state)

        # Build messages for Claude
        messages = []

        # Add conversation history (last 10 messages to manage context)
        for msg in history[-10:]:
            messages.append(msg)

        # Add current user message with context
        user_message = f"""<bot_state>
{context}
</bot_state>

User question: {message}"""

        messages.append({"role": "user", "content": user_message})

        # Store user message in history (without context for cleaner history)
        history.append({"role": "user", "content": message})

        try:
            # Stream response from Claude
            full_response = ""

            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=TRADING_ASSISTANT_PROMPT,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    full_response += text
                    yield {
                        "content": text,
                        "done": False,
                        "conversation_id": conv_id
                    }

            # Store assistant response in history
            history.append({"role": "assistant", "content": full_response})

            # Final message indicating completion
            yield {
                "content": "",
                "done": True,
                "conversation_id": conv_id
            }

        except Exception as e:
            yield {
                "content": f"Error: {str(e)}",
                "done": True,
                "error": True,
                "conversation_id": conv_id
            }

    def clear_conversation(self, conversation_id: str) -> bool:
        """Clear a conversation history"""
        if conversation_id in self.conversations:
            del self.conversations[conversation_id]
            return True
        return False

    def get_suggested_questions(self, bot_state: Optional[Dict[str, Any]] = None) -> List[str]:
        """Get contextual suggested questions based on bot state"""
        base_questions = [
            "What markets should I trade based on current spreads?",
            "Explain my current risk exposure",
            "What does the adverse fill rate mean?",
            "Suggest a strategy for current market conditions",
            "Summarize my trading performance",
        ]

        if not bot_state:
            return base_questions

        # Add contextual questions based on state
        contextual = []

        risk = bot_state.get('risk_metrics', {})
        if risk.get('inventory_imbalance', 0) > 0.3:
            contextual.append("My inventory is imbalanced - what should I do?")

        if risk.get('is_halted'):
            contextual.append("Why is trading halted and how do I resume?")

        sim_stats = bot_state.get('simulation_stats')
        if sim_stats and sim_stats.get('adverse_fill_rate', 0) > 0.2:
            contextual.append("My adverse fill rate is high - how can I improve?")

        positions = bot_state.get('positions', [])
        if positions:
            contextual.append("Should I close any of my current positions?")

        return contextual[:3] + base_questions[:3]  # Mix contextual and base
