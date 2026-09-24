import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import Icon from './Icon.jsx'

// Explicit allowlist prevents external, protocol-relative, or unknown redirects.
export function intendedDestination(value) {
  return ['/', '/knowledge-bases', '/documents', '/ask'].includes(value) ? value : '/'
}

function AuthStatus() {
  const { isLoading, refreshCurrentUser, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  function signInAgain() {
    logout()
    navigate('/login', {
      replace: true,
      state: { from: intendedDestination(location.state?.from || location.pathname) },
    })
  }
  return (
    <main className="auth-status-screen">
      <section className="panel auth-status-card" aria-busy={isLoading}>
        <span className="auth-card-icon"><Icon name="document" size={25} /></span>
        <p className="eyebrow">DEEPDOCS AI</p>
        <h1>{isLoading ? 'Checking your account' : 'Unable to verify your account'}</h1>
        <p role={isLoading ? 'status' : 'alert'}>
          {isLoading
            ? 'One moment while we securely check your sign-in.'
            : 'The authentication service is temporarily unavailable. Your sign-in has been kept.'}
        </p>
        {!isLoading && <div className="auth-status-actions">
          <button className="auth-submit" type="button" onClick={refreshCurrentUser}>Try again</button>
          <button className="session-button" type="button" onClick={signInAgain}>Sign in again</button>
        </div>}
      </section>
    </main>
  )
}

export function ProtectedRoute() {
  const { isAuthenticated, isLoading, error } = useAuth()
  const location = useLocation()
  if (isLoading || error) return <AuthStatus />
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: intendedDestination(location.pathname) }} />
  }
  return <Outlet />
}

export function PublicOnlyRoute() {
  const { isAuthenticated, isLoading, error } = useAuth()
  const location = useLocation()
  if (isLoading || error) return <AuthStatus />
  // Login carries an allowlisted destination; ordinary visits go to Dashboard.
  if (isAuthenticated) return <Navigate to={intendedDestination(location.state?.from)} replace />
  return <Outlet />
}
