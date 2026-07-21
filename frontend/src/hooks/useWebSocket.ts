import React from 'react'
import { apiBaseUrl } from '../lib/utils'

export type WsStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

const RECONNECT_BASE_DELAY = 1000
const RECONNECT_MAX_DELAY = 30000
const RECONNECT_BACKOFF_FACTOR = 2

function wsUrl(path: string) {
  const url = new URL(path, apiBaseUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export interface UseWebSocketOptions {
  path: string
  /** Called on each (re)connect to derive the WS URL. When set, the connection
   * consults this callback in ``ensureOpen`` instead of using the static
   * ``path``. Used to inject a ``?replay_from=<stream_id>`` query string that
   * reflects the latest tick seen by the caller. */
  reconnectPath?: () => string
  subscribeMsg?: Record<string, unknown>
  unsubscribeMsg?: Record<string, unknown>
  onMessage: (data: unknown) => void
  onError?: (detail: string) => void
}

// ============================================================
// Module-level WebSocket connection registry: one connection per path,
// shared across all hook instances via reference counting.
// ============================================================

interface MessageHandler {
  onMessage: (data: unknown) => void
  onError?: (detail: string) => void
}

class WebSocketConnection {
  private socket: WebSocket | null = null
  private refCount = 0
  private statusListeners = new Set<(s: WsStatus) => void>()
  private messageHandlers = new Set<MessageHandler>()
  private subscriptions = new Map<string, Record<string, unknown>>()  // key -> subscribeMsg
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = RECONNECT_BASE_DELAY
  private closed = false  // permanently closed (no refs)
  private reconnectPath: (() => string) | null = null

  constructor(private path: string) {}

  acquire(): void {
    this.refCount++
    this.closed = false
    this.ensureOpen()
  }

  release(): void {
    this.refCount = Math.max(0, this.refCount - 1)
    if (this.refCount === 0) {
      this.closePermanently()
    }
  }

  setReconnectPath(builder: (() => string) | null): void {
    this.reconnectPath = builder
  }

  addSubscription(msg: Record<string, unknown>): void {
    const key = JSON.stringify(msg)
    if (this.subscriptions.has(key)) return
    this.subscriptions.set(key, msg)
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(msg))
    }
  }

  removeSubscription(msg: Record<string, unknown>): void {
    const key = JSON.stringify(msg)
    if (!this.subscriptions.has(key)) return
    this.subscriptions.delete(key)
    // We don't auto-send unsubscribe unless caller explicitly provided one;
    // server will drop the subscription when connection closes.
  }

  onStatus(cb: (s: WsStatus) => void): () => void {
    this.statusListeners.add(cb)
    // Notify caller of current status immediately
    cb(this.currentStatus())
    return () => this.statusListeners.delete(cb)
  }

  addMessageHandler(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler)
    return () => this.messageHandlers.delete(handler)
  }

  private currentStatus(): WsStatus {
    if (!this.socket) return 'idle'
    switch (this.socket.readyState) {
      case WebSocket.CONNECTING: return 'connecting'
      case WebSocket.OPEN: return 'open'
      case WebSocket.CLOSING: return 'closed'
      case WebSocket.CLOSED: return 'closed'
      default: return 'idle'
    }
  }

  private notifyStatus(status: WsStatus): void {
    for (const cb of this.statusListeners) {
      try { cb(status) } catch { /* ignore */ }
    }
  }

  private resolvePath(): string {
    // On each (re)connect, consult the reconnectPath builder if set so the
    // URL can reflect the latest client-side state (e.g. ?replay_from=<id>).
    if (this.reconnectPath) {
      try {
        return this.reconnectPath()
      } catch {
        // Fall back to static path if builder throws
      }
    }
    return this.path
  }

  private ensureOpen(): void {
    if (this.closed) return
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) return
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }

    try {
      this.socket = new WebSocket(wsUrl(this.resolvePath()))
    } catch {
      this.notifyStatus('error')
      this.scheduleReconnect()
      return
    }
    this.notifyStatus('connecting')

    this.socket.addEventListener('open', () => {
      if (this.closed || !this.socket) return
      this.notifyStatus('open')
      this.reconnectDelay = RECONNECT_BASE_DELAY
      // Replay all subscriptions on (re)connect
      for (const msg of this.subscriptions.values()) {
        try {
          this.socket.send(JSON.stringify(msg))
        } catch { /* ignore */ }
      }
    })

    this.socket.addEventListener('message', (event) => {
      if (this.closed) return
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(String(event.data)) as Record<string, unknown>
      } catch {
        this.notifyStatus('error')
        return
      }
      if (payload.type === 'error') {
        const detail = typeof payload.detail === 'string' ? payload.detail : 'connection error'
        for (const h of this.messageHandlers) {
          try { h.onError?.(detail) } catch { /* ignore */ }
        }
        // Don't broadcast error payloads to onMessage handlers
        return
      }
      if (payload.type === 'ping') {
        // Heartbeat from server; optionally respond pong (best-effort)
        try { this.socket?.send(JSON.stringify({ type: 'pong' })) } catch { /* ignore */ }
        return
      }
      for (const h of this.messageHandlers) {
        try { h.onMessage(payload) } catch { /* ignore */ }
      }
    })

    this.socket.addEventListener('error', () => {
      if (!this.closed) this.notifyStatus('error')
    })

    this.socket.addEventListener('close', () => {
      if (this.closed) return
      this.notifyStatus('closed')
      this.scheduleReconnect()
    })
  }

  private scheduleReconnect(): void {
    if (this.closed) return
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.ensureOpen()
    }, this.reconnectDelay)
    this.reconnectDelay = Math.min(
      this.reconnectDelay * RECONNECT_BACKOFF_FACTOR,
      RECONNECT_MAX_DELAY,
    )
  }

  private closePermanently(): void {
    this.closed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.socket) {
      try { this.socket.close() } catch { /* ignore */ }
      this.socket = null
    }
    // Clear the reconnect path builder so a future connection on the same
    // path (after closePermanently) starts fresh — the builder captured
    // state from the previous caller is no longer relevant.
    this.reconnectPath = null
    this.subscriptions.clear()
    this.messageHandlers.clear()
    this.statusListeners.clear()
  }
}

