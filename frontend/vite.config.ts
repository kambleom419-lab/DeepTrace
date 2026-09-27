import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': import.meta.dirname + '/src',
      },
    },
    server: {
      port: 5173,
      proxy: {
        // Forward /api to the FastAPI backend in development, so the frontend can use the
        // same relative paths it uses in production, where the backend serves the built
        // app on its own origin. Without this, `npm run dev:api` 404s on every request.
        //
        // Point VITE_PROXY_TARGET at a deployed URL to develop against Azure instead.
        '/api': {
          target: env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
