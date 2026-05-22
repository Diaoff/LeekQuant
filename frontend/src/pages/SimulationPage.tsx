import React from 'react'
import { AlertTriangle, Banknote, BriefcaseBusiness, LineChart, Loader2, Play, Plus, RefreshCw, X } from 'lucide-react'
import { ColorType, LineSeries, createChart, type IChartApi, type UTCTimestamp } from 'lightweight-charts'
import { fetchJson, formatDate, formatDateTime, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

interface Account {
  id: number
  name: string
  strategy_name: string | null
  initial_cash: string
  available_cash: string
  frozen_cash: string
  total_asset: string
  status: string
  updated_at: string
}

interface Position {
  id: number
  ts_code: string
  shares: number
  available_shares: number
  frozen_shares: number
  avg_cost: string
  current_price: string | null
  market_value: string
  unrealized_pnl: string
  profit_rate: string
}

interface Order {
  id: number
  ts_code: string
  direction: string
  price: string | null
  volume: number
  filled_volume: number
  frozen_amount: string
  status: string
  reject_reason: string | null
  submit_time: string
}

interface Trade {
  id: number
  ts_code: string
  direction: string
  price: string
  volume: number
  amount: string
  total_fee: string
  trade_time: string
}

interface CashFlow {
  id: number
  flow_type: string
  amount: string
  balance_after: string
  remark: string | null
  created_at: string
}

interface NavPoint {
  id: number
  nav_date: string
  total_asset: string
  available_cash: string
  frozen_cash: string
  position_value: string
  daily_return: string
  cumulative_nav: string
}

function money(value: string | number | null | undefined) {
  return `¥${formatNumber(value, 2)}`
}

function cnMarketTone(value: string | number | null | undefined) {
  const n = Number(value)
  if (n > 0) return 'text-red-600'
  if (n < 0) return 'text-emerald-600'
  return 'text-muted'
}

function NavChart({ points }: { points: NavPoint[] }) {
  const containerRef = React.useRef<HTMLDivElement | null>(null)
  const chartRef = React.useRef<IChartApi | null>(null)
  const ordered = React.useMemo(
    () => [...points].reverse().filter((point) => Number.isFinite(Number(point.cumulative_nav))),
    [points],
  )

  React.useEffect(() => {
    const container = containerRef.current
    if (!container || ordered.length < 2) return
    const first = Number(ordered[0].cumulative_nav)
    const last = Number(ordered[ordered.length - 1].cumulative_nav)
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 180,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.16)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.16)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart
    const series = chart.addSeries(LineSeries, {
      color: last >= first ? '#dc2626' : '#059669',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })
    series.setData(ordered.map((point) => ({
      time: Math.floor(new Date(point.nav_date).getTime() / 1000) as UTCTimestamp,
      value: Number(point.cumulative_nav),
    })))
    chart.timeScale().fitContent()
    const resizeObserver = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) })
      chart.timeScale().fitContent()
    })
    resizeObserver.observe(container)
    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [ordered])

  if (ordered.length < 2) return <div className="flex h-[180px] items-center justify-center text-sm text-muted">暂无净值曲线</div>
  return (
    <div ref={containerRef} className="h-[180px] w-full" />
  )
}

const matchModeOptions = [
  { value: 'close', label: '收盘价' },
  { value: 'open', label: '开盘价' },
  { value: 'limit', label: '限价' },
] as const

