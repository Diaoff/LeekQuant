import React from 'react'
import { AlertTriangle, Plus, Play, Save, Trash2, Loader2, ToggleLeft, ToggleRight } from 'lucide-react'
import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { fetchJson, formatDateTime, formatNumber } from '../lib/utils'
import { MYTT_FUNCTIONS, MYTT_CATEGORY_LABELS, createCompletionItem, createSignatureHelpProvider } from '../lib/mytt-completions'
import Skeleton from '../components/Skeleton'
import BacktestRunModal from '../components/BacktestRunModal'
import {
  type BacktestRunParams,
  type WatchlistGroupOption,
   buildBatchBacktestRequest,
  buildSingleBacktestRequest,
  createDefaultBacktestRunParams,
  applyLastBacktestRiskParams,
} from '../lib/backtest-run'

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

interface BatchBacktestSubmitResponse {
  backtest_ids: number[]
  task_ids: string[]
  total: number
  strategy_names: string[]
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
  params_snapshot?: unknown
  trade_records: unknown[] | null
  error_message: string | null
  created_at: string
  finished_at: string | null
}

type ViewKey = 'list' | 'edit'

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
  const [selectedIds, setSelectedIds] = React.useState<Set<number>>(new Set())
  const [batchMode, setBatchMode] = React.useState(false)
  const [submittingBacktest, setSubmittingBacktest] = React.useState(false)
  const [backtestParams, setBacktestParams] = React.useState<BacktestRunParams>(() => createDefaultBacktestRunParams())

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
      const body = { name: name.trim(), description: description.trim() || null, source_code: sourceCode, status: 'active' }
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

  const toggleStatus = async (s: Strategy) => {
    setError(null)
    setNotice(null)
    const next = s.status === 'active' ? 'paused' : 'active'
    try {
      await fetchJson(`/api/strategies/${s.id}`, { method: 'PATCH', body: JSON.stringify({ status: next }) })
      setNotice(next === 'active' ? '策略已启用' : '策略已暂停')
      await loadStrategies()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const runBacktest = (strategy: Strategy) => {
    setError(null)
    setBatchMode(false)
    setTargetStrategy(strategy)
    const defaults = createDefaultBacktestRunParams()
    const lastRunForStrategy = runHistory
      .filter((r) => r.strategy_id === strategy.id)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
    setBacktestParams(applyLastBacktestRiskParams(defaults, lastRunForStrategy ?? null))
    setShowBacktestModal(true)
  }

  const confirmBatchBacktest = async (params: BacktestRunParams) => {
    setError(null)
    setNotice(null)
    setSubmittingBacktest(true)
    try {
      const body = buildBatchBacktestRequest([...selectedIds], params)
      const result = await fetchJson<BatchBacktestSubmitResponse>('/api/backtests/batch', { method: 'POST', body: JSON.stringify(body) })
      setNotice(`已提交 ${result.total} 个回测任务: ${result.strategy_names.join(', ')}`)
      setShowBacktestModal(false)
      setBatchMode(false)
      setSelectedIds(new Set())
      await loadRunHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmittingBacktest(false)
    }
  }

  const confirmBacktest = async (params: BacktestRunParams) => {
    if (batchMode) {
      await confirmBatchBacktest(params)
      return
    }
    if (!targetStrategy) return
    setError(null)
    setNotice(null)
    setSubmittingBacktest(true)
    try {
      const body = buildSingleBacktestRequest(targetStrategy.id, params)
      await fetchJson('/api/backtests', { method: 'POST', body: JSON.stringify(body) })
      setNotice('回测任务已提交')
      setShowBacktestModal(false)
      setTargetStrategy(null)
      await loadRunHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmittingBacktest(false)
    }
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (strategies.length === 0) return
    if (selectedIds.size === strategies.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(strategies.map((s) => s.id)))
    }
  }

  const openBatchBacktest = () => {
    setError(null)
    setBatchMode(true)
    setTargetStrategy(null)
    const defaults = createDefaultBacktestRunParams()
    const lastRun = runHistory
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
    setBacktestParams(applyLastBacktestRiskParams(defaults, lastRun ?? null))
    setShowBacktestModal(true)
  }

  const loadRunHistory = async () => {
    setLoadingRuns(true)
    try {
      const data = await fetchJson<{ items: BacktestListResult[]; total: number } | BacktestListResult[]>('/api/backtests?limit=20')
      const items = Array.isArray(data) ? data : data.items
      setRunHistory(items)
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
            {selectedIds.size >= 2 && (
              <button onClick={openBatchBacktest} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700">
                <Play className="h-4 w-4" />
                批量回测({selectedIds.size})
              </button>
            )}
            <button onClick={createNew} className="ml-auto inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700">
              <Plus className="h-4 w-4" />
              新建策略
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-tableHead text-xs font-semibold uppercase text-muted">
                <tr>
                  <th className="w-10 px-2 py-3">
                    <input type="checkbox" onChange={toggleSelectAll} checked={strategies.length > 0 && selectedIds.size === strategies.length} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                  </th>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">更新时间</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-4">
                      <Skeleton.Table rows={3} columns={5} />
                    </td>
                  </tr>
                ) : strategies.length === 0 ? <tr><td colSpan={5} className="px-4 py-8 text-center text-muted">暂无策略，点击上方按钮创建</td></tr> : strategies.map((s) => (
                  <tr key={s.id} className="hover:bg-rowHover">
                    <td className="px-2 py-3">
                      <input type="checkbox" checked={selectedIds.has(s.id)} onChange={() => toggleSelect(s.id)} className="h-4 w-4 rounded border-line text-accent focus:ring-accent" />
                    </td>
                    <td className="px-4 py-3 font-medium">{s.name}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${s.status === 'active' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-line bg-tableHead text-muted'}`}>{s.status}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDateTime(s.updated_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        {s.status !== 'archived' && (
                          <button onClick={() => void toggleStatus(s)} className="inline-flex items-center gap-1 text-sm font-medium text-amber-600 hover:underline" title={s.status === 'active' ? '暂停' : '启用'}>
                            {s.status === 'active' ? <ToggleRight className="h-3.5 w-3.5" /> : <ToggleLeft className="h-3.5 w-3.5" />}
                            {s.status === 'active' ? '暂停' : '启用'}
                          </button>
                        )}
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
        <BacktestRunModal
          title={batchMode ? `回测参数设置（${selectedIds.size} 个策略，将依次执行）` : '回测参数设置'}
          submitLabel="确认回测"
          initialParams={backtestParams}
          watchlistGroups={watchlistGroups}
          submitting={submittingBacktest}
          submitError={error}
          onCancel={() => { setError(null); setShowBacktestModal(false) }}
          onSubmit={confirmBacktest}
        />
      )}
    </>
  )
}
