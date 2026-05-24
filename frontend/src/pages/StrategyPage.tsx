import React from 'react'
import { AlertTriangle, Plus, Play, Save, Trash2, Loader2 } from 'lucide-react'
import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { fetchJson, formatDateTime, formatNumber } from '../lib/utils'
import { MYTT_FUNCTIONS, MYTT_CATEGORY_LABELS, createCompletionItem, createSignatureHelpProvider } from '../lib/mytt-completions'
import Skeleton from '../components/Skeleton'

loader.config({ monaco })

let myttProvidersRegistered = false

function registerMyTTProviders() {
  if (myttProvidersRegistered) return
  myttProvidersRegistered = true

  monaco.languages.registerCompletionItemProvider('python', {
    triggerCharacters: ['.', ' ', '('],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      const items = MYTT_FUNCTIONS.map((f) => createCompletionItem(monaco, f, range))
      return { suggestions: items }
    },
  })

  monaco.languages.registerSignatureHelpProvider('python', createSignatureHelpProvider(monaco))
}

interface Strategy {
  id: number
  name: string
  description: string | null
  status: string
  created_at: string
  updated_at: string
}

interface StrategyDetail {
  id: number
  name: string
  description: string | null
  status: string
  source_code: string
  created_at: string
  updated_at: string
}

interface BacktestSubmitResponse {
  backtest_id: number
  strategy_id: number
  task_id: string
  status: string
}

interface BacktestListResult {
  id: number
  strategy_id: number
  strategy_name: string | null
  target_type?: 'all' | 'market' | 'watchlist_group'
  target_value?: string | string[] | null
  target_label?: string | null
  start_date: string
  end_date: string
  initial_cash: string
  status: string
  total_return: string | null
  annual_return: string | null
  sharpe_ratio: string | null
  max_drawdown: string | null
  annual_vol: string | null
  win_rate: string | null
  trade_count: number | null
  performance: Record<string, unknown> | null
  trade_records: unknown[] | null
  error_message: string | null
  created_at: string
  finished_at: string | null
}

interface BacktestParams {
  start_date: string
  end_date: string
  initial_cash: number
  target_type: 'all' | 'market' | 'watchlist_group'
  target_value: string | string[]
  exclude_st: boolean
  exclude_loss_pe: boolean
  stop_loss_pct: string
  take_profit_pct: string
  trailing_stop_pct: string
  time_stop_days: string
}

interface WatchlistGroupOption {
  group_name: string
  item_count: number
}

const MARKET_OPTIONS = ['主板', '创业板', '科创板', '北交所'] as const

type ViewKey = 'list' | 'edit'
type BacktestTargetType = BacktestParams['target_type']
type MarketOption = typeof MARKET_OPTIONS[number]

function defaultFiltersForTarget(targetType: BacktestTargetType) {
  const enabled = targetType === 'all' || targetType === 'market'
  return { exclude_st: enabled, exclude_loss_pe: enabled }
}

function selectedMarkets(value: BacktestParams['target_value']): MarketOption[] {
  if (!Array.isArray(value)) return []
  return value.filter((market): market is MarketOption => MARKET_OPTIONS.includes(market as MarketOption))
}

const DEFAULT_CODE = `# 双均线策略示例
# 可用指标: MA, EMA, RSI, MACD, BOLL, KDJ, ATR 等 (MyTT)
# ctx 对象提供: ctx.close, ctx.open, ctx.high, ctx.low, ctx.volume, ctx.amount
# ctx.current_position 当前持仓比例 (0.0 ~ 1.0)
# 返回值: dict, 如 {"signal_type": "买入", "target_position": 1.0}

def generate_signal(ctx):
    close = ctx.close
    ma5 = MA(close, 5)
    ma20 = MA(close, 20)
    
    # 金叉 → 买入, 死叉 → 卖出
    if len(close) < 21:
        return {"signal_type": "观望"}
    
    if ma5[-1] > ma20[-1] and ma5[-2] <= ma20[-2]:
        return {"signal_type": "买入", "target_position": 1.0}
    elif ma5[-1] < ma20[-1] and ma5[-2] >= ma20[-2]:
        return {"signal_type": "卖出"}
    elif ma5[-1] > ma20[-1]:
        return {"signal_type": "观望"}
    else:
        return {"signal_type": "观望"}
`

