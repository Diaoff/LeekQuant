import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: 'var(--color-ink)',
        surface: 'var(--color-surface)',
        panel: 'var(--color-panel)',
        line: 'var(--color-line)',
        accent: 'var(--color-accent)',
        mint: 'var(--color-mint)',
        warn: 'var(--color-warn)',
        bg: 'var(--color-bg)',
        muted: 'var(--color-muted)',
        tableHead: 'var(--color-table-head)',
        rowHover: 'var(--color-row-hover)',
        rowAlt: 'var(--color-row-alt)',
      },
      fontFamily: {
        sans: [
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
} satisfies Config
