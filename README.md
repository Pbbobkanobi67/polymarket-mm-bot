# Polymarket Market Making Bot

A Python-based market making bot for Polymarket prediction markets. This bot provides liquidity by continuously quoting bid and ask prices, profiting from the spread while managing inventory risk.

## ⚠️ Disclaimer

**This is for educational purposes.** Market making involves significant financial risk:
- You can lose money due to adverse selection
- You can lose money if markets move against your inventory
- You can lose your entire position if a market resolves against you
- Past performance does not guarantee future results

**Never trade with money you can't afford to lose.**

## How It Works

### The Market Making Strategy

1. **Quote Both Sides**: The bot posts buy orders (bids) below fair value and sell orders (asks) above fair value
2. **Earn the Spread**: When both sides fill, you pocket the difference (the spread)
3. **Manage Inventory**: Adjust quotes to avoid accumulating too much risk on one side
4. **Repeat**: Continuously refresh quotes as the market moves

```
Example:
  Fair value: $0.50
  Bot bids:   $0.48
  Bot asks:   $0.52
  
  If both fill: Buy at $0.48, sell at $0.52 = $0.04 profit per share
```

### Key Components

- **Quote Engine**: Calculates optimal bid/ask prices based on fair value, spread, and inventory
- **Order Manager**: Handles placing, tracking, and canceling orders
- **Risk Manager**: Enforces position limits, exposure limits, and loss limits
- **Inventory Manager**: Tracks positions and calculates P&L

## Installation

```bash
# Clone or download the bot
cd polymarket-mm-bot

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Paper Trading (Simulation)

Start in paper trading mode to test without real money:

```bash
# Interactive mode - select markets from a list
python main.py

# Discover available markets
python main.py --discover

# Trade specific token IDs
python main.py --token-id <token_id_1> <token_id_2>
```

### Live Trading

**⚠️ WARNING: Live trading uses real money!**

1. Set up environment variables:

```bash
export POLYMARKET_PK="your_private_key"
export POLYMARKET_API_KEY="your_api_key"
export POLYMARKET_API_SECRET="your_api_secret"
export POLYMARKET_PASSPHRASE="your_passphrase"
export POLYMARKET_FUNDER="your_funder_address"
```

2. Generate API credentials at [reveal.polymarket.com](https://reveal.polymarket.com)

3. Run with `--live` flag:

```bash
python main.py --live --token-id <token_id>
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--spread` | 0.02 | Base spread (2 cents) |
| `--size` | 20.0 | Default order size in USDC |
| `--max-position` | 500 | Maximum shares per market |
| `--max-exposure` | 1000.0 | Maximum total USDC exposure |
| `--refresh` | 5.0 | Quote refresh interval (seconds) |
| `--min-liquidity` | 1000.0 | Min liquidity for discovery |

### Example Commands

```bash
# Conservative settings (wider spread, smaller size)
python main.py --spread 0.03 --size 10 --max-position 200

# Aggressive settings (tighter spread, larger size)
python main.py --spread 0.015 --size 50 --max-position 1000

# Fast refresh for volatile markets
python main.py --refresh 2.0 --spread 0.04
```

## Architecture

```
polymarket-mm-bot/
├── main.py              # Entry point and CLI
├── requirements.txt     # Dependencies
├── config/
│   └── settings.py      # Configuration dataclasses
└── src/
    ├── __init__.py      # Package exports
    ├── client.py        # Polymarket API client
    ├── quote_engine.py  # Quote calculation logic
    ├── order_manager.py # Order lifecycle management
    ├── risk_manager.py  # Risk and inventory management
    └── bot.py           # Main bot orchestration
```

## Risk Management

The bot includes several risk management features:

### Position Limits
- Maximum position per market (default: 500 shares)
- Maximum total exposure (default: $1000)

### Inventory Skew
When inventory builds up on one side:
- Quotes skew to encourage offsetting trades
- Spread widens to reduce risk
- Size adjusts to slow accumulation

### Daily Loss Limit
Trading halts automatically if daily loss exceeds the limit.

### Price Bounds
Bot won't quote when prices are near 0 or 1 (about to resolve).

## Understanding P&L

### Where Profit Comes From
- **Spread capture**: Buying below fair value, selling above
- You're paid for providing liquidity

### Where Losses Come From
- **Adverse selection**: Informed traders pick you off
- **Inventory risk**: Market moves against your position
- **Resolution risk**: Holding inventory when market resolves

### Realistic Expectations

| Scenario | Daily P&L |
|----------|-----------|
| Good day | +$30-50 |
| Average day | +$10-20 |
| Bad day | -$20-50 |
| Terrible day | -$100+ (resolution loss) |

Long-term profitability depends on:
- Market selection (volume, competition)
- Spread sizing (too tight = losses, too wide = no fills)
- Risk management (inventory control)

## Tips for Success

1. **Start with paper trading** - Understand the mechanics before risking real money

2. **Choose markets carefully**:
   - Good: Moderate volume, wide spreads, low competition
   - Avoid: Very high volume (HFT competition) or very low volume (no fills)

3. **Monitor actively** - Markets can move fast, especially around news events

4. **Manage inventory** - Don't let positions get too large on one side

5. **Have a stop loss** - Know when to exit if things go wrong

6. **Understand resolution risk** - Don't hold large positions near market resolution

## API Reference

### Getting Token IDs

Use the `--discover` flag to find markets:

```bash
python main.py --discover --min-liquidity 5000
```

Or fetch from the Gamma API directly:
```
GET https://gamma-api.polymarket.com/markets?active=true
```

### Polymarket API Documentation

- [CLOB API Docs](https://docs.polymarket.com/developers/CLOB/introduction)
- [Gamma API (Market Data)](https://docs.polymarket.com/developers/gamma-markets-api)

## Troubleshooting

### "Not enough balance / allowance"
- Ensure your wallet is funded with USDC.e on Polygon
- Run the allowance setup script (see Polymarket docs)

### No fills
- Check if spread is too wide
- Verify market has sufficient volume
- Ensure orders are actually posting

### Rapid losses
- Check for adverse selection (you're getting picked off)
- Market might be too competitive
- Consider widening spread or reducing size

## License

MIT License - See LICENSE file

## Contributing

Contributions welcome! Please open an issue or PR.

---

**Remember**: This is educational software. Trade responsibly and never risk more than you can afford to lose.
