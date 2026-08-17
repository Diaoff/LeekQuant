import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

test('watchlist page shows real-time data from websocket (no snapshot)', async ({ page }) => {
  await page.addInitScript(() => {
    type Listener = (event?: unknown) => void

    class MockWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      readyState = MockWebSocket.CONNECTING
      private listeners: Record<string, Listener[]> = {}

      constructor(public url: string) {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN
          this.emit('open')
        }, 0)
      }

      addEventListener(type: string, listener: Listener) {
        this.listeners[type] = [...(this.listeners[type] ?? []), listener]
      }

      send(data: string) {
        const message = JSON.parse(data) as { action?: string; ts_codes?: string[] }
        if (message.action !== 'subscribe') return
        setTimeout(() => {
          this.emit('message', {
            data: JSON.stringify({
              ts_code: '900001.SZ',
              price: '10.55',
              change: '0.35',
              change_pct: '3.43',
              volume: 1300,
              amount: '13715.00',
              bid1: '10.54',
              ask1: '10.56',
              ts: '2026-05-24T09:31:00+08:00',
            }),
          })
        }, 20)
      }

      close() {
        this.readyState = MockWebSocket.CLOSED
        this.emit('close')
      }

      private emit(type: string, event: unknown = {}) {
        for (const listener of this.listeners[type] ?? []) listener(event)
      }
    }

    Reflect.set(window, 'WebSocket', MockWebSocket)
  })

  await page.route(`${apiBase}/api/watchlist`, async (route) => {
    await route.fulfill({
      json: [
        {
          group_name: '默认',
          items: [
            {
              id: 1,
              ts_code: '900001.SZ',
              name: '无实时样本',
              group_name: '默认',
              added_at: '2026-05-24T08:00:00Z',
              note: null,
              latest_close: '10.20',
              pre_close: '10.00',
              latest_trade_date: '2026-05-23',
            },
          ],
        },
      ],
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({ json: [{ group_name: '默认', item_count: 1 }] })
  })

  await page.goto('/watchlist')

  const row = page.getByRole('row').filter({ hasText: '无实时样本' })
  await expect(row).toContainText('10.55')
  await expect(row).toContainText('实时')
  await expect(row).toContainText('+0.35')
  await expect(row).toContainText('+3.43%')
  await expect(row.getByText('10.2')).toHaveCount(0)
})

test('watchlist page applies realtime websocket ticks with A-share colors', async ({ page }) => {
  await page.addInitScript(() => {
    type Listener = (event?: unknown) => void

    class MockWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      readyState = MockWebSocket.CONNECTING
      private listeners: Record<string, Listener[]> = {}

      constructor(public url: string) {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN
          this.emit('open')
        }, 0)
      }

      addEventListener(type: string, listener: Listener) {
        this.listeners[type] = [...(this.listeners[type] ?? []), listener]
      }

      send(data: string) {
        const message = JSON.parse(data) as { action?: string; ts_codes?: string[] }
        if (message.action !== 'subscribe') return
        setTimeout(() => {
          this.emit('message', {
            data: JSON.stringify({
              ts_code: '900001.SZ',
              price: '10.50',
              change: '0.30',
              change_pct: '2.94',
              volume: 1200,
              amount: '12600.00',
              bid1: '10.49',
              ask1: '10.51',
              ts: '2026-05-24T09:31:00+08:00',
            }),
          })
          this.emit('message', {
            data: JSON.stringify({
              ts_code: '900002.SZ',
              price: '7.80',
              change: '-0.20',
              change_pct: '-2.50',
              volume: 800,
              amount: '6240.00',
              bid1: '7.79',
              ask1: '7.81',
              ts: '2026-05-24T09:31:00+08:00',
            }),
          })
        }, 20)
      }

      close() {
        this.readyState = MockWebSocket.CLOSED
        this.emit('close')
      }

      private emit(type: string, event: unknown = {}) {
        for (const listener of this.listeners[type] ?? []) listener(event)
      }
    }

    Reflect.set(window, 'WebSocket', MockWebSocket)
  })

  await page.route(`${apiBase}/api/watchlist`, async (route) => {
    await route.fulfill({
      json: [
        {
          group_name: '默认',
          items: [
            {
              id: 1,
              ts_code: '900001.SZ',
              name: '实时样本一',
              group_name: '默认',
              added_at: '2026-05-24T08:00:00Z',
              note: null,
              latest_close: '10.20',
              pre_close: '10.00',
              latest_trade_date: '2026-05-23',
            },
            {
              id: 2,
              ts_code: '900002.SZ',
              name: '实时样本二',
              group_name: '默认',
              added_at: '2026-05-24T08:00:00Z',
              note: null,
              latest_close: '8.00',
              pre_close: '8.20',
              latest_trade_date: '2026-05-23',
            },
          ],
        },
      ],
    })
  })
  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({ json: [{ group_name: '默认', item_count: 2 }] })
  })

  await page.goto('/watchlist')

  const upRow = page.getByRole('row').filter({ hasText: '实时样本一' })
  const downRow = page.getByRole('row').filter({ hasText: '实时样本二' })
  await expect(upRow).toContainText('10.5')
  await expect(upRow).toContainText('+0.3')
  await expect(upRow).toContainText('+2.94%')
  await expect(upRow.getByText('10.5')).toHaveClass(/text-red-600/)
  await expect(downRow).toContainText('7.8')
  await expect(downRow).toContainText('-0.2')
  await expect(downRow).toContainText('-2.5%')
  await expect(downRow.getByText('7.8')).toHaveClass(/text-emerald-600/)
})
