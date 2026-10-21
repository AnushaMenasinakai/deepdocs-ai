import { useRef } from 'react'
import Modal from './Modal.jsx'

export default function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false, pending = false, retryVersion = 0, onConfirm, onCancel }) {
  const cancel = useRef(null)
  const submitted = useRef(false)
  const observedRetry = useRef(retryVersion)
  // A caller explicitly authorizes retry after failure. Consume changes while
  // pending without unlocking: pending=false alone is not evidence of failure.
  if (!open) submitted.current = false
  else if (!Object.is(observedRetry.current, retryVersion) && !pending) submitted.current = false
  observedRetry.current = retryVersion
  return <Modal open={open} onClose={onCancel} title={title} description={description} dismissible={!pending} initialFocusRef={cancel}>
    <div className="modal-actions" aria-busy={pending}>
      <button ref={cancel} className="session-button" type="button" disabled={pending || !open} onClick={onCancel}>{cancelLabel}</button>
      <button className={destructive ? 'kb-danger' : 'kb-primary'} type="button" disabled={pending || !open} onClick={() => {
        if (!open || pending || submitted.current) return
        submitted.current = true
        onConfirm()
      }}>{pending ? 'Working…' : confirmLabel}</button>
    </div>
    {pending && <p role="status" className="modal-description">Please wait. Closing this dialog would not cancel the operation.</p>}
  </Modal>
}
