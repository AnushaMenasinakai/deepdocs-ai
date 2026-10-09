import { getAccessToken } from './tokenStorage.js'
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL?.trim() || 'http://127.0.0.1:8000',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

function safeError(error, action) {
  if (axios.isCancel(error)) return error
  const status = error.response?.status
  let message = 'Something went wrong. Please try again.'
  if (!error.response) message = 'Unable to connect. Please check your connection and try again.'
  else if (status === 409 && action === 'register') message = 'An account with this email already exists.'
  else if (status === 401 && action === 'login') message = 'Invalid email or password.'
  else if (status === 422) message = 'Please check your details and try again.'
  else if (status === 503) message = 'Authentication service is temporarily unavailable.'
  // Never pass Axios errors (which contain the request body) or server details to the UI.
  const safe = new Error(message)
  safe.status = status
  return safe
}

export async function register({ name, email, password }, signal) {
  let response
  try {
    response = await api.post('/api/auth/register', { name: name.trim(), email: email.trim().toLowerCase(), password }, { signal })
  } catch (error) {
    throw safeError(error, 'register')
  }
  if (response.status !== 201) throw new Error('We could not confirm registration. Please try again.')
}

export async function login({ email, password }, signal) {
  let response
  try {
    response = await api.post('/api/auth/login', { email: email.trim().toLowerCase(), password }, { signal })
  } catch (error) {
    throw safeError(error, 'login')
  }
  const data = response.data
  if (response.status !== 200 || data?.token_type !== 'bearer' ||
      typeof data.access_token !== 'string' || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(data.access_token)) {
    throw new Error('We could not complete sign in. Please try again.')
  }
  return data.access_token
}

// Authorization is constructed here, never in page components or URLs.
export async function getCurrentUser(token, signal) {
  let response
  try {
    response = await api.get('/api/auth/me', {
      headers: { Authorization: 'Bearer ' + token },
      signal,
    })
  } catch (error) {
    throw safeError(error, 'currentUser')
  }
  const user = response.data
  if (response.status !== 200 || !user || typeof user.id !== 'string' ||
      typeof user.name !== 'string' || typeof user.email !== 'string' ||
      typeof user.created_at !== 'string') {
    throw new Error('We could not confirm your account. Please try again.')
  }
  // Keep only the public fields, even if an unexpected server field is added.
  return { id: user.id, name: user.name, email: user.email, created_at: user.created_at }
}


// Shared authenticated request path; pages never construct Bearer headers.
async function knowledgeBaseRequest(method, id, data, signal) {
  try {
    const response = await api.request({
      method,
      url: '/api/knowledge-bases' + (id ? '/' + encodeURIComponent(id) : ''),
      data,
      signal,
      headers: { Authorization: 'Bearer ' + getAccessToken() },
    })
    return response.data
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    let message = 'Knowledge Base service is temporarily unavailable. Please try again.'
    if (!error.response) message = 'Unable to connect. Please check your connection and try again.'
    if (status === 401) message = 'Your session has expired. Please sign in again.'
    if (status === 404) message = 'This Knowledge Base is no longer available. The list has been refreshed.'
    if (status === 409) message = 'This Knowledge Base contains documents or an upload is in progress. Delete its documents before deleting the Knowledge Base.'
    if (status === 422) message = 'Check that the name contains 1–100 characters and the description is no longer than 500 characters.'
    const safe = new Error(message)
    safe.status = status
    throw safe
  }
}

