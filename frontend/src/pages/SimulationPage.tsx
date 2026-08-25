import React from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, Banknote, BriefcaseBusiness, LineChart, Loader2, Plus, RefreshCw, Trash2, X, GitBranch, ShieldCheck } from 'lucide-react'
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
  position_value: string
  unrealized_pnl: string
  today_pnl: string
  today_pnl_rate: string
  total_asset: string
  valuation_source: string
  valuation_error: string | null
  status: string
  config: Record<string, unknown> | null
  updated_at: string
}

interface Position {
  id: number
  ts_code: string
  stock_name: string | null
  shares: number
  available_shares: number
  frozen_shares: number
  avg_cost: string
  current_price: string | null
  market_value: string
  unrealized_pnl: string
  profit_rate: string
  today_pnl: string
  today_pnl_rate: string
  closed_today?: boolean
}

type PositionSortKey = 'unrealized_pnl'
type SortDirection = 'asc' | 'desc'

interface Order {
  id: number
  ts_code: string
  stock_name: string | null
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
  stock_name: string | null
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

interface RiskGuardStatus {
  status: 'running' | 'stale' | 'missing'
  last_seen_at: string | null
  seconds_since_seen: number | null
  loaded_positions: number
  tracked_symbols: number
  last_error: string | null
  last_trigger: Record<string, unknown> | null
  last_blocked_reason: string | null
}

function money(value: string | number | null | undefined) {
  return `¥${formatNumber(value, 2)}`
}

function fixedMoney(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  return `¥${new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numeric)}`
}

function cnMarketTone(value: string | number | null | undefined) {
  const n = Number(value)
  if (n > 0) return 'text-red-600'
  if (n < 0) return 'text-emerald-600'
  return 'text-muted'
}

function signedMoney(value: string | number | null | undefined) {
  const n = Number(value)
  const sign = n > 0 ? '+' : ''
  return `${sign}${money(value)}`
}

function signedFixedMoney(value: string | number | null | undefined) {
  const n = Number(value)
  const sign = n > 0 ? '+' : ''
  return `${sign}${fixedMoney(value)}`
}

function formatPercentFromRatio(value: unknown) {
  if (value === null || value === undefined || value === '') return ''
  return formatNumber(Number(value) * 100, 4)
}

function signedPercentFromRatio(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === '') return '暂无'
  const n = Number(value)
  if (!Number.isFinite(n)) return '暂无'
  const sign = n > 0 ? '+' : ''
  return `${sign}${formatNumber(n * 100, digits)}%`
}

function formatSeconds(value: number | null | undefined) {
  if (value === null || value === undefined) return '暂无'
  if (value < 60) return `${value} 秒前`
  return `${Math.floor(value / 60)} 分钟前`
}

function riskGuardLabel(status: RiskGuardStatus['status']) {
  if (status === 'running') return '运行中'
  if (status === 'stale') return '已过期'
  return '未发现'
}

function RiskGuardSection({
  status,
  takeProfitBreached,
}: {
  status: RiskGuardStatus | null
  takeProfitBreached: boolean
}) {
  const tone = status?.status === 'running'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : 'border-amber-200 bg-amber-50 text-amber-800'
  const message = !status
    ? '正在读取后台守护状态'
    : status.status === 'running'
      ? status.last_blocked_reason
        ? `最近阻断: ${status.last_blocked_reason}`
        : status.last_trigger
          ? `最近触发: ${String(status.last_trigger.ts_code ?? '')} ${String(status.last_trigger.reason ?? '')}`
          : '后台守护正在轮询实时行情'
      : takeProfitBreached
        ? '持仓已达到止盈线，但后台守护未运行或未及时轮询'
        : '后台守护未运行或心跳过期'

  return (
    <section className={`rounded-lg border p-4 text-sm ${tone}`}>
      <div className="flex flex-wrap items-center gap-3">
        <ShieldCheck className="h-4 w-4 shrink-0" />
        <span className="font-medium">实时风控守护</span>
        <span className="rounded-md border border-current/20 px-2 py-0.5 text-xs">{status ? riskGuardLabel(status.status) : '读取中'}</span>
        {status?.last_seen_at ? <span className="text-xs opacity-80">最近心跳 {formatSeconds(status.seconds_since_seen)}</span> : null}
      </div>
      <div className="mt-2">{message}</div>
      {status ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs opacity-80">
          <span>持仓 {formatNumber(status.loaded_positions)}</span>
          <span>股票 {formatNumber(status.tracked_symbols)}</span>
          {status.last_error ? <span>错误: {status.last_error}</span> : null}
          {status.last_seen_at ? <span>{formatDateTime(status.last_seen_at)}</span> : null}
        </div>
      ) : null}
    </section>
  )
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

