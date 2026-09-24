import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { getCurrentUser, login } from '../services/api.js'
import { clearAccessToken, getAccessToken, setAccessToken } from '../services/tokenStorage.js'

const AuthContext = createContext(null)
const UNAVAILABLE = 'We cannot verify your account right now. Your sign-in has been kept. Please try again.'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(() => Boolean(getAccessToken()))
  const [error, setError] = useState('')
  const operation = useRef(null)

  const beginOperation = useCallback(() => {
    operation.current?.abort()
    const controller = new AbortController()
    operation.current = controller
    return controller
  }, [])

  // All authoritative /me failures are handled here; future authenticated
  // requests can use the same status distinction when refreshing the session.
  const handleUserFailure = useCallback((failure) => {
    setUser(null)
    if (failure.status === 401) {
      clearAccessToken()
      setError('')
    } else {
      setError(UNAVAILABLE)
    }
  }, [])

  const refreshCurrentUser = useCallback(async () => {
    const controller = beginOperation()
    const token = getAccessToken()
    setError('')
    setUser(null)
    if (!token) {
      setIsLoading(false)
      return null
    }
    setIsLoading(true)
    try {
      const currentUser = await getCurrentUser(token, controller.signal)
      if (controller.signal.aborted) return null
      setUser(currentUser)
      return currentUser
    } catch (failure) {
      if (!controller.signal.aborted) handleUserFailure(failure)
      return null
    } finally {
      if (!controller.signal.aborted) setIsLoading(false)
    }
  }, [beginOperation, handleUserFailure])

  useEffect(() => {
    refreshCurrentUser()
    return () => operation.current?.abort()
  }, [refreshCurrentUser])

  const authenticate = useCallback(async (credentials, signal) => {
    const controller = beginOperation()
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    let tokenIssued = false
    try {
      const token = await login(credentials, controller.signal)
      if (controller.signal.aborted) return null
      setAccessToken(token)
      tokenIssued = true
      const currentUser = await getCurrentUser(token, controller.signal)
      if (controller.signal.aborted) return null
      setError('')
      setUser(currentUser)
      return currentUser
    } catch (failure) {
      if (controller.signal.aborted) return null
      if (tokenIssued) {
        handleUserFailure(failure)
        if (failure.status === 401) {
          throw new Error('Your sign-in could not be verified. Please sign in again.')
        }
      }
      throw failure
    } finally {
      signal?.removeEventListener('abort', abort)
    }
  }, [beginOperation, handleUserFailure])

  const logout = useCallback(() => {
    operation.current?.abort()
    clearAccessToken()
    setUser(null)
    setError('')
    setIsLoading(false)
  }, [])

  return (
    <AuthContext.Provider value={{
      user, isAuthenticated: Boolean(user), isLoading, error,
      authenticate, logout, refreshCurrentUser,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}