export const listKnowledgeBases = (signal) => knowledgeBaseRequest('get', null, undefined, signal)
// Explicit paginated management APIs preserve legacy array callers/Ask selectors.
async function browseRequest(url, params, signal, documents = false) {
  let response
  try {
    response = await api.get(url, { params, signal, headers: { Authorization: 'Bearer ' + getAccessToken() } })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const safe = new Error(error.response?.status === 404 ? 'This Knowledge Base is no longer available.' : 'Unable to load results. Please try again.')
    safe.status = error.response?.status
    throw safe
  }
  const data = response.data
  const invalid = () => { throw new Error('We could not read the results. Please try again.') }
  if (response.status !== 200 || !data || !Array.isArray(data.items) ||
    !Number.isSafeInteger(data.page) || data.page < 1 || !Number.isSafeInteger(data.limit) || data.limit < 1 || data.limit > 100 ||
    !Number.isSafeInteger(data.total) || data.total < 0 || data.total_pages !== Math.ceil(data.total / data.limit) ||
    data.items.length > data.limit || data.items.length > data.total) invalid()
  const items = data.items.map(item => {
    if (!item || typeof item.id !== 'string' || !item.id ||
      !['created_at', 'updated_at'].every(key => typeof item[key] === 'string' && Number.isFinite(Date.parse(item[key])))) invalid()
    if (documents) {
      if (typeof item.filename !== 'string' || !item.filename || typeof item.indexed !== 'boolean' || typeof item.failed !== 'boolean' ||
        !Number.isSafeInteger(item.file_size) || item.file_size < 0 || !['uploaded', 'processing', 'processed', 'failed'].includes(item.status)) invalid()
      return Object.fromEntries(['id', 'filename', 'status', 'indexed', 'failed', 'file_size', 'created_at', 'updated_at'].map(key => [key, item[key]]))
    }
    if (typeof item.name !== 'string' || !item.name || (item.description != null && typeof item.description !== 'string')) invalid()
    return { id: item.id, name: item.name, description: item.description, created_at: item.created_at, updated_at: item.updated_at }
  })
  return { items, page: data.page, limit: data.limit, total: data.total, total_pages: data.total_pages }
}
export const browseKnowledgeBases = (params, signal) => browseRequest('/api/knowledge-bases/browse', params, signal)
export const browseDocuments = (id, params, signal) => browseRequest('/api/knowledge-bases/' + encodeURIComponent(id) + '/documents/browse', params, signal, true)
export const createKnowledgeBase = (data, signal) => knowledgeBaseRequest('post', null, data, signal)
export const updateKnowledgeBase = (id, data, signal) => knowledgeBaseRequest('patch', id, data, signal)
export const deleteKnowledgeBase = (id, signal) => knowledgeBaseRequest('delete', id, undefined, signal)

// Documents use the same Axios instance and centralized token helper.
async function documentRequest(method, url, data, signal) {
  try {
    const response = await api.request({
      method, url, data, signal,
      headers: {
        Authorization: 'Bearer ' + getAccessToken(),
        // Clear the instance JSON default; the browser supplies the boundary.
        ...(data instanceof FormData ? { 'Content-Type': undefined } : {}),
      },
    })
    const expected = method === 'post' ? 201 : method === 'delete' ? 204 : 200
    if (response.status !== expected) throw new Error('Unexpected response')
    return response.data
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    let message = 'Document service is temporarily unavailable. Please try again.'
    if (!error.response) message = 'Unable to confirm the request. Check your connection and refresh the document list before trying again.'
    if (status === 401) message = 'Your session has expired. Please sign in again.'
    if (status === 404) message = 'This document or Knowledge Base is no longer available.'
    if (status === 413) message = 'The PDF exceeds the upload limit. Choose a PDF up to 10 MiB; the server may have a lower limit.'
    if (status === 415 || status === 422) message = 'Choose a valid, non-empty PDF file and try again.'
    const safe = new Error(message)
    safe.status = status
    throw safe
  }
}

export const listDocuments = (knowledgeBaseId, signal) =>
  documentRequest('get', '/api/knowledge-bases/' + encodeURIComponent(knowledgeBaseId) + '/documents', undefined, signal)

export function uploadDocument(knowledgeBaseId, file, signal) {
  const data = new FormData()
  // Some browsers report no MIME type. The backend still checks the signature.
  data.append('file', file.type ? file : new File([file], file.name, { type: 'application/pdf' }))
  return documentRequest('post', '/api/knowledge-bases/' + encodeURIComponent(knowledgeBaseId) + '/documents', data, signal)
}

export const deleteDocument = (documentId, signal) =>
  documentRequest('delete', '/api/documents/' + encodeURIComponent(documentId), undefined, signal)

