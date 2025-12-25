import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Play,
  Square,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Activity,
  DollarSign,
  BarChart3,
  Settings,
  Wifi,
  WifiOff
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import './App.css'

const API_URL = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws'

interface Position {
  token_id: string
  quantity: number
  avg_entry_price: number
  realized_pnl: number
  unrealized_pnl: number
}

interface OrderLevel {
  price: number
  size: number
}

interface OrderBook {
  bids: OrderLevel[]
  asks: OrderLevel[]
  mid_price: number | null
  spread: number | null
}

interface Order {
  order_id: string
  token_id: string
  side: string
  price: number
  size: number
  status: string
}

interface RiskMetrics {
  total_exposure: number
  max_position_size: number
  current_max_position: number
  inventory_imbalance: number
  realized_pnl: number
  unrealized_pnl: number
  is_halted: boolean
}

interface PnLSnapshot {
  timestamp: string
  realized: number
  unrealized: number
  total: number
}

interface Trade {
  trade_id: string
  token_id: string
  side: string
  price: number
  size: number
  timestamp: string
}

interface BotState {
  status: string
  timestamp: string
  paper_trading: boolean
  use_websocket: boolean
  target_markets: string[]
  positions: Position[]
  orderbooks: Record<string, OrderBook>
  live_orders: Order[]
  risk_metrics: RiskMetrics
  pnl_history: PnLSnapshot[]
  fills_count: number
  recent_trades: Trade[]
}

interface Market {
  condition_id: string
  question: string
  slug: string
  yes_token_id: string
  no_token_id: string
  active: boolean
}

interface Config {
  paper_trading: boolean
  use_websocket: boolean
  base_spread: number
  order_size: number
  max_position: number
  max_exposure: number
  refresh_interval: number
}