export default function StrategyPage() {
  const [view, setView] = React.useState<ViewKey>('list')
  const [strategies, setStrategies] = React.useState<Strategy[]>([])
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [editing, setEditing] = React.useState<StrategyDetail | null>(null)
  const [name, setName] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [sourceCode, setSourceCode] = React.useState(DEFAULT_CODE)
  const [watchlistGroups, setWatchlistGroups] = React.useState<WatchlistGroupOption[]>([])
  const [runHistory, setRunHistory] = React.useState<BacktestListResult[]>([])
  const [loadingRuns, setLoadingRuns] = React.useState(false)
  const [showBacktestModal, setShowBacktestModal] = React.useState(false)
  const [targetStrategy, setTargetStrategy] = React.useState<Strategy | null>(null)
  const [backtestParams, setBacktestParams] = React.useState<BacktestParams>({
    start_date: new Date(new Date().setFullYear(new Date().getFullYear() - 1)).toISOString().split('T')[0],
    end_date: new Date().toISOString().split('T')[0],
    initial_cash: 100000,
    target_type: 'all',
    target_value: '',
    ...defaultFiltersForTarget('all'),
    stop_loss_pct: '',
    take_profit_pct: '',
    trailing_stop_pct: '',
    time_stop_days: '',
  })

  const loadStrategies = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<Strategy[]>('/api/strategies')
      setStrategies(data)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadWatchlistGroups = React.useCallback(async () => {
    try {
      const data = await fetchJson<WatchlistGroupOption[]>('/api/watchlist/groups')
      setWatchlistGroups(data)
    } catch {
      setWatchlistGroups([])
    }
  }, [])

  const createNew = () => {
    setEditing(null)
    setName('')
    setDescription('')
    setSourceCode(DEFAULT_CODE)
    setView('edit')
  }

  const editStrategy = async (s: Strategy) => {
    try {
      const detail = await fetchJson<StrategyDetail>(`/api/strategies/${s.id}`)
      setEditing(detail)
      setName(detail.name)
      setDescription(detail.description ?? '')
      setSourceCode(detail.source_code)
      setView('edit')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const saveStrategy = async () => {
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const body = { name: name.trim(), description: description.trim() || null, source_code: sourceCode }
      if (editing) {
        await fetchJson(`/api/strategies/${editing.id}`, { method: 'PATCH', body: JSON.stringify(body) })
        setNotice('策略已更新')
      } else {
        await fetchJson('/api/strategies', { method: 'POST', body: JSON.stringify(body) })
        setNotice('策略已创建')
      }
      await loadStrategies()
      setView('list')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const deleteStrategy = async (id: number) => {
    setError(null)
    setNotice(null)
    try {
      await fetchJson(`/api/strategies/${id}`, { method: 'DELETE' })
      setNotice('策略已删除')
      await loadStrategies()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const selectTargetType = (targetType: BacktestTargetType) => {
    setBacktestParams({
      ...backtestParams,
      target_type: targetType,
      target_value: targetType === 'market' ? [] : '',
      ...defaultFiltersForTarget(targetType),
    })
  }

  const toggleMarketTarget = (market: MarketOption) => {
    const current = selectedMarkets(backtestParams.target_value)
    const next = current.includes(market)
      ? current.filter((item) => item !== market)
      : MARKET_OPTIONS.filter((item) => item === market || current.includes(item))
    setBacktestParams({ ...backtestParams, target_value: next })
  }

  const runBacktest = async (strategy: Strategy) => {
    setTargetStrategy(strategy)
    setBacktestParams({
      start_date: new Date(new Date().setFullYear(new Date().getFullYear() - 1)).toISOString().split('T')[0],
      end_date: new Date().toISOString().split('T')[0],
      initial_cash: 100000,
      target_type: 'all',
      target_value: '',
      ...defaultFiltersForTarget('all'),
      stop_loss_pct: '',
      take_profit_pct: '',
      trailing_stop_pct: '',
      time_stop_days: '',
    })
    setShowBacktestModal(true)
  }

  const confirmBacktest = async () => {
    if (!targetStrategy) return
    setError(null)
    setNotice(null)
    try {
      const config: Record<string, unknown> = {}
      if (backtestParams.stop_loss_pct) config.stop_loss_pct = parseFloat(backtestParams.stop_loss_pct) / 100
      if (backtestParams.take_profit_pct) config.take_profit_pct = parseFloat(backtestParams.take_profit_pct) / 100
      if (backtestParams.trailing_stop_pct) config.trailing_stop_pct = parseFloat(backtestParams.trailing_stop_pct) / 100
      if (backtestParams.time_stop_days) config.time_stop_days = parseInt(backtestParams.time_stop_days, 10)
      const body = {
        strategy_id: targetStrategy.id,
        start_date: backtestParams.start_date,
        end_date: backtestParams.end_date,
        initial_cash: backtestParams.initial_cash,
        config: Object.keys(config).length > 0 ? config : undefined,
        target_type: backtestParams.target_type,
        target_value: backtestParams.target_type === 'all' ? null : backtestParams.target_value,
        exclude_st: backtestParams.exclude_st,
        exclude_loss_pe: backtestParams.exclude_loss_pe,
      }
      await fetchJson('/api/backtests', { method: 'POST', body: JSON.stringify(body) })
      setNotice('回测任务已提交')
      setShowBacktestModal(false)
      setTargetStrategy(null)
      await loadRunHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const isBacktestTargetValid =
    backtestParams.target_type === 'all' ||
    (backtestParams.target_type === 'market' && selectedMarkets(backtestParams.target_value).length > 0) ||
    (backtestParams.target_type === 'watchlist_group' && typeof backtestParams.target_value === 'string' && backtestParams.target_value.trim().length > 0)

  const loadRunHistory = async () => {
    setLoadingRuns(true)
    try {
      const data = await fetchJson<BacktestListResult[]>('/api/backtests?limit=20')
      setRunHistory(data)
    } catch {
      // ignore
    } finally {
      setLoadingRuns(false)
    }
  }

  React.useEffect(() => { void loadStrategies() }, [loadStrategies])
  React.useEffect(() => { void loadWatchlistGroups() }, [loadWatchlistGroups])
  React.useEffect(() => { void loadRunHistory() }, [])

  return (
    <>
      {(notice || error) && (
        <section className={`rounded-lg border p-4 text-sm ${error ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50 text-emerald-900'}`} role="status">
          <div className="flex items-start gap-2">
            {error ? <AlertTriangle className="mt-0.5 h-4 w-4" /> : null}
            <span className="break-words">{error ?? notice}</span>
          </div>
        </section>
      )}

      {view === 'list' && (
        <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">我的策略</h2>
            <button onClick={createNew} className="ml-auto inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700">
              <Plus className="h-4 w-4" />
              新建策略
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr><th className="px-4 py-3">名称</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-4">
                      <Skeleton.Table rows={3} columns={4} />
                    </td>
                  </tr>
                ) : strategies.length === 0 ? <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">暂无策略，点击上方按钮创建</td></tr> : strategies.map((s) => (
                  <tr key={s.id} className="hover:bg-rowHover">
                    <td className="px-4 py-3 font-medium">{s.name}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${s.status === 'active' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-line bg-tableHead text-muted'}`}>{s.status}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDateTime(s.updated_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        <button onClick={() => editStrategy(s)} className="text-sm font-medium text-accent hover:underline">编辑</button>
                        <button onClick={() => void runBacktest(s)} className="inline-flex items-center gap-1 text-sm font-medium text-mint hover:underline"><Play className="h-3 w-3" />回测</button>
                        <button onClick={() => void deleteStrategy(s.id)} className="text-sm font-medium text-red-600 hover:underline">删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {view === 'edit' && (
        <section className="rounded-lg border border-line bg-white shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <button onClick={() => setView('list')} className="text-sm font-medium text-accent hover:underline">← 返回</button>
            <h2 className="text-base font-semibold text-ink">{editing ? '编辑策略' : '新建策略'}</h2>
          </div>
          <div className="p-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">策略名称</label>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入策略名称" className="w-full h-10 rounded-md border border-line bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">描述（可选）</label>
                <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="策略描述" className="w-full h-10 rounded-md border border-line bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
              </div>
            </div>

            <div className="mt-4">
              <label className="mb-1 block text-sm font-medium text-slate-700">策略代码</label>
              <div className="h-96 overflow-hidden rounded-md border border-line">
                <Editor
                  height="100%"
                  defaultLanguage="python"
                  value={sourceCode}
                  onChange={(v) => setSourceCode(v ?? '')}
                  theme="vs-light"
                  onMount={() => registerMyTTProviders()}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    quickSuggestions: true,
                    parameterHints: { enabled: true },
                    suggestOnTriggerCharacters: true,
                  }}
                />
              </div>
            </div>

            <div className="mt-4 flex gap-2">
              <button onClick={() => void saveStrategy()} disabled={saving || !name.trim()} className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-4 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {saving ? '保存中' : '保存'}
              </button>
              <button onClick={() => setView('list')} className="h-10 rounded-md border border-line px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50">取消</button>
            </div>
          </div>
        </section>
      )}

      {runHistory.length > 0 && (
        <section className="mt-6 overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">回测历史</h2>
            <button onClick={() => void loadRunHistory()} className="ml-auto text-sm font-medium text-accent hover:underline">刷新</button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr><th className="px-4 py-3">策略</th><th className="px-4 py-3">标的</th><th className="px-4 py-3">区间</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">收益率</th><th className="px-4 py-3">完成时间</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loadingRuns ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-4">
                      <Skeleton.Table rows={3} columns={6} />
                    </td>
                  </tr>
                ) : runHistory.map((r) => {
                  const perf = r.performance as Record<string, string> | null
                  const totalReturn = perf?.total_return ?? r.total_return ?? null
                  return (
                  <tr key={r.id} className="hover:bg-rowHover">
                    <td className="px-4 py-3 font-medium">{r.strategy_name ?? '—'}</td>
                    <td className="px-4 py-3 text-muted">{r.target_label ?? '全市场'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{r.start_date} ~ {r.end_date}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${r.status === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : r.status === 'running' ? 'border-amber-200 bg-amber-50 text-amber-800' : r.status === 'failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-line bg-tableHead text-muted'}`}>{r.status}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums font-medium">
                      {totalReturn !== null && totalReturn !== '—' ? (
                        <span className={Number(totalReturn) >= 0 ? 'text-red-600' : 'text-emerald-600'}>
                          {totalReturn.toString().startsWith('-') || Number(totalReturn) < 0 ? '' : ''}{totalReturn.toString().includes('%') ? totalReturn : `${(Number(totalReturn) * 100).toFixed(2)}%`}
                        </span>
                      ) : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{r.finished_at ? formatDateTime(r.finished_at) : '—'}</td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {showBacktestModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowBacktestModal(false)}>
          <div className={`w-full max-w-lg rounded-lg p-6 ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'bg-slate-800 text-slate-100' : 'bg-white text-ink'}`} onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-4 text-lg font-semibold">回测参数设置</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-600">标的范围</label>
                <div className="grid grid-cols-3 gap-2">
                  <button type="button" onClick={() => selectTargetType('all')} className={`h-10 rounded-md border px-3 text-sm font-medium ${backtestParams.target_type === 'all' ? 'border-accent bg-accent text-white' : 'border-line text-slate-700 hover:bg-slate-50'}`}>全市场</button>
                  <button type="button" onClick={() => selectTargetType('market')} className={`h-10 rounded-md border px-3 text-sm font-medium ${backtestParams.target_type === 'market' ? 'border-accent bg-accent text-white' : 'border-line text-slate-700 hover:bg-slate-50'}`}>市场板块</button>
                  <button type="button" onClick={() => selectTargetType('watchlist_group')} className={`h-10 rounded-md border px-3 text-sm font-medium ${backtestParams.target_type === 'watchlist_group' ? 'border-accent bg-accent text-white' : 'border-line text-slate-700 hover:bg-slate-50'}`}>自选分组</button>
                </div>
              </div>
              {backtestParams.target_type === 'market' && (
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">市场板块</label>
                  <div className="grid grid-cols-2 gap-2">
                    {MARKET_OPTIONS.map((market) => {
                      const checked = selectedMarkets(backtestParams.target_value).includes(market)
                      return (
                        <label key={market} className={`flex min-h-10 items-center gap-2 rounded-md border px-3 text-sm font-medium ${checked ? 'border-accent bg-blue-50 text-accent' : document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700 text-slate-200' : 'border-line bg-white text-slate-700'}`}>
                          <input type="checkbox" checked={checked} onChange={() => toggleMarketTarget(market)} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                          <span>{market}</span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}
              {backtestParams.target_type === 'watchlist_group' && (
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">自选股分组</label>
                  <select value={typeof backtestParams.target_value === 'string' ? backtestParams.target_value : ''} onChange={(e) => setBacktestParams({ ...backtestParams, target_value: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} disabled={watchlistGroups.length === 0}>
                    <option value="">{watchlistGroups.length === 0 ? '暂无分组' : '请选择'}</option>
                    {watchlistGroups.map((group) => (
                      <option key={group.group_name} value={group.group_name}>{group.group_name} ({group.item_count} 只)</option>
                    ))}
                  </select>
                </div>
              )}
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <label className={`flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm font-medium ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700 text-slate-200' : 'border-line bg-white text-slate-700'}`}>
                  <input type="checkbox" checked={backtestParams.exclude_st} onChange={(e) => setBacktestParams({ ...backtestParams, exclude_st: e.target.checked })} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                  <span>排除 ST</span>
                </label>
                <label className={`flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm font-medium ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700 text-slate-200' : 'border-line bg-white text-slate-700'}`}>
                  <input type="checkbox" checked={backtestParams.exclude_loss_pe} onChange={(e) => setBacktestParams({ ...backtestParams, exclude_loss_pe: e.target.checked })} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                  <span>排除亏损市盈率 PE&lt;=0</span>
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">开始日期</label>
                  <input type="date" value={backtestParams.start_date} onChange={(e) => setBacktestParams({ ...backtestParams, start_date: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">结束日期</label>
                  <input type="date" value={backtestParams.end_date} onChange={(e) => setBacktestParams({ ...backtestParams, end_date: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-600">初始资金</label>
                <input type="number" value={backtestParams.initial_cash} onChange={(e) => setBacktestParams({ ...backtestParams, initial_cash: Number(e.target.value) })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">止损 %（可选）</label>
                  <input type="number" step="0.1" placeholder="例如 5" value={backtestParams.stop_loss_pct} onChange={(e) => setBacktestParams({ ...backtestParams, stop_loss_pct: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">止盈 %（可选）</label>
                  <input type="number" step="0.1" placeholder="例如 10" value={backtestParams.take_profit_pct} onChange={(e) => setBacktestParams({ ...backtestParams, take_profit_pct: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">移动止损 %（可选）</label>
                  <input type="number" step="0.1" placeholder="例如 3" value={backtestParams.trailing_stop_pct} onChange={(e) => setBacktestParams({ ...backtestParams, trailing_stop_pct: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-600">最大持仓天数（可选）</label>
                  <input type="number" step="1" placeholder="例如 20" value={backtestParams.time_stop_days} onChange={(e) => setBacktestParams({ ...backtestParams, time_stop_days: e.target.value })} className={`w-full h-10 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 bg-slate-700' : 'border-line bg-white'}`} />
                </div>
              </div>
            </div>
            <div className="mt-6 flex gap-3">
              <button onClick={() => setShowBacktestModal(false)} className={`flex-1 h-10 rounded-md border px-4 text-sm font-semibold transition ${document.documentElement.getAttribute('data-theme') === 'dark' ? 'border-slate-600 text-slate-300 hover:bg-slate-700' : 'border-line text-slate-700 hover:bg-slate-50'}`}>取消</button>
              <button onClick={() => void confirmBacktest()} disabled={!isBacktestTargetValid} className="flex-1 h-10 rounded-md bg-accent px-4 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60">确认回测</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