export async function inspectDocumentOperation(documentId, signal) {
  if (typeof documentId !== 'string' || !/^[a-f0-9]{24}$/i.test(documentId)) {
    throw new Error('This inspection request is invalid. Refresh the document list.')
  }
  let response
  try {
    response = await api.get('/api/documents/' + encodeURIComponent(documentId) + '/operation-status', {
      signal, headers: { Authorization: 'Bearer ' + getAccessToken() },
    })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    const safe = new Error(status === 401 ? 'Your session has expired. Please sign in again.'
      : status === 404 ? 'This document is no longer available.'
      : status === 422 ? 'This inspection request is invalid. Refresh the document list.'
      : status === 503 ? 'Status inspection is temporarily unavailable. Try inspecting again later.'
      : 'Unable to inspect this document. Check your connection and try inspecting again.')
    safe.status = status
    throw safe
  }
  const data = response.data
  const enums = {
    operation_state: ['idle', 'claimed_unknown'],
    document_state: ['uploaded', 'processing', 'processed', 'indexed', 'failed', 'unknown'],
    attention: ['none', 'needs_processing', 'needs_reindex', 'outcome_uncertain', 'requires_review'],
    recommended_action: ['none', 'process', 'reindex', 'refresh', 'contact_support'],
  }
  if (response.status !== 200 || !data || data.document_id !== documentId.toLowerCase() ||
      Object.entries(enums).some(([key, values]) => !values.includes(data[key])) ||
      (data.operation_state === 'claimed_unknown' && (data.attention !== 'outcome_uncertain' ||
        data.recommended_action !== 'refresh' || data.document_state === 'indexed'))) {
    throw new Error('We could not read the status inspection. Try inspecting again.')
  }
  return Object.fromEntries(['document_id', ...Object.keys(enums)].map(key => [key, data[key]]))
}

async function bulkDocumentRequest(knowledgeBaseId, documentIds, operation, signal) {
  const validId = value => typeof value === 'string' && /^[a-f0-9]{24}$/i.test(value)
  const limit = operation === 'delete' ? 100 : 5
  if (!validId(knowledgeBaseId) || !Array.isArray(documentIds) || documentIds.length < 1 ||
      documentIds.length > limit || !documentIds.every(validId)) {
    throw new Error('Select valid documents from the current page within the action limit.')
  }
  const ids = documentIds.map(id => id.toLowerCase())
  if (new Set(ids).size !== ids.length) throw new Error('Select each document only once.')
  let response
  try {
    response = await api.post('/api/knowledge-bases/' + encodeURIComponent(knowledgeBaseId) + '/documents/bulk-' + operation,
      { document_ids: ids }, { signal, timeout: 300000, headers: { Authorization: 'Bearer ' + getAccessToken() } })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    const safe = new Error(status === 401 ? 'Your session has expired. Please sign in again.'
      : status === 404 ? 'This Knowledge Base is no longer available. Refresh your collections.'
      : status === 422 ? 'The selection could not be submitted. Refresh the list and select documents again.'
      : 'We could not confirm the bulk operation. Some documents may have changed. Refresh the list before deciding whether to try again.')
    safe.status = status
    throw safe
  }
  const data = response.data
  const invalid = () => { throw new Error('We could not confirm the bulk results. Some documents may have changed. Refresh the list before deciding whether to try again.') }
  if (response.status !== 200 || !data || data.operation !== operation || data.requested !== ids.length ||
      !Array.isArray(data.results) || data.results.length !== ids.length) invalid()
  const codes = ['not_found', 'busy', 'service_unavailable', ...(operation === 'reindex' ? ['requires_processing'] : [])]
  let stopped = false
  const results = data.results.map((item, index) => {
    if (!item || item.document_id !== ids[index] || !['succeeded', 'failed', 'not_attempted'].includes(item.outcome)) invalid()
    if (item.outcome === 'failed' ? !codes.includes(item.code) : item.code != null) invalid()
    if ((stopped && item.outcome !== 'not_attempted') || (!stopped && item.outcome === 'not_attempted')) invalid()
    if (item.code === 'service_unavailable') stopped = true
    return { document_id: item.document_id, outcome: item.outcome, code: item.code }
  })
  const counts = Object.fromEntries(['succeeded', 'failed', 'not_attempted'].map(key => [key, results.filter(item => item.outcome === key).length]))
  if (Object.keys(counts).some(key => data[key] !== counts[key])) invalid()
  return { operation, requested: ids.length, ...counts, results }
}

