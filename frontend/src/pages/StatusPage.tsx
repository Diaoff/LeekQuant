import React from 'react'
import { Activity, AlertTriangle, CheckCircle2, ChevronRight, Clock3, Database, Play, RefreshCw, Server, Table2, CalendarDays, Layers } from 'lucide-react'
import { apiBaseUrl, fetchJson, formatDate, formatDateTime, formatDuration, formatNumber } from '../lib/utils'

type HealthState = 'checking' | 'ok' | 'error'
type ActionKey = 'stock-basic' | 'trade-calendar' | 'sample-kline' | 'all-kline' | 'incremental-kline' | 'incremental-kline-catchup' | 'fundamentals'
type ProgressTaskKind = 'all-kline' | 'fundamentals' | 'incremental-kline' | 'incremental-kline-catchup'

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
  subtask_count?: number
  subtask_failed?: number
  subtask_finished?: number
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

interface SyncProgress {
  latest_open_day: string | null
  total: number
  caught_up: number
  remaining: number
  failed: number
  not_caught_up_codes: string[]
  scope: {
    ts_codes: string[] | null
    watchlist_id: number | null
    recent_run: boolean
  }
}

const initialHealth: EndpointHealth = { state: 'checking', message: '检查中' }
const progressTaskLabels: Record<ProgressTaskKind, string> = {
  'all-kline': '全量 K 线同步',
  'incremental-kline': '增量 K 线同步',
  'incremental-kline-catchup': '增量 K 线同步（仅未追上）',
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
  realtime_risk_guard: '实时风控守护',
  incremental_kline_batch: '增量 K线批次',
  full_kline_batch: '全量 K线批次',
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
  if (status === 'dispatched') return 'border-blue-200 bg-blue-50 text-blue-800'
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

function SubtaskTable({
  loading,
  error,
  subtasks,
}: {
  loading: boolean
  error: string | null
  subtasks: Array<Record<string, any>>
}) {
  if (loading) return <p className="py-2 text-xs text-muted">加载批次中…</p>
  if (error) return <p className="py-2 text-xs text-red-700">加载失败：{error}</p>
  if (subtasks.length === 0) return <p className="py-2 text-xs text-muted">该同步暂无可下钻的批次记录</p>
  return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="min-w-full divide-y divide-line text-left text-xs">
        <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
          <tr>
            <th className="px-3 py-2">批次</th>
            <th className="px-3 py-2">状态</th>
            <th className="px-3 py-2">进度</th>
            <th className="px-3 py-2">股票数</th>
            <th className="px-3 py-2">耗时</th>
            <th className="px-3 py-2">错误</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {subtasks.map((s) => {
            const status = String(s.status)
            const prog = s.progress as { current: number; total: number; current_code: string } | undefined
            const total = s.ts_code_count ?? 0
            const current = status === 'running' && prog ? prog.current : (status === 'success' || status === 'failed' || status === 'cancelled' ? (s.synced ?? 0) : 0)
            const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0
            return (
              <tr key={s.id}>
                <td className="px-3 py-2 tabular-nums text-ink">#{s.batch_index}</td>
                <td className="px-3 py-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${taskStatusClasses(status)}`}>{status === 'dispatched' ? '已派发' : status}</span>
                </td>
                <td className="px-3 py-2 tabular-nums text-muted">
                  {status === 'pending' ? (
                    <span className="text-muted">等待中</span>
                  ) : status === 'running' && prog ? (
                    <div className="flex flex-col gap-1">
                      <span className="tabular-nums">{prog.current} / {prog.total || total}{prog.current_code ? <span className="ml-1 font-mono text-muted">{prog.current_code}</span> : null}</span>
                      <div className="h-1 w-20 overflow-hidden rounded-full bg-slate-200">
                        <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  ) : (
                    <span>{s.synced ?? 0} / {total}</span>
                  )}
                </td>
                <td className="px-3 py-2 tabular-nums text-muted">{total}</td>
                <td className="px-3 py-2 tabular-nums text-muted">{formatDuration(s.duration_ms)}</td>
                <td className="max-w-80 break-words px-3 py-2 text-red-700">{s.error_message ? String(s.error_message) : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
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
  const [expandedTaskId, setExpandedTaskId] = React.useState<string | null>(null)
  const [subtasks, setSubtasks] = React.useState<Array<Record<string, any>>>([])
  const [subtasksLoading, setSubtasksLoading] = React.useState(false)
  const [subtasksError, setSubtasksError] = React.useState<string | null>(null)
  const [syncProgress, setSyncProgress] = React.useState<SyncProgress | null>(null)
  const [syncProgressError, setSyncProgressError] = React.useState<string | null>(null)
  const [isRetrying, setIsRetrying] = React.useState<Partial<Record<'incremental' | 'full', boolean>>>({})

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

  const loadSubtasks = React.useCallback(async (parentTaskId: string) => {
    setSubtasksLoading(true)
    setSubtasksError(null)
    try {
      const data = await fetchJson<{ subtasks: Array<Record<string, any>> }>(`/api/data/task-runs/${parentTaskId}/subtasks`)
      setSubtasks(data.subtasks ?? [])
    } catch (caught) {
      setSubtasksError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubtasksLoading(false)
    }
  }, [])

  const toggleExpand = React.useCallback(
    (taskId: string) => {
      if (expandedTaskId === taskId) {
        setExpandedTaskId(null)
        return
      }
      setExpandedTaskId(taskId)
      setSubtasks([])
      void loadSubtasks(taskId)
    },
    [expandedTaskId, loadSubtasks],
  )

  const runAction = React.useCallback(async (action: ActionKey) => {
    if (runningActions[action]) return
    setRunningActions((prev) => ({ ...prev, [action]: true }))
    setNotice(null)
    setError(null)
    try {
      if (action === 'stock-basic') {
        const result = await fetchJson<{ inserted_or_updated: number; total: number; source: string }>('/api/data/sync/stock-basic', { method: 'POST' })
        setNotice(`股票基础信息已同步：${formatNumber(result.inserted_or_updated)} 条（当前共 ${formatNumber(result.total)} 只），来源 ${result.source}`)
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
      if (action === 'incremental-kline-catchup') {
        const result = await fetchJson<{ task_id: string | null; status: string; reason?: string; codes?: string[] }>('/api/tasks/data/incremental-kline/catchup', { method: 'POST', body: JSON.stringify({}) })
        if (result.status === 'noop') {
          setNotice(`增量同步（仅未追上）：${result.reason ?? '无需同步'}`)
        } else {
          setNotice(`增量同步（仅未追上）已提交：${result.task_id}（${result.codes?.length ?? 0} 只）`)
          setProgressTaskId(result.task_id)
          setProgressTaskKind('incremental-kline-catchup')
          setProgressMeta(null)
          setProgressDone(false)
        }
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

  // Lightweight, always-on poll of the TRUE sync progress (source of truth from
  // daily_kline / data_update_state, independent of any Celery task status).
  React.useEffect(() => {
    let cancelled = false
    let retryCount = 0
    const poll = async () => {
      try {
        const controller = new AbortController()
        const timeout = setTimeout(() => controller.abort(), 15000)
        const response = await fetch(`${apiBaseUrl}/api/tasks/data/sync-progress`, {
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
        })
        clearTimeout(timeout)
        if (!response.ok) {
          let detail = `${response.status} ${response.statusText}`
          try {
            const body = await response.json()
            if (body.detail) detail = body.detail
          } catch { /* ignore */ }
          throw new Error(detail)
        }
        const data = await response.json() as SyncProgress
        if (!cancelled) {
          setSyncProgress(data)
          setSyncProgressError(null)
          retryCount = 0
        }
      } catch (caught) {
        if (!cancelled) {
          const message = caught instanceof Error ? caught.message : String(caught)
          setSyncProgressError(message)
          retryCount++
        }
      }
    }
    void poll()
    const interval = setInterval(poll, 10000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // When a parent task is expanded, auto-refresh its subtask list (and the
  // parent status row) every 5s while there are still running/pending batches.
  // This gives live per-batch progress without the user manually clicking
  // refresh — critical for telling whether batches are actually progressing
  // or stuck.
  React.useEffect(() => {
    if (!expandedTaskId) return
    const hasActive = subtasks.some((s) => s.status === 'running' || s.status === 'pending')
    if (!hasActive) return
    const interval = setInterval(() => {
      void loadSubtasks(expandedTaskId)
      void refreshStatus()
    }, 5000)
    return () => clearInterval(interval)
  }, [expandedTaskId, subtasks, loadSubtasks, refreshStatus])

  const retrySync = React.useCallback(
    async (kind: 'incremental' | 'full') => {
      if (isRetrying[kind]) return
      setIsRetrying((prev) => ({ ...prev, [kind]: true }))
      setError(null)
      try {
        const endpoint = kind === 'incremental' ? '/api/tasks/data/incremental-kline/retry' : '/api/tasks/data/sync-all-kline/retry'
        const result = await fetchJson<{ task_id: string | null; status: string; retried_codes?: string[] }>(endpoint, {
          method: 'POST',
          body: JSON.stringify({}),
        })
        if (result.status === 'noop') {
          setNotice(kind === 'incremental' ? '增量同步：上一轮没有失败批次需要重试' : '全量同步：上一轮没有失败批次需要重试')
        } else {
          setNotice(
            `${kind === 'incremental' ? '增量' : '全量'}重试已提交（${result.retried_codes?.length ?? 0} 只），任务 ${result.task_id}`,
          )
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setIsRetrying((prev) => ({ ...prev, [kind]: false }))
      }
    },
    [isRetrying],
  )

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
              style={{ width: `${progressMeta.total > 0 ? Math.round((progressMeta.current / progressMeta.total) * 100) : 0}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-blue-700">
            {progressMeta.total === 0
              ? '检查数据增量区间，暂无需要同步的缺口'
              : `${progressMeta.current} / ${progressMeta.total} 只 · 当前: ${progressMeta.current_code}`}
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

      <section className="rounded-lg border border-line bg-panel p-5 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-accent" aria-hidden="true" />
            <h2 className="text-base font-semibold text-ink">K 线同步真值进度</h2>
            <span className="text-xs text-muted">（数据源：daily_kline / data_update_state，非任务状态）</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void retrySync('incremental')}
              disabled={isRetrying.incremental}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-medium text-ink transition hover:bg-surface focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isRetrying.incremental ? <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
              重试失败批次（增量）
            </button>
            <button
              type="button"
              onClick={() => void retrySync('full')}
              disabled={isRetrying.full}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-medium text-ink transition hover:bg-surface focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isRetrying.full ? <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
              重试失败批次（全量）
            </button>
          </div>
        </div>
        {syncProgress == null ? (
          syncProgressError ? (
            <p className="text-sm text-red-600">真值进度加载失败：{syncProgressError}</p>
          ) : (
            <p className="text-sm text-muted">加载真值进度中…</p>
          )
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-md border border-line bg-surface p-3">
                <p className="text-xs text-muted">总数</p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-ink">{formatNumber(syncProgress.total)}</p>
              </div>
              <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
                <p className="text-xs text-emerald-700">已追上</p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-emerald-800">{formatNumber(syncProgress.caught_up)}</p>
              </div>
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                <p className="text-xs text-amber-700">未完成</p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-amber-800">{formatNumber(syncProgress.remaining)}</p>
              </div>
              <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <p className="text-xs text-red-700">失败</p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-red-800">{formatNumber(syncProgress.failed)}</p>
              </div>
            </div>
            <div className="mt-4 h-2.5 w-full overflow-hidden rounded-full bg-amber-200">
              <div
                className="h-full rounded-full bg-emerald-600 transition-all duration-700"
                style={{ width: `${syncProgress.total > 0 ? Math.round((syncProgress.caught_up / syncProgress.total) * 100) : 0}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted">
              最新交易日 {formatDate(syncProgress.latest_open_day)} · 进度{' '}
              {syncProgress.total > 0 ? Math.round((syncProgress.caught_up / syncProgress.total) * 100) : 0}%
              {syncProgress.remaining === 0 && syncProgress.failed === 0 && ' · 已全部追上 ✅'}
            </p>
          </>
        )}
      </section>

      <div className="flex flex-wrap gap-3">
        <ActionButton running={runningActions['stock-basic'] === true} icon={<Database className="h-4 w-4" aria-hidden="true" />} label="同步股票" onClick={() => void runAction('stock-basic')} />
        <ActionButton running={runningActions['trade-calendar'] === true} icon={<CalendarDays className="h-4 w-4" aria-hidden="true" />} label="同步日历" onClick={() => void runAction('trade-calendar')} />
        <ActionButton running={runningActions['sample-kline'] === true} icon={<Play className="h-4 w-4" aria-hidden="true" />} label="小样本 K 线" onClick={() => void runAction('sample-kline')} />
        <ActionButton running={runningActions['all-kline'] === true} icon={<Layers className="h-4 w-4" aria-hidden="true" />} label="全量 K 线" onClick={() => void runAction('all-kline')} />
        <ActionButton running={runningActions['incremental-kline'] === true} icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />} label="增量（全量）" onClick={() => void runAction('incremental-kline')} />
        <ActionButton running={runningActions['incremental-kline-catchup'] === true} icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />} label="增量（仅未追上）" onClick={() => void runAction('incremental-kline-catchup')} />
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
                {metrics.recent_tasks.length === 0 ? (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-sm text-muted">暂无任务记录</td></tr>
                ) : (
                  metrics.recent_tasks.map((task) => {
                    const taskDisplayName = displayTaskName(task.task_name)
                    const hasSubtasks = (task.subtask_count ?? 0) > 0
                    const isExpanded = expandedTaskId === task.task_id
                    return (
                      <React.Fragment key={task.id}>
                        <tr className={isExpanded ? 'bg-surface' : undefined}>
                          <td className="max-w-72 px-4 py-3">
                            <div className="flex items-start gap-2">
                              {hasSubtasks ? (
                                <button
                                  type="button"
                                  onClick={() => toggleExpand(task.task_id ?? '')}
                                  className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-line text-muted transition hover:bg-panel"
                                  aria-label={isExpanded ? '收起批次' : '展开批次'}
                                  aria-expanded={isExpanded}
                                >
                                  <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isExpanded ? 'rotate-90' : ''}`} aria-hidden="true" />
                                </button>
                              ) : (
                                <span className="mt-0.5 inline-block h-5 w-5 shrink-0" />
                              )}
                              <div className="min-w-0">
                                <p className="font-medium text-ink">{taskDisplayName}</p>
                                <p className="mt-1 break-all font-mono text-xs text-muted">{task.task_name} · {task.task_id ?? 'local'}</p>
                                {task.error_message && <p className="mt-1 break-words text-xs text-red-700">{task.error_message}</p>}
                                {hasSubtasks && (
                                  <span
                                    className={`mt-1 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
                                      task.subtask_failed
                                        ? 'border-red-200 bg-red-50 text-red-800'
                                        : 'border-slate-200 bg-slate-50 text-slate-700'
                                    }`}
                                  >
                                    {task.subtask_finished != null && task.subtask_count
                                      ? `${task.subtask_finished}/${task.subtask_count} 批次完成${task.subtask_failed ? ` · ${task.subtask_failed} 失败` : ''}`
                                      : `共 ${task.subtask_count} 批次${task.subtask_failed ? ` · ${task.subtask_failed} 失败` : ''}`}
                                  </span>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-3"><span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${taskStatusClasses(task.status)}`}>{task.status === 'dispatched' ? '已派发' : task.status}</span></td>
                          <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDateTime(task.started_at)}</td>
                          <td className="whitespace-nowrap px-4 py-3 tabular-nums text-muted">{formatDuration(task.duration_ms)}</td>
                        </tr>
                        {isExpanded && (
                          <tr className="bg-surface">
                            <td colSpan={4} className="px-4 py-3">
                              <SubtaskTable loading={subtasksLoading} error={subtasksError} subtasks={subtasks} />
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })
                )}
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
