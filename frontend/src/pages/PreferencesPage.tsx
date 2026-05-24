import React, { useCallback, useEffect, useState } from 'react'
import { ArrowUp, ArrowDown, Save, RotateCcw, Loader2, Activity, AlertTriangle, CheckCircle2, BadgePercent } from 'lucide-react'
import { fetchJson } from '../lib/utils'

interface SourceConfig {
  id: number
  name: string
  display_name: string
  priority: number
  enabled: boolean
  capabilities?: string[]
}

interface SourceCheckResult {
  name: string
  display_name?: string
  ok: boolean
  checked_capability: string | null
  records: number
  latency_ms: number
  checked_at: string
  error: string | null
}

interface TradingFeePreference {
  commission_rate: string
  min_commission: string
  waive_min_commission: boolean
  stamp_tax_rate: string
  transfer_fee_rate: string
}

const defaultTradingFee: TradingFeePreference = {
  commission_rate: '0.00025',
  min_commission: '5.0',
  waive_min_commission: false,
  stamp_tax_rate: '0.0005',
  transfer_fee_rate: '0.00001',
}

export default function PreferencesPage() {
  const [items, setItems] = useState<SourceConfig[]>([])
  const [fee, setFee] = useState<TradingFeePreference>(defaultTradingFee)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savingFee, setSavingFee] = useState(false)
  const [checkingAll, setCheckingAll] = useState(false)
  const [checking, setChecking] = useState<Record<string, boolean>>({})
  const [checkResults, setCheckResults] = useState<Record<string, SourceCheckResult>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [sourceData, feeData] = await Promise.all([
        fetchJson<SourceConfig[]>('/api/data/sources'),
        fetchJson<TradingFeePreference>('/api/preferences/trading-fee'),
      ])
      setItems(sourceData)
      setFee({ ...defaultTradingFee, ...feeData })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir
    if (target < 0 || target >= items.length) return
    const next = [...items]
    ;[next[index], next[target]] = [next[target], next[index]]
    setItems(next)
  }

  const toggle = (index: number) => {
    const next = [...items]
    next[index] = { ...next[index], enabled: !next[index].enabled }
    setItems(next)
  }

  const reset = () => { void load() }

  const updateFee = (key: keyof TradingFeePreference, value: string | boolean) => {
    setFee((prev) => ({ ...prev, [key]: value }))
  }

  const save = async () => {
    setSaving(true)
    setNotice(null)
    setError(null)
    try {
      await fetchJson('/api/data/sources', {
        method: 'PUT',
        body: JSON.stringify(items),
      })
      setNotice('数据源配置已保存')
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const saveFee = async () => {
    setSavingFee(true)
    setNotice(null)
    setError(null)
    try {
      const saved = await fetchJson<TradingFeePreference>('/api/preferences/trading-fee', {
        method: 'PUT',
        body: JSON.stringify({
          commission_rate: fee.commission_rate,
          min_commission: fee.min_commission,
          waive_min_commission: fee.waive_min_commission,
          stamp_tax_rate: fee.stamp_tax_rate,
          transfer_fee_rate: fee.transfer_fee_rate,
        }),
      })
      setFee({ ...defaultTradingFee, ...saved })
      setNotice('交易费用设置已保存')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSavingFee(false)
    }
  }

  const checkOne = async (name: string) => {
    setNotice(null)
    setError(null)
    setChecking((prev) => ({ ...prev, [name]: true }))
    try {
      const result = await fetchJson<SourceCheckResult>(`/api/data/sources/${encodeURIComponent(name)}/check`, {
        method: 'POST',
      })
      setCheckResults((prev) => ({ ...prev, [name]: result }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setChecking((prev) => ({ ...prev, [name]: false }))
    }
  }

  const checkAll = async () => {
    setCheckingAll(true)
    setNotice(null)
    setError(null)
    setChecking(Object.fromEntries(items.map((item) => [item.name, true])))
    try {
      const results = await fetchJson<SourceCheckResult[]>('/api/data/sources/check', {
        method: 'POST',
        body: JSON.stringify({ names: items.map((item) => item.name) }),
      })
      setCheckResults(Object.fromEntries(results.map((result) => [result.name, result])))
      const okCount = results.filter((result) => result.ok).length
      setNotice(`检测完成：${okCount}/${results.length} 个数据源可用`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setCheckingAll(false)
      setChecking({})
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载中...
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold text-ink">偏好设置</h1>
          <p className="mt-1 text-sm text-slate-500">管理本地数据源优先级和交易费用默认值。</p>
        </div>
      </div>

      {notice && (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </div>
      )}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      <section className="space-y-4 rounded-lg border border-line bg-bg p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-ink">数据源</h2>
            <p className="mt-1 text-sm text-slate-500">调整股票数据源的优先级、启用状态，并检测接口连通性。</p>
          </div>
          <button
            type="button"
            onClick={() => void checkAll()}
            disabled={checkingAll || items.length === 0}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {checkingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
            检测全部
          </button>
        </div>

        <div className="space-y-2">
          {items.map((item, i) => {
            const result = checkResults[item.name]
            const isChecking = checking[item.name]
            return (
              <div
                key={item.name}
                className={`flex flex-col gap-3 rounded-lg border border-line bg-surface px-4 py-3 transition-opacity sm:flex-row sm:items-center ${item.enabled ? '' : 'opacity-50'}`}
              >
                <div className="flex items-center gap-3">
                  <div className="flex flex-col gap-0.5">
                    <button onClick={() => move(i, -1)} disabled={i === 0} className="flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:text-ink disabled:opacity-30" aria-label="上移">
                      <ArrowUp className="h-4 w-4" />
                    </button>
                    <button onClick={() => move(i, 1)} disabled={i === items.length - 1} className="flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:text-ink disabled:opacity-30" aria-label="下移">
                      <ArrowDown className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">{item.display_name}</div>
                    <div className="text-xs text-slate-400">{item.name}</div>
                    {item.capabilities && item.capabilities.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {item.capabilities.map((capability) => (
                          <span key={capability} className="rounded border border-line bg-bg px-2 py-0.5 text-xs text-slate-500">
                            {capability}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 sm:ml-auto sm:justify-end">
                  {result && (
                    <div className={`flex min-h-10 items-center gap-2 rounded-md border px-3 py-2 text-xs ${result.ok ? 'border-green-200 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-800'}`}>
                      {result.ok ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}
                      <div>
                        <div className="font-semibold">{result.ok ? '可用' : '不可用'} · {result.latency_ms}ms</div>
                        <div className="max-w-[16rem] truncate">
                          {result.ok ? `${result.checked_capability} / ${result.records} 条` : result.error}
                        </div>
                      </div>
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={() => void checkOne(item.name)}
                    disabled={isChecking}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-line px-3 text-sm font-semibold text-ink hover:bg-rowHover disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isChecking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
                    检测
                  </button>

                  <label className="relative inline-flex h-10 cursor-pointer items-center">
                    <input
                      type="checkbox"
                      className="peer sr-only"
                      checked={item.enabled}
                      onChange={() => toggle(i)}
                    />
                    <div className="h-6 w-11 rounded-full border border-line bg-slate-200 after:absolute after:start-[2px] after:top-[10px] after:h-5 after:w-5 after:rounded-full after:border after:border-line after:bg-white after:transition-all peer-checked:bg-accent peer-checked:after:translate-x-full" />
                  </label>
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex flex-wrap gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="flex h-10 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存数据源
          </button>
          <button
            onClick={reset}
            className="flex h-10 items-center gap-2 rounded-md border border-line bg-surface px-4 text-sm font-medium text-ink hover:bg-line/50"
          >
            <RotateCcw className="h-4 w-4" />
            重置数据源
          </button>
        </div>
      </section>

      <section className="space-y-4 rounded-lg border border-line bg-bg p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-accent">
            <BadgePercent className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-ink">交易费用</h2>
            <p className="mt-1 text-sm text-slate-500">作为回测和模拟交易的全局默认值，单次配置仍可覆盖。</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-1.5">
            <span className="text-sm font-medium text-ink">佣金率</span>
            <input
              type="number"
              inputMode="decimal"
              min="0"
              step="0.00001"
              value={fee.commission_rate}
              onChange={(event) => updateFee('commission_rate', event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-sm font-medium text-ink">最低佣金</span>
            <input
              type="number"
              inputMode="decimal"
              min="0"
              step="0.1"
              value={fee.min_commission}
              disabled={fee.waive_min_commission}
              onChange={(event) => updateFee('min_commission', event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-50"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-sm font-medium text-ink">印花税率</span>
            <input
              type="number"
              inputMode="decimal"
              min="0"
              step="0.00001"
              value={fee.stamp_tax_rate}
              onChange={(event) => updateFee('stamp_tax_rate', event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-sm font-medium text-ink">过户费率</span>
            <input
              type="number"
              inputMode="decimal"
              min="0"
              step="0.000001"
              value={fee.transfer_fee_rate}
              onChange={(event) => updateFee('transfer_fee_rate', event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
            />
          </label>
        </div>

        <label className="flex min-h-11 items-center justify-between gap-4 rounded-md border border-line bg-surface px-3 py-2">
          <span>
            <span className="block text-sm font-medium text-ink">免5</span>
            <span className="block text-xs text-slate-500">开启后佣金按比例计算，不触发最低佣金。</span>
          </span>
          <span className="relative inline-flex h-10 shrink-0 cursor-pointer items-center">
            <input
              type="checkbox"
              className="peer sr-only"
              checked={fee.waive_min_commission}
              onChange={(event) => updateFee('waive_min_commission', event.target.checked)}
            />
            <span className="h-6 w-11 rounded-full border border-line bg-slate-200 after:absolute after:start-[2px] after:top-[10px] after:h-5 after:w-5 after:rounded-full after:border after:border-line after:bg-white after:transition-all peer-checked:bg-accent peer-checked:after:translate-x-full" />
          </span>
        </label>

        <button
          onClick={() => void saveFee()}
          disabled={savingFee}
          className="flex h-10 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-50"
        >
          {savingFee ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存交易费用
        </button>
      </section>
    </div>
  )
}
