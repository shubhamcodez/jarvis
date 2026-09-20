import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function jarvisTokenPath() {
  const root = path.resolve(__dirname, '..', '.secrets')
  const brand = path.join(root, 'jarvis-api-token')
  const legacy = path.join(root, 'ada-api-token')
  return fs.existsSync(brand) || !fs.existsSync(legacy) ? brand : legacy
}

function ensureJarvisToken() {
  const p = jarvisTokenPath()
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

function injectJarvisToken(proxyReq) {
  const t = ensureJarvisToken()
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
          proxy.on('proxyReq', injectJarvisToken)
        },
      },
      '/ws': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        configure: (proxy) => {
          proxy.on('proxyReq', injectJarvisToken)
          proxy.on('proxyReqWs', injectJarvisToken)
        },
      },
    },
  },
})
