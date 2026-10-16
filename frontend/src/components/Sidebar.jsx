import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import Icon from './Icon.jsx'
const navigation = [['/', 'Dashboard', 'dashboard'], ['/knowledge-bases', 'Knowledge Bases', 'library'], ['/documents', 'Documents', 'document'], ['/ask', 'Ask DeepDocs', 'ask']]
export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const [tooltipDismissed, setTooltipDismissed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [closing, setClosing] = useState(false)
  const drawer = useRef(null)
  const menuButton = useRef(null)
  const closeButton = useRef(null)
  const desktop = useRef(null)
  const backdropPress = useRef(false)
  const location = useLocation()
  const finishClose = useCallback(() => {
    const wasOpen = drawer.current?.open
    drawer.current?.close()
    setDrawerOpen(false)
    setClosing(false)
    if (wasOpen) {
      if (window.matchMedia('(max-width: 600px)').matches) menuButton.current?.focus()
      else desktop.current?.querySelector('.nav-link.active')?.focus()
    }
  }, [])
  const requestClose = useCallback(() => {
    if (!drawer.current?.open) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) finishClose()
    else setClosing(true)
  }, [finishClose])
  useEffect(() => {
    if (!drawerOpen) return
    const element = drawer.current
    element.showModal()
    closeButton.current?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { element.close(); document.body.style.overflow = overflow }
  }, [drawerOpen])
  useEffect(() => {
    const media = window.matchMedia('(max-width: 600px)')
    const resize = () => { if (!media.matches) finishClose() }
    media.addEventListener('change', resize)
    return () => media.removeEventListener('change', resize)
  }, [finishClose])
  useEffect(() => { requestClose() }, [location.key, requestClose])
  useEffect(() => {
    if (!closing) return
    // Keep the dialog modal until the exit finishes, including when reduced
    // motion changes during the animation. No timeout guesses completion.
    let active = true
    const animations = drawer.current.getAnimations()
    Promise.allSettled(animations.map(animation => animation.finished)).then(() => { if (active) finishClose() })
    return () => { active = false }
  }, [closing, finishClose])
  function trapFocus(event) {
    if (event.key !== 'Tab') return
    const controls = [...drawer.current.querySelectorAll('a[href], button:not(:disabled)')].filter(element => element.getClientRects().length)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  function outside(event) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)
  }
  const brand = <><span className="brand-mark"><Icon name="document" /></span><span className="brand-copy">DeepDocs <span className="brand-ai">AI</span></span></>
  return <>
    <div className="mobile-nav-bar">
      <NavLink className="brand" to="/" aria-label="DeepDocs AI home">{brand}</NavLink>
      <button ref={menuButton} type="button" className="mobile-menu-button" aria-label="Open navigation menu" aria-expanded={drawerOpen} aria-controls="mobile-navigation" onClick={() => setDrawerOpen(true)}><Icon name="menu" /></button>
    </div>
    <dialog ref={drawer} id="mobile-navigation" className={'mobile-drawer' + (closing ? ' drawer-closing' : '')} aria-label="Mobile navigation" aria-modal="true"
      onCancel={event => { event.preventDefault(); requestClose() }} onKeyDown={trapFocus}
      onPointerDown={event => { backdropPress.current = outside(event) }}
      onClick={event => { if (backdropPress.current && outside(event)) requestClose() }}>
      <div className="mobile-drawer-heading">
        <NavLink className="brand" to="/" aria-label="DeepDocs AI home" onClick={requestClose}>{brand}</NavLink>
        <button ref={closeButton} type="button" className="mobile-menu-button" aria-label="Close navigation menu" onClick={requestClose}><Icon name="close" /></button>
      </div>
      <nav aria-label="Main navigation">{navigation.map(([to, label, icon]) => <NavLink key={to} to={to} end={to === '/'} onClick={requestClose} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}><Icon name={icon} /><span>{label}</span></NavLink>)}</nav>
    </dialog>
    <aside ref={desktop} className={'sidebar' + (collapsed ? ' sidebar-collapsed' : '') + (tooltipDismissed ? ' sidebar-tooltip-dismissed' : '')}
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
  </aside></>
}