export default function SimulationPage() {
  const [accounts, setAccounts] = React.useState<Account[]>([])
  const [selectedId, setSelectedId] = React.useState<number | null>(null)
  const [positions, setPositions] = React.useState<Position[]>([])
  const [orders, setOrders] = React.useState<Order[]>([])
  const [trades, setTrades] = React.useState<Trade[]>([])
  const [cashFlow, setCashFlow] = React.useState<CashFlow[]>([])
  const [nav, setNav] = React.useState<NavPoint[]>([])
  const [loading, setLoading] = React.useState(true)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [form, setForm] = React.useState({ name: '', initial_cash: '100000' })
  const [matchModes, setMatchModes] = React.useState<Record<number, 'close' | 'open' | 'limit'>>({})

  const selected = accounts.find((account) => account.id === selectedId) ?? null

  const loadAccounts = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<Account[]>('/api/sim/accounts')
      setAccounts(data)
      setSelectedId((current) => current ?? data[0]?.id ?? null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDetail = React.useCallback(async (accountId: number) => {
    setDetailLoading(true)
    setError(null)
    try {
      const [nextPositions, nextOrders, nextTrades, nextCashFlow, nextNav] = await Promise.all([
        fetchJson<Position[]>(`/api/sim/accounts/${accountId}/positions`),
        fetchJson<Order[]>(`/api/sim/accounts/${accountId}/orders`),
        fetchJson<Trade[]>(`/api/sim/accounts/${accountId}/trades`),
        fetchJson<CashFlow[]>(`/api/sim/accounts/${accountId}/cash-flow`),
        fetchJson<NavPoint[]>(`/api/sim/accounts/${accountId}/nav`),
      ])
      setPositions(nextPositions)
      setOrders(nextOrders)
      setTrades(nextTrades)
      setCashFlow(nextCashFlow)
      setNav(nextNav)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setDetailLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void loadAccounts()
  }, [loadAccounts])

  React.useEffect(() => {
    if (selectedId) void loadDetail(selectedId)
  }, [selectedId, loadDetail])

  const createAccount = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const account = await fetchJson<Account>('/api/sim/accounts', {
        method: 'POST',
        body: JSON.stringify({ name: form.name || '模拟账户', initial_cash: form.initial_cash }),
      })
      setAccounts((prev) => [account, ...prev])
      setSelectedId(account.id)
      setNotice('账户已创建')
      setForm({ name: '', initial_cash: '100000' })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const matchOrder = async (order: Order) => {
    setError(null)
    const matchMode = matchModes[order.id] ?? 'close'
    try {
      await fetchJson(`/api/sim/orders/${order.id}/match`, {
        method: 'POST',
        body: JSON.stringify({ match_mode: matchMode }),
      })
      const label = matchModeOptions.find((option) => option.value === matchMode)?.label ?? '收盘价'
      setNotice(`委托 ${order.id} 已按${label}撮合`)
      if (selectedId) await loadDetail(selectedId)
      await loadAccounts()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const cancelOrder = async (order: Order) => {
    setError(null)
    try {
      await fetchJson(`/api/sim/orders/${order.id}/cancel`, { method: 'POST' })
      setNotice(`委托 ${order.id} 已撤单`)
      if (selectedId) await loadDetail(selectedId)
      await loadAccounts()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">模拟交易</h1>
          <p className="mt-1 text-sm text-muted">本地模拟账户、T+1 持仓、委托、成交、资金流水和净值。</p>
        </div>
        <button
          type="button"
          onClick={() => { void loadAccounts(); if (selectedId) void loadDetail(selectedId) }}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3 text-sm text-ink hover:bg-rowHover"
        >
          <RefreshCw className="h-4 w-4" />
          刷新
        </button>
      </section>

      {(error || notice) && (
        <section className={`rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`}>
          <div className="flex items-start gap-2">
            {error ? <AlertTriangle className="mt-0.5 h-4 w-4" /> : null}
            <span>{error ?? notice}</span>
          </div>
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <form onSubmit={createAccount} className="rounded-lg border border-line bg-panel p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink">
              <Plus className="h-4 w-4 text-muted" />
              新建账户
            </div>
            <div className="space-y-3">
              <input
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                placeholder="账户名称"
                className="h-9 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none"
              />
              <input
                value={form.initial_cash}
                onChange={(event) => setForm((prev) => ({ ...prev, initial_cash: event.target.value }))}
                placeholder="初始资金"
                className="h-9 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none"
              />
              <button
                disabled={saving}
                className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-60"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                创建
              </button>
            </div>
          </form>

          <div className="overflow-hidden rounded-lg border border-line bg-panel">
            <div className="border-b border-line px-4 py-3 text-sm font-medium text-ink">账户列表</div>
            {loading ? (
              <div className="p-4"><Skeleton.Table rows={4} columns={2} /></div>
            ) : accounts.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted">暂无账户</div>
            ) : (
              <div className="divide-y divide-line">
                {accounts.map((account) => (
                  <button
                    key={account.id}
                    type="button"
                    onClick={() => setSelectedId(account.id)}
                    className={`block w-full px-4 py-3 text-left hover:bg-rowHover ${selectedId === account.id ? 'bg-rowAlt' : ''}`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-ink">{account.name}</span>
                      <span className="rounded-md bg-surface px-2 py-1 text-xs text-muted">{account.status}</span>
                    </div>
                    <div className="mt-2 text-sm text-muted">{money(account.total_asset)}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        <div className="space-y-4">
          {selected ? (
            <>
              <section className="grid gap-4 md:grid-cols-4">
                {[
                  { label: '总资产', value: money(selected.total_asset), icon: <Banknote className="h-4 w-4" /> },
                  { label: '可用资金', value: money(selected.available_cash), icon: <BriefcaseBusiness className="h-4 w-4" /> },
                  { label: '冻结资金', value: money(selected.frozen_cash), icon: <X className="h-4 w-4" /> },
                  { label: '初始资金', value: money(selected.initial_cash), icon: <LineChart className="h-4 w-4" /> },
                ].map((item) => (
                  <div key={item.label} className="rounded-lg border border-line bg-panel p-4">
                    <div className="flex items-center gap-2 text-xs text-muted">{item.icon}{item.label}</div>
                    <div className="mt-2 text-lg font-semibold text-ink">{item.value}</div>
                  </div>
                ))}
              </section>

              <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                <DataTable title="持仓" loading={detailLoading} empty="暂无持仓">
                  <table className="min-w-[760px] w-full text-left text-sm">
                    <thead className="bg-tableHead text-xs text-muted">
                      <tr>
                        <th className="px-4 py-3">股票</th>
                        <th className="px-4 py-3">持仓</th>
                        <th className="px-4 py-3">可卖</th>
                        <th className="px-4 py-3">成本</th>
                        <th className="px-4 py-3">市值</th>
                        <th className="px-4 py-3">收益率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((position) => (
                        <tr key={position.id} className="border-t border-line">
                          <td className="px-4 py-3 font-medium">{position.ts_code}</td>
                          <td className="px-4 py-3">{formatNumber(position.shares)}</td>
                          <td className="px-4 py-3">{formatNumber(position.available_shares)}</td>
                          <td className="px-4 py-3">{money(position.avg_cost)}</td>
                          <td className="px-4 py-3">{money(position.market_value)}</td>
                          <td className={`px-4 py-3 ${cnMarketTone(position.profit_rate)}`}>{formatNumber(Number(position.profit_rate) * 100, 2)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </DataTable>
                <div className="rounded-lg border border-line bg-panel p-4">
                  <div className="mb-2 text-sm font-medium text-ink">NAV</div>
                  <NavChart points={nav} />
                  <div className="mt-2 text-xs text-muted">最近 {nav.length} 个快照</div>
                </div>
              </section>

              <DataTable title="委托" loading={detailLoading} empty="暂无委托">
                <table className="min-w-[900px] w-full text-left text-sm">
                  <thead className="bg-tableHead text-xs text-muted">
                    <tr>
                      <th className="px-4 py-3">时间</th>
                      <th className="px-4 py-3">股票</th>
                      <th className="px-4 py-3">方向</th>
                      <th className="px-4 py-3">价格</th>
                      <th className="px-4 py-3">数量</th>
                      <th className="px-4 py-3">状态</th>
                      <th className="px-4 py-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={order.id} className="border-t border-line">
                        <td className="px-4 py-3 text-muted">{formatDateTime(order.submit_time)}</td>
                        <td className="px-4 py-3 font-medium">{order.ts_code}</td>
                        <td className={`px-4 py-3 ${order.direction === '买入' ? 'text-red-600' : 'text-emerald-600'}`}>{order.direction}</td>
                        <td className="px-4 py-3">{money(order.price)}</td>
                        <td className="px-4 py-3">{formatNumber(order.volume)}</td>
                        <td className="px-4 py-3">{order.status}</td>
                        <td className="px-4 py-3">
                          {order.status === '待成交' ? (
                            <div className="flex items-center gap-2">
                              <select
                                value={matchModes[order.id] ?? 'close'}
                                onChange={(event) => setMatchModes((prev) => ({ ...prev, [order.id]: event.target.value as 'close' | 'open' | 'limit' }))}
                                className="h-8 rounded-md border border-line bg-surface px-2 text-xs text-ink outline-none"
                                title="撮合模式"
                              >
                                {matchModeOptions.map((option) => (
                                  <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                              </select>
                              <button type="button" onClick={() => void matchOrder(order)} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line hover:bg-rowHover" title="撮合">
                                <Play className="h-4 w-4" />
                              </button>
                              <button type="button" onClick={() => void cancelOrder(order)} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line hover:bg-rowHover" title="撤单">
                                <X className="h-4 w-4" />
                              </button>
                            </div>
                          ) : '暂无'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </DataTable>

              <section className="grid gap-4 xl:grid-cols-2">
                <DataTable title="成交" loading={detailLoading} empty="暂无成交">
                  <table className="min-w-[720px] w-full text-left text-sm">
                    <thead className="bg-tableHead text-xs text-muted">
                      <tr>
                        <th className="px-4 py-3">时间</th>
                        <th className="px-4 py-3">股票</th>
                        <th className="px-4 py-3">方向</th>
                        <th className="px-4 py-3">金额</th>
                        <th className="px-4 py-3">费用</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((trade) => (
                        <tr key={trade.id} className="border-t border-line">
                          <td className="px-4 py-3 text-muted">{formatDateTime(trade.trade_time)}</td>
                          <td className="px-4 py-3 font-medium">{trade.ts_code}</td>
                          <td className={`px-4 py-3 ${trade.direction === '买入' ? 'text-red-600' : 'text-emerald-600'}`}>{trade.direction}</td>
                          <td className="px-4 py-3">{money(trade.amount)}</td>
                          <td className="px-4 py-3">{money(trade.total_fee)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </DataTable>
                <DataTable title="资金流水" loading={detailLoading} empty="暂无流水">
                  <table className="min-w-[720px] w-full text-left text-sm">
                    <thead className="bg-tableHead text-xs text-muted">
                      <tr>
                        <th className="px-4 py-3">时间</th>
                        <th className="px-4 py-3">类型</th>
                        <th className="px-4 py-3">金额</th>
                        <th className="px-4 py-3">余额</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cashFlow.map((flow) => (
                        <tr key={flow.id} className="border-t border-line">
                          <td className="px-4 py-3 text-muted">{formatDateTime(flow.created_at)}</td>
                          <td className="px-4 py-3">{flow.flow_type}</td>
                          <td className={`px-4 py-3 ${cnMarketTone(flow.amount)}`}>{money(flow.amount)}</td>
                          <td className="px-4 py-3">{money(flow.balance_after)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </DataTable>
              </section>
            </>
          ) : (
            <div className="rounded-lg border border-line bg-panel p-10 text-center text-sm text-muted">选择或创建一个模拟账户</div>
          )}
        </div>
      </section>
    </div>
  )
}

function DataTable({
  title,
  loading,
  empty,
  children,
}: {
  title: string
  loading: boolean
  empty: string
  children: React.ReactElement
}) {
  const hasRows = React.Children.count(children.props.children?.[1]?.props?.children) > 0
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="border-b border-line px-4 py-3 text-sm font-medium text-ink">{title}</div>
      {loading ? (
        <div className="p-4"><Skeleton.Table rows={5} columns={4} /></div>
      ) : hasRows ? (
        <div className="overflow-x-auto">{children}</div>
      ) : (
        <div className="p-8 text-center text-sm text-muted">{empty}</div>
      )}
    </div>
  )
}
