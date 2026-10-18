import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import Icon from './Icon.jsx'

// One overlay at a time. Callers own open state; dismissal never cancels work.
export default function Modal({ open, onClose, title, description, children, dismissible = true, initialFocusRef }) {
  const id = useId()
  const dialog = useRef(null)
  const restore = useRef(null)
  useEffect(() => {
    const element = dialog.current
    let active = true
    if (open) {
      restore.current?.()
      const previous = document.activeElement
      const root = document.getElementById('root')
      const overflow = document.body.style.overflow
      // Avoid transforming an application shell containing another top-layer dialog.
      const decorate = !document.querySelector('dialog[open]')
      element.classList.remove('modal-closing')
      element.showModal()
      document.body.style.overflow = 'hidden'
      if (decorate) root?.classList.add('modal-background')
      const target = initialFocusRef?.current
      if (target && element.contains(target) && !target.disabled) target.focus()
      else element.querySelector('.modal-title').focus()
      restore.current = () => {
        element.close()
        if (decorate) root?.classList.remove('modal-background')
        document.body.style.overflow = overflow
        if (previous?.isConnected && !previous.disabled) previous.focus()
        restore.current = null
      }
    } else if (element.open) {
      element.classList.add('modal-closing')
      document.getElementById('root')?.classList.remove('modal-background')
      Promise.allSettled(element.getAnimations().map(animation => animation.finished)).then(() => {
        if (active) restore.current?.()
      })
    }
    return () => { active = false }
  }, [open, initialFocusRef])
  useEffect(() => () => { restore.current?.() }, [])

  function trapFocus(event) {
    if (event.key !== 'Tab') return
    const controls = [...dialog.current.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
      .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length && !element.closest('[inert]'))
    const first = controls[0], last = controls.at(-1)
    if (!first) { event.preventDefault(); dialog.current.querySelector('.modal-title').focus(); return }
    if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (document.activeElement === last || !controls.includes(document.activeElement))) { event.preventDefault(); first.focus() }
  }
  return createPortal(<dialog ref={dialog} className="shared-modal" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={description ? `${id}-description` : undefined}
    onCancel={event => { event.preventDefault(); if (open && dismissible) onClose() }} onKeyDown={trapFocus}>
    <div className="modal-heading"><h2 className="modal-title" id={`${id}-title`} tabIndex={-1}>{title}</h2>
      <button type="button" className="mobile-menu-button" aria-label="Close dialog" disabled={!dismissible || !open} onClick={onClose}><Icon name="close" /></button></div>
    {description && <p className="modal-description" id={`${id}-description`}>{description}</p>}
    <div className="modal-content">{children}</div>
  </dialog>, document.body)
}
