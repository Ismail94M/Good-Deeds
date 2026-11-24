/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In Docker the API is reachable at http://api:8000; locally at http://localhost:8000.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Dev only: lets a phone reach the dev server through a temporary
    // `cloudflared tunnel` HTTPS link (camera and location need HTTPS).
    allowedHosts: ['.trycloudflare.com'],
    // Docker on Windows doesn't forward file-change events into the container,
    // so the dev server polls when VITE_USE_POLLING is set (docker-compose sets it).
    watch: process.env.VITE_USE_POLLING ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
