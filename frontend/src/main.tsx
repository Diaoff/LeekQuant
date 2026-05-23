import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Layout from './components/Layout'
import { ThemeProvider } from './lib/theme'
import BacktestPage from './pages/BacktestPage'
import DashboardPage from './pages/DashboardPage'
import DataSourcePage from './pages/DataSourcePage'
import FactorPage from './pages/FactorPage'
import MarketPage from './pages/MarketPage'
import SignalsPage from './pages/SignalsPage'
import SimulationPage from './pages/SimulationPage'
import StatusPage from './pages/StatusPage'
import StrategyPage from './pages/StrategyPage'
import WatchlistPage from './pages/WatchlistPage'
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
              <Route path="/sources" element={<DataSourcePage />} />
              <Route path="/market" element={<MarketPage />} />
              <Route path="/watchlist" element={<WatchlistPage />} />
              <Route path="/strategy" element={<StrategyPage />} />
              <Route path="/backtests" element={<BacktestPage />} />
              <Route path="/backtests/:id" element={<BacktestPage />} />
              <Route path="/backtests/compare" element={<BacktestPage />} />
              <Route path="/signals" element={<SignalsPage />} />
              <Route path="/simulation" element={<SimulationPage />} />
              <Route path="/factor" element={<FactorPage />} />
            </Routes>
          </App>
        </Layout>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
)
