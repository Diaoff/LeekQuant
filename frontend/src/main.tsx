import React, { Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Layout from './components/Layout'
import { ThemeProvider } from './lib/theme'
import './styles.css'

// Route-level code splitting: every page is loaded on demand so the initial
// bundle only ships the shell + dashboard. Heavy pages (BacktestPage,
// SimulationPage, MarketPage) no longer block first paint.
const BacktestPage = React.lazy(() => import('./pages/BacktestPage'))
const DashboardPage = React.lazy(() => import('./pages/DashboardPage'))
const PreferencesPage = React.lazy(() => import('./pages/PreferencesPage'))
const MarketPage = React.lazy(() => import('./pages/MarketPage'))
const SignalsPage = React.lazy(() => import('./pages/SignalsPage'))
const SimulationPage = React.lazy(() => import('./pages/SimulationPage'))
const StatusPage = React.lazy(() => import('./pages/StatusPage'))
const StrategyPage = React.lazy(() => import('./pages/StrategyPage'))
const WatchlistPage = React.lazy(() => import('./pages/WatchlistPage'))
const SeesawPage = React.lazy(() => import('./pages/SeesawPage'))

function PageFallback() {
  return (
    <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
      加载中…
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <Layout>
          <App>
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/" element={<DashboardPage />} />
                <Route path="/status" element={<StatusPage />} />
                <Route path="/preferences" element={<PreferencesPage />} />
                <Route path="/sources" element={<PreferencesPage />} />
                <Route path="/market" element={<MarketPage />} />
                <Route path="/watchlist" element={<WatchlistPage />} />
                <Route path="/strategy" element={<StrategyPage />} />
                <Route path="/backtests" element={<BacktestPage />} />
                <Route path="/backtests/:id" element={<BacktestPage />} />
                <Route path="/backtests/compare" element={<BacktestPage />} />
                <Route path="/signals" element={<SignalsPage />} />
                <Route path="/simulation" element={<SimulationPage />} />
                <Route path="/seesaw" element={<SeesawPage />} />
              </Routes>
            </Suspense>
          </App>
        </Layout>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
)
