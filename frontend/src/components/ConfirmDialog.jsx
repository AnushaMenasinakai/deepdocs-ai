import { useRef } from 'react'
import Modal from './Modal.jsx'

export default function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false, pending = false, onConfirm, onCancel }) {
  const cancel = useRef(null)
  const submitted = useRef(false)
  // Reset only when the caller closes the dialog; never retry a pending action.
  if (!open) submitted.current = false
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
