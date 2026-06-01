import React from 'react'
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, Play, RefreshCw, Server, Table2, CalendarDays, Layers } from 'lucide-react'
import { fetchJson, formatDate, formatDateTime, formatDuration, formatNumber } from '../lib/utils'

type HealthState = 'checking' | 'ok' | 'error'
type ActionKey = 'stock-basic' | 'trade-calendar' | 'sample-kline' | 'all-kline' | 'incremental-kline' | 'fundamentals'
type ProgressTaskKind = 'all-kline' | 'fundamentals' | 'incremental-kline'

interface ProgressMeta {
  current: number
  total: number
  current_code: string
}

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

const initialHealth: EndpointHealth = { state: 'checking', message: '检查中' }
const progressTaskLabels: Record<ProgressTaskKind, string> = {
  'all-kline': '全量 K 线同步',
  'incremental-kline': '增量 K 线同步',
  fundamentals: '基本面同步',
}
const taskNameLabels: Record<string, string> = {
  update_stock_basic: '股票基础信息同步',
  'update-stock-basic-weekly': '股票基础信息同步',
  update_trade_calendar: '交易日历同步',
  'update-trade-calendar-weekly': '交易日历同步',
  sync_sample_kline: '小样本 K 线同步',
  sync_all_kline: '全量 K 线同步',
  incremental_kline_update: '增量 K 线同步',
  'incremental-kline-daily': '增量 K 线同步',
  sync_fundamentals: '基本面同步',
  'update-fundamentals-daily': '基本面同步',
  compute_daily_factors: '每日因子计算',
  'compute-factors-daily': '每日因子计算',
  analyze_factor_icir: '因子 IC/IR 分析',
  generate_all_signals: '策略信号生成',
  'generate-signals-daily': '策略信号生成',
  unlock_t1_daily: 'T+1 持仓解锁',
  'unlock-t1-positions-daily': 'T+1 持仓解锁',
  match_pending_orders: '待成交委托撮合',
  'match-pending-orders-daily': '待成交委托撮合',
  snapshot_nav_daily: '模拟账户净值快照',
  'snapshot-sim-nav-daily': '模拟账户净值快照',
}

function displayTaskName(taskName: string): string {
  const shortName = taskName.split('.').pop() ?? taskName
  return taskNameLabels[taskName] ?? taskNameLabels[shortName] ?? taskName
}

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

