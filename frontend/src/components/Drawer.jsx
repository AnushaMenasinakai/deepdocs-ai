import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import Icon from './Icon.jsx'
import { showExclusiveDialog } from './overlay.js'

// Controlled, single-overlay drawer. No business logic or operation cancellation.
export default function Drawer({ open, onClose, title, description, children, dismissible = true, initialFocusRef }) {
  const id = useId()
  const dialog = useRef(null)
  const restore = useRef(null)
  const backdropPress = useRef(false)
  useEffect(() => {
    const element = dialog.current
    let active = true
    if (open) {
      restore.current?.()
      const previous = document.activeElement
      const overflow = document.body.style.overflow
      element.classList.remove('side-drawer-closing')
      if (!showExclusiveDialog(element)) return
      document.body.style.overflow = 'hidden'
      const target = initialFocusRef?.current
      if (target && element.contains(target) && !target.disabled) target.focus()
      else element.querySelector('.side-drawer-title').focus()
      restore.current = () => {
        element.close()
        document.body.style.overflow = overflow
        if (previous?.isConnected && !previous.disabled) previous.focus()
        restore.current = null
      }
    } else if (element.open) {
      element.classList.add('side-drawer-closing')
      Promise.allSettled(element.getAnimations().map(animation => animation.finished)).then(() => {
        if (active) restore.current?.()
      })
    }
    return () => { active = false }
  }, [open, initialFocusRef])
  useEffect(() => () => { restore.current?.() }, [])
  function requestClose() { if (open && dismissible) onClose() }
  function outside(event) {
    const box = event.currentTarget.getBoundingClientRect()
    return event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)
  }
  function trapFocus(event) {
    if (event.key !== 'Tab') return
    const controls = [...dialog.current.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
      .filter(element => !element.matches(':disabled') && element.tabIndex >= 0 && element.getClientRects().length && getComputedStyle(element).visibility === 'visible' && !element.closest('[inert]'))
    const first = controls[0], last = controls.at(-1)
    if (!first) { event.preventDefault(); dialog.current.querySelector('.side-drawer-title').focus(); return }
    if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (document.activeElement === last || !controls.includes(document.activeElement))) { event.preventDefault(); first.focus() }
  }
  return createPortal(<dialog ref={dialog} className="side-drawer" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={description ? `${id}-description` : undefined}
    onCancel={event => { event.preventDefault(); requestClose() }} onKeyDown={trapFocus}
    onPointerDown={event => { backdropPress.current = outside(event) }} onClick={event => { if (backdropPress.current && outside(event)) requestClose() }}>
    <header className="side-drawer-heading"><div><h2 className="side-drawer-title" tabIndex={-1} id={`${id}-title`}>{title}</h2>
      {description && <p className="modal-description" id={`${id}-description`}>{description}</p>}</div>
      <button className="mobile-menu-button" type="button" aria-label="Close drawer" disabled={!dismissible || !open} onClick={requestClose}><Icon name="close" /></button>
    </header>
    <div className="side-drawer-content" inert={!open}>{children}</div>
  </dialog>, document.body)
}
