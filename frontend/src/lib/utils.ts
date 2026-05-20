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
