import React from 'react'
import { useWebSocket, type WsStatus } from './useWebSocket'

export interface SignalEvent {
  type: string
  channel: string
  data: string
}

export type SignalEventsStatus = WsStatus

export function useSignalEvents() {
  const [events, setEvents] = React.useState<SignalEvent[]>([])

  const { status } = useWebSocket({
    path: '/ws/signals',
    subscribeMsg: { action: 'subscribe' },
    onMessage: (payload) => {
      setEvents((current) => [payload as SignalEvent, ...current].slice(0, 100))
    },
  })

  const clearEvents = React.useCallback(() => setEvents([]), [])

  return { events, status, clearEvents }
}
