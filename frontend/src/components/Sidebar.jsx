import { NavLink } from 'react-router-dom'
import Icon from './Icon.jsx'
const navigation = [['/', 'Dashboard', 'dashboard'], ['/knowledge-bases', 'Knowledge Bases', 'library'], ['/documents', 'Documents', 'document'], ['/ask', 'Ask DeepDocs', 'ask']]
export default function Sidebar() {
  return <aside className="sidebar">
    <NavLink className="brand" to="/" aria-label="DeepDocs AI home"><span className="brand-mark"><Icon name="document" /></span>DeepDocs <span className="brand-ai">AI</span></NavLink>
    <p className="nav-label">WORKSPACE</p>
    <nav aria-label="Main navigation">{navigation.map(([to, label, icon]) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}><Icon name={icon} /><span>{label}</span></NavLink>)}</nav>
    <div className="sidebar-note"><Icon name="spark" /><strong>A home for your knowledge</strong><p>Your document intelligence workspace, taking shape.</p><span className="small-label">UI FOUNDATION</span></div>
    <div className="sidebar-footer"><span className="workspace-dot" />DeepDocs workspace</div>
  </aside>
}
