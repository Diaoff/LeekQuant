import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import StatusPage from './pages/StatusPage'
import MarketPage from './pages/MarketPage'
import WatchlistPage from './pages/WatchlistPage'
import StrategyPage from './pages/StrategyPage'
import BacktestPage from './pages/BacktestPage'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App>
        <Routes>
          <Route path="/" element={<StatusPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/strategy" element={<StrategyPage />} />
          <Route path="/backtests" element={<BacktestPage />} />
          <Route path="/backtests/:id" element={<BacktestPage />} />
        </Routes>
      </App>
    </BrowserRouter>
  </React.StrictMode>,
)
