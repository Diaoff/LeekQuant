import React from 'react'
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  DatabaseZap,
  Layers3,
  Loader2,
  Play,
  RefreshCw,
  SlidersHorizontal,
} from 'lucide-react'
import Skeleton from '../components/Skeleton'
import { fetchJson, formatDate, formatDateTime, formatNumber } from '../lib/utils'

type ScopeType = 'all' | 'watchlist_group'
type TaskKind = 'compute' | 'analyze'

interface FactorDefinition {
  name: string
  display_name: string | null
  category: string
  expression: string
  direction: number
  default_weight: string
  enabled: boolean
  description: string | null
  created_at: string
  updated_at: string
}

interface RankItem {
  id: number
  trade_date: string
  ts_code: string
  stock_name: string | null
  scope_type: ScopeType
  scope_value: string | null
  total_score: string
  rank: number
  percentile_rank: string | null
  factor_breakdown: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

interface FactorAnalysis {
  id: number
  factor_name: string
  display_name: string | null
  period_start: string
  period_end: string
  forward_days: number
  ic: string | null
  ic_mean: string | null
  ic_std: string | null
  ir: string | null
  icir: string | null
  ic_gt_0_pct: string | null
  group_returns: Record<string, unknown> | null
  details: {
    ic_by_date?: Array<{ trade_date: string; ic: number | string; count?: number }>
    sample_count?: number
  } | null
  created_at: string
  updated_at: string
}

interface FactorValue {
  ts_code: string
  stock_name: string | null
  trade_date: string
  factor_name: string
  value: string | null
  normalized_value: string | null
  percentile_rank: string | null
  data_source: string | null
  created_at: string
  updated_at: string
}

interface PaginatedResponse<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

interface TaskStatus {
  task_id: string
  status: string
  ready: boolean
  result?: unknown
  error?: string
}

interface TaskNotice {
  kind: TaskKind
  taskId: string
  status: string
}

const defaultRankFilters = {
  trade_date: '',
  scope_type: 'all' as ScopeType,
  scope_value: '',
  page_size: '50',
}

const today = new Date().toISOString().slice(0, 10)

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatPercent(value: string | number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || value === '') return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  return `${formatNumber(numeric * 100, digits)}%`
}

function signedNumberClass(value: string | number | null | undefined): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric === 0) return 'text-muted'
  return numeric > 0 ? 'text-red-600' : 'text-emerald-600'
}

function directionText(direction: number): string {
  if (direction > 0) return '正向'
  if (direction < 0) return '反向'
  return '中性'
}

function taskLabel(kind: TaskKind): string {
  return kind === 'compute' ? '因子计算' : 'IC/IR 分析'
}

function normalizeTaskStatus(status: string): string {
  if (status === 'success') return '完成'
  if (status === 'failure' || status === 'failed') return '失败'
  if (status === 'pending') return '等待'
  if (status === 'started' || status === 'running') return '运行中'
  return status
}

function EmptyRow({ colSpan, message }: { colSpan: number; message: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-8 text-center text-sm text-muted">
        {message}
      </td>
    </tr>
  )
}

function AlertBanner({ type, children }: { type: 'success' | 'error' | 'info'; children: React.ReactNode }) {
  const classes =
    type === 'error'
      ? 'border-red-200 bg-red-50 text-red-900'
      : type === 'success'
        ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
        : 'border-blue-200 bg-blue-50 text-blue-900'
  return (
    <section className={`rounded-lg border p-4 text-sm ${classes}`} role="status">
      <div className="flex items-start gap-2">
        {type === 'error' ? (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <span className="break-words">{children}</span>
      </div>
    </section>
  )
}

function SectionHeader({ icon, title, meta }: { icon: React.ReactNode; title: string; meta?: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink">
        {icon}
        <span>{title}</span>
      </div>
      {meta && <span className="text-xs tabular-nums text-muted">{meta}</span>}
    </div>
  )
}

function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="min-w-full divide-y divide-line text-sm">{children}</table>
    </div>
  )
}

