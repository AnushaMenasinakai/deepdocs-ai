import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import Icon from './Icon.jsx'
const navigation = [['/', 'Dashboard', 'dashboard'], ['/knowledge-bases', 'Knowledge Bases', 'library'], ['/documents', 'Documents', 'document'], ['/ask', 'Ask DeepDocs', 'ask']]
export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const [tooltipDismissed, setTooltipDismissed] = useState(false)
  return <aside className={'sidebar' + (collapsed ? ' sidebar-collapsed' : '') + (tooltipDismissed ? ' sidebar-tooltip-dismissed' : '')}
    onKeyDown={event => { if (event.key === 'Escape') setTooltipDismissed(true) }}>
    <NavLink className="brand" to="/" aria-label="DeepDocs AI home"><span className="brand-mark"><Icon name="document" /></span><span className="brand-copy">DeepDocs <span className="brand-ai">AI</span></span></NavLink>
    <button className="sidebar-toggle" type="button" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={() => setCollapsed(value => !value)}>
      <Icon name="arrow" size={20} />
    </button>
    <p className="nav-label">WORKSPACE</p>
    <nav id="workspace-navigation" aria-label="Main navigation">{navigation.map(([to, label, icon]) => <NavLink key={to} to={to} end={to === '/'} aria-label={label}
      onFocus={() => setTooltipDismissed(false)} onMouseEnter={() => setTooltipDismissed(false)}
      className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}><Icon name={icon} /><span className="nav-text">{label}</span></NavLink>)}</nav>
    <div className="sidebar-note"><Icon name="spark" /><strong>A home for your knowledge</strong><p>Organize documents and find grounded answers.</p><span className="small-label">DOCUMENT WORKSPACE</span></div>
    <div className="sidebar-footer"><span className="workspace-dot" />DeepDocs workspace</div>
  </aside>
}
