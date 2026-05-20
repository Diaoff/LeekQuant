import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Layout from './components/Layout'
import { ThemeProvider } from './lib/theme'
import DashboardPage from './pages/DashboardPage'
import StatusPage from './pages/StatusPage'
import MarketPage from './pages/MarketPage'
import WatchlistPage from './pages/WatchlistPage'
import StrategyPage from './pages/StrategyPage'
import BacktestPage from './pages/BacktestPage'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <Layout>
          <App>
            <Routes>
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/" element={<DashboardPage />} />
              <Route path="/status" element={<StatusPage />} />
              <Route path="/market" element={<MarketPage />} />
              <Route path="/watchlist" element={<WatchlistPage />} />
              <Route path="/strategy" element={<StrategyPage />} />
              <Route path="/backtests" element={<BacktestPage />} />
              <Route path="/backtests/:id" element={<BacktestPage />} />
              <Route path="/backtests/compare" element={<BacktestPage />} />
            </Routes>
          </App>
        </Layout>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
)