function BreakdownSummary({ value }: { value: Record<string, unknown> | null }) {
  if (!value || Object.keys(value).length === 0) return <span className="text-muted">暂无</span>
  return (
    <div className="flex max-w-md flex-wrap gap-1">
      {Object.entries(value).slice(0, 4).map(([name, raw]) => {
        const score = isRecord(raw) ? raw.score ?? raw.normalized_value ?? raw.value ?? raw.weight : raw
        return (
          <span key={name} className="rounded border border-line bg-surface px-2 py-0.5 text-xs text-ink">
            {name}:{' '}
            <span className="tabular-nums text-muted">
              {typeof score === 'number' || typeof score === 'string' ? formatNumber(score, 3) : '已计入'}
            </span>
          </span>
        )
      })}
      {Object.keys(value).length > 4 && <span className="px-1 py-0.5 text-xs text-muted">+{Object.keys(value).length - 4}</span>}
    </div>
  )
}

function MiniIcChart({ points }: { points: Array<{ trade_date: string; ic: number | string; count?: number }> }) {
  const parsed = points
    .map((point) => ({ ...point, value: Number(point.ic) }))
    .filter((point) => Number.isFinite(point.value))
    .slice(-24)
  if (parsed.length === 0) return <div className="text-xs text-muted">暂无 IC 序列</div>

  const width = 240
  const height = 62
  const xStep = parsed.length > 1 ? width / (parsed.length - 1) : width
  const yFor = (value: number) => height / 2 - Math.max(-1, Math.min(1, value)) * (height / 2 - 6)
  const line = parsed.map((point, index) => `${index * xStep},${yFor(point.value)}`).join(' ')

  return (
    <div className="min-w-[240px]">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-16 w-full" role="img" aria-label="IC trend">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="currentColor" className="text-line" strokeWidth="1" />
        <polyline points={line} fill="none" stroke="currentColor" className="text-accent" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {parsed.map((point, index) => (
          <circle
            key={`${point.trade_date}-${index}`}
            cx={index * xStep}
            cy={yFor(point.value)}
            r="2"
            className={point.value >= 0 ? 'fill-red-500' : 'fill-emerald-500'}
          />
        ))}
      </svg>
      <div className="flex justify-between text-[11px] text-muted">
        <span>{parsed[0]?.trade_date}</span>
        <span>{parsed[parsed.length - 1]?.trade_date}</span>
      </div>
    </div>
  )
}

function GroupReturnBars({ returns }: { returns: Record<string, unknown> | null }) {
  const entries = Object.entries(returns ?? {})
    .map(([key, value]) => ({ key, value: Number(value) }))
    .filter((item) => Number.isFinite(item.value))
    .sort((a, b) => Number(a.key) - Number(b.key))
  if (entries.length === 0) return <div className="text-xs text-muted">暂无分组收益</div>
  const maxAbs = Math.max(...entries.map((item) => Math.abs(item.value)), 0.000001)

  return (
    <div className="space-y-1.5">
      {entries.map((item) => (
        <div key={item.key} className="grid grid-cols-[2.5rem_1fr_4rem] items-center gap-2 text-xs">
          <span className="text-muted">Q{item.key}</span>
          <div className="h-2 overflow-hidden rounded-full bg-line">
            <div
              className={`h-full rounded-full ${item.value >= 0 ? 'bg-red-500' : 'bg-emerald-500'}`}
              style={{ width: `${Math.max(4, (Math.abs(item.value) / maxAbs) * 100)}%` }}
            />
          </div>
          <span className={`text-right tabular-nums ${signedNumberClass(item.value)}`}>{formatPercent(item.value, 2)}</span>
        </div>
      ))}
    </div>
  )
}

