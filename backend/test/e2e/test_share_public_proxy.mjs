import assert from 'node:assert/strict'
import { createServer as createHttpServer, request as httpRequest } from 'node:http'
import { join } from 'node:path'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'

const webRoot = process.env.PROXY_TEST_WEB_ROOT || '/app'
const { createServer, loadConfigFromFile } = await import(
  pathToFileURL(join(webRoot, 'node_modules/vite/dist/node/index.js')).href
)

test('share and API proxies preserve the public origin across TLS termination', async (t) => {
  const upstream = createHttpServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ path: request.url, headers: request.headers }))
  })
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve) => upstream.close(resolve)))

  const loaded = await loadConfigFromFile(
    { command: 'serve', mode: 'development' },
    process.env.PROXY_TEST_CONFIG_FILE || join(webRoot, 'vite.config.js')
  )
  assert.ok(loaded, 'Vite project configuration must load')
  const target = `http://127.0.0.1:${upstream.address().port}`
  const proxy = await createServer({
    configFile: false,
    root: webRoot,
    logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] },
    server: {
      host: '127.0.0.1',
      port: 0,
      allowedHosts: loaded.config.server.allowedHosts,
      proxy: {
        '^/share': { ...loaded.config.server.proxy['^/share'], target },
        '^/api': { ...loaded.config.server.proxy['^/api'], target }
      }
    }
  })
  t.after(() => proxy.close())
  await proxy.listen()
  const origin = `http://127.0.0.1:${proxy.httpServer.address().port}`

  for (const path of ['/share/case/test-token', '/api/mp/share/cases']) {
    for (const forwardedHost of [undefined, 'public.example.test']) {
      for (const prefix of ['', '/boyun']) {
        await t.test(`${path} with ${forwardedHost || 'no existing forwarded host'} and prefix ${prefix || '/'}`, async () => {
          const headers = { Host: 'ai.hi-run.net', 'X-Forwarded-Proto': 'https' }
          if (forwardedHost) headers['X-Forwarded-Host'] = forwardedHost
          if (prefix) headers['X-Forwarded-Prefix'] = prefix
          const response = await new Promise((resolve, reject) => {
            const call = httpRequest(`${origin}${path}`, { headers }, resolve)
            call.on('error', reject)
            call.end()
          })
          assert.equal(response.statusCode, 200)
          let body = ''
          for await (const chunk of response) body += chunk
          const received = JSON.parse(body)
          assert.equal(received.path, path)
          assert.equal(received.headers['x-forwarded-host'], forwardedHost || 'ai.hi-run.net')
          assert.equal(received.headers['x-forwarded-proto'].split(',')[0], 'https')
          assert.equal(received.headers['x-forwarded-prefix'] || '', prefix)
        })
      }
    }
  }
})
