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
  WifiOff,
  LayoutDashboard,
  Shield,
  Clock,
  Percent,
  Target,
  AlertTriangle,
  Zap,
  PieChart,
  Bell,
  BellOff,
  Download,
  Filter
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import './App.css'
import ChatWidget from './components/ChatWidget'

const API_URL = import.meta.env.VITE_API_URL || 'https://polymarket-mm-bot.onrender.com'
const WS_URL = import.meta.env.VITE_WS_URL || 'wss://polymarket-mm-bot.onrender.com/ws'

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

interface SimulationStats {
  orders_placed: number
  orders_filled: number
  orders_partial: number
  orders_cancelled: number
  total_volume: number
  maker_volume: number
  taker_volume: number
  total_fees: number
  adverse_fills: number
  favorable_fills: number
  adverse_fill_rate: number
  balance: number
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
  simulation_stats: SimulationStats | null
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

interface MarketRecommendation {
  token_id: string
  question: string
  mid_price: number
  spread: number
  spread_pct: number
  bid_depth: number
  ask_depth: number
  profit_score: number
  already_active: boolean
}

type ViewType = 'dashboard' | 'admin'

function App() {
  const [botState, setBotState] = useState<BotState | null>(null)
  const [markets, setMarkets] = useState<Market[]>([])
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>([])
  const [config, setConfig] = useState<Config | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [currentView, setCurrentView] = useState<ViewType>('dashboard')
  const [botStartTime, setBotStartTime] = useState<Date | null>(null)
  const [sessionDuration, setSessionDuration] = useState<string>('00:00:00')
  const wsRef = useRef<WebSocket | null>(null)
  const [recommendations, setRecommendations] = useState<MarketRecommendation[]>([])
  const [aiExplanation, setAiExplanation] = useState<string | null>(null)
  const [loadingRecs, setLoadingRecs] = useState(false)
  const [notificationsEnabled, setNotificationsEnabled] = useState(false)
  const [lastFillsCount, setLastFillsCount] = useState(0)
  const [pnlAlertThreshold, setPnlAlertThreshold] = useState<number | null>(null)
  const [marketFilter, setMarketFilter] = useState<'all' | 'selected' | 'unselected'>('all')
  const [stopLossThreshold, setStopLossThreshold] = useState<number | null>(null)
  const [stopLossEnabled, setStopLossEnabled] = useState(false)

  // Update session duration every second
  useEffect(() => {
    const interval = setInterval(() => {
      if (botStartTime && botState?.status === 'running') {
        const diff = Date.now() - botStartTime.getTime()
        const hours = Math.floor(diff / 3600000)
        const minutes = Math.floor((diff % 3600000) / 60000)
        const seconds = Math.floor((diff % 60000) / 1000)
        setSessionDuration(
          `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
        )
      }
    }, 1000)
    return () => clearInterval(interval)
  }, [botStartTime, botState?.status])

  // Track when bot starts/stops
  useEffect(() => {
    if (botState?.status === 'running' && !botStartTime) {
      setBotStartTime(new Date())
    } else if (botState?.status !== 'running' && botStartTime) {
      setBotStartTime(null)
      setSessionDuration('00:00:00')
    }
  }, [botState?.status, botStartTime])

  // Request notification permission
  const enableNotifications = async () => {
    if ('Notification' in window) {
      const permission = await Notification.requestPermission()
      setNotificationsEnabled(permission === 'granted')
    }
  }

  // Check for new fills and send notifications
  useEffect(() => {
    if (!notificationsEnabled || !botState) return

    const currentFills = botState.fills_count || 0
    if (currentFills > lastFillsCount && lastFillsCount > 0) {
      const newFills = currentFills - lastFillsCount
      new Notification('Trade Filled!', {
        body: `${newFills} new fill${newFills > 1 ? 's' : ''} executed`,
        icon: '/favicon.ico'
      })
      // Play sound
      const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdH2Onp6WjHxwZGJqa3N8jJefoJ2TiH50b3J4gIuWnp6cloqAeXV2eoONl56enJWNhHx5eXyDjJSbm5mVkIiDfn1/gYeMkpaYlpOPioWCgYGEiIyQk5WTko6KhoSCg4WIi46RkpGPjImGhIODhYeKjI6Pj46Ni4mGhYWGh4qMjY6Ojo2LioeGhYaHiYuMjY6OjYyKiIaFhYaHiYqMjI2NjIuJh4aFhYaHiImLjIyMi4qIh4aFhYaHiImKi4uLioqIh4aFhYaGiImKi4uKiomHhoWFhYaHiImKioqJiIeGhYWFhoaIiYqKiomIh4aFhYWGh4iJiYqJiIeGhYWFhYaHiIiJiYiIh4aFhYWFhoeIiIiIiIeGhYWFhYaGh4iIiIiHh4aFhYWFhoaHiIiIh4eGhYWFhYWGhoeIiIeHh4aFhYWFhYaGh4eHh4eGhYWFhYWGhoeHh4eGhoaFhYWFhYaGh4eHhoaGhYWFhYWFhoaHh4eGhoaFhYWFhYWGhoeHh4aGhYWFhYWFhYaGhoaGhoWFhYWFhYWGhoaGhoaFhYWFhYWFhYaGhoaGhYWFhYWFhYWFhoaGhoWFhYWFhYWFhYWGhoaFhYWFhYWFhYWFhoaFhYWFhYWFhYWFhYaFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhQ==')
      audio.volume = 0.3
      audio.play().catch(() => {})
    }
    setLastFillsCount(currentFills)

    // PnL threshold alert
    if (pnlAlertThreshold !== null) {
      const totalPnl = (botState.risk_metrics?.realized_pnl || 0) + (botState.risk_metrics?.unrealized_pnl || 0)
      if (Math.abs(totalPnl) >= Math.abs(pnlAlertThreshold)) {
        new Notification('PnL Alert!', {
          body: `Total PnL: $${totalPnl.toFixed(2)} (threshold: $${pnlAlertThreshold})`,
          icon: '/favicon.ico'
        })
        setPnlAlertThreshold(null) // Reset to prevent spam
      }
    }
  }, [botState?.fills_count, botState?.risk_metrics, notificationsEnabled, lastFillsCount, pnlAlertThreshold])

  // Stop-loss auto-stop
  useEffect(() => {
    if (!stopLossEnabled || stopLossThreshold === null || !botState) return
    if (botState.status !== 'running') return

    const totalPnl = (botState.risk_metrics?.realized_pnl || 0) + (botState.risk_metrics?.unrealized_pnl || 0)
    if (totalPnl <= stopLossThreshold) {
      // Trigger stop
      stopBot()
      setStopLossEnabled(false)
      if (notificationsEnabled) {
        new Notification('Stop-Loss Triggered!', {
          body: `Bot stopped. PnL: $${totalPnl.toFixed(2)} (limit: $${stopLossThreshold})`,
          icon: '/favicon.ico'
        })
      }
      setError(`Stop-loss triggered at $${totalPnl.toFixed(2)}. Bot has been stopped.`)
    }
  }, [botState?.risk_metrics, stopLossEnabled, stopLossThreshold, botState?.status, notificationsEnabled])

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

  const fetchRecommendations = async () => {
    setLoadingRecs(true)
    try {
      const res = await fetch(`${API_URL}/api/ai/recommend-markets?limit=10`)
      if (res.ok) {
        const data = await res.json()
        setRecommendations(data.recommendations || [])
        setAiExplanation(data.ai_explanation || null)
      }
    } catch (e) {
      console.error('Failed to fetch recommendations:', e)
    } finally {
      setLoadingRecs(false)
    }
  }

  const addRecommendedMarket = async (tokenId: string) => {
    // If bot is running, use API to add market dynamically
    if (botState?.status === 'running') {
      try {
        const res = await fetch(`${API_URL}/api/bot/markets/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token_ids: [tokenId] }),
        })
        if (res.ok) {
          const data = await res.json()
          setSelectedMarkets(data.all_markets)
        }
      } catch (e) {
        console.error('Failed to add market:', e)
      }
    } else {
      // Bot not running - just update local state
      setSelectedMarkets(prev =>
        prev.includes(tokenId) ? prev : [...prev, tokenId]
      )
    }

    // Update recommendation status
    setRecommendations(prev => prev.map(r =>
      r.token_id === tokenId ? { ...r, already_active: true } : r
    ))
  }

