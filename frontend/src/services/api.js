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
