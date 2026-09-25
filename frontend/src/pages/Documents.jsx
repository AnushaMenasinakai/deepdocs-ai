import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import DocumentUpload, { fileSize } from '../components/DocumentUpload.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { listKnowledgeBases, listDocuments, uploadDocument, deleteDocument } from '../services/api.js'

const ordered = items => [...items].sort((a, b) =>
  Date.parse(b.created_at) - Date.parse(a.created_at) || b.id.localeCompare(a.id))

// Keyed by selection: old requests and UI state cannot cross collection boundaries.
function DocumentCollection({ base, onBusy, onRefreshBases }) {
  const { logout } = useAuth()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState('')
  const listRequest = useRef(null)
  const mutation = useRef(null)
  const pending = useRef(false)
  const active = useRef(true)
  const returnFocus = useRef(null)
  const heading = useRef(null)

  const load = useCallback(async () => {
    listRequest.current?.abort()
    const controller = new AbortController()
    listRequest.current = controller
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listDocuments(base.id, controller.signal)
      if (!controller.signal.aborted && active.current) setItems(ordered(data))
    } catch (error) {
      if (controller.signal.aborted || !active.current) return
      if (error.status === 401) logout()
      else setLoadError(error)
    } finally {
      if (!controller.signal.aborted && active.current) setLoading(false)
    }
  }, [base.id, logout])

  useEffect(() => {
    active.current = true
    load()
    return () => {
      active.current = false
      listRequest.current?.abort()
      mutation.current?.abort()
    }
  }, [load])

  function closeDelete() {
    setDeleting(null)
    setDeleteError('')
    requestAnimationFrame(() => {
      if (active.current) (returnFocus.current?.isConnected ? returnFocus.current : heading.current)?.focus()
    })
  }

  async function mutate(file) {
    if (pending.current || loading || loadError) return false
    pending.current = true
    const kind = file ? 'upload' : 'delete'
    setBusy(kind)
    onBusy(true)
    setUploadError('')
    setDeleteError('')
    setNotice('')
    const controller = new AbortController()
    mutation.current = controller
    try {
      if (file) {
        const saved = await uploadDocument(base.id, file, controller.signal)
        if (controller.signal.aborted || !active.current) return false
        setItems(current => ordered([...current.filter(item => item.id !== saved.id), saved]))
        setNotice('PDF uploaded.')
      } else {
        await deleteDocument(deleting.id, controller.signal)
        if (controller.signal.aborted || !active.current) return false
        setItems(current => current.filter(item => item.id !== deleting.id))
        closeDelete()
        setNotice('Document deleted.')
      }
      return true
    } catch (error) {
      if (controller.signal.aborted || !active.current) return false
      if (error.status === 401) logout()
      else if (error.status === 404) {
        if (file) onRefreshBases()
        else {
          setItems(current => current.filter(item => item.id !== deleting.id))
          closeDelete()
          setNotice('This document is no longer available. Refreshing the list.')
          await load()
        }
      } else if (file) setUploadError(error.message)
      else setDeleteError(error.message)
      return false
    } finally {
      pending.current = false
      if (active.current) {
        setBusy('')
        onBusy(false)
      }
    }
  }

  const locked = Boolean(busy || deleting)
  return <>
    <div role="status" aria-live="polite">{notice && <p className="auth-success">{notice}</p>}</div>
    <DocumentUpload busy={locked || loading || Boolean(loadError)} uploading={busy === 'upload'} error={uploadError} onUpload={mutate} onChange={() => setUploadError('')} />
    <div className="kb-toolbar document-list-heading">
      <div><h2 ref={heading} tabIndex={-1}>Documents in {base.name}</h2><p className="kb-muted">Original files · Uploaded only</p></div>
      <button className="session-button" disabled={locked || loading} onClick={load}>Refresh documents</button>
    </div>
    {deleting && <section className="panel kb-editor kb-delete" aria-labelledby="document-delete-title">
      <h2 id="document-delete-title">Delete document?</h2>
      <p>Delete <strong>{deleting.filename}</strong>? The original PDF and its metadata will be removed. This cannot be undone.</p>
      {deleteError && <p role="alert" className="auth-error">{deleteError}</p>}
      <div className="kb-actions">
        <button autoFocus className="session-button" disabled={Boolean(busy)} onClick={closeDelete}>Cancel</button>
        <button className="kb-danger" disabled={Boolean(busy)} onClick={() => mutate()}>{busy === 'delete' ? 'Deleting…' : 'Confirm deletion'}</button>
      </div>
    </section>}
    {loading ? <section className="panel kb-state" role="status">Loading documents…</section>
      : loadError ? <section className="panel kb-state">
        <p role="alert">{loadError.message}</p>
        <button className="session-button" onClick={loadError.status === 404 ? onRefreshBases : load}>{loadError.status === 404 ? 'Refresh Knowledge Bases' : 'Try again'}</button>
      </section>
      : items.length === 0 ? <section className="panel empty-state">
        <span className="empty-icon"><Icon name="document" size={32} /></span>
        <h2>No documents here yet</h2><p>Choose a PDF above to add your first document to this Knowledge Base.</p>
      </section>
      : <div className="document-list">{items.map(item => <article key={item.id} className="panel document-card">
        <span className="feature-icon document"><Icon name="document" size={24} /></span>
        <div className="document-info">
          <h3>{item.filename}</h3>
          <p>{fileSize(item.file_size)} <span aria-hidden="true">·</span> Added <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p>
          <span className="document-status">{item.status === 'uploaded' ? 'Uploaded' : 'Status unavailable'}</span>
        </div>
        <button className="session-button kb-delete-link" disabled={locked} aria-label={'Delete ' + item.filename} onClick={event => {
          returnFocus.current = event.currentTarget
          setNotice('')
          setDeleteError('')
          setDeleting(item)
        }}>Delete</button>
      </article>)}</div>}
  </>
}