function RiskConfigSection({
  account,
  patchAccount,
  saving,
}: {
  account: Account
  patchAccount: (id: number, body: Record<string, unknown>, notice?: string) => Promise<void>
  saving: boolean
}) {
  const riskConfig = (account.config as Record<string, unknown> | null)?.['risk_config'] as Record<string, unknown> | undefined ?? {}
  const [editing, setEditing] = React.useState(false)
  const [form, setForm] = React.useState({
    stop_loss_pct: formatPercentFromRatio(riskConfig.stop_loss_pct),
    take_profit_pct: formatPercentFromRatio(riskConfig.take_profit_pct),
    trailing_stop_pct: formatPercentFromRatio(riskConfig.trailing_stop_pct),
    trailing_activation_pct: formatPercentFromRatio(riskConfig.trailing_activation_pct),
    time_stop_days: String(riskConfig.time_stop_days ?? ''),
  })

  const handleSave = () => {
    const rc: Record<string, number> = {}
    if (form.stop_loss_pct) rc.stop_loss_pct = Number(form.stop_loss_pct) / 100
    if (form.take_profit_pct) rc.take_profit_pct = Number(form.take_profit_pct) / 100
    if (form.trailing_stop_pct) rc.trailing_stop_pct = Number(form.trailing_stop_pct) / 100
    if (form.trailing_activation_pct) rc.trailing_activation_pct = Number(form.trailing_activation_pct) / 100
    if (form.time_stop_days) rc.time_stop_days = Number(form.time_stop_days)
    void patchAccount(account.id, { config: { risk_config: rc } }, '风控配置已更新')
    setEditing(false)
  }

  return (
    <section className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-center gap-3">
        <AlertTriangle className="h-4 w-4 text-muted" />
        <span className="text-sm font-medium text-ink">风控设置</span>
        {!editing && (
          <button
            type="button"
            onClick={() => {
              setForm({
                stop_loss_pct: formatPercentFromRatio(riskConfig.stop_loss_pct),
                take_profit_pct: formatPercentFromRatio(riskConfig.take_profit_pct),
                trailing_stop_pct: formatPercentFromRatio(riskConfig.trailing_stop_pct),
                trailing_activation_pct: formatPercentFromRatio(riskConfig.trailing_activation_pct),
                time_stop_days: riskConfig.time_stop_days != null ? String(riskConfig.time_stop_days) : '',
              })
              setEditing(true)
            }}
            className="ml-auto text-sm font-medium text-accent hover:underline"
          >
            编辑
          </button>
        )}
      </div>
      {editing ? (
        <div className="mt-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs text-muted">
              止损 (%)
              <input value={form.stop_loss_pct} onChange={(e) => setForm((f) => ({ ...f, stop_loss_pct: e.target.value }))} className="h-8 rounded border border-line bg-surface px-2 text-sm text-ink outline-none" placeholder="例: 8" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              止盈 (%)
              <input value={form.take_profit_pct} onChange={(e) => setForm((f) => ({ ...f, take_profit_pct: e.target.value }))} className="h-8 rounded border border-line bg-surface px-2 text-sm text-ink outline-none" placeholder="例: 20" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              移动止盈回撤 (%)
              <input value={form.trailing_stop_pct} onChange={(e) => setForm((f) => ({ ...f, trailing_stop_pct: e.target.value }))} className="h-8 rounded border border-line bg-surface px-2 text-sm text-ink outline-none" placeholder="例: 10" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              移动止盈激活 (%)
              <input value={form.trailing_activation_pct} onChange={(e) => setForm((f) => ({ ...f, trailing_activation_pct: e.target.value }))} className="h-8 rounded border border-line bg-surface px-2 text-sm text-ink outline-none" placeholder="例: 15" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              时间止损 (天)
              <input value={form.time_stop_days} onChange={(e) => setForm((f) => ({ ...f, time_stop_days: e.target.value }))} className="h-8 rounded border border-line bg-surface px-2 text-sm text-ink outline-none" placeholder="例: 60" />
            </label>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              disabled={saving}
              onClick={handleSave}
              className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-xs font-medium text-white disabled:opacity-60"
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-xs font-medium text-muted hover:underline"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-5 gap-2 text-xs text-muted">
          <div>止损: <span className="text-ink">{riskConfig.stop_loss_pct != null ? `${formatPercentFromRatio(riskConfig.stop_loss_pct)}%` : '-'}</span></div>
          <div>止盈: <span className="text-ink">{riskConfig.take_profit_pct != null ? `${formatPercentFromRatio(riskConfig.take_profit_pct)}%` : '-'}</span></div>
          <div>移动回撤: <span className="text-ink">{riskConfig.trailing_stop_pct != null ? `${formatPercentFromRatio(riskConfig.trailing_stop_pct)}%` : '-'}</span></div>
          <div>移动激活: <span className="text-ink">{riskConfig.trailing_activation_pct != null ? `${formatPercentFromRatio(riskConfig.trailing_activation_pct)}%` : '-'}</span></div>
          <div>时间止损: <span className="text-ink">{riskConfig.time_stop_days != null ? `${riskConfig.time_stop_days}天` : '-'}</span></div>
        </div>
      )}
    </section>
  )
}

