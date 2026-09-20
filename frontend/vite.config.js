import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function adaTokenPath() {
  return path.resolve(__dirname, '..', '.secrets', 'jarvis-api-token')
}

function ensureAdaToken() {
  const p = adaTokenPath()
  try {
    if (fs.existsSync(p)) {
      const t = fs.readFileSync(p, 'utf8').trim()
      if (t) return t
    }
    fs.mkdirSync(path.dirname(p), { recursive: true })
    const t = crypto.randomBytes(32).toString('hex')
    fs.writeFileSync(p, t, { mode: 0o600 })
    return t
  } catch {
    return ''
  }
}

function injectAdaToken(proxyReq) {
  const t = ensureAdaToken()
  if (t) proxyReq.setHeader('X-Jarvis-Token', t)
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
        configure: (proxy) => {
          proxy.on('proxyReq', injectAdaToken)
        },
      },
      '/ws': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        configure: (proxy) => {
          proxy.on('proxyReq', injectAdaToken)
          proxy.on('proxyReqWs', injectAdaToken)
        },
      },
    },
  },
})
