import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { resolveApiUrl } from '../config/api-url.js'

test('development fallback and explicit development URL', () => {
  for (const value of [undefined, '', '  ']) assert.equal(resolveApiUrl(value, false), 'http://127.0.0.1:8000')
  assert.equal(resolveApiUrl('http://dev.example.test:8000/', false), 'http://dev.example.test:8000')
})

test('production normalization and controlled loopback HTTP', () => {
  for (const [input, expected] of [
    [' HTTPS://API.Example.com:443/ ', 'https://api.example.com'],
    ['https://api.example.com:8443', 'https://api.example.com:8443'],
    ['http://localhost:8000', 'http://localhost:8000'],
    ['http://127.0.0.1:8000/', 'http://127.0.0.1:8000'],
    ['http://[::1]:8000', 'http://[::1]:8000'],
  ]) assert.equal(resolveApiUrl(input, true), expected)
})

for (const [name, value] of Object.entries({
  missing: undefined, blank: '', whitespace: '  ', bare: 'api.example.com',
  protocol: 'ftp://api.example.com', credentials: 'https://user:secret@api.example.com',
  query: 'https://api.example.com?q=secret', emptyQuery: 'https://api.example.com?',
  fragment: 'https://api.example.com#secret', path: 'https://api.example.com/api',
  portZero: 'https://api.example.com:0', portLarge: 'https://api.example.com:65536',
  portText: 'https://api.example.com:abc', portEmpty: 'https://api.example.com:',
  wildcard: 'https://*.example.com', badHost: 'https://bad_host',
  badLabel: 'https://-bad.example.com', emptyLabel: 'https://api..example.com',
  newline: 'https://api.exa\nmple.com', backslash: 'https://api.example.com\\foo',
  remoteHttp: 'http://api.example.com', shorthandIp: 'http://127.1',
  invalidIp: 'https://999.999.999.999', trailingDot: 'https://api.example.com.',
})) test(`rejects ${name} safely`, () => {
  assert.throws(() => resolveApiUrl(value, true), error => {
    assert.match(error.message, /^Invalid VITE_API_URL:/)
    assert.ok(!error.message.includes('secret'))
    return true
  })
})

test('real Vite builds reject missing, blank and invalid configuration; valid URL is bundled', () => {
  const frontend = fileURLToPath(new URL('../', import.meta.url))
  const temporary = mkdtempSync(join(tmpdir(), 'deepdocs-api-config-'))
  const env = { ...process.env }
  delete env.VITE_API_URL
  const run = (value, mode = 'production') => spawnSync(process.execPath, [
    join(frontend, 'node_modules/vite/bin/vite.js'), 'build', frontend,
    '--config', join(frontend, 'vite.config.js'), '--mode', mode,
    '--outDir', join(temporary, 'dist'),
  ], { cwd: temporary, env: value === undefined ? env : { ...env, VITE_API_URL: value }, encoding: 'utf8', timeout: 90000 })
  try {
    // Isolated working directory prevents developer dotenv files supplying defaults.
    for (const value of [undefined, '', 'https://user:secret@example.com']) {
      const result = run(value)
      assert.notEqual(result.status, 0)
      assert.match(result.stdout + result.stderr, /Invalid VITE_API_URL:/)
      assert.ok(!(result.stdout + result.stderr).includes('user:secret'))
    }
    const customMode = run(undefined, 'development')
    assert.notEqual(customMode.status, 0)
    assert.match(customMode.stdout + customMode.stderr, /Invalid VITE_API_URL:/)
    const success = run(' https://api.example.test:443/ ')
    assert.equal(success.status, 0, success.stdout + success.stderr)
    const assets = join(temporary, 'dist/assets')
    const js = readdirSync(assets).filter(f => f.endsWith('.js')).map(f => readFileSync(join(assets, f), 'utf8')).join('\n')
    assert.ok(js.includes('https://api.example.test'))
    assert.ok(!js.includes('http://127.0.0.1:8000'))
    assert.ok(readFileSync(resolve(frontend, 'src/services/api.js'), 'utf8').includes('baseURL: import.meta.env.VITE_API_URL'))
  } finally {
    rmSync(temporary, { recursive: true, force: true })
  }
})
