import React from 'react'
import { useWebSocket, type WsStatus } from './useWebSocket'

export interface TaskEvent {
  type: string
  channel: string
  data: string
}

export type TaskEventsStatus = WsStatus

export function useTaskEvents() {
  const [events, setEvents] = React.useState<TaskEvent[]>([])

  const { status } = useWebSocket({
    path: '/ws/tasks',
    subscribeMsg: { action: 'subscribe' },
    onMessage: (payload) => {
      setEvents((current) => [payload as TaskEvent, ...current].slice(0, 100))
    },
  })

  const clearEvents = React.useCallback(() => setEvents([]), [])

  return { events, status, clearEvents }
}
