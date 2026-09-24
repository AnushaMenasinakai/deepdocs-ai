import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import Sidebar from './Sidebar.jsx'

export default function AppLayout() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const title = { '/': 'Dashboard', '/knowledge-bases': 'Knowledge Bases', '/documents': 'Documents', '/ask': 'Ask DeepDocs' }[pathname.replace(/\/$/, '') || '/'] || 'Page not found'

  function signOut() {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-layout">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <Sidebar />
      <div className="main-shell">
        <header className="topbar authenticated-topbar">
          <span>Workspace <span className="breadcrumb-divider">/</span> <strong>{title}</strong></span>
          <div className="session-controls">
            <span className="session-identity" title={user.name}>
              <span className="session-avatar" aria-hidden="true">{user.name.slice(0, 1).toUpperCase()}</span>
              <span className="session-name">{user.name}</span>
            </span>
            <button className="session-button" type="button" onClick={signOut}>Log out</button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}><Outlet /></main>
        <footer className="main-footer">DeepDocs AI <span>Document intelligence starts here.</span></footer>
      </div>
    </div>
  )
}
