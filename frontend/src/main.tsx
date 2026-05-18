import React from 'react'
import ReactDOM from 'react-dom/client'
import {
  Activity,
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Database,
  Eye,
  FolderPlus,
  LayoutGrid,
  ListFilter,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Star,
  Table2,
  Trash2,
} from 'lucide-react'

import './styles.css'

type HealthState = 'checking' | 'ok' | 'error'
type ActionKey = 'stock-basic' | 'trade-calendar' | 'sample-kline' | 'fundamentals'
type ViewKey = 'status' | 'market' | 'pools' | 'watchlist'

interface EndpointHealth {
  state: HealthState
  message: string
}

interface TaskRun {
  id: number
  task_name: string
  task_id: string | null
  status: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  error_message: string | null
}

interface AlertEvent {
  id: number
  level: string
  category: string
  title: string
  message: string | null
  created_at: string
  is_resolved: boolean
}

interface DataStatus {
  stock_basic_count: number
  trade_calendar_count: number
  latest_trade_calendar_date: string | null
  daily_kline_count: number
  latest_kline_trade_date: string | null
  recent_tasks: TaskRun[]
  recent_alerts: AlertEvent[]
}

interface StockRow {
  ts_code: string
  symbol: string
  name: string
  market: string | null
  exchange: string | null
  industry: string | null
  is_st: boolean
  is_delisted: boolean
  report_date: string | null
  pe_ttm: string | null
  pb: string | null
  ps_ttm: string | null
  pcf_ttm: string | null
  market_cap: string | null
  float_market_cap: string | null
  latest_trade_date: string | null
  latest_close: string | null
}

interface StockListResponse {
  items: StockRow[]
  page: number
  page_size: number
  total: number
}

interface Pool {
  id: number
  name: string
  description: string | null
  filters: Record<string, unknown>
  is_dynamic: boolean
  last_built_at: string | null
  item_count?: number
}

interface WatchGroup {
  group_name: string
  items: Array<{
    id: number
    ts_code: string
    name: string
    industry: string | null
    group_name: string
    note: string | null
    sort_order: number
    latest_trade_date: string | null
    latest_close: string | null
    is_st: boolean
    is_delisted: boolean
  }>
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
const initialHealth: EndpointHealth = { state: 'checking', message: '检查中' }

function statusClasses(state: HealthState): string {
  if (state === 'ok') return 'border-emerald-200 bg-emerald-50 text-emerald-900'
  if (state === 'error') return 'border-red-200 bg-red-50 text-red-900'
  return 'border-amber-200 bg-amber-50 text-amber-900'
}

function statusText(state: HealthState): string {
  if (state === 'ok') return '正常'
  if (state === 'error') return '异常'
  return '检查中'
}

function taskStatusClasses(status: string): string {
  if (status === 'success') return 'border-emerald-200 bg-emerald-50 text-emerald-800'
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-800'
  if (status === 'running' || status === 'pending') return 'border-amber-200 bg-amber-50 text-amber-800'
  return 'border-slate-200 bg-slate-50 text-slate-700'
}

function formatDate(value: string | null): string {
  return value ?? '暂无'
}

function formatDateTime(value: string | null): string {
  if (!value) return '暂无'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value))
}

function formatNumber(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === '') return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(numeric)
}

function formatMarketCap(value: string | null): string {
  if (!value) return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  if (numeric >= 100000000) return `${formatNumber(numeric / 100000000, 2)} 亿`
  if (numeric >= 10000) return `${formatNumber(numeric / 10000, 2)} 万`
  return formatNumber(numeric, 2)
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) detail = body.detail
    } catch {
      detail = `${response.status} ${response.statusText}`
    }
    throw new Error(detail)
  }
  return response.json()
}

function EmptyRow({ text, colSpan }: { text: string; colSpan: number }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-8 text-center text-sm text-slate-500">
        {text}
      </td>
    </tr>
  )
}