const connectionRegistry = new Map<string, WebSocketConnection>()

function getConnection(path: string): WebSocketConnection {
  let conn = connectionRegistry.get(path)
  if (!conn) {
    conn = new WebSocketConnection(path)
    connectionRegistry.set(path, conn)
  }
  return conn
}

// ============================================================
// React hook: thin wrapper around shared WebSocketConnection.
// Returns { status }. Backwards-compatible with previous API.
// ============================================================

export function useWebSocket({
  path,
  reconnectPath,
  subscribeMsg,
  unsubscribeMsg,
  onMessage,
  onError,
}: UseWebSocketOptions) {
  const [status, setStatus] = React.useState<WsStatus>('idle')

  // Keep latest callbacks in refs so effect deps stay stable
  const onMessageRef = React.useRef(onMessage)
  onMessageRef.current = onMessage
  const onErrorRef = React.useRef(onError)
  onErrorRef.current = onError
  // Keep the reconnectPath builder in a ref so we can install it on the
  // connection without re-running the acquire/release effect on every render.
  const reconnectPathRef = React.useRef<(() => string) | undefined>(reconnectPath)
  reconnectPathRef.current = reconnectPath

  const subscribeMsgKey = subscribeMsg ? JSON.stringify(subscribeMsg) : ''
  const unsubscribeMsgKey = unsubscribeMsg ? JSON.stringify(unsubscribeMsg) : ''

  React.useEffect(() => {
    const conn = getConnection(path)
    // Install (or clear) the reconnect path builder. Consulted by
    // ensureOpen() on each (re)connect so the URL can reflect the latest
    // client-side state (e.g. ?replay_from=<latest_stream_id>).
    conn.setReconnectPath(reconnectPathRef.current ?? null)
    conn.acquire()
    const offStatus = conn.onStatus(setStatus)
    const offHandler = conn.addMessageHandler({
      onMessage: (data) => onMessageRef.current(data),
      onError: (detail) => onErrorRef.current?.(detail),
    })
    if (subscribeMsg) {
      conn.addSubscription(subscribeMsg)
    }
    return () => {
      if (unsubscribeMsg) {
        conn.removeSubscription(unsubscribeMsg)
      }
      offHandler()
      offStatus()
      conn.release()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  // Keep the connection's reconnectPath builder in sync with prop changes
  // without tearing down the connection. Called on every render that
  // provides a new builder reference.
  React.useEffect(() => {
    const conn = connectionRegistry.get(path)
    if (!conn) return
    conn.setReconnectPath(reconnectPathRef.current ?? null)
  }, [path, reconnectPath])

  // Subscribe message changes: add/remove without rebuilding connection.
  React.useEffect(() => {
    if (!subscribeMsg) return
    const conn = connectionRegistry.get(path)
    if (!conn) return
    conn.addSubscription(subscribeMsg)
    return () => {
      // Remove only if unsubscribeMsg not provided; otherwise caller controls removal
      if (!unsubscribeMsg) {
        conn.removeSubscription(subscribeMsg)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, subscribeMsgKey])

  // Cleanup stale subscription when unsubscribeMsg changes (caller-driven removal)
  React.useEffect(() => {
    if (!unsubscribeMsg) return
    const conn = connectionRegistry.get(path)
    if (!conn) return
    return () => {
      conn.removeSubscription(unsubscribeMsg)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, unsubscribeMsgKey])

  return { status }
}
