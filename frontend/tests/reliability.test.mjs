import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import axios from '../node_modules/axios/index.js'

const source = readFileSync(new URL('../src/services/api.js', import.meta.url), 'utf8')
  .replace("import { getAccessToken } from './tokenStorage.js'", "const getAccessToken = () => 'test-token'")
  .replace("import axios from 'axios'", "const axios = globalThis.__testAxios")
  .replace('import.meta.env.VITE_API_URL', "'http://example.invalid'")
globalThis.__testAxios = axios
let calls = []
let respond
axios.defaults.adapter = async config => { calls.push(config); return respond(config) }
const api = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const id = '507f1f77bcf86cd799439011'
const file = new File(['pdf'], 'test.pdf', { type: 'application/pdf' })
const actions = [
  () => api.uploadDocument(id, file), () => api.processDocument(id),
  () => api.deleteDocument(id), () => api.bulkDeleteDocuments(id, [id]),
  () => api.bulkReindexDocuments(id, [id]),
]
function reject(status, detail, retry) {
  respond = async () => { throw { response: { status, data: { detail }, headers: retry === undefined ? {} : { 'retry-after': retry } } } }
}
test('exact admission contract rejects each mutation once without uncertainty', async () => {
  reject(503, 'Service is busy. Please try again later.', '1')
  for (const action of actions) {
    calls = []
    await assert.rejects(action, e => e.notAdmitted === true && /not admitted/.test(e.message) && !/may still/.test(e.message))
    assert.equal(calls.length, 1)
  }
})
test('nonmatching or inaccessible admission headers preserve uncertainty', async () => {
  for (const [status, detail, retry] of [
    [503, 'Other failure', '1'], [503, 'Service is busy. Please try again later.', undefined],
    [503, 'Service is busy. Please try again later.', '2'], [500, 'Service is busy. Please try again later.', '1'],
  ]) {
    reject(status, detail, retry)
    await assert.rejects(() => api.processDocument(id), e => !e.notAdmitted && /may still be running/.test(e.message))
  }
})
test('timeouts do not repeat mutations and upload has a bounded separate timeout', async () => {
  respond = async () => { throw new axios.AxiosError('timeout', 'ECONNABORTED') }
  for (const action of actions) {
    calls = []
    await assert.rejects(action, e => !e.notAdmitted && /confirm/.test(e.message))
    assert.equal(calls.length, 1)
  }
  calls = []
  await assert.rejects(() => api.uploadDocument(id, file), /refresh the document list/)
  assert.equal(calls[0].timeout, 120000)
  await assert.rejects(() => api.deleteDocument(id))
  assert.equal(calls[1].timeout, 15000)
})
test('single deletion conflict gives inspection guidance', async () => {
  reject(409, 'busy')
  await assert.rejects(() => api.deleteDocument(id), /Inspect or refresh/)
})
test('history accepts code-point boundary and rejects above it', async () => {
  for (const [count, valid] of [[999, true], [1000, true], [1001, false]]) {
    respond = async () => ({ status: 200, data: [{
      id, question: '\u{1F600}'.repeat(count), created_at: '2026-01-01T00:00:00Z',
      status: 'insufficient_context', answer: 'No context.', retrieved_chunk_count: 0,
      sources: [], citation_version: 1, claims: [],
    }] })
    if (valid) assert.equal((await api.listAskHistory(id))[0].question.length, count * 2)
    else await assert.rejects(() => api.listAskHistory(id))
  }
})
test('failed token removal suppresses stored token until a successful sign-in', async () => {
  const storage = await import('../src/services/tokenStorage.js')
  let token = 'old'
  globalThis.localStorage = { getItem: () => token, setItem: (_, value) => { token = value }, removeItem: () => { throw Error('denied') } }
  assert.equal(storage.clearAccessToken(), false)
  assert.equal(token, 'old')
  assert.equal(storage.getAccessToken(), null)
  storage.setAccessToken('new')
  assert.equal(storage.getAccessToken(), 'new')
  globalThis.localStorage.removeItem = () => { token = null }
  assert.equal(storage.clearAccessToken(), true)
  assert.equal(storage.getAccessToken(), null)
})

test('successful authentication and document requests retain their contracts', async () => {
  respond = async config => {
    if (config.url.endsWith('/login')) return { status: 200, data: { token_type: 'bearer', access_token: 'abc.def.ghi' } }
    if (config.url.endsWith('/register')) return { status: 201, data: {} }
    if (config.url.endsWith('/process')) return { status: 200, data: { id, status: 'processed' } }
    return { status: config.method === 'delete' ? 204 : 201, data: { id } }
  }
  assert.equal(await api.login({ email: 'USER@example.com', password: 'password' }), 'abc.def.ghi')
  await api.register({ name: 'User', email: 'USER@example.com', password: 'password' })
  assert.equal((await api.uploadDocument(id, file)).id, id)
  assert.equal((await api.processDocument(id)).status, 'processed')
  await api.deleteDocument(id)
})
test('cancellation stays cancellation and never triggers another mutation', async () => {
  const cancelled = new axios.CanceledError()
  respond = async () => { throw cancelled }
  for (const action of actions) {
    calls = []
    await assert.rejects(action, e => e === cancelled)
    assert.equal(calls.length, 1)
  }
})
