import { useState } from 'react'

export default function KnowledgeBaseForm({ item, busy, error, onSave, onCancel }) {
  const [name, setName] = useState(item?.name || '')
  const [description, setDescription] = useState(item?.description || '')
  const [validation, setValidation] = useState('')

  function submit(event) {
    event.preventDefault()
    if (busy) return
    const trimmedName = name.trim()
    const trimmedDescription = description.trim()
    if (!trimmedName || trimmedName.length > 100 || trimmedDescription.length > 500) {
      setValidation('Enter a name of 1–100 characters and a description of up to 500 characters.')
      return
    }
    setValidation('')
    onSave({ name: trimmedName, description: trimmedDescription || null })
  }

  return <section className="panel kb-editor" aria-labelledby="kb-form-title">
    <h2 id="kb-form-title">{item ? 'Edit Knowledge Base' : 'Create Knowledge Base'}</h2>
    <p className="kb-muted">Give your collection a clear name and a little context.</p>
    <form onSubmit={submit}>
      <fieldset className="auth-fields" disabled={busy}>
        <div className="auth-field">
          <label htmlFor="kb-name">Name</label>
          <input id="kb-name" autoFocus required maxLength={100} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. Machine Learning Notes" />
        </div>
        <div className="auth-field">
          <label htmlFor="kb-description">Description <span className="kb-muted">(optional)</span></label>
          <textarea id="kb-description" maxLength={500} rows={4} value={description} onChange={event => setDescription(event.target.value)} aria-describedby="kb-description-hint" />
          <p id="kb-description-hint" className="auth-hint">{description.length}/500 characters</p>
        </div>
      </fieldset>
      {(validation || error) && <p className="auth-error" role="alert">{validation || error}</p>}
      <div className="kb-actions">
        <button className="kb-primary" disabled={busy} type="submit">{busy ? 'Saving…' : item ? 'Save changes' : 'Create Knowledge Base'}</button>
        <button className="session-button" disabled={busy} type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  </section>
}
