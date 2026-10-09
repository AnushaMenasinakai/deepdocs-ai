import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import DocumentUpload, { fileSize } from '../components/DocumentUpload.jsx'
import DocumentBulk from '../components/DocumentBulk.jsx'
import DocumentInspection from '../components/DocumentInspection.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { listKnowledgeBases, browseDocuments, uploadDocument, deleteDocument, bulkDeleteDocuments, bulkReindexDocuments } from '../services/api.js'

import { ManagementControls, ManagementPagination, useManagementQuery } from '../components/ManagementControls.jsx'
const SORTS = ['created_at', 'updated_at', 'filename']

// Keyed by selection: old requests and UI state cannot cross collection boundaries.
function DocumentCollection({ base, onBusy, onRefreshBases }) {
  const { logout } = useAuth()
  const view = useManagementQuery('created_at', SORTS, true)
  const { query, waiting, change } = view
  const queryKey = JSON.stringify(query)
  const selectionKey = queryKey + ':' + view.search
  const [loadedKey, setLoadedKey] = useState(null)
  const [selection, setSelection] = useState({ key: '', ids: [] })
  const [confirmation, setConfirmation] = useState(null)
  const [bulkResult, setBulkResult] = useState(null)
  const [bulkError, setBulkError] = useState('')
  const [inspection, setInspection] = useState(null)
  const inspectionOrigin = useRef(null)
  const [meta, setMeta] = useState({ page: 1, total: 0, total_pages: 0 })
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
  const restoreBulkFocus = useRef(false)
  const heading = useRef(null)

  const load = useCallback(async () => {
    listRequest.current?.abort()
    const controller = new AbortController()
    listRequest.current = controller
    setLoading(true)
    setInspection(null)
    setLoadError(null)
    setSelection({ key: '', ids: [] })
    setBulkError('')
    if (waiting) return
    try {
      const data = await browseDocuments(base.id, query, controller.signal)
      if (!controller.signal.aborted && active.current) {
        if (query.page > Math.max(1, data.total_pages)) { change({ page: Math.max(1, data.total_pages) }, true); return }
        setItems(data.items); setMeta(data); setLoadedKey(JSON.stringify(query))
      }
    } catch (error) {
      if (controller.signal.aborted || !active.current) return
      if (error.status === 401) logout()
      else setLoadError(error)
    } finally {
      if (!controller.signal.aborted && active.current) setLoading(false)
    }
  }, [base.id, logout, query, waiting, change])

  useEffect(() => {
    active.current = true
    load()
    return () => {
      active.current = false
      listRequest.current?.abort()
      mutation.current?.abort()
    }
  }, [load])

  useEffect(() => {
    setSelection({ key: '', ids: [] })
    setConfirmation(null)
  }, [selectionKey])
  useEffect(() => () => onBusy(false), [onBusy])
  useEffect(() => {
    if (!confirmation && restoreBulkFocus.current) {
      restoreBulkFocus.current = false
      const origin = returnFocus.current
      ;(origin?.isConnected && !origin.disabled ? origin : heading.current)?.focus()
    }
  }, [confirmation])

  function closeDelete() {
    setDeleting(null)
    setDeleteError('')
    requestAnimationFrame(() => {
      if (active.current) (returnFocus.current?.isConnected ? returnFocus.current : heading.current)?.focus()
    })
  }

  async function mutate(file) {
    if (pending.current || loading || loadError) return false
    setInspection(null)
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
        await uploadDocument(base.id, file, controller.signal)
        if (controller.signal.aborted || !active.current) return false
        setNotice('PDF uploaded.')
      } else {
        await deleteDocument(deleting.id, controller.signal)
        if (controller.signal.aborted || !active.current) return false
        setItems(current => current.filter(item => item.id !== deleting.id))
        closeDelete()
        setNotice('Document deleted.')
      }
      await load()
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

  const currentPage = !loading && !waiting && !loadError && loadedKey === queryKey
  const selectedIds = currentPage && selection.key === selectionKey ? selection.ids.filter(id => items.some(item => item.id === id)) : []
  const visibleConfirmation = confirmation?.key === selectionKey && currentPage ? confirmation : null
  const locked = Boolean(busy || deleting || visibleConfirmation)

  function openBulk(operation, event) {
    if (!currentPage || locked || pending.current || selectedIds.length === 0 || (operation === 'reindex' && selectedIds.length > 5)) return
    returnFocus.current = event.currentTarget
    setBulkError(''); setBulkResult(null); setNotice('')
    setConfirmation({ key: selectionKey, operation, items: items.filter(item => selectedIds.includes(item.id)).map(({ id, filename }) => ({ id, filename })) })
  }

  function closeBulk() {
    restoreBulkFocus.current = true
    setConfirmation(null)
  }

  async function submitBulk() {
    if (pending.current || !visibleConfirmation) return
    setInspection(null)
    const snapshot = visibleConfirmation
    pending.current = true
    setBusy('bulk'); onBusy(true)
    const controller = new AbortController()
    mutation.current = controller
    try {
      const action = snapshot.operation === 'delete' ? bulkDeleteDocuments : bulkReindexDocuments
      const result = await action(base.id, snapshot.items.map(item => item.id), controller.signal)
      if (controller.signal.aborted || !active.current) return
      setBulkResult({ ...result, results: result.results.map((item, index) => ({ ...item, filename: snapshot.items[index].filename })) })
      closeBulk()
      setSelection({ key: '', ids: [] })
      await load()
    } catch (error) {
      if (controller.signal.aborted || !active.current) return
      closeBulk()
      setSelection({ key: '', ids: [] })
      setLoadedKey(null)
      if (error.status === 401) logout()
      else setBulkError(error.message)
      // An uncertain response is not permission to replay. Refresh is explicit.
    } finally {
      pending.current = false
      if (active.current) { setBusy(''); onBusy(false) }
    }
  }
  return <>
    <div role="status" aria-live="polite">{notice && <p className="auth-success">{notice}</p>}</div>
    <DocumentUpload busy={locked || !currentPage} uploading={busy === 'upload'} error={uploadError} onUpload={mutate} onChange={() => setUploadError('')} />
    <div className="kb-toolbar document-list-heading">
      <div><h2 ref={heading} tabIndex={-1}>Documents in {base.name}</h2><p className="kb-muted">Original files · Indexed reflects last-confirmed vector synchronization</p></div>
      <button className="session-button" disabled={locked || loading} onClick={load}>Refresh documents</button>
    </div>
    <ManagementControls state={view} documents disabled={locked} />
    <ManagementPagination meta={meta} loading={loading || waiting} change={change} disabled={locked || Boolean(loadError)} />
    <DocumentBulk count={selectedIds.length} total={items.length} disabled={locked || !currentPage}
      onSelectPage={checked => { if (currentPage && !locked) setSelection({ key: selectionKey, ids: checked ? items.map(item => item.id) : [] }) }}
      onClear={() => setSelection({ key: '', ids: [] })} onOpen={openBulk}
      confirmation={visibleConfirmation} onCancel={closeBulk} onConfirm={submitBulk}
      busy={busy === 'bulk'} result={bulkResult} error={bulkError} />
    {inspection?.key === selectionKey && currentPage && !busy && <DocumentInspection
      key={inspection.item.id} document={inspection.item} onClose={() => {
        setInspection(null)
        requestAnimationFrame(() => {
          const origin = inspectionOrigin.current
          if (active.current) (origin?.isConnected && !origin.disabled ? origin : heading.current)?.focus()
        })
      }} />}
    {deleting && <section className="panel kb-editor kb-delete" aria-labelledby="document-delete-title">
      <h2 id="document-delete-title">Delete document?</h2>
      <p>Delete <strong>{deleting.filename}</strong>? The original PDF and its metadata will be removed. This cannot be undone.</p>
      {deleteError && <p role="alert" className="auth-error">{deleteError}</p>}
      <div className="kb-actions">
        <button autoFocus className="session-button" disabled={Boolean(busy)} onClick={closeDelete}>Cancel</button>
        <button className="kb-danger" disabled={Boolean(busy)} onClick={() => mutate()}>{busy === 'delete' ? 'Deleting…' : 'Confirm deletion'}</button>
      </div>
    </section>}
    {loading && items.length === 0 ? <section className="panel kb-state" role="status">Loading documents…</section>
      : loadError ? <section className="panel kb-state">
        <p role="alert">{loadError.message}</p>
        <button className="session-button" onClick={loadError.status === 404 ? onRefreshBases : load}>{loadError.status === 404 ? 'Refresh Knowledge Bases' : 'Try again'}</button>
      </section>
      : items.length === 0 ? <section className="panel empty-state">
        <span className="empty-icon"><Icon name="document" size={32} /></span>
        <h2>{query.search || query.status !== 'all' ? 'No matching documents' : 'No documents here yet'}</h2><p>{query.search || query.status !== 'all' ? 'Try another filename or clear your filters.' : 'Choose a PDF above to add your first document to this Knowledge Base.'}</p>
      </section>
      : <div className="document-list" aria-busy={loading || waiting}>{items.map(item => <article key={item.id} className="panel document-card">
        <input className="document-row-check" type="checkbox" aria-label={'Select ' + item.filename}
          disabled={locked || !currentPage} checked={selectedIds.includes(item.id)} onChange={event => {
            if (!currentPage || locked) return
            setSelection({ key: selectionKey, ids: event.target.checked ? [...selectedIds, item.id] : selectedIds.filter(id => id !== item.id) })
          }} />
        <span className="feature-icon document"><Icon name="document" size={24} /></span>
        <div className="document-info">
          <h3>{item.filename}</h3>
          <p>{fileSize(item.file_size)} <span aria-hidden="true">·</span> Added <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p>
          <span className="document-status">{item.failed ? 'Failed' : item.indexed ? 'Indexed' : ({ uploaded: 'Uploaded', processing: 'Processing', processed: 'Processed', failed: 'Failed' })[item.status] || 'Status unavailable'}</span>
        </div>
        <button className="session-button" disabled={locked || !currentPage} aria-label={'Inspect status for ' + item.filename} onClick={event => {
          inspectionOrigin.current = event.currentTarget
          setInspection({ key: selectionKey, item })
        }}>Inspect status</button>
        <button className="session-button kb-delete-link" disabled={locked || !currentPage} aria-label={'Delete ' + item.filename} onClick={event => {
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
  const [url, setUrl] = useSearchParams()
  const selected = bases.some(item => item.id === url.get('kb')) ? url.get('kb') : bases[0]?.id || ''
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
            const id = event.target.value
            setUrl(current => { const next = new URLSearchParams(current); next.set('kb', id); next.set('page', '1'); return next })
            setNotice('')
          }}>{bases.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        </div>
        {base && <DocumentCollection key={base.id} base={base} onBusy={setBusy} onRefreshBases={reconcile} />}
      </>}
  </>
}