function MetricCard({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return (
    <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
        {icon}
        <span>{label}</span>
      </div>
      <p className="mt-3 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="mt-1 min-h-5 text-sm text-slate-600">{detail}</p>
    </section>
  )
}

function HealthPill({ icon, label, health }: { icon: React.ReactNode; label: string; health: EndpointHealth }) {
  return (
    <div className="flex min-h-24 items-start justify-between gap-3 rounded-lg border border-line bg-white p-4 shadow-sm">
      <div className="flex min-w-0 gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-accent">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="font-medium text-ink">{label}</p>
          <p className="mt-1 break-words text-sm leading-6 text-slate-600">{health.message}</p>
        </div>
      </div>
      <span className={`shrink-0 rounded-full border px-3 py-1 text-sm font-medium ${statusClasses(health.state)}`}>
        {statusText(health.state)}
      </span>
    </div>
  )
}

function ActionButton({
  action,
  activeAction,
  icon,
  label,
  onClick,
}: {
  action: ActionKey
  activeAction: ActionKey | null
  icon: React.ReactNode
  label: string
  onClick: () => void
}) {
  const isActive = activeAction === action
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={activeAction !== null}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {isActive ? <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" /> : icon}
      {label}
    </button>
  )
}

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`h-10 rounded-md border border-line bg-white px-3 text-sm text-ink outline-none transition placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-blue-100 ${props.className ?? ''}`}
    />
  )
}

