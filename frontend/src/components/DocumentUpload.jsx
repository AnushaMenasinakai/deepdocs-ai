import { useRef, useState } from 'react'

const MAX_PDF_BYTES = 10 * 1024 * 1024

export function fileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KiB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MiB'
}

export default function DocumentUpload({ busy, uploading, error, onUpload, onChange }) {
  const [file, setFile] = useState(null)
  const [validation, setValidation] = useState('')
  const input = useRef(null)

  function choose(selected) {
    setFile(selected)
    setValidation('')
    onChange()
  }

  function clear() {
    choose(null)
    if (input.current) input.current.value = ''
  }

  async function submit(event) {
    event.preventDefault()
    if (busy) return
    let message = ''
    if (!file) message = 'Choose a PDF file first.'
    else if (!/\.pdf$/i.test(file.name) || (file.type && file.type !== 'application/pdf')) message = 'Only PDF files are accepted.'
    else if (file.size === 0) message = 'The selected PDF is empty.'
    else if (file.size > MAX_PDF_BYTES) message = 'Choose a PDF no larger than 10 MiB.'
    setValidation(message)
    if (message) return
    if (await onUpload(file)) clear()
  }

  return <section className="panel document-upload" aria-labelledby="upload-title">
    <div><h2 id="upload-title">Add a PDF</h2><p className="kb-muted">Keep your original PDFs together. Content processing is not available yet.</p></div>
    <form onSubmit={submit} aria-busy={uploading}>
      <label htmlFor="pdf-file">PDF file</label>
      <input ref={input} id="pdf-file" type="file" accept=".pdf,application/pdf" disabled={busy} onChange={event => choose(event.target.files?.[0] || null)} aria-describedby="pdf-hint" />
      <p id="pdf-hint" className="auth-hint">One PDF at a time · Up to 10 MiB · Validated by the server</p>
      {file && <p className="document-selected"><strong>{file.name}</strong> <span>{fileSize(file.size)}</span></p>}
      {(validation || error) && <p role="alert" className="auth-error">{validation || error}</p>}
      <div className="kb-actions">
        <button className="kb-primary" disabled={busy} type="submit">{uploading ? 'Uploading PDF…' : 'Upload PDF'}</button>
        {file && <button className="session-button" type="button" disabled={busy} onClick={clear}>Clear file</button>}
      </div>
      {uploading && <p className="auth-hint" role="status">Uploading and saving your PDF. Please wait.</p>}
    </form>
  </section>
}
