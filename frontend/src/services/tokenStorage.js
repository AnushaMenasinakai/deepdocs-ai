const ACCESS_TOKEN_KEY = 'deepdocs_access_token'

// Temporary persistence for this phase, isolated for later replacement.
export function getAccessToken() {
  try { return localStorage.getItem(ACCESS_TOKEN_KEY) } catch { return null }
}

export function setAccessToken(token) {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, token)
  } catch {
    throw new Error('Your browser could not save your sign in. Please allow site storage and try again.')
  }
}

export function clearAccessToken() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
}
