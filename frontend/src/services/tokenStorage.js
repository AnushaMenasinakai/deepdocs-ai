let locallySignedOut = false
const ACCESS_TOKEN_KEY = 'deepdocs_access_token'

// Temporary persistence for this phase, isolated for later replacement.
export function getAccessToken() {
  if (locallySignedOut) return null
  try { return localStorage.getItem(ACCESS_TOKEN_KEY) } catch { return null }
}

export function setAccessToken(token) {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, token)
    locallySignedOut = false
  } catch {
    throw new Error('Your browser could not save your sign in. Please allow site storage and try again.')
  }
}

export function clearAccessToken() {
  locallySignedOut = true
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    return localStorage.getItem(ACCESS_TOKEN_KEY) === null
  } catch {
    return false
  }
}