function SeesawSection({
  account,
  patchAccount,
  saving,
}: {
  account: Account
  patchAccount: (id: number, body: Record<string, unknown>, notice?: string) => Promise<void>
  saving: boolean
}) {
  const seesawEnabled = Boolean((account.config as Record<string, unknown> | null)?.['seesaw_enabled'])
  const seesawMode = String((account.config as Record<string, unknown> | null)?.['seesaw_mode'] ?? 'normal')
  const inDefensive = seesawMode === 'defensive'

  const toggle = () => {
    void patchAccount(
      account.id,
      { config: { seesaw_enabled: !seesawEnabled } },
      seesawEnabled ? '已关闭跷跷板自动切换' : '已启用跷跷板自动切换',
    )
  }

  return (
    <section className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-center gap-3">
        <ShieldCheck className="h-4 w-4 text-muted" />
        <span className="text-sm font-medium text-ink">跷跷板避险自动切换</span>
        <button
          type="button"
          disabled={saving}
          onClick={toggle}
          className={
            'ml-auto inline-flex h-7 items-center rounded-md px-3 text-xs font-medium ' +
            (seesawEnabled ? 'bg-accent text-white' : 'border border-line text-muted hover:text-ink')
          }
        >
          {seesawEnabled ? '已启用' : '未启用'}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted">
        大盘转入弱势时自动清仓权益并等权买入避险库；恢复后清回现金。当前状态：
        <span className="text-ink">{inDefensive ? '避险中' : '常态'}</span>
        。默认仅推荐，需在此显式启用才会自动交易。
      </p>
    </section>
  )
}