export default function Documents() {
  const { logout } = useAuth()
  const [bases, setBases] = useState([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const request = useRef(null)

  const loadBases = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    setError('')
    setBusy(false)
    try {
      const data = await listKnowledgeBases(controller.signal)
      if (controller.signal.aborted) return
      setBases(data)
      setSelected(current => data.some(base => base.id === current) ? current : data[0]?.id || '')
    } catch (failure) {
      if (controller.signal.aborted) return
      if (failure.status === 401) logout()
      else setError(failure.message)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [logout])

  useEffect(() => {
    loadBases()
    return () => request.current?.abort()
  }, [loadBases])

  function reconcile() {
    setNotice('The selected Knowledge Base may no longer be available. Your collections have been refreshed.')
    loadBases()
  }

  const base = bases.find(item => item.id === selected)
  return <>
    <Header eyebrow="YOUR SOURCE OF KNOWLEDGE" title="Documents" description="Upload and organize original PDFs within your Knowledge Bases." />
    {notice && <p className="auth-success" role="status">{notice}</p>}
    {loading ? <section className="panel kb-state" role="status">Loading Knowledge Bases…</section>
      : error ? <section className="panel kb-state"><p role="alert">{error}</p><button className="session-button" onClick={loadBases}>Try again</button></section>
      : bases.length === 0 ? <section className="panel empty-state">
        <span className="empty-icon"><Icon name="library" size={32} /></span>
        <h2>Create a Knowledge Base first</h2>
        <p>Every PDF belongs to a Knowledge Base. Create a collection before uploading documents.</p>
        <Link className="primary-link" to="/knowledge-bases">Go to Knowledge Bases</Link>
      </section>
      : <>
        <div className="document-selector">
          <label htmlFor="document-base">Knowledge Base</label>
          <select id="document-base" value={selected} disabled={busy} onChange={event => {
            setSelected(event.target.value)
            setNotice('')
          }}>{bases.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        </div>
        {base && <DocumentCollection key={base.id} base={base} onBusy={setBusy} onRefreshBases={reconcile} />}
      </>}
  </>
}
