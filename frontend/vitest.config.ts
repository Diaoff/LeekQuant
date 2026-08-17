import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Unit-test config for pure logic (formatters, MyTT completions, helpers).
// E2E smoke tests live separately under frontend/tests (Playwright).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
  },
})
