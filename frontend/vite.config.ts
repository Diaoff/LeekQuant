import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Manual vendor chunking keeps the initial bundle small: route-level code
// splitting (React.lazy below) already splits pages; here we further isolate
// the heavy, rarely-changing vendors so they cache independently.
function manualChunks(id: string): string | undefined {
  if (id.indexOf('node_modules') === -1) return undefined
  if (id.indexOf('monaco-editor') !== -1) return 'vendor-monaco'
  if (id.indexOf('lightweight-charts') !== -1 || id.indexOf('tradingview') !== -1) return 'vendor-charts'
  if (
    id.indexOf('react-router') !== -1 ||
    id.indexOf('react-dom') !== -1 ||
    id.indexOf('/react/') !== -1 ||
    id.indexOf('scheduler') !== -1
  ) {
    return 'vendor-react'
  }
  return 'vendor'
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
  optimizeDeps: {
    include: ['monaco-editor'],
  },
})
