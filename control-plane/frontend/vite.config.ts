import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/billing': 'http://127.0.0.1:8000',
      '/services': 'http://127.0.0.1:8000',
      '^/node/': 'http://127.0.0.1:8000',
      '/tenants': 'http://127.0.0.1:8000',
      '/users': 'http://127.0.0.1:8000',
      '/allocations': 'http://127.0.0.1:8000',
      '/me': 'http://127.0.0.1:8000',
      '/feeds': 'http://127.0.0.1:8000',
      '/blacklist': 'http://127.0.0.1:8000',
      '/alerts': 'http://127.0.0.1:8000',
      '/jobs': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
