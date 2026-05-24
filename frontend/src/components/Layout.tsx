import React, { useState, useEffect, useCallback } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Activity,
  ListFilter,
  Star,
  Code,
  BarChart3,
  RadioTower,
  WalletCards,
  Sigma,
  Settings,
  ChevronLeft,
  ChevronRight,
  Sun,
  Moon,
} from 'lucide-react'
import { useTheme } from '../lib/theme'

type ViewKey = 'dashboard' | 'status' | 'market' | 'watchlist' | 'strategy' | 'backtest' | 'signals' | 'simulation' | 'factor' | 'preferences'

const navItems: Array<{ key: ViewKey; label: string; icon: React.ReactNode; to: string }> = [
  { key: 'dashboard', label: '仪表盘', icon: <LayoutDashboard className="h-5 w-5" />, to: '/' },
  { key: 'status', label: '数据状态', icon: <Activity className="h-5 w-5" />, to: '/status' },
  { key: 'market', label: '市场', icon: <ListFilter className="h-5 w-5" />, to: '/market' },
  { key: 'watchlist', label: '自选股', icon: <Star className="h-5 w-5" />, to: '/watchlist' },
  { key: 'strategy', label: '策略中心', icon: <Code className="h-5 w-5" />, to: '/strategy' },
  { key: 'backtest', label: '回测', icon: <BarChart3 className="h-5 w-5" />, to: '/backtests' },
  { key: 'signals', label: '信号中心', icon: <RadioTower className="h-5 w-5" />, to: '/signals' },
  { key: 'simulation', label: '模拟交易', icon: <WalletCards className="h-5 w-5" />, to: '/simulation' },
  { key: 'factor', label: '因子选股', icon: <Sigma className="h-5 w-5" />, to: '/factor' },
  { key: 'preferences', label: '偏好设置', icon: <Settings className="h-5 w-5" />, to: '/preferences' },
]

const SIDEBAR_EXPANDED_WIDTH = 240
const SIDEBAR_COLLAPSED_WIDTH = 64
const STORAGE_KEY = 'sidebar-collapsed'

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const { theme, setTheme } = useTheme()
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(STORAGE_KEY) === 'true'
  })

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(collapsed))
  }, [collapsed])

  const toggleSidebar = useCallback(() => {
    setCollapsed((prev) => !prev)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }, [theme, setTheme])

  const currentKey = React.useMemo<ViewKey>(() => {
    const path = location.pathname
    if (path === '/' || path === '/dashboard') return 'dashboard'
    if (path === '/status') return 'status'
    if (path === '/market') return 'market'
    if (path === '/watchlist') return 'watchlist'
    if (path === '/strategy') return 'strategy'
    if (path.startsWith('/backtests')) return 'backtest'
    if (path === '/signals') return 'signals'
    if (path === '/simulation') return 'simulation'
    if (path === '/factor') return 'factor'
    if (path === '/preferences' || path === '/sources') return 'preferences'
    return 'dashboard'
  }, [location.pathname])

  return (
    <div className="flex h-dvh w-full bg-bg text-ink">
      <aside
        className="relative flex shrink-0 flex-col border-r border-line bg-surface transition-all duration-300 ease-in-out"
        style={{ width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_EXPANDED_WIDTH }}
      >
        <div className="flex h-14 shrink-0 items-center justify-between px-4 border-b border-line">
          {!collapsed && (
            <div className="flex items-center gap-2 text-sm font-medium text-mint">
              <Activity className="h-4 w-4" aria-hidden="true" />
              <span>Leek Quant</span>
            </div>
          )}
          <button
            onClick={toggleSidebar}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-ink hover:bg-line transition-colors"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>

        {!collapsed && (
          <div className="px-4 py-2 text-xs font-medium text-slate-500">
            Local-first A-share quant platform
          </div>
        )}

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-3">
          {navItems.map((item) => {
            const isActive = currentKey === item.key
            return (
              <Link
                key={item.key}
                to={item.to}
                className={`flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-accent text-white'
                    : 'text-slate-700 hover:bg-line/50'
                } ${collapsed ? 'justify-center px-0' : ''}`}
                title={collapsed ? item.label : undefined}
              >
                <span className="shrink-0">{item.icon}</span>
                {!collapsed && <span>{item.label}</span>}
              </Link>
            )
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-surface px-6">
          <h2 className="text-lg font-semibold text-ink">
            {navItems.find((item) => item.key === currentKey)?.label ?? 'Leek Quant'}
          </h2>
          <button
            onClick={toggleTheme}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink hover:bg-line transition-colors"
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </header>

        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  )
}
