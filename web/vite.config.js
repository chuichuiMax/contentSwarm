import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  const forwardPublicOrigin = (proxy) => {
    proxy.on('proxyReq', (proxyReq, req) => {
      const forwardedHost = req.headers['x-forwarded-host'] || req.headers.host
      if (forwardedHost) {
        proxyReq.setHeader('X-Forwarded-Host', String(forwardedHost).split(',')[0].trim())
      }
      const forwardedProto = req.headers['x-forwarded-proto']
      if (forwardedProto) {
        proxyReq.setHeader('X-Forwarded-Proto', String(forwardedProto).split(',')[0].trim())
      }
    })
  }
  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true,
          xfwd: true,
          configure: (proxy) => {
            forwardPublicOrigin(proxy)
            proxy.on('proxyRes', (proxyRes) => {
              const contentType = String(proxyRes.headers['content-type'] || '')
              if (contentType.includes('text/event-stream')) {
                proxyRes.headers['cache-control'] = 'no-cache, no-transform'
                proxyRes.headers['x-accel-buffering'] = 'no'
              }
            })
          }
        },
        '^/share': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true,
          xfwd: true,
          configure: forwardPublicOrigin
        },
        '^/public': {
          target: env.VITE_MINIO_URL || 'http://minio:9000',
          changeOrigin: true
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**'],
      },
      host: '0.0.0.0',
      allowedHosts: ['ai.hi-run.net'],
    }
  }
})