export const bulkDeleteDocuments = (id, ids, signal) => bulkDocumentRequest(id, ids, 'delete', signal)
export const bulkReindexDocuments = (id, ids, signal) => bulkDocumentRequest(id, ids, 'reindex', signal)


export async function askKnowledgeBase(knowledgeBaseId, question, signal) {
  let response
  try {
    response = await api.post('/api/knowledge-bases/' + encodeURIComponent(knowledgeBaseId) + '/ask',
      { question: question.trim() }, {
        signal, timeout: 60000,
        headers: { Authorization: 'Bearer ' + getAccessToken() },
      })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    let message = 'Answer service is temporarily unavailable. Please try again.'
    if (!error.response) message = 'Unable to connect to the answer service. Please try again.'
    if (status === 401) message = 'Your session has expired. Please sign in again.'
    if (status === 404) message = 'This Knowledge Base is no longer available. Refresh the Knowledge Base list.'
    if (status === 422) message = 'Enter a question containing 1–1,000 characters.'
    const safe = new Error(message)
    safe.status = status
    throw safe
  }
  if (response.status !== 200) throw new Error('We could not read the answer. Please try again.')
  return publicAskResult(response.data)
}

function publicAskResult(data) {
  const invalid = () => { throw new Error('We could not read the answer. Please try again.') }
  const length = value => Array.from(value).length
  const validId = value => Number.isInteger(value) && value >= 1 && value <= 5
  const version = data?.citation_version === undefined ? 0 : data.citation_version
  if (!data || ![0, 1].includes(version) || !['answered', 'insufficient_context'].includes(data.status) ||
      typeof data.answer !== 'string' || !data.answer.trim() || length(data.answer) > 4000 ||
      !Number.isInteger(data.retrieved_chunk_count) || data.retrieved_chunk_count < 0 || data.retrieved_chunk_count > 5 ||
      !Array.isArray(data.sources) || data.sources.length > data.retrieved_chunk_count ||
      (data.status === 'insufficient_context' && data.sources.length !== 0) ||
      (data.status === 'answered' && data.sources.length === 0)) invalid()
  const seen = new Set(), identifiers = new Set(), sources = []
  for (const source of data.sources) {
    if (!source || typeof source.document_id !== 'string' || !/^[a-f0-9]{24}$/i.test(source.document_id) ||
        typeof source.source_filename !== 'string' || !source.source_filename.trim() ||
        /[\\/:\x00-\x1f]/.test(source.source_filename) ||
        !Number.isInteger(source.page_start) || source.page_start < 1 ||
        !Number.isInteger(source.page_end) || source.page_end < source.page_start) invalid()
    const key = JSON.stringify([source.document_id, source.page_start, source.page_end])
    if (version === 1) {
      if (!validId(source.citation_id) || identifiers.has(source.citation_id) || seen.has(key)) invalid()
      identifiers.add(source.citation_id)
    } else if (source.citation_id != null) invalid()
    if (seen.has(key)) continue // Preserve legacy page deduplication only.
    seen.add(key)
    const { document_id, source_filename, page_start, page_end } = source
    sources.push({ document_id, source_filename, page_start, page_end,
      ...(version === 1 ? { citation_id: source.citation_id } : {}) })
  }
  let claims = []
  if (version === 1) {
    if (!Array.isArray(data.claims) || data.claims.length > 12 ||
        (data.status === 'answered' ? data.claims.length === 0 : data.claims.length !== 0)) invalid()
    const used = new Set()
    claims = data.claims.map(claim => {
      if (!claim || typeof claim.text !== 'string' || !claim.text.trim() || claim.text !== claim.text.trim() ||
          length(claim.text) > 4000 || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(claim.text) ||
          !Array.isArray(claim.citation_ids) || !claim.citation_ids.length || claim.citation_ids.length > 5 ||
          claim.citation_ids.some(id => !validId(id) || !identifiers.has(id))) invalid()
      const citation_ids = [...new Set(claim.citation_ids)]
      citation_ids.forEach(id => used.add(id))
      return { text: claim.text, citation_ids }
    })
    if (used.size !== identifiers.size ||
        (data.status === 'answered' && data.answer !== claims.map(claim => claim.text).join('\n\n'))) invalid()
  } else if (data.claims !== undefined && (!Array.isArray(data.claims) || data.claims.length)) invalid()
  return { status: data.status, answer: data.answer, retrieved_chunk_count: data.retrieved_chunk_count,
    citation_version: version, claims, sources }
}


