import { useId, useLayoutEffect, useRef, useState } from 'react'

// Native popovers escape clipping while remaining descendants of their dialog.
// items: { id, label, onSelect, disabled?, destructive? }[]
export default function DropdownMenu({ items, triggerLabel = 'More actions', alignment = 'end', disabled = false }) {
  const id = useId()
  const trigger = useRef(null)
  const menu = useRef(null)
  const firstFocus = useRef('first')
  const [open, setOpen] = useState(false)
  function close(restoreFocus = false) {
    menu.current?.hidePopover()
    setOpen(false)
    if (restoreFocus && trigger.current?.isConnected && !trigger.current.disabled) trigger.current.focus()
  }
  function enabled() { return [...menu.current.querySelectorAll('[role="menuitem"]:not(:disabled)')] }
  function show(position = 'first') {
    const modal = document.querySelector('dialog:modal')
    if (disabled || (modal && !modal.contains(trigger.current))) return
    firstFocus.current = position
    setOpen(true)
  }
  useLayoutEffect(() => {
    if (!open || disabled) { if (disabled && open) close(); return }
    const element = menu.current
    element.showPopover()
    const box = trigger.current.getBoundingClientRect()
    const bounds = element.getBoundingClientRect()
    const left = alignment === 'start' ? box.left : box.right - bounds.width
    const top = box.bottom + 6 + bounds.height <= innerHeight - 8 ? box.bottom + 6 : box.top - bounds.height - 6
    element.style.left = `${Math.max(8, Math.min(left, innerWidth - bounds.width - 8))}px`
    element.style.top = `${Math.max(8, Math.min(top, innerHeight - bounds.height - 8))}px`
    const controls = enabled()
    ;(firstFocus.current === 'last' ? controls.at(-1) : controls[0])?.focus()
    if (!controls.length) element.focus()
    const dismiss = () => close(false)
    const scroll = event => { if (!element.contains(event.target)) dismiss() }
    document.addEventListener('deepdocs:modal-opening', dismiss)
    window.addEventListener('resize', dismiss)
    document.addEventListener('scroll', scroll, true)
    return () => {
      document.removeEventListener('deepdocs:modal-opening', dismiss)
      window.removeEventListener('resize', dismiss)
      document.removeEventListener('scroll', scroll, true)
      element.hidePopover()
    }
  }, [open, disabled, alignment])
  function navigate(event) {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return }
    if (event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); close(true); return }
    const controls = enabled(), index = controls.indexOf(document.activeElement)
    let next
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = controls.length - 1
    if (event.key === 'ArrowDown') next = (index + 1) % controls.length
    if (event.key === 'ArrowUp') next = (index - 1 + controls.length) % controls.length
    if (next !== undefined) { event.preventDefault(); event.stopPropagation(); controls[next]?.focus() }
  }
  return <>
    <button ref={trigger} id={`${id}-trigger`} type="button" className="dropdown-trigger" aria-label={triggerLabel} aria-haspopup="menu" aria-expanded={open} aria-controls={`${id}-menu`} disabled={disabled}
      onClick={() => open ? close(true) : show()} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); show(event.key === 'ArrowUp' ? 'last' : 'first') }
      }}><span aria-hidden="true">⋯</span></button>
    <div ref={menu} id={`${id}-menu`} className="dropdown-menu" popover="auto" role="menu" tabIndex={-1} aria-labelledby={`${id}-trigger`} onKeyDown={navigate}
      onToggle={event => { if (event.newState === 'closed') setOpen(false) }}>
      {items.map(item => <button key={item.id} type="button" role="menuitem" tabIndex={-1} disabled={item.disabled} className={'dropdown-item' + (item.destructive ? ' dropdown-destructive' : '')}
        onClick={() => { if (item.disabled || !open) return; close(true); item.onSelect?.() }}>{item.label}</button>)}
    </div>
  </>
}
