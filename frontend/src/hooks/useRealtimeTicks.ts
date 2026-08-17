import React from 'react'
import { useWebSocket } from './useWebSocket'

export interface RealtimeTick {
  ts_code: string
  price: string
  change: string
  change_pct: string
  volume: number
  amount: string
  bid1: string | null
  ask1: string | null
  ts: string
  /** Redis Stream ID — set by backend when persistence is enabled.
   * Tracked so client can replay missed ticks via ?replay_from=<stream_id>
   * after a reconnect. Forward-compat: undefined when backend doesn't emit. */
  stream_id?: string
}

type RealtimeStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

function normalizeCodes(tsCodes: string[]) {
  return Array.from(new Set(tsCodes.map((code) => code.trim().toUpperCase()).filter(Boolean))).sort()
}

export function useRealtimeTicks(tsCodes: string[]) {
  const [ticks, setTicks] = React.useState<Record<string, RealtimeTick>>({})
  const [status, setStatus] = React.useState<RealtimeStatus>('idle')
  const [error, setError] = React.useState<string | null>(null)
  const codes = React.useMemo(() => normalizeCodes(tsCodes), [tsCodes])
  const codesKey = codes.join(',')

  // Track the latest Redis stream_id so we can ask the server to replay any
  // ticks produced during a disconnect window on the next reconnect.
  const lastTickStreamIdRef = React.useRef<string | undefined>(undefined)

  const subscribeMsg = React.useMemo(
    () => codes.length > 0 ? { action: 'subscribe', ts_codes: codes } as Record<string, unknown> : undefined,
    [codesKey],
  )

  // On each (re)connect, build the WS URL with ?replay_from=<stream_id> if we
  // have one. Called by WebSocketConnection.ensureOpen() via reconnectPath.
  const buildWsPath = React.useCallback((): string => {
    const streamId = lastTickStreamIdRef.current
    if (!streamId) return '/ws/realtime'
    return `/ws/realtime?replay_from=${encodeURIComponent(streamId)}`
  }, [])

  useWebSocket({
    path: '/ws/realtime',
    reconnectPath: buildWsPath,
    subscribeMsg,
    onMessage: (payload) => {
      const tick = payload as Partial<RealtimeTick>
      if (!tick.ts_code || tick.price === undefined) return
      if (tick.stream_id) {
        lastTickStreamIdRef.current = tick.stream_id
      }
      setError(null)
      setTicks((current) => ({ ...current, [tick.ts_code!]: tick as RealtimeTick }))
    },
    onError: (detail) => setError(detail),
  })

  return { ticks, status, error }
}
