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
