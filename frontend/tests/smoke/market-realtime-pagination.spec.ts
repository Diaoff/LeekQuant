import { expect, test } from '@playwright/test'

const apiBase = 'http://localhost:8000'

test('market page uses realtime websocket tick and advanced pagination with 20 rows per page', async ({ page }) => {
  const stockRequests: string[] = []

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
        if (!message.ts_codes?.includes('900001.SZ')) return
        setTimeout(() => {
          this.emit('message', {
            data: JSON.stringify({
              ts_code: '900001.SZ',
              price: '10.68',
              change: '-0.02',
              change_pct: '-0.19',
              volume: 954133,
              amount: '1020057083.13',
              bid1: '10.68',
              ask1: '10.69',
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

  await page.route(`${apiBase}/api/watchlist/groups`, async (route) => {
    await route.fulfill({ json: [{ group_name: '默认', item_count: 0 }] })
  })

  await page.route(`${apiBase}/api/stocks**`, async (route) => {
    const url = new URL(route.request().url())
    const requestedPage = Number(url.searchParams.get('page') ?? '1')
    stockRequests.push(route.request().url())
    const start = (requestedPage - 1) * 20
    await route.fulfill({
      json: {
        items: Array.from({ length: 20 }, (_, index) => {
          const ordinal = start + index + 1
          const symbol = String(900000 + ordinal).slice(-6)
          return {
            ts_code: `${symbol}.SZ`,
            symbol,
            name: `市场样本${ordinal}`,
            industry: '银行',
            list_date: '2026-01-01',
            exchange: 'SZ',
            is_delisted: false,
            is_st: false,
            latest_close: '99.99',
            latest_trade_date: '2026-05-22',
            pe_ttm: '8.10',
            pb: '0.90',
            market_cap: '12300000000',
            daily_kline_count: 120,
          }
        }),
        page: requestedPage,
        page_size: 20,
        total: 42,
      },
    })
  })

  await page.goto('/market')

  const firstRow = page.getByRole('row').filter({ has: page.getByText('市场样本1', { exact: true }) })
  await expect(firstRow).toContainText('10.68')
  await expect(firstRow).toContainText('-0.19%')
  await expect(firstRow).toContainText('954,133')
  await expect(firstRow.getByText('99.99')).toHaveCount(0)
  await expect(firstRow.getByText('10.68')).toHaveClass(/text-emerald-600/)

  await expect(page.getByText('20 条/页')).toBeVisible()
  await expect(page.getByText('第 1/3 页')).toBeVisible()
  expect(stockRequests.every((url) => new URL(url).searchParams.get('page_size') === '20')).toBe(true)

  await page.getByRole('button', { name: '2' }).click()
  await expect(page.getByText('市场样本21')).toBeVisible()
  await expect
    .poll(() => stockRequests.some((url) => new URL(url).searchParams.get('page') === '2'))
    .toBe(true)
})
