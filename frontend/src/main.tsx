import React from 'react'
import ReactDOM from 'react-dom/client'
import { Activity, Database, RefreshCw, Server, ShieldCheck } from 'lucide-react'

import './styles.css'

type HealthState = 'checking' | 'ok' | 'error'

interface EndpointHealth {
  state: HealthState
  message: string
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const initialHealth: EndpointHealth = {
  state: 'checking',
  message: '检查中',
}

function statusClasses(state: HealthState): string {
  if (state === 'ok') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-900'
  }

  if (state === 'error') {
    return 'border-red-200 bg-red-50 text-red-900'
  }

  return 'border-amber-200 bg-amber-50 text-amber-900'
}

function statusText(state: HealthState): string {
  if (state === 'ok') {
    return '正常'
  }

  if (state === 'error') {
    return '异常'
  }

  return '检查中'
}

function HealthCard({
  icon,
  title,
  endpoint,
  health,
}: {
  icon: React.ReactNode
  title: string
  endpoint: string
  health: EndpointHealth
}) {
  return (
    <section className="rounded-lg border border-line bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-accent">
            {icon}
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            <p className="mt-1 break-all font-mono text-sm text-slate-600">{endpoint}</p>
          </div>
        </div>
        <span className={`shrink-0 rounded-full border px-3 py-1 text-sm font-medium ${statusClasses(health.state)}`}>
          {statusText(health.state)}
        </span>
      </div>
      <p className="mt-4 min-h-6 text-sm leading-6 text-slate-700">{health.message}</p>
    </section>
  )
}

function App() {
  const [apiHealth, setApiHealth] = React.useState<EndpointHealth>(initialHealth)
  const [dbHealth, setDbHealth] = React.useState<EndpointHealth>(initialHealth)
  const [lastCheckedAt, setLastCheckedAt] = React.useState<string>('尚未完成')
  const [isRefreshing, setIsRefreshing] = React.useState(false)

  const checkHealth = React.useCallback(async () => {
    setIsRefreshing(true)
    setApiHealth(initialHealth)
    setDbHealth(initialHealth)

    const fetchJson = async (path: string) => {
      const response = await fetch(`${apiBaseUrl}${path}`)

      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      return response.json()
    }

    const [apiResult, dbResult] = await Promise.allSettled([
      fetchJson('/health'),
      fetchJson('/api/health/db'),
    ])

    if (apiResult.status === 'fulfilled') {
      setApiHealth({ state: 'ok', message: `服务状态：${apiResult.value.status}` })
    } else {
      setApiHealth({ state: 'error', message: `无法连接后端：${apiResult.reason.message}` })
    }

    if (dbResult.status === 'fulfilled') {
      setDbHealth({ state: 'ok', message: `数据库返回：${dbResult.value.result}` })
    } else {
      setDbHealth({ state: 'error', message: `数据库检查失败：${dbResult.reason.message}` })
    }

    setLastCheckedAt(new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'short',
      timeStyle: 'medium',
    }).format(new Date()))
    setIsRefreshing(false)
  }, [])

  React.useEffect(() => {
    void checkHealth()
  }, [checkHealth])

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-line pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-mint">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              <span>Local-first A-share quant platform</span>
            </div>
            <h1 className="text-3xl font-semibold tracking-normal text-ink sm:text-4xl">Leek Quant</h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-slate-700">
              M0 基础环境已提供 FastAPI、PostgreSQL 迁移、Redis 配置和前端健康检查入口。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void checkHealth()}
            disabled={isRefreshing}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-md border border-accent bg-accent px-4 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            刷新状态
          </button>
        </header>

        <section className="grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border border-line bg-white p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <Activity className="h-4 w-4 text-mint" aria-hidden="true" />
              运行阶段
            </div>
            <p className="mt-2 text-2xl font-semibold text-ink">M0</p>
          </div>
          <div className="rounded-lg border border-line bg-white p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <Server className="h-4 w-4 text-accent" aria-hidden="true" />
              API 地址
            </div>
            <p className="mt-2 break-all font-mono text-sm text-ink">{apiBaseUrl}</p>
          </div>
          <div className="rounded-lg border border-line bg-white p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <Database className="h-4 w-4 text-warn" aria-hidden="true" />
              最近检查
            </div>
            <p className="mt-2 text-sm font-medium text-ink">{lastCheckedAt}</p>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <HealthCard
            icon={<Server className="h-5 w-5" aria-hidden="true" />}
            title="后端 API"
            endpoint="/health"
            health={apiHealth}
          />
          <HealthCard
            icon={<Database className="h-5 w-5" aria-hidden="true" />}
            title="PostgreSQL"
            endpoint="/api/health/db"
            health={dbHealth}
          />
        </section>
      </div>
    </main>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