function MetricCard({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-sm">
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

function ActionButton({
  running,
  icon,
  label,
  onClick,
}: {
  running: boolean
  icon: React.ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={running}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {running ? <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" /> : icon}
      {label}
    </button>
  )
}

export default function StatusPage() {
  const [apiHealth, setApiHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dbHealth, setDbHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dataStatus, setDataStatus] = React.useState<DataStatus | null>(null)
  const [lastCheckedAt, setLastCheckedAt] = React.useState<string>('尚未完成')
  const [isRefreshing, setIsRefreshing] = React.useState(false)
  const [runningActions, setRunningActions] = React.useState<Partial<Record<ActionKey, boolean>>>({})
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [progressTaskId, setProgressTaskId] = React.useState<string | null>(null)
  const [progressTaskKind, setProgressTaskKind] = React.useState<ProgressTaskKind | null>(null)
  const [progressMeta, setProgressMeta] = React.useState<ProgressMeta | null>(null)
  const [progressDone, setProgressDone] = React.useState(false)

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

  const runAction = React.useCallback(async (action: ActionKey) => {
    if (runningActions[action]) return
    setRunningActions((prev) => ({ ...prev, [action]: true }))
    setNotice(null)
    setError(null)
    try {
      if (action === 'stock-basic') {
        const result = await fetchJson<{ inserted_or_updated: number; source: string }>('/api/data/sync/stock-basic', { method: 'POST' })
        setNotice(`股票基础信息已同步：${formatNumber(result.inserted_or_updated)} 条，来源 ${result.source}`)
      }
      if (action === 'trade-calendar') {
        const result = await fetchJson<{ inserted_or_updated: number; source: string }>('/api/data/sync/trade-calendar', { method: 'POST' })
        setNotice(`交易日历已同步：${formatNumber(result.inserted_or_updated)} 条，来源 ${result.source}`)
      }
      if (action === 'sample-kline') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/sample-kline', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`小样本 K 线任务已提交：${result.task_id}`)
      }
      if (action === 'all-kline') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/sync-all-kline', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`全量 K 线同步任务已提交：${result.task_id}`)
        setProgressTaskId(result.task_id)
        setProgressTaskKind('all-kline')
        setProgressMeta(null)
        setProgressDone(false)
        return
      }
      if (action === 'incremental-kline') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/incremental-kline', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`增量 K 线同步任务已提交：${result.task_id}`)
        setProgressTaskId(result.task_id)
        setProgressTaskKind('incremental-kline')
        setProgressMeta(null)
        setProgressDone(false)
        return
      }
      setProgressTaskId(null)
      setProgressTaskKind(null)
      setProgressMeta(null)
      setProgressDone(false)
      if (action === 'fundamentals') {
        const result = await fetchJson<{ task_id: string }>('/api/tasks/data/fundamentals', { method: 'POST', body: JSON.stringify({}) })
        setNotice(`基本面同步任务已提交：${result.task_id}`)
        setProgressTaskId(result.task_id)
        setProgressTaskKind('fundamentals')
        setProgressMeta(null)
        setProgressDone(false)
        return
      }
      await refreshStatus()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setRunningActions((prev) => ({ ...prev, [action]: false }))
    }
  }, [refreshStatus, runningActions])

  React.useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  React.useEffect(() => {
    if (!progressTaskId) return
    const progressLabel = progressTaskLabels[progressTaskKind ?? 'all-kline']
    const interval = setInterval(async () => {
      try {
        const result = await fetchJson<{ meta?: ProgressMeta; ready: boolean; error?: string }>(`/api/tasks/${progressTaskId}`)
        if (result.meta) {
          setProgressMeta(result.meta)
        }
        if (result.ready) {
          if (result.error) {
            setError(`${progressLabel}失败：${result.error}`)
            setProgressTaskId(null)
            setProgressTaskKind(null)
            setProgressMeta(null)
            setProgressDone(false)
            clearInterval(interval)
            void refreshStatus()
            return
          }
          setProgressDone(true)
          setNotice(`${progressLabel}完成`)
          clearInterval(interval)
          void refreshStatus()
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
        clearInterval(interval)
      }
    }, 3000)
    return () => clearInterval(interval)
  }, [progressTaskId, progressTaskKind, refreshStatus])

  const metrics = dataStatus ?? {
    stock_basic_count: 0,
    trade_calendar_count: 0,
    latest_trade_calendar_date: null,
    daily_kline_count: 0,
    latest_kline_trade_date: null,
    recent_tasks: [],
    recent_alerts: [],
  }

  return (
    <div className="space-y-8">
      {(notice || error) && (
        <section className={`rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`} role="status">
          <div className="flex items-start gap-2">
            {error ? <AlertTriangle className="mt-0.5 h-4 w-4" aria-hidden="true" /> : <CheckCircle2 className="mt-0.5 h-4 w-4" aria-hidden="true" />}
            <span className="break-words">{error ?? notice}</span>
          </div>
        </section>
      )}

      {progressTaskId && !progressDone && progressMeta && (
        <section className="rounded-lg border border-blue-200 bg-blue-50 p-4">
          <div className="mb-2 flex items-center gap-2">
            <RefreshCw className="h-4 w-4 animate-spin text-blue-600" />
            <span className="text-sm font-medium text-blue-900">{progressTaskLabels[progressTaskKind ?? 'all-kline']}进行中</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-blue-200">
            <div
              className="h-full rounded-full bg-blue-600 transition-all duration-500"
              style={{ width: `${Math.round((progressMeta.current / progressMeta.total) * 100)}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-blue-700">
            {progressMeta.current} / {progressMeta.total} 只 · 当前: {progressMeta.current_code}
          </p>
        </section>
      )}
      {progressDone && (
        <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            <span className="font-medium">{progressTaskLabels[progressTaskKind ?? 'all-kline']}完成</span>
            <button
              type="button"
              onClick={() => { setProgressTaskId(null); setProgressTaskKind(null); setProgressDone(false); setProgressMeta(null) }}
              className="ml-auto text-xs text-emerald-700 underline"
            >
              关闭
            </button>
          </div>
        </section>
      )}

      <div className="flex flex-wrap gap-3">
        <ActionButton running={runningActions['stock-basic'] === true} icon={<Database className="h-4 w-4" aria-hidden="true" />} label="同步股票" onClick={() => void runAction('stock-basic')} />
        <ActionButton running={runningActions['trade-calendar'] === true} icon={<CalendarDays className="h-4 w-4" aria-hidden="true" />} label="同步日历" onClick={() => void runAction('trade-calendar')} />
        <ActionButton running={runningActions['sample-kline'] === true} icon={<Play className="h-4 w-4" aria-hidden="true" />} label="小样本 K 线" onClick={() => void runAction('sample-kline')} />
        <ActionButton running={runningActions['all-kline'] === true} icon={<Layers className="h-4 w-4" aria-hidden="true" />} label="全量 K 线" onClick={() => void runAction('all-kline')} />
        <ActionButton running={runningActions['incremental-kline'] === true} icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />} label="增量 K 线" onClick={() => void runAction('incremental-kline')} />
        <ActionButton running={runningActions.fundamentals === true} icon={<Table2 className="h-4 w-4" aria-hidden="true" />} label="同步基本面" onClick={() => void runAction('fundamentals')} />
        <button
          type="button"
          onClick={() => void refreshStatus()}
          disabled={isRefreshing}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-semibold text-ink transition hover:bg-surface focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
          刷新
        </button>
      </div>

      <section className="grid gap-6 lg:grid-cols-2">
        <HealthPill icon={<Server className="h-5 w-5" aria-hidden="true" />} label="后端 API" health={apiHealth} />
        <HealthPill icon={<Database className="h-5 w-5" aria-hidden="true" />} label="PostgreSQL" health={dbHealth} />
      </section>

      <section className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={<Database className="h-4 w-4 text-accent" aria-hidden="true" />} label="股票基础表" value={formatNumber(metrics.stock_basic_count)} detail="stock_basic" />
        <MetricCard icon={<CalendarDays className="h-4 w-4 text-mint" aria-hidden="true" />} label="交易日历" value={formatDate(metrics.latest_trade_calendar_date)} detail={`${formatNumber(metrics.trade_calendar_count)} 条记录`} />
        <MetricCard icon={<Table2 className="h-4 w-4 text-warn" aria-hidden="true" />} label="日 K 行数" value={formatNumber(metrics.daily_kline_count)} detail={`最新交易日 ${formatDate(metrics.latest_kline_trade_date)}`} />
        <MetricCard icon={<Clock3 className="h-4 w-4 text-slate-600" aria-hidden="true" />} label="最近检查" value={lastCheckedAt} detail="Leek Quant" />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <Activity className="h-4 w-4 text-accent" aria-hidden="true" />
            <h2 className="text-base font-semibold text-ink">最近数据任务</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr><th className="px-4 py-3">任务</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">开始时间</th><th className="px-4 py-3">耗时</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {metrics.recent_tasks.length === 0 ? <tr><td colSpan={4} className="px-4 py-8 text-center text-sm text-muted">暂无任务记录</td></tr> : metrics.recent_tasks.map((task) => {
                  const taskDisplayName = displayTaskName(task.task_name)
                  return (
                    <tr key={task.id}>
                      <td className="max-w-64 px-4 py-3">
                        <p className="font-medium text-ink">{taskDisplayName}</p>
                        <p className="mt-1 break-all font-mono text-xs text-muted">{task.task_name} · {task.task_id ?? 'local'}</p>
                        {task.error_message && <p className="mt-1 break-words text-xs text-red-700">{task.error_message}</p>}
                      </td>
                      <td className="px-4 py-3"><span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${taskStatusClasses(task.status)}`}>{task.status}</span></td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDateTime(task.started_at)}</td>
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-muted">{formatDuration(task.duration_ms)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
        <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
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
    </div>
  )
}
