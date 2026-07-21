import React from 'react'
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, TrendingUp, TrendingDown, Star, BarChart3, Server, CalendarDays, Table2, Play, RefreshCw } from 'lucide-react'
import { fetchJson, formatDate, formatDateTime, formatNumber } from '../lib/utils'
import Skeleton from '../components/Skeleton'

type HealthState = 'checking' | 'ok' | 'error'

interface EndpointHealth {
  state: HealthState
  message: string
}

interface BacktestSummary {
  id: number
  strategy_name: string | null
  total_return: string | null
  status: string
  created_at: string
}

interface WatchlistSummary {
  total_count: number
  today_gainers: number
  today_losers: number
  today_flat: number
}

interface DataMetrics {
  stock_basic_count: number
  trade_calendar_count: number
  latest_trade_calendar_date: string | null
  daily_kline_count: number
  latest_kline_trade_date: string | null
}

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

function MetricCard({ icon, label, value, detail, className = '' }: { icon: React.ReactNode; label: string; value: string; detail: string; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-panel p-4 shadow-sm ${className}`}>
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
    <div className="flex min-h-24 items-start justify-between gap-3 rounded-lg border border-line bg-panel p-4 shadow-sm">
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

export default function DashboardPage() {
  const [apiHealth, setApiHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dbHealth, setDbHealth] = React.useState<EndpointHealth>(initialHealth)
  const [metrics, setMetrics] = React.useState<DataMetrics | null>(null)
  const [recentBacktests, setRecentBacktests] = React.useState<BacktestSummary[]>([])
  const [watchlistSummary, setWatchlistSummary] = React.useState<WatchlistSummary | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const refreshStatus = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    const [apiResult, dbResult, dataResult, backtestsResult, watchlistResult] = await Promise.allSettled([
      fetchJson<{ status: string }>('/health'),
      fetchJson<{ result: number }>('/api/health/db'),
      fetchJson<DataMetrics>('/api/data/status'),
      fetchJson<{ items: BacktestSummary[]; total: number } | BacktestSummary[]>('/api/backtests?limit=5'),
      fetchJson<WatchlistSummary>('/api/watchlist/summary'),
    ])
    if (apiResult.status === 'fulfilled') setApiHealth({ state: 'ok', message: `服务状态：${apiResult.value.status}` })
    else setApiHealth({ state: 'error', message: `无法连接后端：${apiResult.reason.message}` })
    if (dbResult.status === 'fulfilled') setDbHealth({ state: 'ok', message: `数据库返回：${dbResult.value.result}` })
    else setDbHealth({ state: 'error', message: `数据库检查失败：${dbResult.reason.message}` })
    if (dataResult.status === 'fulfilled') setMetrics(dataResult.value)
    else setError(`数据状态加载失败：${dataResult.reason.message}`)
    if (backtestsResult.status === 'fulfilled') {
      const raw = backtestsResult.value
      setRecentBacktests(Array.isArray(raw) ? raw : (raw.items ?? []))
    }
    if (watchlistResult.status === 'fulfilled') setWatchlistSummary(watchlistResult.value)
    setLoading(false)
  }, [])

  React.useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  const defaultMetrics: DataMetrics = {
    stock_basic_count: 0,
    trade_calendar_count: 0,
    latest_trade_calendar_date: null,
    daily_kline_count: 0,
    latest_kline_trade_date: null,
  }
  const currentMetrics = metrics ?? defaultMetrics

  return (
    <div className="space-y-6">
      {(error) && (
        <section className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900" role="status">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4" aria-hidden="true" />
            <span className="break-words">{error}</span>
          </div>
        </section>
      )}

      {loading ? (
        <div className="space-y-6">
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton.Card lines={2} />
            <Skeleton.Card lines={2} />
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => <Skeleton.Card key={i} lines={2} />)}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton.Table rows={3} columns={3} />
            <Skeleton.Table rows={5} columns={2} />
          </div>
        </div>
      ) : (
        <>
          {/* Health Status */}
          <section className="grid gap-4 lg:grid-cols-2">
            <HealthPill icon={<Server className="h-5 w-5" aria-hidden="true" />} label="后端 API" health={apiHealth} />
            <HealthPill icon={<Database className="h-5 w-5" aria-hidden="true" />} label="PostgreSQL" health={dbHealth} />
          </section>

          {/* Data Metrics */}
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard icon={<Database className="h-4 w-4 text-accent" aria-hidden="true" />} label="股票基础表" value={formatNumber(currentMetrics.stock_basic_count)} detail="stock_basic" />
            <MetricCard icon={<CalendarDays className="h-4 w-4 text-mint" aria-hidden="true" />} label="交易日历" value={formatDate(currentMetrics.latest_trade_calendar_date)} detail={`${formatNumber(currentMetrics.trade_calendar_count)} 条记录`} />
            <MetricCard icon={<Table2 className="h-4 w-4 text-warn" aria-hidden="true" />} label="日 K 行数" value={formatNumber(currentMetrics.daily_kline_count)} detail={`最新交易日 ${formatDate(currentMetrics.latest_kline_trade_date)}`} />
            <MetricCard icon={<Clock3 className="h-4 w-4 text-slate-600" aria-hidden="true" />} label="最近检查" value={new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date())} detail="Leek Quant" />
          </section>

          {/* Backtests + Watchlist */}
          <section className="grid gap-4 lg:grid-cols-2">
            {/* Recent Backtests */}
            <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
              <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                <BarChart3 className="h-4 w-4 text-accent" aria-hidden="true" />
                <h2 className="text-base font-semibold text-ink">最近回测</h2>
              </div>
              {recentBacktests.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-500">暂无回测记录</div>
              ) : (
                <div className="divide-y divide-line">
                  {recentBacktests.map((bt) => (
                    <div key={bt.id} className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <p className="font-medium text-ink truncate">{bt.strategy_name ?? '未命名策略'}</p>
                        <p className="text-xs text-slate-500">{formatDateTime(bt.created_at)}</p>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${bt.status === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : bt.status === 'failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-amber-200 bg-amber-50 text-amber-800'}`}>
                          {bt.status}
                        </span>
                        {bt.total_return !== null && (
                          <span className={`flex items-center gap-1 text-sm font-semibold tabular-nums ${Number(bt.total_return) >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                            {Number(bt.total_return) >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                            {(Number(bt.total_return) * 100).toFixed(2)}%
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Watchlist Overview */}
            <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
              <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                <Star className="h-4 w-4 text-warn" aria-hidden="true" />
                <h2 className="text-base font-semibold text-ink">自选股概览</h2>
              </div>
              {watchlistSummary ? (
                <div className="p-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="text-center">
                      <p className="text-3xl font-semibold tabular-nums text-ink">{watchlistSummary.total_count}</p>
                      <p className="mt-1 text-sm text-slate-600">总数</p>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="text-center">
                        <p className="text-lg font-semibold tabular-nums text-red-600">{watchlistSummary.today_gainers}</p>
                        <p className="text-xs text-slate-500">上涨</p>
                      </div>
                      <div className="text-center">
                        <p className="text-lg font-semibold tabular-nums text-slate-600">{watchlistSummary.today_flat}</p>
                        <p className="text-xs text-slate-500">平盘</p>
                      </div>
                      <div className="text-center">
                        <p className="text-lg font-semibold tabular-nums text-emerald-600">{watchlistSummary.today_losers}</p>
                        <p className="text-xs text-slate-500">下跌</p>
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="px-4 py-8 text-center text-sm text-slate-500">暂无自选股数据</div>
              )}
            </section>
          </section>

          {/* Quick Actions */}
          <section className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void refreshStatus()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-semibold text-ink transition hover:bg-surface"
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              刷新状态
            </button>
          </section>
        </>
      )}
    </div>
  )
}
