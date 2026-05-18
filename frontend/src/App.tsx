import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, ListFilter, Star, Code, BarChart3 } from 'lucide-react'
import './styles.css'

type ViewKey = 'status' | 'market' | 'watchlist' | 'strategy' | 'backtest'

export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
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

export function formatNumber(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === '') return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(numeric)
}

export function formatDate(value: string | null): string {
  return value ?? '暂无'
}

export function formatDateTime(value: string | null): string {
  if (!value) return '暂无'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value))
}

export function formatMarketCap(value: string | null): string {
  if (!value) return '暂无'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '暂无'
  if (numeric >= 100000000) return `${formatNumber(numeric / 100000000, 2)} 亿`
  if (numeric >= 10000) return `${formatNumber(numeric / 10000, 2)} 万`
  return formatNumber(numeric, 2)
}

const navItems: Array<{ key: ViewKey; label: string; icon: React.ReactNode; to: string }> = [
  { key: 'status', label: '数据状态', icon: <Activity className="h-4 w-4" />, to: '/' },
  { key: 'market', label: '市场', icon: <ListFilter className="h-4 w-4" />, to: '/market' },
  { key: 'watchlist', label: '自选股', icon: <Star className="h-4 w-4" />, to: '/watchlist' },
  { key: 'strategy', label: '策略中心', icon: <Code className="h-4 w-4" />, to: '/strategy' },
  { key: 'backtest', label: '回测', icon: <BarChart3 className="h-4 w-4" />, to: '/backtests' },
]

export default function App({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const currentKey = React.useMemo<ViewKey>(() => {
    const path = location.pathname
    if (path === '/') return 'status'
    if (path === '/market') return 'market'
    if (path === '/watchlist') return 'watchlist'
    if (path === '/strategy') return 'strategy'
    if (path.startsWith('/backtests')) return 'backtest'
    return 'status'
  }, [location.pathname])

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-line pb-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-mint">
              <Activity className="h-4 w-4" aria-hidden="true" />
              <span>Local-first A-share quant platform</span>
            </div>
            <h1 className="text-3xl font-semibold tracking-normal text-ink sm:text-4xl">Leek Quant</h1>
            <p className="mt-2 max-w-3xl text-base leading-7 text-slate-700">
              A股量化研究、策略回测与模拟交易平台。
            </p>
          </div>
        </header>

        <nav className="flex gap-2 overflow-x-auto border-b border-line pb-3">
          {navItems.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-md border px-3 text-sm font-semibold transition ${
                currentKey === item.key ? 'border-accent bg-accent text-white' : 'border-line bg-white text-slate-700 hover:bg-slate-50'
              }`}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>

        {children}
      </div>
    </main>
  )
}
