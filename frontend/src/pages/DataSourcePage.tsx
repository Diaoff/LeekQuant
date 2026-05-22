import React, { useCallback, useEffect, useState } from 'react'
import { ArrowUp, ArrowDown, Save, RotateCcw, Loader2 } from 'lucide-react'
import { fetchJson } from '../lib/utils'

interface SourceConfig {
  id: number
  name: string
  display_name: string
  priority: number
  enabled: boolean
}

export default function DataSourcePage() {
  const [items, setItems] = useState<SourceConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<SourceConfig[]>('/api/data/sources')
      setItems(data)
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

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载中...
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-ink">数据源配置</h1>
        <p className="mt-1 text-sm text-slate-500">调整股票数据源的优先级和启用状态。</p>
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

      <div className="space-y-2">
        {items.map((item, i) => (
          <div
            key={item.name}
            className={`flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 transition-opacity ${item.enabled ? '' : 'opacity-50'}`}
          >
            <div className="flex flex-col gap-0.5">
              <button onClick={() => move(i, -1)} disabled={i === 0} className="rounded p-0.5 text-slate-400 hover:text-ink disabled:opacity-30" aria-label="上移">
                <ArrowUp className="h-4 w-4" />
              </button>
              <button onClick={() => move(i, 1)} disabled={i === items.length - 1} className="rounded p-0.5 text-slate-400 hover:text-ink disabled:opacity-30" aria-label="下移">
                <ArrowDown className="h-4 w-4" />
              </button>
            </div>

            <div className="flex-1">
              <div className="text-sm font-medium text-ink">{item.display_name}</div>
              <div className="text-xs text-slate-400">{item.name}</div>
            </div>

            <label className="relative inline-flex cursor-pointer items-center">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={item.enabled}
                onChange={() => toggle(i)}
              />
              <div className="h-6 w-11 rounded-full border border-line bg-slate-200 after:absolute after:start-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-line after:bg-white after:transition-all peer-checked:bg-accent peer-checked:after:translate-x-full" />
            </label>
          </div>
        ))}
      </div>

      <div className="flex gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存
        </button>
        <button
          onClick={reset}
          className="flex items-center gap-2 rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-line/50"
        >
          <RotateCcw className="h-4 w-4" />
          重置
        </button>
      </div>
    </div>
  )
}
