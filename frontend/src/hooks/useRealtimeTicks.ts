import React from 'react'
import { apiBaseUrl, fetchJson } from '../lib/utils'

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
}

type RealtimeStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

interface SnapshotResponse {
  items: RealtimeTick[]
  errors: string[]
}

function realtimeWebSocketUrl() {
  const url = new URL('/ws/realtime', apiBaseUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function normalizeCodes(tsCodes: string[]) {
  return Array.from(new Set(tsCodes.map((code) => code.trim().toUpperCase()).filter(Boolean))).sort()
}

export function useRealtimeTicks(tsCodes: string[]) {
  const [ticks, setTicks] = React.useState<Record<string, RealtimeTick>>({})
  const [status, setStatus] = React.useState<RealtimeStatus>('idle')
  const [error, setError] = React.useState<string | null>(null)
  const codes = React.useMemo(() => normalizeCodes(tsCodes), [tsCodes])
  const codesKey = codes.join(',')

  React.useEffect(() => {
    if (codes.length === 0) {
      setStatus('idle')
      setError(null)
      return
    }

    let closed = false
    const socket = new WebSocket(realtimeWebSocketUrl())
    setStatus('connecting')
    setError(null)

    socket.addEventListener('open', () => {
      if (closed) return
      setStatus('open')
      socket.send(JSON.stringify({ action: 'subscribe', ts_codes: codes }))
    })

    socket.addEventListener('message', (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as Partial<RealtimeTick> & { type?: string; detail?: string }
        if (payload.type === 'error') {
          setStatus('error')
          setError(payload.detail ?? '实时行情连接异常')
          return
        }
        if (!payload.ts_code || payload.price === undefined) return
        const tick = payload as RealtimeTick
        setError(null)
        setTicks((current) => ({ ...current, [tick.ts_code]: tick }))
      } catch {
        setStatus('error')
        setError('实时行情数据解析失败')
      }
    })

    socket.addEventListener('error', () => {
      if (!closed) {
        setStatus('error')
        setError('实时行情连接失败')
      }
    })

    socket.addEventListener('close', () => {
      if (!closed) setStatus('closed')
    })

    return () => {
      closed = true
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: 'unsubscribe', ts_codes: codes }))
      }
      socket.close()
    }
  }, [codesKey])

  React.useEffect(() => {
    if (codes.length === 0) return
    let cancelled = false
    fetchJson<SnapshotResponse>(`/api/realtime/snapshot?ts_codes=${encodeURIComponent(codes.join(','))}`)
      .then((payload) => {
        if (cancelled) return
        if (payload.errors.length > 0) {
          setError(payload.errors[0])
          return
        }
        setError(null)
        setTicks((current) => {
          const next = { ...current }
          for (const tick of payload.items) {
            next[tick.ts_code] = tick
          }
          return next
        })
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught))
      })
    return () => {
      cancelled = true
    }
  }, [codesKey])

  return { ticks, status, error }
}