export default function FactorPage() {
  const [definitions, setDefinitions] = React.useState<FactorDefinition[]>([])
  const [watchlistGroups, setWatchlistGroups] = React.useState<WatchlistGroupOption[]>([])
  const [rankData, setRankData] = React.useState<PaginatedResponse<RankItem>>({ items: [], page: 1, page_size: 50, total: 0 })
  const [analysisData, setAnalysisData] = React.useState<PaginatedResponse<FactorAnalysis>>({ items: [], page: 1, page_size: 50, total: 0 })
  const [valueData, setValueData] = React.useState<PaginatedResponse<FactorValue>>({ items: [], page: 1, page_size: 100, total: 0 })
  const [rankFilters, setRankFilters] = React.useState(defaultRankFilters)
  const [analysisFactor, setAnalysisFactor] = React.useState('')
  const [valueFilters, setValueFilters] = React.useState({ trade_date: '', factor_name: '', page_size: '100' })
  const [analyzeForm, setAnalyzeForm] = React.useState({ factor_name: '', period_start: today, period_end: today, forward_days: '5' })
  const [loadingInitial, setLoadingInitial] = React.useState(true)
  const [loadingRank, setLoadingRank] = React.useState(false)
  const [loadingAnalysis, setLoadingAnalysis] = React.useState(false)
  const [loadingValues, setLoadingValues] = React.useState(false)
  const [activeTask, setActiveTask] = React.useState<TaskKind | null>(null)
  const [taskNotice, setTaskNotice] = React.useState<TaskNotice | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [formError, setFormError] = React.useState<string | null>(null)

  const factorOptions = React.useMemo(() => definitions.map((factor) => factor.name), [definitions])
  const selectedFactorLabel = React.useMemo(() => {
    const factor = definitions.find((item) => item.name === valueFilters.factor_name)
    return factor?.display_name ?? factor?.name ?? valueFilters.factor_name
  }, [definitions, valueFilters.factor_name])

  const validateRankScope = React.useCallback(() => {
    if (rankFilters.scope_type === 'watchlist_group' && !rankFilters.scope_value.trim()) {
      setFormError('请选择自选分组')
      return false
    }
    setFormError(null)
    return true
  }, [rankFilters.scope_type, rankFilters.scope_value])

  const buildRankPath = React.useCallback(() => {
    const params = new URLSearchParams({ page_size: rankFilters.page_size || '50' })
    if (rankFilters.trade_date) params.set('trade_date', rankFilters.trade_date)
    params.set('scope_type', rankFilters.scope_type)
    if (rankFilters.scope_type === 'watchlist_group') params.set('scope_value', rankFilters.scope_value)
    return `/api/factors/rank?${params.toString()}`
  }, [rankFilters])

  const loadRank = React.useCallback(async () => {
    if (!validateRankScope()) return
    setLoadingRank(true)
    setError(null)
    try {
      const data = await fetchJson<PaginatedResponse<RankItem>>(buildRankPath())
      setRankData(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoadingRank(false)
    }
  }, [buildRankPath, validateRankScope])

  const loadAnalysis = React.useCallback(async () => {
    setLoadingAnalysis(true)
    setError(null)
    const params = new URLSearchParams({ page_size: '50' })
    if (analysisFactor) params.set('factor_name', analysisFactor)
    try {
      const data = await fetchJson<PaginatedResponse<FactorAnalysis>>(`/api/factors/analysis?${params.toString()}`)
      setAnalysisData(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoadingAnalysis(false)
    }
  }, [analysisFactor])

  const loadValues = React.useCallback(async () => {
    if (!valueFilters.trade_date || !valueFilters.factor_name) {
      setValueData({ items: [], page: 1, page_size: Number(valueFilters.page_size) || 100, total: 0 })
      return
    }
    setLoadingValues(true)
    setError(null)
    const params = new URLSearchParams({
      trade_date: valueFilters.trade_date,
      factor_name: valueFilters.factor_name,
      page_size: valueFilters.page_size || '100',
    })
    try {
      const data = await fetchJson<PaginatedResponse<FactorValue>>(`/api/factors/values?${params.toString()}`)
      setValueData(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoadingValues(false)
    }
  }, [valueFilters])

  const loadStaticData = React.useCallback(async () => {
    setLoadingInitial(true)
    setError(null)
    try {
      const [factorList, groups] = await Promise.all([
        fetchJson<FactorDefinition[]>('/api/factors?enabled_only=false'),
        fetchJson<WatchlistGroupOption[]>('/api/watchlist/groups'),
      ])
      setDefinitions(factorList)
      setWatchlistGroups(groups)
      const firstFactor = factorList[0]?.name ?? ''
      setValueFilters((current) => ({ ...current, factor_name: current.factor_name || firstFactor }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoadingInitial(false)
    }
  }, [])

  const refreshAll = React.useCallback(async () => {
    await Promise.all([loadStaticData(), loadRank(), loadAnalysis(), loadValues()])
  }, [loadAnalysis, loadRank, loadStaticData, loadValues])

  React.useEffect(() => {
    void loadStaticData()
  }, [loadStaticData])

  React.useEffect(() => {
    void loadRank()
  }, [loadRank])

  React.useEffect(() => {
    void loadAnalysis()
  }, [loadAnalysis])

  React.useEffect(() => {
    void loadValues()
  }, [loadValues])

  React.useEffect(() => {
    if (!taskNotice?.taskId) return
    const interval = window.setInterval(async () => {
      try {
        const result = await fetchJson<TaskStatus>(`/api/tasks/${taskNotice.taskId}`)
        setTaskNotice((current) => (current ? { ...current, status: result.status } : current))
        if (result.ready) {
          window.clearInterval(interval)
          setActiveTask(null)
          if (result.error) {
            setError(`${taskLabel(taskNotice.kind)}失败：${result.error}`)
            return
          }
          setNotice(`${taskLabel(taskNotice.kind)}完成：${taskNotice.taskId}`)
          if (taskNotice.kind === 'compute') {
            void loadRank()
            void loadValues()
          } else {
            void loadAnalysis()
          }
        }
      } catch (caught) {
        window.clearInterval(interval)
        setActiveTask(null)
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    }, 3000)
    return () => window.clearInterval(interval)
  }, [loadAnalysis, loadRank, loadValues, taskNotice?.kind, taskNotice?.taskId])

  const submitCompute = React.useCallback(async () => {
    if (!validateRankScope()) return
    setActiveTask('compute')
    setNotice(null)
    setError(null)
    try {
      const body = {
        trade_date: rankFilters.trade_date || null,
        scope_type: rankFilters.scope_type,
        scope_value: rankFilters.scope_type === 'watchlist_group' ? rankFilters.scope_value : null,
      }
      const result = await fetchJson<{ task_id: string; status: string }>('/api/tasks/factors/compute', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setTaskNotice({ kind: 'compute', taskId: result.task_id, status: result.status })
      setNotice(`因子计算任务已提交：${result.task_id}`)
    } catch (caught) {
      setActiveTask(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [rankFilters, validateRankScope])

  const submitAnalyze = React.useCallback(async () => {
    if (!analyzeForm.factor_name || !analyzeForm.period_start || !analyzeForm.period_end) {
      setFormError('请选择因子和 IC/IR 日期区间')
      return
    }
    setFormError(null)
    setActiveTask('analyze')
    setNotice(null)
    setError(null)
    try {
      const result = await fetchJson<{ task_id: string; status: string }>('/api/tasks/factors/analyze', {
        method: 'POST',
        body: JSON.stringify({
          factor_name: analyzeForm.factor_name,
          period_start: analyzeForm.period_start,
          period_end: analyzeForm.period_end,
          forward_days: Number(analyzeForm.forward_days) || 5,
        }),
      })
      setTaskNotice({ kind: 'analyze', taskId: result.task_id, status: result.status })
      setNotice(`IC/IR 分析任务已提交：${result.task_id}`)
    } catch (caught) {
      setActiveTask(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [analyzeForm])

  const allBusy = loadingInitial || loadingRank || loadingAnalysis || loadingValues || activeTask !== null
  const watchlistScopeBlocked = rankFilters.scope_type === 'watchlist_group' && !rankFilters.scope_value.trim()

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">因子选股</h1>
          <p className="mt-1 text-sm text-muted">因子定义、评分排行、单因子值和 IC/IR 分析。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void refreshAll()}
            disabled={allBusy}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 text-sm font-medium text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${allBusy ? 'animate-spin' : ''}`} aria-hidden="true" />
            刷新
          </button>
          <button
            type="button"
            onClick={() => void submitCompute()}
            disabled={activeTask !== null || watchlistScopeBlocked}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {activeTask === 'compute' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <DatabaseZap className="h-4 w-4" aria-hidden="true" />}
            计算因子
          </button>
          <button
            type="button"
            onClick={() => void submitAnalyze()}
            disabled={activeTask !== null || !analyzeForm.factor_name}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {activeTask === 'analyze' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
            IC/IR 分析
          </button>
        </div>
      </section>

      {(notice || error || formError || taskNotice) && (
        <div className="space-y-2">
          {error && <AlertBanner type="error">{error}</AlertBanner>}
          {formError && <AlertBanner type="error">{formError}</AlertBanner>}
          {notice && <AlertBanner type="success">{notice}</AlertBanner>}
          {taskNotice && activeTask && (
            <AlertBanner type="info">
              {taskLabel(taskNotice.kind)}任务 {taskNotice.taskId}：{normalizeTaskStatus(taskNotice.status)}
            </AlertBanner>
          )}
        </div>
      )}

      <section className="rounded-lg border border-line bg-panel p-4">
        <SectionHeader icon={<SlidersHorizontal className="h-4 w-4 text-muted" aria-hidden="true" />} title="筛选" />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
          <label className="space-y-1 text-xs text-muted">
            交易日期
            <input
              type="date"
              value={rankFilters.trade_date}
              onChange={(event) => setRankFilters((prev) => ({ ...prev, trade_date: event.target.value }))}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            />
          </label>
          <label className="space-y-1 text-xs text-muted">
            范围
            <select
              value={rankFilters.scope_type}
              onChange={(event) =>
                setRankFilters((prev) => ({ ...prev, scope_type: event.target.value as ScopeType, scope_value: event.target.value === 'all' ? '' : prev.scope_value }))
              }
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="all">全市场</option>
              <option value="watchlist_group">自选分组</option>
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            自选分组
            <select
              value={rankFilters.scope_value}
              onChange={(event) => setRankFilters((prev) => ({ ...prev, scope_value: event.target.value }))}
              disabled={rankFilters.scope_type !== 'watchlist_group' || watchlistGroups.length === 0}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
            >
              <option value="">{watchlistGroups.length === 0 ? '暂无分组' : '请选择'}</option>
              {watchlistGroups.map((group) => (
                <option key={group.group_name} value={group.group_name}>
                  {group.group_name}（{group.item_count}）
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            Top N
            <input
              type="number"
              min="1"
              max="200"
              value={rankFilters.page_size}
              onChange={(event) => setRankFilters((prev) => ({ ...prev, page_size: event.target.value }))}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm tabular-nums text-ink outline-none focus:ring-2 focus:ring-accent"
            />
          </label>
          <label className="space-y-1 text-xs text-muted">
            因子
            <select
              value={analysisFactor}
              onChange={(event) => {
                setAnalysisFactor(event.target.value)
                setAnalyzeForm((prev) => ({ ...prev, factor_name: event.target.value }))
              }}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="">全部因子</option>
              {factorOptions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            IC 开始
            <input
              type="date"
              value={analyzeForm.period_start}
              onChange={(event) => setAnalyzeForm((prev) => ({ ...prev, period_start: event.target.value }))}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            />
          </label>
          <label className="space-y-1 text-xs text-muted">
            IC 结束 / forward
            <div className="grid grid-cols-[1fr_4.5rem] gap-2">
              <input
                type="date"
                value={analyzeForm.period_end}
                onChange={(event) => setAnalyzeForm((prev) => ({ ...prev, period_end: event.target.value }))}
                className="h-10 min-w-0 rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              />
              <input
                type="number"
                min="1"
                max="60"
                value={analyzeForm.forward_days}
                onChange={(event) => setAnalyzeForm((prev) => ({ ...prev, forward_days: event.target.value }))}
                className="h-10 min-w-0 rounded-md border border-line bg-surface px-2 text-sm tabular-nums text-ink outline-none focus:ring-2 focus:ring-accent"
                aria-label="forward days"
              />
            </div>
          </label>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.9fr)]">
        <section className="rounded-lg border border-line bg-panel p-4">
          <SectionHeader
            icon={<BarChart3 className="h-4 w-4 text-muted" aria-hidden="true" />}
            title="排行榜"
            meta={`共 ${formatNumber(rankData.total)} 条`}
          />
          {loadingRank ? (
            <Skeleton.Table rows={8} columns={6} />
          ) : (
            <TableShell>
              <thead className="bg-tableHead text-left text-xs font-medium text-muted">
                <tr>
                  <th className="px-4 py-3">Rank</th>
                  <th className="px-4 py-3">股票</th>
                  <th className="px-4 py-3 text-right">总分</th>
                  <th className="px-4 py-3 text-right">百分位</th>
                  <th className="px-4 py-3">Scope</th>
                  <th className="px-4 py-3">Breakdown</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-panel">
                {rankData.items.length === 0 ? (
                  <EmptyRow colSpan={6} message="暂无排行榜数据" />
                ) : (
                  rankData.items.map((item) => (
                    <tr key={item.id} className="hover:bg-rowHover">
                      <td className="px-4 py-3 font-mono text-sm text-ink">#{item.rank}</td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink">{item.stock_name ?? item.ts_code}</div>
                        <div className="font-mono text-xs text-muted">{item.ts_code} · {formatDate(item.trade_date)}</div>
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-ink">{formatNumber(item.total_score, 4)}</td>
                      <td className="px-4 py-3 text-right font-mono text-ink">{formatPercent(item.percentile_rank, 1)}</td>
                      <td className="px-4 py-3 text-xs text-muted">{item.scope_type === 'all' ? '全市场' : item.scope_value}</td>
                      <td className="px-4 py-3"><BreakdownSummary value={item.factor_breakdown} /></td>
                    </tr>
                  ))
                )}
              </tbody>
            </TableShell>
          )}
        </section>

        <section className="rounded-lg border border-line bg-panel p-4">
          <SectionHeader icon={<Layers3 className="h-4 w-4 text-muted" aria-hidden="true" />} title="因子库" meta={`${definitions.length} 个`} />
          {loadingInitial ? (
            <Skeleton.Table rows={6} columns={4} />
          ) : (
            <TableShell>
              <thead className="bg-tableHead text-left text-xs font-medium text-muted">
                <tr>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">类别</th>
                  <th className="px-4 py-3">方向</th>
                  <th className="px-4 py-3 text-right">权重</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">表达式</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-panel">
                {definitions.length === 0 ? (
                  <EmptyRow colSpan={6} message="暂无因子定义" />
                ) : (
                  definitions.map((factor) => (
                    <tr key={factor.name} className="hover:bg-rowHover">
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink">{factor.display_name ?? factor.name}</div>
                        <div className="font-mono text-xs text-muted">{factor.name}</div>
                      </td>
                      <td className="px-4 py-3 text-sm text-ink">{factor.category}</td>
                      <td className="px-4 py-3 text-sm text-ink">{directionText(factor.direction)}</td>
                      <td className="px-4 py-3 text-right font-mono text-sm text-ink">{formatNumber(factor.default_weight, 3)}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${factor.enabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-600'}`}>
                          {factor.enabled ? '启用' : '停用'}
                        </span>
                      </td>
                      <td className="max-w-[18rem] px-4 py-3">
                        <code className="block truncate text-xs text-muted" title={factor.expression}>{factor.expression}</code>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </TableShell>
          )}
        </section>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="rounded-lg border border-line bg-panel p-4">
          <SectionHeader icon={<BarChart3 className="h-4 w-4 text-muted" aria-hidden="true" />} title="IC/IR" meta={`共 ${formatNumber(analysisData.total)} 条`} />
          {loadingAnalysis ? (
            <Skeleton.Table rows={6} columns={5} />
          ) : (
            <div className="space-y-4">
              {analysisData.items.length === 0 ? (
                <div className="rounded-lg border border-line px-4 py-8 text-center text-sm text-muted">暂无 IC/IR 分析数据</div>
              ) : (
                analysisData.items.map((item) => (
                  <div key={item.id} className="rounded-lg border border-line p-4">
                    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="font-medium text-ink">{item.display_name ?? item.factor_name}</div>
                        <div className="mt-1 text-xs text-muted">
                          {formatDate(item.period_start)} 至 {formatDate(item.period_end)} · forward {item.forward_days}
                        </div>
                      </div>
                      <div className="grid grid-cols-4 gap-3 text-right text-xs">
                        <div>
                          <div className="text-muted">IC 均值</div>
                          <div className={`mt-1 font-mono text-sm ${signedNumberClass(item.ic_mean)}`}>{formatNumber(item.ic_mean, 4)}</div>
                        </div>
                        <div>
                          <div className="text-muted">IC Std</div>
                          <div className="mt-1 font-mono text-sm text-ink">{formatNumber(item.ic_std, 4)}</div>
                        </div>
                        <div>
                          <div className="text-muted">IR</div>
                          <div className={`mt-1 font-mono text-sm ${signedNumberClass(item.ir ?? item.icir)}`}>{formatNumber(item.ir ?? item.icir, 3)}</div>
                        </div>
                        <div>
                          <div className="text-muted">IC&gt;0</div>
                          <div className="mt-1 font-mono text-sm text-ink">{formatPercent(item.ic_gt_0_pct, 1)}</div>
                        </div>
                      </div>
                    </div>
                    <div className="grid gap-4 lg:grid-cols-[minmax(240px,0.9fr)_minmax(220px,1fr)]">
                      <MiniIcChart points={item.details?.ic_by_date ?? []} />
                      <GroupReturnBars returns={item.group_returns} />
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-line bg-panel p-4">
          <SectionHeader icon={<DatabaseZap className="h-4 w-4 text-muted" aria-hidden="true" />} title="单因子值" meta={valueFilters.factor_name ? selectedFactorLabel : undefined} />
          <div className="mb-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_5rem]">
            <label className="space-y-1 text-xs text-muted">
              交易日期
              <input
                type="date"
                value={valueFilters.trade_date}
                onChange={(event) => setValueFilters((prev) => ({ ...prev, trade_date: event.target.value }))}
                className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="space-y-1 text-xs text-muted">
              因子
              <select
                value={valueFilters.factor_name}
                onChange={(event) => setValueFilters((prev) => ({ ...prev, factor_name: event.target.value }))}
                className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="">请选择</option>
                {factorOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-xs text-muted">
              Top
              <input
                type="number"
                min="1"
                max="500"
                value={valueFilters.page_size}
                onChange={(event) => setValueFilters((prev) => ({ ...prev, page_size: event.target.value }))}
                className="h-10 w-full rounded-md border border-line bg-surface px-2 text-sm tabular-nums text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
          </div>
          {loadingValues ? (
            <Skeleton.Table rows={6} columns={5} />
          ) : (
            <TableShell>
              <thead className="bg-tableHead text-left text-xs font-medium text-muted">
                <tr>
                  <th className="px-4 py-3">股票</th>
                  <th className="px-4 py-3 text-right">原始值</th>
                  <th className="px-4 py-3 text-right">标准化</th>
                  <th className="px-4 py-3 text-right">百分位</th>
                  <th className="px-4 py-3">来源</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-panel">
                {!valueFilters.trade_date || !valueFilters.factor_name ? (
                  <EmptyRow colSpan={5} message="请选择交易日期和因子" />
                ) : valueData.items.length === 0 ? (
                  <EmptyRow colSpan={5} message="暂无单因子值" />
                ) : (
                  valueData.items.map((item) => (
                    <tr key={`${item.factor_name}-${item.trade_date}-${item.ts_code}`} className="hover:bg-rowHover">
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink">{item.stock_name ?? item.ts_code}</div>
                        <div className="font-mono text-xs text-muted">{item.ts_code}</div>
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-ink">{formatNumber(item.value, 4)}</td>
                      <td className="px-4 py-3 text-right font-mono text-ink">{formatNumber(item.normalized_value, 4)}</td>
                      <td className="px-4 py-3 text-right font-mono text-ink">{formatPercent(item.percentile_rank, 1)}</td>
                      <td className="px-4 py-3 text-xs text-muted">{item.data_source ?? '暂无'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </TableShell>
          )}
          <div className="mt-3 text-xs text-muted">最近更新：{valueData.items[0] ? formatDateTime(valueData.items[0].updated_at) : '暂无'}</div>
        </section>
      </div>
    </div>
  )
}
