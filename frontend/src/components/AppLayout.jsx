import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
export default function AppLayout() {
  const { pathname } = useLocation()
  const title = { '/': 'Dashboard', '/knowledge-bases': 'Knowledge Bases', '/documents': 'Documents', '/ask': 'Ask DeepDocs' }[pathname.replace(/\/$/, '') || '/'] || 'Page not found'
  return <div className="app-layout"><a className="skip-link" href="#main-content">Skip to content</a><Sidebar /><div className="main-shell"><header className="topbar"><span>Workspace <span className="breadcrumb-divider">/</span> <strong>{title}</strong></span><span className="foundation-badge">Foundation preview</span></header><main id="main-content" tabIndex={-1}><Outlet /></main><footer className="main-footer">DeepDocs AI <span>Document intelligence starts here.</span></footer></div></div>
}