function App() {
  const [botState, setBotState] = useState<BotState | null>(null)
  const [markets, setMarkets] = useState<Market[]>([])
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>([])
  const [config, setConfig] = useState<Config | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const wsRef = useRef<WebSocket | null>(null)

  // Helper to get market name from token ID
  const getMarketName = useCallback((tokenId: string): string => {
    for (const market of markets) {
      if (market.yes_token_id === tokenId) {
        return `${market.question.slice(0, 40)}${market.question.length > 40 ? '...' : ''} (YES)`
      }
      if (market.no_token_id === tokenId) {
        return `${market.question.slice(0, 40)}${market.question.length > 40 ? '...' : ''} (NO)`
      }
    }
    return `${tokenId.slice(0, 12)}...`
  }, [markets])

  // Fetch initial data
  useEffect(() => {
    fetchMarkets()
    fetchConfig()
    fetchStatus()
  }, [])

  // WebSocket connection
  useEffect(() => {
    connectWebSocket()
    return () => {
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [])

  const connectWebSocket = useCallback(() => {
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setWsConnected(true)
      setError(null)
    }

    ws.onclose = () => {
      setWsConnected(false)
      // Reconnect after 3 seconds
      setTimeout(connectWebSocket, 3000)
    }

    ws.onerror = () => {
      setWsConnected(false)
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'keepalive') return
        if (data.status) {
          setBotState(data)
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e)
      }
    }
  }, [])

  const fetchMarkets = async () => {
    try {
      const res = await fetch(`${API_URL}/api/markets?limit=100`)
      if (res.ok) {
        const data = await res.json()
        setMarkets(data)
      }
    } catch (e) {
      console.error('Failed to fetch markets:', e)
    }
  }

  const fetchConfig = async () => {
    try {
      const res = await fetch(`${API_URL}/api/config`)
      if (res.ok) {
        const data = await res.json()
        setConfig(data)
      }
    } catch (e) {
      console.error('Failed to fetch config:', e)
    }
  }

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/api/status`)
      if (res.ok) {
        const data = await res.json()
        setBotState(data)
      }
    } catch (e) {
      console.error('Failed to fetch status:', e)
    }
  }

  const startBot = async () => {
    if (selectedMarkets.length === 0) {
      setError('Please select at least one market')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const res = await fetch(`${API_URL}/api/bot/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token_ids: selectedMarkets }),
      })

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Failed to start bot')
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const stopBot = async () => {
    setLoading(true)
    setError(null)

    try {
      const res = await fetch(`${API_URL}/api/bot/stop`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Failed to stop bot')
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const updateConfig = async (updates: Partial<Config>) => {
    try {
      const res = await fetch(`${API_URL}/api/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      })
      if (res.ok) {
        const data = await res.json()
        setConfig(data)
      }
    } catch (e) {
      console.error('Failed to update config:', e)
    }
  }

  const toggleMarket = async (tokenId: string) => {
    const isSelected = selectedMarkets.includes(tokenId)

    // If bot is running, use API to add/remove markets dynamically
    if (botState?.status === 'running') {
      try {
        const endpoint = isSelected ? '/api/bot/markets/remove' : '/api/bot/markets/add'
        const res = await fetch(`${API_URL}${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token_ids: [tokenId] }),
        })

        if (res.ok) {
          const data = await res.json()
          setSelectedMarkets(data.all_markets)
        } else {
          const data = await res.json()
          setError(data.detail || 'Failed to update markets')
        }
      } catch (e: any) {
        setError(e.message)
      }
    } else {
      // Bot not running - just update local state
      setSelectedMarkets(prev =>
        isSelected
          ? prev.filter(id => id !== tokenId)
          : [...prev, tokenId]
      )
    }
  }

  const filteredMarkets = markets.filter(m =>
    m.question.toLowerCase().includes(searchQuery.toLowerCase())
  )

  const isRunning = botState?.status === 'running'
  const totalPnL = (botState?.risk_metrics?.realized_pnl || 0) + (botState?.risk_metrics?.unrealized_pnl || 0)

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-left">
          <h1>Polymarket MM Bot</h1>
          <span className={`status-badge ${isRunning ? 'running' : 'stopped'}`}>
            {isRunning ? 'Running' : 'Stopped'}
          </span>
          {botState?.paper_trading && (
            <span className="paper-badge">Paper Trading</span>
          )}
        </div>
        <div className="header-right">
          <span className={`ws-status ${wsConnected ? 'connected' : 'disconnected'}`}>
            {wsConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
            {wsConnected ? 'Live' : 'Disconnected'}
          </span>
        </div>
      </header>

      {error && (
        <div className="error-banner">
          {error}
          <button onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      <main className="main">
        {/* Left Panel - Controls */}
        <aside className="sidebar">
          {/* Bot Controls */}
          <section className="card">
            <h2><Settings size={18} /> Controls</h2>
            <div className="controls">
              {!isRunning ? (
                <button
                  className="btn btn-primary"
                  onClick={startBot}
                  disabled={loading || selectedMarkets.length === 0}
                >
                  <Play size={16} /> Start Bot
                </button>
              ) : (
                <button
                  className="btn btn-danger"
                  onClick={stopBot}
                  disabled={loading}
                >
                  <Square size={16} /> Stop Bot
                </button>
              )}
              <button className="btn btn-secondary" onClick={fetchStatus}>
                <RefreshCw size={16} /> Refresh
              </button>
            </div>
          </section>

          {/* Config */}
          {config && !isRunning && (
            <section className="card">
              <h2>Configuration</h2>
              <div className="config-form">
                <label>
                  <span>Base Spread</span>
                  <input
                    type="number"
                    step="0.001"
                    value={config.base_spread}
                    onChange={e => updateConfig({ base_spread: parseFloat(e.target.value) })}
                  />
                </label>
                <label>
                  <span>Order Size ($)</span>
                  <input
                    type="number"
                    value={config.order_size}
                    onChange={e => updateConfig({ order_size: parseFloat(e.target.value) })}
                  />
                </label>
                <label>
                  <span>Max Position</span>
                  <input
                    type="number"
                    value={config.max_position}
                    onChange={e => updateConfig({ max_position: parseInt(e.target.value) })}
                  />
                </label>
                <label>
                  <span>Max Exposure ($)</span>
                  <input
                    type="number"
                    value={config.max_exposure}
                    onChange={e => updateConfig({ max_exposure: parseFloat(e.target.value) })}
                  />
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={config.use_websocket}
                    onChange={e => updateConfig({ use_websocket: e.target.checked })}
                  />
                  <span>Use WebSocket</span>
                </label>
              </div>
            </section>
          )}

          {/* Market Selection */}
          <section className="card market-selection">
            <h2>Markets ({selectedMarkets.length} active)</h2>
            <input
              type="text"
              placeholder="Search markets..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="search-input"
            />
            <div className="market-list">
              {filteredMarkets.slice(0, 50).map(market => (
                <label key={market.yes_token_id} className="market-item">
                  <input
                    type="checkbox"
                    checked={selectedMarkets.includes(market.yes_token_id)}
                    onChange={() => toggleMarket(market.yes_token_id)}
                  />
                  <span className="market-question">{market.question}</span>
                </label>
              ))}
            </div>
          </section>
        </aside>

        {/* Main Content */}
        <div className="content">
          {/* Stats Row */}
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-icon"><DollarSign size={20} /></div>
              <div className="stat-content">
                <span className="stat-label">Total PnL</span>
                <span className={`stat-value ${totalPnL >= 0 ? 'positive' : 'negative'}`}>
                  ${totalPnL.toFixed(2)}
                </span>
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-icon"><TrendingUp size={20} /></div>
              <div className="stat-content">
                <span className="stat-label">Realized</span>
                <span className="stat-value">${(botState?.risk_metrics?.realized_pnl || 0).toFixed(2)}</span>
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-icon"><TrendingDown size={20} /></div>
              <div className="stat-content">
                <span className="stat-label">Unrealized</span>
                <span className="stat-value">${(botState?.risk_metrics?.unrealized_pnl || 0).toFixed(2)}</span>
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-icon"><Activity size={20} /></div>
              <div className="stat-content">
                <span className="stat-label">Fills</span>
                <span className="stat-value">{botState?.fills_count || 0}</span>
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-icon"><BarChart3 size={20} /></div>
              <div className="stat-content">
                <span className="stat-label">Exposure</span>
                <span className="stat-value">${(botState?.risk_metrics?.total_exposure || 0).toFixed(2)}</span>
              </div>
            </div>
          </div>

          {/* PnL Chart */}
          {botState?.pnl_history && botState.pnl_history.length > 0 && (
            <section className="card chart-card">
              <h2>PnL History</h2>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={botState.pnl_history}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(v) => new Date(v).toLocaleTimeString()}
                    stroke="#888"
                  />
                  <YAxis stroke="#888" />
                  <Tooltip
                    contentStyle={{ background: '#1a1a2e', border: '1px solid #333' }}
                    labelFormatter={(v) => new Date(v).toLocaleTimeString()}
                  />
                  <Line type="monotone" dataKey="total" stroke="#00d4aa" dot={false} />
                  <Line type="monotone" dataKey="realized" stroke="#4a9eff" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </section>
          )}

          {/* Positions & Orders */}
          <div className="grid-2">
            {/* Positions */}
            <section className="card">
              <h2>Positions</h2>
              {botState?.positions && botState.positions.length > 0 ? (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Token</th>
                      <th>Qty</th>
                      <th>Avg Price</th>
                      <th>PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {botState.positions.map(pos => (
                      <tr key={pos.token_id}>
                        <td className="token-id">{pos.token_id.slice(0, 12)}...</td>
                        <td className={pos.quantity >= 0 ? 'positive' : 'negative'}>
                          {pos.quantity}
                        </td>
                        <td>${pos.avg_entry_price.toFixed(3)}</td>
                        <td className={(pos.realized_pnl + pos.unrealized_pnl) >= 0 ? 'positive' : 'negative'}>
                          ${(pos.realized_pnl + pos.unrealized_pnl).toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="empty-state">No positions</p>
              )}
            </section>

            {/* Live Orders */}
            <section className="card">
              <h2>Live Orders ({botState?.live_orders?.length || 0})</h2>
              {botState?.live_orders && botState.live_orders.length > 0 ? (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Size</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {botState.live_orders.slice(0, 20).map(order => (
                      <tr key={order.order_id}>
                        <td className={order.side === 'BUY' ? 'buy' : 'sell'}>
                          {order.side}
                        </td>
                        <td>${order.price.toFixed(3)}</td>
                        <td>{order.size.toFixed(2)}</td>
                        <td>{order.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="empty-state">No live orders</p>
              )}
            </section>
          </div>

          {/* Trade History */}
          <section className="card">
            <h2>Trade History ({botState?.fills_count || 0} total fills)</h2>
            {botState?.recent_trades && botState.recent_trades.length > 0 ? (
              <div className="trade-history-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Market</th>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Size</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {botState.recent_trades.map(trade => (
                      <tr key={trade.trade_id}>
                        <td className="trade-time">
                          {new Date(trade.timestamp).toLocaleTimeString()}
                        </td>
                        <td className="trade-market" title={trade.token_id}>
                          {getMarketName(trade.token_id)}
                        </td>
                        <td className={trade.side === 'BUY' ? 'buy' : 'sell'}>
                          {trade.side}
                        </td>
                        <td>${trade.price.toFixed(3)}</td>
                        <td>{trade.size.toFixed(2)}</td>
                        <td>${(trade.price * trade.size).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="empty-state">No trades yet</p>
            )}
          </section>

          {/* Orderbooks */}
          {botState?.orderbooks && Object.keys(botState.orderbooks).length > 0 && (
            <section className="card">
              <h2>Orderbooks</h2>
              <div className="orderbooks-grid">
                {Object.entries(botState.orderbooks).map(([tokenId, book]) => (
                  <div key={tokenId} className="orderbook">
                    <h3 title={tokenId}>{getMarketName(tokenId)}</h3>
                    <div className="orderbook-header">
                      <span>Mid: ${book.mid_price?.toFixed(3) || '-'}</span>
                      <span>Spread: ${book.spread?.toFixed(3) || '-'}</span>
                    </div>
                    <div className="orderbook-content">
                      <div className="orderbook-side asks">
                        <h4>Asks</h4>
                        {book.asks.slice(0, 5).map((level, i) => (
                          <div key={i} className="level">
                            <span className="price">${level.price.toFixed(3)}</span>
                            <span className="size">{level.size.toFixed(0)}</span>
                          </div>
                        ))}
                      </div>
                      <div className="orderbook-side bids">
                        <h4>Bids</h4>
                        {book.bids.slice(0, 5).map((level, i) => (
                          <div key={i} className="level">
                            <span className="price">${level.price.toFixed(3)}</span>
                            <span className="size">{level.size.toFixed(0)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
