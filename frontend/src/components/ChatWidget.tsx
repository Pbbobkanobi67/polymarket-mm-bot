import { useState, useEffect, useRef, useCallback } from 'react'
import { MessageCircle, X, Send, Loader2, Trash2, Sparkles } from 'lucide-react'
import './ChatWidget.css'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  isStreaming?: boolean
}

interface BotState {
  status: string
  paper_trading: boolean
  positions: any[]
  risk_metrics: any
  [key: string]: any
}

interface Market {
  question: string
  yes_token_id: string
  no_token_id: string
}

interface ChatWidgetProps {
  botState: BotState | null
  markets: Market[]
  apiUrl: string
}

export default function ChatWidget({ apiUrl }: ChatWidgetProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [aiAvailable, setAiAvailable] = useState<boolean | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Check AI availability on mount
  useEffect(() => {
    const checkAI = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/ai/status`)
        const data = await res.json()
        setAiAvailable(data.available)
      } catch {
        setAiAvailable(false)
      }
    }
    checkAI()
  }, [apiUrl])

  // Fetch suggestions when opening
  useEffect(() => {
    if (isOpen && aiAvailable) {
      const fetchSuggestions = async () => {
        try {
          const res = await fetch(`${apiUrl}/api/ai/suggestions`)
          const data = await res.json()
          setSuggestions(data.suggestions || [])
        } catch {
          setSuggestions([])
        }
      }
      fetchSuggestions()
    }
  }, [isOpen, aiAvailable, apiUrl])

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when opening
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  const sendMessage = useCallback(async (message: string) => {
    if (!message.trim() || isLoading) return

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: message.trim(),
      timestamp: new Date(),
    }

    setMessages(prev => [...prev, userMessage])
    setInputValue('')
    setIsLoading(true)

    // Add placeholder for assistant response
    const assistantMessageId = `assistant-${Date.now()}`
    setMessages(prev => [...prev, {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
    }])

    try {
      const res = await fetch(`${apiUrl}/api/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: message.trim(),
          conversation_id: conversationId,
        }),
      })

      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.detail || 'Failed to send message')
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let fullContent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))

              if (data.conversation_id && !conversationId) {
                setConversationId(data.conversation_id)
              }

              if (data.content) {
                fullContent += data.content
                setMessages(prev => prev.map(msg =>
                  msg.id === assistantMessageId
                    ? { ...msg, content: fullContent }
                    : msg
                ))
              }

              if (data.done) {
                setMessages(prev => prev.map(msg =>
                  msg.id === assistantMessageId
                    ? { ...msg, isStreaming: false }
                    : msg
                ))
              }

              if (data.error) {
                throw new Error(data.content || 'AI error')
              }
            } catch (e) {
              // Ignore parse errors for incomplete chunks
            }
          }
        }
      }
    } catch (error: any) {
      setMessages(prev => prev.map(msg =>
        msg.id === assistantMessageId
          ? { ...msg, content: `Error: ${error.message}`, isStreaming: false }
          : msg
      ))
    } finally {
      setIsLoading(false)
    }
  }, [apiUrl, conversationId, isLoading])

  const clearChat = useCallback(async () => {
    if (conversationId) {
      try {
        await fetch(`${apiUrl}/api/ai/conversation/${conversationId}`, {
          method: 'DELETE',
        })
      } catch {
        // Ignore errors
      }
    }
    setMessages([])
    setConversationId(null)
  }, [apiUrl, conversationId])

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(inputValue)
    }
  }

  // Render collapsed bubble
  if (!isOpen) {
    return (
      <div className="chat-widget">
        <button
          className="chat-bubble"
          onClick={() => setIsOpen(true)}
          title="AI Trading Assistant"
        >
          <MessageCircle size={24} />
        </button>
      </div>
    )
  }

  // Render expanded panel
  return (
    <div className="chat-widget">
      <div className="chat-panel">
        {/* Header */}
        <div className="chat-header">
          <div className="chat-header-title">
            <Sparkles size={18} />
            <span>AI Trading Assistant</span>
          </div>
          <div className="chat-header-actions">
            {messages.length > 0 && (
              <button
                className="chat-header-btn"
                onClick={clearChat}
                title="Clear chat"
              >
                <Trash2 size={16} />
              </button>
            )}
            <button
              className="chat-header-btn"
              onClick={() => setIsOpen(false)}
              title="Close"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="chat-messages">
          {aiAvailable === false && (
            <div className="chat-notice error">
              AI assistant not configured. Set ANTHROPIC_API_KEY on the server.
            </div>
          )}

          {messages.length === 0 && aiAvailable && (
            <div className="chat-welcome">
              <Sparkles size={32} className="welcome-icon" />
              <h3>AI Trading Assistant</h3>
              <p>Ask me about market conditions, trading strategies, or explain any metrics you see.</p>

              {suggestions.length > 0 && (
                <div className="chat-suggestions">
                  {suggestions.slice(0, 4).map((suggestion, i) => (
                    <button
                      key={i}
                      className="suggestion-btn"
                      onClick={() => sendMessage(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map(message => (
            <div
              key={message.id}
              className={`chat-message ${message.role}`}
            >
              <div className="message-content">
                {message.content || (message.isStreaming && (
                  <span className="typing-indicator">
                    <span></span><span></span><span></span>
                  </span>
                ))}
              </div>
              <div className="message-time">
                {message.timestamp.toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="chat-input-container">
          <input
            ref={inputRef}
            type="text"
            className="chat-input"
            placeholder={aiAvailable ? "Ask about markets, strategies, metrics..." : "AI not available"}
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            disabled={!aiAvailable || isLoading}
          />
          <button
            className="chat-send-btn"
            onClick={() => sendMessage(inputValue)}
            disabled={!aiAvailable || isLoading || !inputValue.trim()}
          >
            {isLoading ? <Loader2 size={18} className="spinning" /> : <Send size={18} />}
          </button>
        </div>
      </div>
    </div>
  )
}