  // Export trades to CSV
  const exportTradesToCSV = () => {
    if (!botState?.recent_trades?.length) {
      setError('No trades to export')
      return
    }

    const headers = ['Timestamp', 'Token ID', 'Side', 'Price', 'Size', 'Trade ID']
    const rows = botState.recent_trades.map(trade => [
      trade.timestamp,
      trade.token_id,
      trade.side,
      trade.price,
      trade.size,
      trade.trade_id
    ])

    const csv = [headers, ...rows].map(row => row.join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
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

  const cashout = async () => {
    if (!confirm('CASHOUT: This will cancel all orders, close all positions, and stop the bot. Continue?')) {
      return
    }

    setLoading(true)
    setError(null)

    try {
      const res = await fetch(`${API_URL}/api/bot/cashout`, { method: 'POST' })
      const data = await res.json()

      if (!res.ok) {
        throw new Error(data.detail || 'Cashout failed')
      }

      // Show results
      alert(`CASHOUT COMPLETE!\n\nOrders Cancelled: ${data.results.orders_cancelled}\nPositions Closed: ${data.results.positions_closed.length}\n\nFinal PnL:\n  Realized: $${data.results.final_pnl.realized.toFixed(2)}\n  Unrealized: $${data.results.final_pnl.unrealized.toFixed(2)}\n  Total: $${data.results.final_pnl.total.toFixed(2)}`)
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

  const filteredMarkets = markets.filter(m => {
    // Text search filter
    if (!m.question.toLowerCase().includes(searchQuery.toLowerCase())) {
      return false
    }
    // Selection filter
    if (marketFilter === 'selected' && !selectedMarkets.includes(m.yes_token_id)) {
      return false
    }
    if (marketFilter === 'unselected' && selectedMarkets.includes(m.yes_token_id)) {
      return false
    }
    return true
  })

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
          {isRunning && (
            <span className="session-timer">
              <Clock size={14} /> {sessionDuration}
            </span>
          )}
        </div>
        <nav className="header-nav">
          <button
            className={`nav-btn ${currentView === 'dashboard' ? 'active' : ''}`}
            onClick={() => setCurrentView('dashboard')}
          >
            <LayoutDashboard size={16} /> Dashboard
          </button>
          <button
            className={`nav-btn ${currentView === 'admin' ? 'active' : ''}`}
            onClick={() => setCurrentView('admin')}
          >
            <Shield size={16} /> Admin
          </button>
        </nav>
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
        {currentView === 'dashboard' ? (
          <>
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
                <>
                  <button
                    className="btn btn-danger"
                    onClick={stopBot}
                    disabled={loading}
                  >
                    <Square size={16} /> Stop Bot
                  </button>
                  <button
                    className="btn btn-cashout"
                    onClick={cashout}
                    disabled={loading}
                  >
                    <DollarSign size={16} /> Cashout
                  </button>
                </>
              )}
              <button className="btn btn-secondary" onClick={fetchStatus}>
                <RefreshCw size={16} /> Refresh
              </button>
            </div>
          </section>

          {/* Notifications */}
          <section className="card">
            <h2>{notificationsEnabled ? <Bell size={18} /> : <BellOff size={18} />} Notifications</h2>
            <div className="notification-controls">
              <button
                className={`btn ${notificationsEnabled ? 'btn-secondary' : 'btn-primary'} btn-sm`}
                onClick={enableNotifications}
                style={{ width: '100%', marginBottom: '0.5rem' }}
              >
                {notificationsEnabled ? 'Notifications Enabled' : 'Enable Notifications'}
              </button>
              <label className="pnl-alert-input">
                <span>PnL Alert Threshold ($)</span>
                <input
                  type="number"
                  step="0.01"
                  placeholder="e.g. -5.00 or 10.00"
                  value={pnlAlertThreshold ?? ''}
                  onChange={e => setPnlAlertThreshold(e.target.value ? parseFloat(e.target.value) : null)}
                />
              </label>
              <p className="notification-hint">
                Get alerts for trade fills and when PnL crosses your threshold.
              </p>
              <div className="stop-loss-section">
                <label className="checkbox stop-loss-toggle">
                  <input
                    type="checkbox"
                    checked={stopLossEnabled}
                    onChange={e => setStopLossEnabled(e.target.checked)}
                  />
                  <span>Enable Stop-Loss</span>
                </label>
                {stopLossEnabled && (
                  <label className="pnl-alert-input">
                    <span>Stop at PnL ($)</span>
                    <input
                      type="number"
                      step="0.01"
                      placeholder="e.g. -10.00"
                      value={stopLossThreshold ?? ''}
                      onChange={e => setStopLossThreshold(e.target.value ? parseFloat(e.target.value) : null)}
                    />
                  </label>
                )}
                {stopLossEnabled && stopLossThreshold !== null && (
                  <p className="stop-loss-warning">
                    Bot will auto-stop if PnL reaches ${stopLossThreshold.toFixed(2)}
                  </p>
                )}
              </div>
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

          {/* AI Market Recommendations */}
          <section className="card ai-recommendations">
            <h2><Zap size={18} /> AI Recommendations</h2>
            <button
              className="btn btn-secondary btn-sm"
              onClick={fetchRecommendations}
              disabled={loadingRecs}
              style={{ marginBottom: '0.75rem', width: '100%' }}
            >
              {loadingRecs ? 'Analyzing...' : 'Find Best Markets'}
            </button>

            {aiExplanation && (
              <div className="ai-explanation">
                {aiExplanation}
              </div>
            )}

            {recommendations.length > 0 && (
              <div className="recommendation-list">
                {recommendations.slice(0, 5).map(rec => (
                  <div key={rec.token_id} className="recommendation-item">
                    <div className="rec-info">
                      <span className="rec-question">{rec.question}</span>
                      <span className="rec-stats">
                        Spread: {rec.spread_pct}% | Score: {rec.profit_score}
                      </span>
                    </div>
                    <button
                      className={`btn btn-sm ${rec.already_active ? 'btn-secondary' : 'btn-primary'}`}
                      onClick={() => !rec.already_active && addRecommendedMarket(rec.token_id)}
                      disabled={rec.already_active}
                    >
                      {rec.already_active ? 'Active' : 'Add'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Market Selection */}
          <section className="card market-selection">
            <h2><Filter size={18} /> Markets ({selectedMarkets.length} active)</h2>
            <div className="market-filters">
              <button
                className={`filter-btn ${marketFilter === 'all' ? 'active' : ''}`}
                onClick={() => setMarketFilter('all')}
              >
                All
              </button>
              <button
                className={`filter-btn ${marketFilter === 'selected' ? 'active' : ''}`}
                onClick={() => setMarketFilter('selected')}
              >
                Selected
              </button>
              <button
                className={`filter-btn ${marketFilter === 'unselected' ? 'active' : ''}`}
                onClick={() => setMarketFilter('unselected')}
              >
                Available
              </button>
            </div>
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
            <div className="trade-history-header">
              <h2>Trade History ({botState?.fills_count || 0} total fills)</h2>
              <button
                className="btn btn-secondary btn-sm"
                onClick={exportTradesToCSV}
                disabled={!botState?.recent_trades?.length}
              >
                <Download size={14} /> Export CSV
              </button>
            </div>
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
          </>
        ) : (
          /* Admin Panel */
          <div className="admin-panel">
            {/* Session Stats */}
            <section className="admin-header">
              <div className="admin-stats-grid">
                <div className="admin-stat-card">
                  <div className="admin-stat-icon session">
                    <Clock size={24} />
                  </div>
                  <div className="admin-stat-content">
                    <span className="admin-stat-label">Session Duration</span>
                    <span className="admin-stat-value">
                      {sessionDuration}
                    </span>
                  </div>
                </div>
                <div className="admin-stat-card">
                  <div className="admin-stat-icon trades">
                    <Activity size={24} />
                  </div>
                  <div className="admin-stat-content">
                    <span className="admin-stat-label">Total Trades</span>
                    <span className="admin-stat-value">{botState?.fills_count || 0}</span>
                  </div>
                </div>
                <div className="admin-stat-card">
                  <div className="admin-stat-icon pnl">
                    <DollarSign size={24} />
                  </div>
                  <div className="admin-stat-content">
                    <span className="admin-stat-label">Total PnL</span>
                    <span className={`admin-stat-value ${totalPnL >= 0 ? 'positive' : 'negative'}`}>
                      ${totalPnL.toFixed(2)}
                    </span>
                  </div>
                </div>
                <div className="admin-stat-card">
                  <div className="admin-stat-icon winrate">
                    <Percent size={24} />
                  </div>
                  <div className="admin-stat-content">
                    <span className="admin-stat-label">Win Rate</span>
                    <span className="admin-stat-value">
                      {botState?.recent_trades && botState.recent_trades.length > 0
                        ? `${Math.round((botState.recent_trades.filter(t => t.side === 'SELL').length / botState.recent_trades.length) * 100)}%`
                        : 'N/A'}
                    </span>
                  </div>
                </div>
              </div>
            </section>

            {/* Risk Overview */}
            <section className="card admin-risk-section">
              <h2><AlertTriangle size={18} /> Risk Management</h2>
              <div className="risk-grid">
                <div className="risk-item">
                  <span className="risk-label">Total Exposure</span>
                  <span className="risk-value">${(botState?.risk_metrics?.total_exposure || 0).toFixed(2)}</span>
                </div>
                <div className="risk-item">
                  <span className="risk-label">Max Position Size</span>
                  <span className="risk-value">{botState?.risk_metrics?.max_position_size || 0}</span>
                </div>
                <div className="risk-item">
                  <span className="risk-label">Current Max Position</span>
                  <span className="risk-value">{botState?.risk_metrics?.current_max_position || 0}</span>
                </div>
                <div className="risk-item">
                  <span className="risk-label">Inventory Imbalance</span>
                  <span className="risk-value">{((botState?.risk_metrics?.inventory_imbalance || 0) * 100).toFixed(1)}%</span>
                </div>
                <div className="risk-item">
                  <span className="risk-label">Risk Status</span>
                  <span className={`risk-value ${botState?.risk_metrics?.is_halted ? 'danger' : 'safe'}`}>
                    {botState?.risk_metrics?.is_halted ? 'HALTED' : 'Normal'}
                  </span>
                </div>
                <div className="risk-item">
                  <span className="risk-label">Active Markets</span>
                  <span className="risk-value">{selectedMarkets.length}</span>
                </div>
              </div>
            </section>

            {/* Simulation Stats */}
            {botState?.simulation_stats && (
              <section className="card admin-simulation-section">
                <h2><Zap size={18} /> Simulation Statistics</h2>
                <div className="simulation-grid">
                  <div className="simulation-card primary">
                    <div className="simulation-card-header">
                      <PieChart size={20} />
                      <span>Adverse Fill Rate</span>
                    </div>
                    <div className={`simulation-card-value ${botState.simulation_stats.adverse_fill_rate > 0.3 ? 'danger' : botState.simulation_stats.adverse_fill_rate > 0.1 ? 'warning' : 'success'}`}>
                      {(botState.simulation_stats.adverse_fill_rate * 100).toFixed(1)}%
                    </div>
                    <div className="simulation-card-subtitle">
                      {botState.simulation_stats.adverse_fills} adverse / {botState.simulation_stats.favorable_fills} favorable
                    </div>
                  </div>
                  <div className="simulation-card">
                    <div className="simulation-card-header">
                      <BarChart3 size={20} />
                      <span>Maker Volume</span>
                    </div>
                    <div className="simulation-card-value">
                      ${botState.simulation_stats.maker_volume.toFixed(2)}
                    </div>
                    <div className="simulation-card-subtitle">
                      {botState.simulation_stats.total_volume > 0
                        ? `${((botState.simulation_stats.maker_volume / botState.simulation_stats.total_volume) * 100).toFixed(0)}% of total`
                        : '0% of total'}
                    </div>
                  </div>
                  <div className="simulation-card">
                    <div className="simulation-card-header">
                      <TrendingDown size={20} />
                      <span>Taker Volume</span>
                    </div>
                    <div className="simulation-card-value">
                      ${botState.simulation_stats.taker_volume.toFixed(2)}
                    </div>
                    <div className="simulation-card-subtitle">
                      {botState.simulation_stats.total_volume > 0
                        ? `${((botState.simulation_stats.taker_volume / botState.simulation_stats.total_volume) * 100).toFixed(0)}% of total`
                        : '0% of total'}
                    </div>
                  </div>
                  <div className="simulation-card">
                    <div className="simulation-card-header">
                      <DollarSign size={20} />
                      <span>Paper Balance</span>
                    </div>
                    <div className={`simulation-card-value ${botState.simulation_stats.balance >= 1000 ? 'success' : 'danger'}`}>
                      ${botState.simulation_stats.balance.toFixed(2)}
                    </div>
                    <div className="simulation-card-subtitle">
                      Started at $1,000.00
                    </div>
                  </div>
                </div>
                <div className="simulation-details">
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Orders Placed</span>
                    <span className="simulation-detail-value">{botState.simulation_stats.orders_placed}</span>
                  </div>
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Orders Filled</span>
                    <span className="simulation-detail-value">{botState.simulation_stats.orders_filled}</span>
                  </div>
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Partial Fills</span>
                    <span className="simulation-detail-value">{botState.simulation_stats.orders_partial}</span>
                  </div>
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Orders Cancelled</span>
                    <span className="simulation-detail-value">{botState.simulation_stats.orders_cancelled}</span>
                  </div>
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Total Volume</span>
                    <span className="simulation-detail-value">${botState.simulation_stats.total_volume.toFixed(2)}</span>
                  </div>
                  <div className="simulation-detail-row">
                    <span className="simulation-detail-label">Total Fees</span>
                    <span className="simulation-detail-value">${botState.simulation_stats.total_fees.toFixed(4)}</span>
                  </div>
                </div>
              </section>
            )}

            {/* Full Trade History */}
            <section className="card admin-trades-section">
              <h2><Target size={18} /> Complete Trade History</h2>
              {botState?.recent_trades && botState.recent_trades.length > 0 ? (
                <div className="admin-trades-container">
                  <table className="data-table admin-trades-table">
                    <thead>
                      <tr>
                        <th>Trade ID</th>
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
                          <td className="trade-id">{trade.trade_id.slice(0, 8)}...</td>
                          <td className="trade-time">
                            {new Date(trade.timestamp).toLocaleString()}
                          </td>
                          <td className="trade-market" title={trade.token_id}>
                            {getMarketName(trade.token_id)}
                          </td>
                          <td className={trade.side === 'BUY' ? 'buy' : 'sell'}>
                            {trade.side}
                          </td>
                          <td>${trade.price.toFixed(4)}</td>
                          <td>{trade.size.toFixed(2)}</td>
                          <td>${(trade.price * trade.size).toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-state">No trades recorded this session</p>
              )}
            </section>

            {/* Configuration Overview */}
            <section className="card admin-config-section">
              <h2><Settings size={18} /> Current Configuration</h2>
              {config && (
                <div className="config-grid">
                  <div className="config-item">
                    <span className="config-label">Trading Mode</span>
                    <span className={`config-value ${config.paper_trading ? 'paper' : 'live'}`}>
                      {config.paper_trading ? 'Paper Trading' : 'LIVE'}
                    </span>
                  </div>
                  <div className="config-item">
                    <span className="config-label">Base Spread</span>
                    <span className="config-value">{(config.base_spread * 100).toFixed(1)}%</span>
                  </div>
                  <div className="config-item">
                    <span className="config-label">Order Size</span>
                    <span className="config-value">${config.order_size}</span>
                  </div>
                  <div className="config-item">
                    <span className="config-label">Max Position</span>
                    <span className="config-value">{config.max_position}</span>
                  </div>
                  <div className="config-item">
                    <span className="config-label">Max Exposure</span>
                    <span className="config-value">${config.max_exposure}</span>
                  </div>
                  <div className="config-item">
                    <span className="config-label">WebSocket</span>
                    <span className="config-value">{config.use_websocket ? 'Enabled' : 'Disabled'}</span>
                  </div>
                </div>
              )}
            </section>

            {/* Bot Controls */}
            <section className="card admin-controls-section">
              <h2><Shield size={18} /> Bot Controls</h2>
              <div className="admin-controls">
                {!isRunning ? (
                  <button
                    className="btn btn-primary btn-lg"
                    onClick={startBot}
                    disabled={loading || selectedMarkets.length === 0}
                  >
                    <Play size={20} /> Start Bot
                  </button>
                ) : (
                  <>
                    <button
                      className="btn btn-danger btn-lg"
                      onClick={stopBot}
                      disabled={loading}
                    >
                      <Square size={20} /> Stop Bot
                    </button>
                    <button
                      className="btn btn-cashout btn-lg"
                      onClick={cashout}
                      disabled={loading}
                    >
                      <DollarSign size={20} /> Emergency Cashout
                    </button>
                  </>
                )}
                <button className="btn btn-secondary" onClick={fetchStatus}>
                  <RefreshCw size={16} /> Refresh Data
                </button>
              </div>
            </section>
          </div>
        )}
      </main>

      {/* AI Trading Assistant */}
      <ChatWidget
        botState={botState}
        markets={markets}
        apiUrl={API_URL}
      />
    </div>
  )
}

export default App
