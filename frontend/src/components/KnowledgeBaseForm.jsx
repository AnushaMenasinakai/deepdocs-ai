import { useState } from 'react'

export default function KnowledgeBaseForm({ item, inputRef, busy, error, onSave, onCancel }) {
  const [name, setName] = useState(item?.name || '')
  const [description, setDescription] = useState(item?.description || '')
  const [attempted, setAttempted] = useState(false)
  const nameInvalid = attempted && (!name.trim() || name.trim().length > 100)
  const descriptionInvalid = attempted && description.trim().length > 500
  const validation = nameInvalid || descriptionInvalid ? 'Enter a name of 1–100 characters and a description of up to 500 characters.' : ''

  function submit(event) {
    event.preventDefault()
    if (busy) return
    const trimmedName = name.trim()
    const trimmedDescription = description.trim()
    if (!trimmedName || trimmedName.length > 100 || trimmedDescription.length > 500) {
      setAttempted(true)
      return
    }
    setAttempted(false)
    onSave({ name: trimmedName, description: trimmedDescription || null })
  }

  return <section className="kb-editor kb-modal-editor">
    <p className="kb-muted">Give your collection a clear name and a little context.</p>
    <form onSubmit={submit}>
      <fieldset className="auth-fields" disabled={busy}>
        <div className="auth-field">
          <label htmlFor="kb-name">Name</label>
          <input ref={inputRef} id="kb-name" required maxLength={100} onInvalid={() => setAttempted(true)} aria-invalid={nameInvalid} aria-describedby={nameInvalid ? 'kb-validation-error' : undefined} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. Machine Learning Notes" />
        </div>
        <div className="auth-field">
          <label htmlFor="kb-description">Description <span className="kb-muted">(optional)</span></label>
          <textarea id="kb-description" maxLength={500} rows={4} value={description} onChange={event => setDescription(event.target.value)} aria-invalid={descriptionInvalid} aria-describedby={descriptionInvalid ? 'kb-description-hint kb-validation-error' : 'kb-description-hint'} />
          <p id="kb-description-hint" className="auth-hint">{description.length}/500 characters</p>
        </div>
      </fieldset>
      {(validation || error) && <p id={validation ? 'kb-validation-error' : undefined} className="auth-error" role="alert">{validation || error}</p>}
      <div className="kb-actions">
        <button className="kb-primary" disabled={busy} type="submit">{busy ? 'Saving…' : item ? 'Save changes' : 'Create Knowledge Base'}</button>
        <button className="session-button" disabled={busy} type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  </section>
}