export default function SimulationPage() {
  const [accounts, setAccounts] = React.useState<Account[]>([])
  const [selectedId, setSelectedId] = React.useState<number | null>(null)
  const [positions, setPositions] = React.useState<Position[]>([])
  const [orders, setOrders] = React.useState<Order[]>([])
  const [trades, setTrades] = React.useState<Trade[]>([])
  const [cashFlow, setCashFlow] = React.useState<CashFlow[]>([])
  const [nav, setNav] = React.useState<NavPoint[]>([])
  const [riskGuardStatus, setRiskGuardStatus] = React.useState<RiskGuardStatus | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [deletingId, setDeletingId] = React.useState<number | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [form, setForm] = React.useState({ name: '', initial_cash: '100000', strategy_id: '' })
  const [strategies, setStrategies] = React.useState<{ id: number; name: string }[]>([])
  const [editingStrategy, setEditingStrategy] = React.useState<{ id: number; selected: string } | null>(null)
  const [positionSort, setPositionSort] = React.useState<{ key: PositionSortKey; direction: SortDirection } | null>(null)

  const selected = accounts.find((account) => account.id === selectedId) ?? null
  const selectedRiskConfig = (selected?.config as Record<string, unknown> | null)?.['risk_config'] as Record<string, unknown> | undefined ?? {}
  const takeProfitPct = Number(selectedRiskConfig.take_profit_pct ?? 0)
  const takeProfitBreached = takeProfitPct > 0 && positions.some((position) => Number(position.profit_rate) >= takeProfitPct)
  const sortedPositions = React.useMemo(() => {
    if (!positionSort) return positions
    const direction = positionSort.direction === 'desc' ? -1 : 1
    return [...positions].sort((left, right) => {
      const leftValue = Number(left[positionSort.key])
      const rightValue = Number(right[positionSort.key])
      const normalizedLeft = Number.isFinite(leftValue) ? leftValue : Number.NEGATIVE_INFINITY
      const normalizedRight = Number.isFinite(rightValue) ? rightValue : Number.NEGATIVE_INFINITY
      return (normalizedLeft - normalizedRight) * direction
    })
  }, [positions, positionSort])
  const togglePositionSort = (key: PositionSortKey) => {
    setPositionSort((current) => {
      if (!current || current.key !== key) return { key, direction: 'desc' }
      return { key, direction: current.direction === 'desc' ? 'asc' : 'desc' }
    })
  }
  const positionSortIcon = (key: PositionSortKey) => {
    if (positionSort?.key !== key) return <ArrowDown className="h-3.5 w-3.5 opacity-30" aria-hidden="true" />
    return positionSort.direction === 'desc'
      ? <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
      : <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
  }

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

  const loadRiskGuardStatus = React.useCallback(async () => {
    try {
      setRiskGuardStatus(await fetchJson<RiskGuardStatus>('/api/realtime/risk-guard/status'))
    } catch {
      setRiskGuardStatus({
        status: 'missing',
        last_seen_at: null,
        seconds_since_seen: null,
        loaded_positions: 0,
        tracked_symbols: 0,
        last_error: '状态接口不可用',
        last_trigger: null,
        last_blocked_reason: null,
      })
    }
  }, [])

  const loadStrategies = React.useCallback(async () => {
    try {
      const data = await fetchJson<{ id: number; name: string; status: string }[]>('/api/strategies')
      setStrategies(data)
    } catch { /* ignore */ }
  }, [])

  React.useEffect(() => {
    void loadAccounts()
    void loadStrategies()
    void loadRiskGuardStatus()
  }, [loadAccounts, loadStrategies])

  React.useEffect(() => {
    if (selectedId) void loadDetail(selectedId)
  }, [selectedId, loadDetail])

  const createAccount = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const body: Record<string, unknown> = { name: form.name || '模拟账户', initial_cash: form.initial_cash }
      if (form.strategy_id) body.strategy_id = parseInt(form.strategy_id, 10)
      const account = await fetchJson<Account>('/api/sim/accounts', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setAccounts((prev) => [account, ...prev])
      setSelectedId(account.id)
      setNotice('账户已创建')
      setForm({ name: '', initial_cash: '100000', strategy_id: '' })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const patchAccount = async (accountId: number, body: Record<string, unknown>, noticeMsg?: string) => {
    setSaving(true)
    setError(null)
    try {
      const updated = await fetchJson<Account>(`/api/sim/accounts/${accountId}`, { method: 'PATCH', body: JSON.stringify(body) })
      setAccounts((prev) => prev.map((a) => a.id === accountId ? updated : a))
      setNotice(noticeMsg ?? '账户已更新')
      setEditingStrategy(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const deleteAccount = async (account: Account) => {
    if (!window.confirm(`删除账户「${account.name}」？该账户的持仓、委托、成交、资金流水和净值记录会一并删除。`)) return
    setDeletingId(account.id)
    setError(null)
    try {
      await fetchJson(`/api/sim/accounts/${account.id}`, { method: 'DELETE' })
      setAccounts((prev) => {
        const next = prev.filter((item) => item.id !== account.id)
        if (selectedId === account.id) {
          setSelectedId(next[0]?.id ?? null)
          if (next.length === 0) {
            setPositions([])
            setOrders([])
            setTrades([])
            setCashFlow([])
            setNav([])
          }
        }
        return next
      })
      setNotice('账户已删除')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setDeletingId(null)
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
          onClick={() => { void loadAccounts(); void loadRiskGuardStatus(); if (selectedId) void loadDetail(selectedId) }}
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
              <div className="relative">
                <select
                  value={form.strategy_id}
                  onChange={(event) => setForm((prev) => ({ ...prev, strategy_id: event.target.value }))}
                  className="h-9 w-full appearance-none rounded-md border border-line bg-surface px-3 pr-8 text-sm text-ink outline-none"
                >
                  <option value="">不绑定策略</option>
                  {strategies.map((st) => (
                    <option key={st.id} value={st.id}>{st.name}</option>
                  ))}
                </select>
                <GitBranch className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              </div>
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
                  <div
                    key={account.id}
                    className={`group flex items-start gap-2 px-4 py-3 hover:bg-rowHover ${selectedId === account.id ? 'bg-rowAlt' : ''}`}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(account.id)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate font-medium text-ink">{account.name}</span>
                        <span className="rounded-md bg-surface px-2 py-1 text-xs text-muted">{account.status}</span>
                      </div>
                      <div className="mt-2 text-sm text-muted">{money(account.total_asset)}</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteAccount(account)}
                      disabled={deletingId === account.id}
                      className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-transparent text-muted opacity-0 hover:border-red-200 hover:bg-red-50 hover:text-red-700 group-hover:opacity-100 focus:opacity-100 disabled:opacity-50"
                      title="删除账户"
                      aria-label={`删除账户 ${account.name}`}
                    >
                      {deletingId === account.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>

        <div className="space-y-4">
          {selected ? (
            <>
              <section className="rounded-lg border border-line bg-panel p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-medium text-ink">账户资金</div>
                  {selected.valuation_source === 'realtime' ? (
                    <span className="rounded-md border border-red-100 bg-red-50 px-2 py-1 text-xs font-medium text-red-700">实时估值</span>
                  ) : (
                    <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700">行情暂不可用，使用最近估值</span>
                  )}
                </div>
                {selected.valuation_error ? (
                  <div className="mb-3 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>{selected.valuation_error}</span>
                  </div>
                ) : null}
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                  {[
                    { label: '总资产', value: money(selected.total_asset), subValue: null, icon: <Banknote className="h-4 w-4" />, tone: 'text-ink' },
                    { label: '持仓市值', value: money(selected.position_value), subValue: null, icon: <LineChart className="h-4 w-4" />, tone: 'text-ink' },
                    { label: '可用资金', value: money(selected.available_cash), subValue: null, icon: <BriefcaseBusiness className="h-4 w-4" />, tone: 'text-ink' },
                    { label: '浮动盈亏', value: signedMoney(selected.unrealized_pnl), subValue: null, icon: <LineChart className="h-4 w-4" />, tone: cnMarketTone(selected.unrealized_pnl) },
                    { label: '今日盈亏', value: signedMoney(selected.today_pnl), subValue: signedPercentFromRatio(selected.today_pnl_rate), icon: <LineChart className="h-4 w-4" />, tone: cnMarketTone(selected.today_pnl) },
                  ].map((item) => (
                    <div key={item.label} className="min-h-[92px] rounded-md border border-line bg-surface px-3 py-3">
                      <div className="flex items-center gap-2 text-xs text-muted">{item.icon}{item.label}</div>
                      <div className={`mt-2 font-mono text-base font-semibold tabular-nums ${item.tone}`}>{item.value}</div>
                      {item.subValue ? <div className={`mt-1 font-mono text-xs tabular-nums ${item.tone}`}>{item.subValue}</div> : null}
                    </div>
                  ))}
                </div>
              </section>

              <section className="rounded-lg border border-line bg-panel p-4">
                <div className="flex items-center gap-3">
                  <GitBranch className="h-4 w-4 text-muted" />
                  <span className="text-sm font-medium text-ink">绑定策略</span>

                  {editingStrategy?.id === selected.id ? (
                    <div className="ml-auto flex items-center gap-2">
                      <select
                        value={editingStrategy.selected}
                        onChange={(e) => setEditingStrategy({ id: selected.id, selected: e.target.value })}
                        className="h-8 rounded-md border border-line bg-surface px-2 text-sm text-ink outline-none"
                        autoFocus
                      >
                        <option value="">不绑定</option>
                        {strategies.map((st) => (
                          <option key={st.id} value={st.id}>{st.name}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => void patchAccount(selected.id, { strategy_id: editingStrategy.selected ? parseInt(editingStrategy.selected, 10) : null })}
                        className="text-sm font-medium text-accent hover:underline disabled:opacity-50"
                      >
                        {saving ? '保存中...' : '保存'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingStrategy(null)}
                        className="text-sm font-medium text-muted hover:underline"
                      >
                        取消
                      </button>
                    </div>
                  ) : (
                    <>
                      <span className="ml-auto text-sm text-muted">{selected.strategy_name ?? '未绑定'}</span>
                      <button
                        type="button"
                        onClick={() => setEditingStrategy({ id: selected.id, selected: String(strategies.find(s => s.name === selected.strategy_name)?.id ?? '') })}
                        className="text-sm font-medium text-accent hover:underline"
                      >
                        更改
                      </button>
                    </>
                  )}
                </div>
              </section>

              <RiskConfigSection account={selected} patchAccount={patchAccount} saving={saving} />

              <SeesawSection account={selected} patchAccount={patchAccount} saving={saving} />

              <RiskGuardSection status={riskGuardStatus} takeProfitBreached={takeProfitBreached} />

              <DataTable title="持仓" loading={detailLoading} empty="暂无持仓">
                <table className="min-w-[840px] w-full text-left text-sm">
                  <thead className="bg-tableHead text-xs text-muted">
                    <tr>
                      <th className="px-4 py-3">股票</th>
                      <th className="px-4 py-3">持仓 / 可卖</th>
                      <th className="px-4 py-3">成本 / 现价</th>
                      <th className="px-4 py-3">市值</th>
                      <th className="w-[160px] px-4 py-3 text-right">
                        <button
                          type="button"
                          onClick={() => togglePositionSort('unrealized_pnl')}
                          className="ml-auto inline-flex min-h-8 items-center gap-1.5 rounded px-1 text-right font-medium text-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
                          aria-label={`按收益金额${positionSort?.key === 'unrealized_pnl' && positionSort.direction === 'desc' ? '升序' : '降序'}排序`}
                        >
                          累计收益
                          {positionSortIcon('unrealized_pnl')}
                        </button>
                      </th>
                      <th className="w-[160px] px-4 py-3 text-right">今日收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPositions.map((position) => (
                      <tr key={position.id} className="border-t border-line">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2 font-medium text-ink">
                            <span>{position.stock_name || position.ts_code}</span>
                            {position.closed_today ? <span className="rounded bg-line px-1.5 py-0.5 text-[10px] font-medium text-muted">今日清仓</span> : null}
                          </div>
                          <div className="mt-0.5 font-mono text-xs text-muted">{position.ts_code}</div>
                        </td>
                        <td className="px-4 py-3 font-mono tabular-nums">
                          <div>{formatNumber(position.shares)}</div>
                          <div className="mt-0.5 text-xs text-muted">{formatNumber(position.available_shares)}</div>
                        </td>
                        <td className="px-4 py-3 font-mono tabular-nums">
                          <div>{money(position.avg_cost)}</div>
                          <div className="mt-0.5 text-xs text-muted">{money(position.current_price)}</div>
                        </td>
                        <td className="px-4 py-3 font-mono tabular-nums">{money(position.market_value)}</td>
                        <td className={`w-[160px] px-4 py-3 text-right font-mono tabular-nums ${cnMarketTone(position.unrealized_pnl)}`}>
                          <div className="whitespace-nowrap">{signedFixedMoney(position.unrealized_pnl)}</div>
                          <div className="mt-0.5 whitespace-nowrap text-xs">{signedPercentFromRatio(position.profit_rate)}</div>
                        </td>
                        <td className={`w-[160px] px-4 py-3 text-right font-mono tabular-nums ${cnMarketTone(position.today_pnl)}`}>
                          <div className="whitespace-nowrap">{signedFixedMoney(position.today_pnl)}</div>
                          <div className="mt-0.5 whitespace-nowrap text-xs">{signedPercentFromRatio(position.today_pnl_rate)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </DataTable>

              <section className="rounded-lg border border-line bg-panel p-4">
                <div className="mb-2 text-sm font-medium text-ink">NAV</div>
                <NavChart points={nav} />
                <div className="mt-2 text-xs text-muted">最近 {nav.length} 个快照</div>
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
                        <td className="px-4 py-3">
                          <div className="font-medium text-ink">{order.stock_name || order.ts_code}</div>
                          <div className="mt-0.5 font-mono text-xs text-muted">{order.ts_code}</div>
                        </td>
                        <td className={`px-4 py-3 ${order.direction === '买入' ? 'text-red-600' : 'text-emerald-600'}`}>{order.direction}</td>
                        <td className="px-4 py-3">{money(order.price)}</td>
                        <td className="px-4 py-3">{formatNumber(order.volume)}</td>
                        <td className="px-4 py-3">{order.status}</td>
                        <td className="px-4 py-3">
                          {order.status === '待成交' ? (
                            <div className="flex items-center gap-2">
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
                          <td className="px-4 py-3">
                            <div className="font-medium text-ink">{trade.stock_name || trade.ts_code}</div>
                            <div className="mt-0.5 font-mono text-xs text-muted">{trade.ts_code}</div>
                          </td>
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