async function historyRequest(method, knowledgeBaseId, entryId, signal) {
  let response
  try {
    response = await api.request({ method,
      url: '/api/knowledge-bases/' + encodeURIComponent(knowledgeBaseId) + '/ask-history' +
        (entryId ? '/' + encodeURIComponent(entryId) : ''),
      signal, headers: { Authorization: 'Bearer ' + getAccessToken() },
    })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const status = error.response?.status
    let message = 'Ask history is temporarily unavailable. Please try again.'
    if (!error.response) message = 'Unable to connect to Ask history. Please try again.'
    if (status === 401) message = 'Your session has expired. Please sign in again.'
    if (status === 404) message = 'This history entry or Knowledge Base is no longer available.'
    const safe = new Error(message)
    safe.status = status
    throw safe
  }
  if (method === 'delete') {
    if (response.status !== 204) throw new Error('We could not confirm history deletion. Please refresh history before retrying.')
    return
  }
  try {
    if (response.status !== 200 || !Array.isArray(response.data) || response.data.length > 100) throw new Error()
    return response.data.map(item => {
      if (!item || typeof item.id !== 'string' || !item.id || typeof item.question !== 'string' ||
          !item.question.trim() || item.question.length > 1000 || typeof item.created_at !== 'string' ||
          !Number.isFinite(Date.parse(item.created_at))) throw new Error()
      return { id: item.id, question: item.question, created_at: item.created_at, ...publicAskResult(item) }
    })
  } catch {
    throw new Error('We could not read Ask history. Please try again.')
  }
}

export const listAskHistory = (knowledgeBaseId, signal) => historyRequest('get', knowledgeBaseId, null, signal)
export const deleteAskHistory = (knowledgeBaseId, entryId, signal) => historyRequest('delete', knowledgeBaseId, entryId, signal)


export async function getDashboardSummary(signal) {
  let response
  try {
    response = await api.get('/api/dashboard/summary', { signal, headers: { Authorization: 'Bearer ' + getAccessToken() } })
  } catch (error) {
    if (axios.isCancel(error)) throw error
    const safe = new Error('Workspace overview is temporarily unavailable. Please try again.')
    safe.status = error.response?.status
    throw safe
  }
  const data = response.data
  const counts = ['knowledge_base_count', 'document_count', 'indexed_document_count', 'processing_document_count', 'failed_document_count', 'ask_history_count']
  if (response.status !== 200 || !data || counts.some(key => !Number.isSafeInteger(data[key]) || data[key] < 0) ||
      !Array.isArray(data.recent_knowledge_bases) || data.recent_knowledge_bases.length > 5 ||
      data.recent_knowledge_bases.some(item => !item || typeof item.id !== 'string' || !item.id ||
        typeof item.name !== 'string' || !item.name.trim() || !Number.isSafeInteger(item.document_count) || item.document_count < 0 ||
        typeof item.updated_at !== 'string' || !Number.isFinite(Date.parse(item.updated_at)))) {
    throw new Error('We could not read your workspace overview. Please try again.')
  }
  return { ...Object.fromEntries(counts.map(key => [key, data[key]])), recent_knowledge_bases: data.recent_knowledge_bases.map(({id, name, document_count, updated_at}) => ({id, name, document_count, updated_at})) }
}

export async function getServiceHealth(service, signal) {
  const paths = { api: '/api/health', database: '/api/health/database', vector: '/api/health/qdrant' }
  if (!paths[service]) return 'unavailable'
  try {
    const response = await api.get(paths[service], { signal, timeout: 7000 })
    return response.status === 200 && response.data?.status === 'ok' ? 'operational' : 'unavailable'
  } catch {
    return 'unavailable'
  }
}