function App() {
  const [view, setView] = React.useState<ViewKey>('status')
  const [apiHealth, setApiHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dbHealth, setDbHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dataStatus, setDataStatus] = React.useState<DataStatus | null>(null)
  const [lastCheckedAt, setLastCheckedAt] = React.useState<string>('尚未完成')
  const [isRefreshing, setIsRefreshing] = React.useState(false)
  const [activeAction, setActiveAction] = React.useState<ActionKey | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [stocks, setStocks] = React.useState<StockListResponse>({ items: [], page: 1, page_size: 50, total: 0 })
  const [stockQuery, setStockQuery] = React.useState('')
  const [industry, setIndustry] = React.useState('')
  const [exchange, setExchange] = React.useState('')
  const [excludeSt, setExcludeSt] = React.useState(true)
  const [excludeDelisted, setExcludeDelisted] = React.useState(true)
  const [peMax, setPeMax] = React.useState('')
  const [pbMax, setPbMax] = React.useState('')
  const [marketCapMin, setMarketCapMin] = React.useState('')
  const [pools, setPools] = React.useState<Pool[]>([])
  const [poolName, setPoolName] = React.useState('')
  const [poolDescription, setPoolDescription] = React.useState('')
  const [selectedPool, setSelectedPool] = React.useState<Pool | null>(null)
  const [poolItems, setPoolItems] = React.useState<StockRow[]>([])
  const [watchGroups, setWatchGroups] = React.useState<WatchGroup[]>([])
  const [watchCode, setWatchCode] = React.useState('')
  const [watchGroup, setWatchGroup] = React.useState('默认')
  const [watchNote, setWatchNote] = React.useState('')

  const refreshStatus = React.useCallback(async () => {
    setIsRefreshing(true)
    setApiHealth(initialHealth)
    setDbHealth(initialHealth)
    setError(null)
    const [apiResult, dbResult, dataResult] = await Promise.allSettled([
      fetchJson<{ status: string }>('/health'),
      fetchJson<{ result: number }>('/api/health/db'),
      fetchJson<DataStatus>('/api/data/status'),
    ])
    if (apiResult.status === 'fulfilled') setApiHealth({ state: 'ok', message: `服务状态：${apiResult.value.status}` })
    else setApiHealth({ state: 'error', message: `无法连接后端：${apiResult.reason.message}` })
    if (dbResult.status === 'fulfilled') setDbHealth({ state: 'ok', message: `数据库返回：${dbResult.value.result}` })
    else setDbHealth({ state: 'error', message: `数据库检查失败：${dbResult.reason.message}` })
    if (dataResult.status === 'fulfilled') setDataStatus(dataResult.value)
    else setError(`数据状态加载失败：${dataResult.reason.message}`)
    setLastCheckedAt(new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date()))
    setIsRefreshing(false)
  }, [])

  const loadStocks = React.useCallback(async () => {
    const params = new URLSearchParams({
      page: '1',
      page_size: '50',
      exclude_st: String(excludeSt),
      exclude_delisted: String(excludeDelisted),
    })
    if (stockQuery.trim()) params.set('query', stockQuery.trim())
    if (industry.trim()) params.set('industry', industry.trim())
    if (exchange) params.set('exchange', exchange)
    if (peMax.trim()) params.set('pe_max', peMax.trim())
    if (pbMax.trim()) params.set('pb_max', pbMax.trim())
    if (marketCapMin.trim()) params.set('market_cap_min', marketCapMin.trim())
    setStocks(await fetchJson<StockListResponse>(`/api/stocks?${params.toString()}`))
  }, [excludeDelisted, excludeSt, exchange, industry, marketCapMin, pbMax, peMax, stockQuery])

  const loadPools = React.useCallback(async () => {
    setPools(await fetchJson<Pool[]>('/api/pools'))
  }, [])

  const loadWatchlist = React.useCallback(async () => {
    setWatchGroups(await fetchJson<WatchGroup[]>('/api/watchlist'))
  }, [])

  const runAction = React.useCallback(async (action: ActionKey) => {
    setActiveAction(action)
    setNotice(null)
    setError(null)
    try {
      if (action === 'stock-basic') {
        const result = await fetchJson<{ inserted_or_updated: number; source: string }>('/api/data/sync/stock-basic', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`股票基础信息已同步：${formatNumber(result.inserted_or_updated)} 条，来源 ${result.source}`)
      }
      if (action === 'trade-calendar') {
        const result = await fetchJson<{ inserted_or_updated: number; source: string }>('/api/data/sync/trade-calendar', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`交易日历已同步：${formatNumber(result.inserted_or_updated)} 条，来源 ${result.source}`)
      }
      if (action === 'sample-kline') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/sample-kline', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`小样本 K 线任务已提交：${result.task_id}`)
      }
      if (action === 'fundamentals') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/fundamentals', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`基本面同步任务已提交：${result.task_id}`)
      }
      await Promise.all([refreshStatus(), loadStocks(), loadPools(), loadWatchlist()])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setActiveAction(null)
    }
  }, [loadPools, loadStocks, loadWatchlist, refreshStatus])

  const addToWatchlist = React.useCallback(async (tsCode: string) => {
    try {
      await fetchJson('/api/watchlist', { method: 'POST', body: JSON.stringify({ ts_code: tsCode, group_name: '默认' }) })
      setNotice(`${tsCode} 已加入自选股`)
      await loadWatchlist()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [loadWatchlist])

  const createPoolFromForm = React.useCallback(async () => {
    if (!poolName.trim()) return
    const filters = {
      exclude_st: excludeSt,
      exclude_delisted: excludeDelisted,
      ...(industry.trim() ? { industry: industry.trim() } : {}),
      ...(exchange ? { exchange } : {}),
      ...(peMax.trim() ? { pe_ttm: { max: Number(peMax) } } : {}),
      ...(pbMax.trim() ? { pb: { max: Number(pbMax) } } : {}),
      ...(marketCapMin.trim() ? { market_cap: { min: Number(marketCapMin) } } : {}),
    }
    try {
      const pool = await fetchJson<Pool>('/api/pools', {
        method: 'POST',
        body: JSON.stringify({ name: poolName.trim(), description: poolDescription || null, filters }),
      })
      setPoolName('')
      setPoolDescription('')
      setSelectedPool(pool)
      setNotice(`股票池已创建：${pool.name}`)
      await loadPools()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [excludeDelisted, excludeSt, exchange, industry, loadPools, marketCapMin, pbMax, peMax, poolDescription, poolName])

  const rebuildSelectedPool = React.useCallback(async (pool: Pool) => {
    try {
      const result = await fetchJson<{ item_count: number }>(`/api/pools/${pool.id}/rebuild`, { method: 'POST', body: JSON.stringify({}) })
      setNotice(`${pool.name} 已重建：${formatNumber(result.item_count)} 只股票`)
      await loadPools()
      const items = await fetchJson<{ items: StockRow[] }>(`/api/pools/${pool.id}/items`)
      setSelectedPool(pool)
      setPoolItems(items.items)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [loadPools])

  const addWatchFromForm = React.useCallback(async () => {
    if (!watchCode.trim()) return
    try {
      await fetchJson('/api/watchlist', {
        method: 'POST',
        body: JSON.stringify({ ts_code: watchCode.trim(), group_name: watchGroup || '默认', note: watchNote || null }),
      })
      setWatchCode('')
      setWatchNote('')
      setNotice('自选股已更新')
      await loadWatchlist()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [loadWatchlist, watchCode, watchGroup, watchNote])

  const moveWatchItem = React.useCallback(async (id: number, groupName: string) => {
    await fetchJson(`/api/watchlist/${id}`, { method: 'PATCH', body: JSON.stringify({ group_name: groupName }) })
    await loadWatchlist()
  }, [loadWatchlist])

  const deleteWatchItem = React.useCallback(async (id: number) => {
    await fetchJson(`/api/watchlist/${id}`, { method: 'DELETE' })
    await loadWatchlist()
  }, [loadWatchlist])

  React.useEffect(() => {
    void Promise.all([refreshStatus(), loadStocks(), loadPools(), loadWatchlist()])
  }, [loadPools, loadStocks, loadWatchlist, refreshStatus])

  const metrics = dataStatus ?? {
    stock_basic_count: 0,
    trade_calendar_count: 0,
    latest_trade_calendar_date: null,
    daily_kline_count: 0,
    latest_kline_trade_date: null,
    recent_tasks: [],
    recent_alerts: [],
  }

  const navItems: Array<{ key: ViewKey; label: string; icon: React.ReactNode }> = [
    { key: 'status', label: 'M1 数据状态', icon: <Activity className="h-4 w-4" aria-hidden="true" /> },
    { key: 'market', label: '市场', icon: <ListFilter className="h-4 w-4" aria-hidden="true" /> },
    { key: 'pools', label: '股票池', icon: <LayoutGrid className="h-4 w-4" aria-hidden="true" /> },
    { key: 'watchlist', label: '自选股', icon: <Star className="h-4 w-4" aria-hidden="true" /> },
  ]

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-line pb-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-mint">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              <span>Local-first A-share quant platform</span>
            </div>
            <h1 className="text-3xl font-semibold tracking-normal text-ink sm:text-4xl">Leek Quant</h1>
            <p className="mt-2 max-w-3xl text-base leading-7 text-slate-700">
              股票管理、数据同步和本地任务状态。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <ActionButton action="stock-basic" activeAction={activeAction} icon={<Database className="h-4 w-4" aria-hidden="true" />} label="同步股票" onClick={() => void runAction('stock-basic')} />
            <ActionButton action="trade-calendar" activeAction={activeAction} icon={<CalendarDays className="h-4 w-4" aria-hidden="true" />} label="同步日历" onClick={() => void runAction('trade-calendar')} />
            <ActionButton action="sample-kline" activeAction={activeAction} icon={<Play className="h-4 w-4" aria-hidden="true" />} label="小样本 K 线" onClick={() => void runAction('sample-kline')} />
            <ActionButton action="fundamentals" activeAction={activeAction} icon={<Table2 className="h-4 w-4" aria-hidden="true" />} label="同步基本面" onClick={() => void runAction('fundamentals')} />
            <button
              type="button"
              onClick={() => void Promise.all([refreshStatus(), loadStocks(), loadPools(), loadWatchlist()])}
              disabled={isRefreshing || activeAction !== null}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-semibold text-ink transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
              刷新
            </button>
          </div>
        </header>

        <nav className="flex gap-2 overflow-x-auto border-b border-line pb-3">
          {navItems.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setView(item.key)}
              className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-md border px-3 text-sm font-semibold transition ${
                view === item.key ? 'border-accent bg-accent text-white' : 'border-line bg-white text-slate-700 hover:bg-slate-50'
              }`}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>

        {(notice || error) && (
          <section className={`rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`} role="status">
            <div className="flex items-start gap-2">
              {error ? <AlertTriangle className="mt-0.5 h-4 w-4" aria-hidden="true" /> : <CheckCircle2 className="mt-0.5 h-4 w-4" aria-hidden="true" />}
              <span className="break-words">{error ?? notice}</span>
            </div>
          </section>
        )}

        {view === 'status' && (
          <>
            <section className="grid gap-4 lg:grid-cols-2">
              <HealthPill icon={<Server className="h-5 w-5" aria-hidden="true" />} label="后端 API" health={apiHealth} />
              <HealthPill icon={<Database className="h-5 w-5" aria-hidden="true" />} label="PostgreSQL" health={dbHealth} />
            </section>
            <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <MetricCard icon={<Database className="h-4 w-4 text-accent" aria-hidden="true" />} label="股票基础表" value={formatNumber(metrics.stock_basic_count)} detail="stock_basic" />
              <MetricCard icon={<CalendarDays className="h-4 w-4 text-mint" aria-hidden="true" />} label="交易日历" value={formatDate(metrics.latest_trade_calendar_date)} detail={`${formatNumber(metrics.trade_calendar_count)} 条记录`} />
              <MetricCard icon={<Table2 className="h-4 w-4 text-warn" aria-hidden="true" />} label="日 K 行数" value={formatNumber(metrics.daily_kline_count)} detail={`最新交易日 ${formatDate(metrics.latest_kline_trade_date)}`} />
              <MetricCard icon={<Clock3 className="h-4 w-4 text-slate-600" aria-hidden="true" />} label="最近检查" value={lastCheckedAt} detail={apiBaseUrl} />
            </section>
            <section className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
              <section className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
                <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                  <Activity className="h-4 w-4 text-accent" aria-hidden="true" />
                  <h2 className="text-base font-semibold text-ink">最近数据任务</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-line text-left text-sm">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                      <tr><th className="px-4 py-3">任务</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">开始时间</th><th className="px-4 py-3">耗时</th></tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {metrics.recent_tasks.length === 0 ? <EmptyRow text="暂无任务记录" colSpan={4} /> : metrics.recent_tasks.map((task) => (
                        <tr key={task.id}>
                          <td className="max-w-64 px-4 py-3">
                            <p className="font-medium text-ink">{task.task_name}</p>
                            <p className="mt-1 break-all font-mono text-xs text-slate-500">{task.task_id ?? 'local'}</p>
                            {task.error_message && <p className="mt-1 break-words text-xs text-red-700">{task.error_message}</p>}
                          </td>
                          <td className="px-4 py-3"><span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${taskStatusClasses(task.status)}`}>{task.status}</span></td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-700">{formatDateTime(task.started_at)}</td>
                          <td className="whitespace-nowrap px-4 py-3 tabular-nums text-slate-700">{task.duration_ms === null ? '进行中' : `${task.duration_ms} ms`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
                <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                  <AlertTriangle className="h-4 w-4 text-warn" aria-hidden="true" />
                  <h2 className="text-base font-semibold text-ink">最近告警</h2>
                </div>
                <div className="divide-y divide-line">
                  {metrics.recent_alerts.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-500">暂无告警</div> : metrics.recent_alerts.map((alert) => (
                    <article key={alert.id} className="p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-medium text-ink">{alert.title}</p>
                          <p className="mt-1 text-xs uppercase text-slate-500">{alert.category}</p>
                        </div>
                        <span className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${taskStatusClasses(alert.level === 'error' ? 'failed' : 'running')}`}>{alert.level}</span>
                      </div>
                      {alert.message && <p className="mt-2 break-words text-sm leading-6 text-slate-700">{alert.message}</p>}
                      <p className="mt-2 text-xs text-slate-500">{formatDateTime(alert.created_at)}</p>
                    </article>
                  ))}
                </div>
              </section>
            </section>
          </>
        )}

        {view === 'market' && (
          <section className="rounded-lg border border-line bg-white shadow-sm">
            <div className="border-b border-line p-4">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
                <TextInput placeholder="代码 / 名称" value={stockQuery} onChange={(event) => setStockQuery(event.target.value)} className="xl:col-span-2" />
                <TextInput placeholder="行业" value={industry} onChange={(event) => setIndustry(event.target.value)} />
                <select value={exchange} onChange={(event) => setExchange(event.target.value)} className="h-10 rounded-md border border-line bg-white px-3 text-sm text-ink outline-none focus:border-accent focus:ring-2 focus:ring-blue-100">
                  <option value="">全部交易所</option>
                  <option value="SSE">SSE</option>
                  <option value="SZSE">SZSE</option>
                </select>
                <TextInput placeholder="PE 上限" value={peMax} onChange={(event) => setPeMax(event.target.value)} />
                <TextInput placeholder="PB 上限" value={pbMax} onChange={(event) => setPbMax(event.target.value)} />
                <button type="button" onClick={() => void loadStocks()} className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white">
                  <Search className="h-4 w-4" aria-hidden="true" />
                  筛选
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-700">
                <label className="inline-flex items-center gap-2"><input type="checkbox" checked={excludeSt} onChange={(event) => setExcludeSt(event.target.checked)} /> 排除 ST</label>
                <label className="inline-flex items-center gap-2"><input type="checkbox" checked={excludeDelisted} onChange={(event) => setExcludeDelisted(event.target.checked)} /> 排除退市</label>
                <TextInput placeholder="市值下限" value={marketCapMin} onChange={(event) => setMarketCapMin(event.target.value)} className="w-36" />
                <span className="self-center text-slate-500">共 {formatNumber(stocks.total)} 只</span>
              </div>
            </div>
            <StockTable stocks={stocks.items} onWatch={addToWatchlist} />
          </section>
        )}

        {view === 'pools' && (
          <section className="grid gap-4 xl:grid-cols-[420px_1fr]">
            <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-ink">创建股票池</h2>
              <div className="mt-4 grid gap-3">
                <TextInput placeholder="股票池名称" value={poolName} onChange={(event) => setPoolName(event.target.value)} />
                <TextInput placeholder="描述" value={poolDescription} onChange={(event) => setPoolDescription(event.target.value)} />
                <button type="button" onClick={() => void createPoolFromForm()} className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white">
                  <FolderPlus className="h-4 w-4" aria-hidden="true" />
                  创建
                </button>
              </div>
              <div className="mt-5 divide-y divide-line">
                {pools.length === 0 ? <div className="py-8 text-center text-sm text-slate-500">暂无股票池</div> : pools.map((pool) => (
                  <article key={pool.id} className="py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{pool.name}</p>
                        <p className="mt-1 text-sm text-slate-600">{pool.description ?? '无描述'} · {formatNumber(pool.item_count ?? 0)} 只</p>
                      </div>
                      <button type="button" onClick={() => void rebuildSelectedPool(pool)} className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-slate-50">
                        <RefreshCw className="h-4 w-4" aria-hidden="true" />
                        重建
                      </button>
                    </div>
                    <pre className="mt-2 overflow-x-auto rounded-md bg-slate-50 p-2 text-xs text-slate-700">{JSON.stringify(pool.filters, null, 2)}</pre>
                  </article>
                ))}
              </div>
            </section>
            <section className="rounded-lg border border-line bg-white shadow-sm">
              <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                <Eye className="h-4 w-4 text-accent" aria-hidden="true" />
                <h2 className="text-base font-semibold text-ink">{selectedPool ? `${selectedPool.name} 成员` : '股票池成员'}</h2>
              </div>
              <StockTable stocks={poolItems} onWatch={addToWatchlist} />
            </section>
          </section>
        )}

        {view === 'watchlist' && (
          <section className="grid gap-4 xl:grid-cols-[360px_1fr]">
            <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-ink">添加自选股</h2>
              <div className="mt-4 grid gap-3">
                <TextInput placeholder="600000.SH / 000001.SZ" value={watchCode} onChange={(event) => setWatchCode(event.target.value)} />
                <TextInput placeholder="分组" value={watchGroup} onChange={(event) => setWatchGroup(event.target.value)} />
                <TextInput placeholder="备注" value={watchNote} onChange={(event) => setWatchNote(event.target.value)} />
                <button type="button" onClick={() => void addWatchFromForm()} className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white">
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  添加
                </button>
              </div>
            </section>
            <section className="space-y-4">
              {watchGroups.length === 0 ? (
                <div className="rounded-lg border border-line bg-white px-4 py-8 text-center text-sm text-slate-500 shadow-sm">暂无自选股</div>
              ) : watchGroups.map((group) => (
                <section key={group.group_name} className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
                  <div className="border-b border-line px-4 py-3">
                    <h2 className="text-base font-semibold text-ink">{group.group_name}</h2>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-line text-left text-sm">
                      <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                        <tr><th className="px-4 py-3">股票</th><th className="px-4 py-3">行业</th><th className="px-4 py-3">最新价</th><th className="px-4 py-3">备注</th><th className="px-4 py-3">操作</th></tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {group.items.map((item) => (
                          <tr key={item.id}>
                            <td className="px-4 py-3"><p className="font-medium text-ink">{item.name}</p><p className="font-mono text-xs text-slate-500">{item.ts_code}</p></td>
                            <td className="px-4 py-3 text-slate-700">{item.industry ?? '暂无'}</td>
                            <td className="px-4 py-3 tabular-nums text-slate-700">{formatNumber(item.latest_close, 2)}<p className="text-xs text-slate-500">{formatDate(item.latest_trade_date)}</p></td>
                            <td className="max-w-64 px-4 py-3 text-slate-700">{item.note ?? '暂无'}</td>
                            <td className="px-4 py-3">
                              <div className="flex gap-2">
                                <button type="button" onClick={() => void moveWatchItem(item.id, item.group_name === '默认' ? '重点' : '默认')} className="inline-flex h-9 items-center rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-slate-50">移动</button>
                                <button type="button" onClick={() => void deleteWatchItem(item.id)} className="inline-flex h-9 items-center rounded-md border border-red-200 px-3 text-sm font-semibold text-red-700 hover:bg-red-50">
                                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
            </section>
          </section>
        )}
      </div>
    </main>
  )
}

function StockTable({ stocks, onWatch }: { stocks: StockRow[]; onWatch: (tsCode: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-left text-sm">
        <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
          <tr>
            <th className="px-4 py-3">股票</th>
            <th className="px-4 py-3">状态</th>
            <th className="px-4 py-3">行业</th>
            <th className="px-4 py-3">最新价</th>
            <th className="px-4 py-3">PE</th>
            <th className="px-4 py-3">PB</th>
            <th className="px-4 py-3">总市值</th>
            <th className="px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {stocks.length === 0 ? <EmptyRow text="暂无股票" colSpan={8} /> : stocks.map((stock) => (
            <tr key={stock.ts_code}>
              <td className="px-4 py-3">
                <p className="font-medium text-ink">{stock.name}</p>
                <p className="font-mono text-xs text-slate-500">{stock.ts_code}</p>
              </td>
              <td className="px-4 py-3">
                <div className="flex flex-wrap gap-1">
                  {stock.is_st && <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800">ST</span>}
                  {stock.is_delisted && <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800">退市</span>}
                  {!stock.is_st && !stock.is_delisted && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">正常</span>}
                </div>
              </td>
              <td className="px-4 py-3 text-slate-700">{stock.industry ?? '暂无'}</td>
              <td className="px-4 py-3 tabular-nums text-slate-700">{formatNumber(stock.latest_close, 2)}<p className="text-xs text-slate-500">{formatDate(stock.latest_trade_date)}</p></td>
              <td className="px-4 py-3 tabular-nums text-slate-700">{formatNumber(stock.pe_ttm, 2)}</td>
              <td className="px-4 py-3 tabular-nums text-slate-700">{formatNumber(stock.pb, 2)}</td>
              <td className="px-4 py-3 tabular-nums text-slate-700">{formatMarketCap(stock.market_cap)}</td>
              <td className="px-4 py-3">
                <button type="button" onClick={() => onWatch(stock.ts_code)} className="inline-flex h-9 items-center gap-2 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-slate-50">
                  <Star className="h-4 w-4" aria-hidden="true" />
                  自选
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
