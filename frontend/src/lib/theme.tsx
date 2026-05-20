import React, { createContext, useContext, useEffect, useState, useCallback } from 'react'

type ThemeMode = 'light' | 'dark' | 'system'
type ResolvedTheme = 'light' | 'dark'

interface ThemeContextValue {
  theme: ResolvedTheme
  themeMode: ThemeMode
  setTheme: (mode: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

const STORAGE_KEY = 'theme'

function getSystemPreference(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return getSystemPreference()
  return mode
}

function applyThemeToDOM(theme: ResolvedTheme) {
  document.documentElement.setAttribute('data-theme', theme)
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [themeMode, setThemeModeState] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'light'
    const stored = localStorage.getItem(STORAGE_KEY) as ThemeMode | null
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
  })

  const [systemPreference, setSystemPreference] = useState<ResolvedTheme>(() => getSystemPreference())

  const theme: ResolvedTheme = resolveTheme(themeMode)

  useEffect(() => {
    applyThemeToDOM(theme)
  }, [theme])

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => {
      setSystemPreference(e.matches ? 'dark' : 'light')
    }
    mediaQuery.addEventListener('change', handler)
    return () => mediaQuery.removeEventListener('change', handler)
  }, [])

  const setTheme = useCallback((mode: ThemeMode) => {
    setThemeModeState(mode)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [])

  const value: ThemeContextValue = React.useMemo(
    () => ({ theme, themeMode, setTheme }),
    [theme, themeMode, setTheme]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}

export { type ThemeMode, type ResolvedTheme }
