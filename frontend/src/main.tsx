import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { App } from './App.tsx'

const rootEl = document.getElementById('root')!

async function enableMocking() {
  // Only use MSW in dev when explicitly enabled (default on for mock-first dev).
  if (import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS !== 'false') {
    try {
      const { worker } = await import('./mocks/browser')
      // findWorkerScript / start may throw in some environments; never block the app.
      await worker.start({ onUnhandledRequest: 'bypass', quiet: true })
      console.info('[DeepTrace] MSW mock server active')
    } catch (err) {
      console.warn('[DeepTrace] MSW failed to start, running without mocks:', err)
    }
  }
}

// Render immediately — never gate the app on MSW.
createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Start mocks in the background (best-effort).
void enableMocking()
