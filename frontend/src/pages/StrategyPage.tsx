import React from 'react'
import { AlertTriangle, Plus, Play, Save, Trash2, Loader2 } from 'lucide-react'
import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { fetchJson, formatDateTime, formatNumber } from '../App'

loader.config({ monaco })

interface Strategy {
  id: number
  name: string
  description: string | null
  pool_id: number | null
  pool_name: string | null
  status: string
  created_at: string
  updated_at: string
}

interface StrategyDetail {
  id: number
  name: string
  description: string | null
  pool_id: number | null
  pool_name: string | null
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
  pool_id: number | null
  pool_name: string | null
  strategy_name: string | null
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

interface Pool {
  id: number
  name: string
  description: string | null
  is_dynamic: boolean
  item_count: number
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
  const [pools, setPools] = React.useState<Pool[]>([])
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [editing, setEditing] = React.useState<StrategyDetail | null>(null)
  const [name, setName] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [sourceCode, setSourceCode] = React.useState(DEFAULT_CODE)
  const [selectedPoolId, setSelectedPoolId] = React.useState<number | null>(null)
  const [runHistory, setRunHistory] = React.useState<BacktestListResult[]>([])
  const [loadingRuns, setLoadingRuns] = React.useState(false)

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

  const loadPools = React.useCallback(async () => {
    try {
      const data = await fetchJson<Pool[]>('/api/pools')
      setPools(data)
    } catch {
      setPools([])
    }
  }, [])

  const createNew = () => {
    setEditing(null)
    setName('')
    setDescription('')
    setSourceCode(DEFAULT_CODE)
    setSelectedPoolId(null)
    setView('edit')
  }

  const editStrategy = async (s: Strategy) => {
    try {
      const detail = await fetchJson<StrategyDetail>(`/api/strategies/${s.id}`)
      setEditing(detail)
      setName(detail.name)
      setDescription(detail.description ?? '')
      setSourceCode(detail.source_code)
      setSelectedPoolId(detail.pool_id)
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
      const body = { name: name.trim(), description: description.trim() || null, source_code: sourceCode, pool_id: selectedPoolId }
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

  const runBacktest = async (strategy: Strategy) => {
    setError(null)
    setNotice(null)
    try {
      const result = await fetchJson<BacktestSubmitResponse>(`/api/backtests/${strategy.id}/run`, { method: 'POST' })
      setNotice(`回测任务已提交: ${result.backtest_id}`)
      await loadRunHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

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
  React.useEffect(() => { void loadPools() }, [loadPools])
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
        <section className="overflow-hidden rounded-lg border border-line bg-white shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">我的策略</h2>
            <button onClick={createNew} className="ml-auto inline-flex h-9 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-white transition hover:bg-blue-700">
              <Plus className="h-4 w-4" />
              新建策略
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                <tr><th className="px-4 py-3">名称</th><th className="px-4 py-3">股票池</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loading ? <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">加载中</td></tr> : strategies.length === 0 ? <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">暂无策略，点击上方按钮创建</td></tr> : strategies.map((s) => (
                  <tr key={s.id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-3 font-medium">{s.name}</td>
                    <td className="px-4 py-3 text-slate-700">{s.pool_name ?? '全市场'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${s.status === 'active' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-slate-200 bg-slate-50 text-slate-700'}`}>{s.status}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">{formatDateTime(s.updated_at)}</td>
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
                <label className="mb-1 block text-sm font-medium text-slate-700">股票池（可选）</label>
                <select
                  value={selectedPoolId ?? ''}
                  onChange={(e) => setSelectedPoolId(e.target.value ? Number(e.target.value) : null)}
                  className="w-full h-10 rounded-md border border-line bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  <option value="">全市场</option>
                  {pools.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.item_count} 只)</option>
                  ))}
                </select>
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
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
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
        <section className="mt-6 overflow-hidden rounded-lg border border-line bg-white shadow-sm">
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <h2 className="text-base font-semibold text-ink">回测历史</h2>
            <button onClick={() => void loadRunHistory()} className="ml-auto text-sm font-medium text-accent hover:underline">刷新</button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
                <tr><th className="px-4 py-3">策略</th><th className="px-4 py-3">股票池</th><th className="px-4 py-3">区间</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">完成时间</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {loadingRuns ? <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">加载中</td></tr> : runHistory.map((r) => (
                  <tr key={r.id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-3 font-medium">{r.strategy_name ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-700">{r.pool_name ?? '全市场'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">{r.start_date} ~ {r.end_date}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${r.status === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : r.status === 'running' ? 'border-amber-200 bg-amber-50 text-amber-800' : r.status === 'failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-slate-200 bg-slate-50 text-slate-700'}`}>{r.status}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">{r.finished_at ? formatDateTime(r.finished_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}
