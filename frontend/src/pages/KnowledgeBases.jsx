import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Modal from '../components/Modal.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import DropdownMenu from '../components/DropdownMenu.jsx'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import KnowledgeBaseForm from '../components/KnowledgeBaseForm.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { browseKnowledgeBases, createKnowledgeBase, updateKnowledgeBase, deleteKnowledgeBase } from '../services/api.js'

import { ManagementControls, ManagementPagination, useManagementQuery } from '../components/ManagementControls.jsx'
const SORTS = ['updated_at', 'created_at', 'name']

export default function KnowledgeBases() {
  const { logout } = useAuth()
  const view = useManagementQuery('updated_at', SORTS)
  const { query, waiting, change } = view
  const [meta, setMeta] = useState({ page: 1, total: 0, total_pages: 0 })
  const mutation = useRef(null)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [editor, setEditor] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const request = useRef(null)
  const pending = useRef(false)
  const createButton = useRef(null)
  const returnFocus = useRef(null)
  const nameInput = useRef(null)
  const restoreFocus = useRef(false)
  const session = useRef(0)
  const [retryVersion, setRetryVersion] = useState(0)

  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    setLoadError('')
    if (waiting) return
    try {
      const data = await browseKnowledgeBases(query, controller.signal)
      if (!controller.signal.aborted) {
        if (query.page > Math.max(1, data.total_pages)) { change({ page: Math.max(1, data.total_pages) }, true); return }
        setItems(data.items); setMeta(data)
      }
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else setLoadError(error.message)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [logout, query, waiting, change])

  useEffect(() => {
    load()
    return () => request.current?.abort()
  }, [load])

  useEffect(() => () => { session.current++; mutation.current?.abort() }, [])

  // Shared dialogs restore their trigger. After deletion that trigger may no
  // longer exist, so wait for both dialog closure and list refresh before
  // focusing the enabled fallback. Never steal focus from another overlay.
  useEffect(() => {
    function restore() {
      if (!restoreFocus.current || (editor || deleting) || busy || loading || document.querySelector('dialog:modal')) return
      const target = returnFocus.current?.isConnected && !returnFocus.current.disabled ? returnFocus.current : createButton.current
      if (target && !target.disabled) { target.focus(); restoreFocus.current = false }
    }
    document.addEventListener('close', restore, true)
    restore()
    return () => document.removeEventListener('close', restore, true)
  }, [editor, deleting, busy, loading])

  function close() {
    session.current++
    setEditor(null)
    setDeleting(null)
    setActionError('')
    restoreFocus.current = true
  }

  function open(kind, item, event) {
    if (pending.current) return
    session.current++
    restoreFocus.current = false
    setRetryVersion(0)
    returnFocus.current = event?.currentTarget || document.activeElement
    setActionError('')
    setNotice('')
    setEditor(kind === 'edit' ? { item } : null)
    setDeleting(kind === 'delete' ? item : null)
  }

  async function mutate(data) {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setActionError('')
    const controller = new AbortController()
    mutation.current = controller
    const attemptSession = session.current
    const current = () => !controller.signal.aborted && session.current === attemptSession
    let retryAllowed = false
    try {
      if (deleting) {
        await deleteKnowledgeBase(deleting.id, controller.signal)
        if (!current()) return
        setNotice('Knowledge Base deleted.')
      } else {
        await (editor.item
          ? updateKnowledgeBase(editor.item.id, data, controller.signal)
          : createKnowledgeBase(data, controller.signal))
        if (!current()) return
        setNotice(editor.item ? 'Knowledge Base updated.' : 'Knowledge Base created.')
      }
      close()
      await load()
    } catch (error) {
      if (!current()) return
      if (error.status === 401) logout()
      else if (error.status === 404) {
        close()
        setNotice('This Knowledge Base is no longer available. Updating your list.')
        setBusy(false)
        await load()
      } else {
        setActionError(error.message)
        retryAllowed = Boolean(deleting)
      }
    } finally {
      if (mutation.current === controller) {
        pending.current = false
        if (!controller.signal.aborted) setBusy(false)
        if (retryAllowed && current()) setRetryVersion(version => version + 1)
      }
    }
  }

  const editing = Boolean(editor || deleting)
  return <>
    <Header eyebrow="ORGANIZE YOUR KNOWLEDGE" title="Knowledge Bases" description="Keep related documents together, with room for every topic and project." />
    <div className="kb-toolbar">
      <div><h2>Your collections</h2><p className="kb-muted">A focused space for each topic or project.</p></div>
      <button ref={createButton} className="kb-primary" disabled={editing || busy || loading || Boolean(loadError)} onClick={event => open('edit', null, event)}>New Knowledge Base</button>
    </div>
    <div role="status" aria-live="polite">{notice && <p className="auth-success">{notice}</p>}</div>
    <Modal open={Boolean(editor)} onClose={() => { if (!pending.current) close() }} title={editor?.item ? 'Edit Knowledge Base' : 'Create Knowledge Base'} dismissible={!busy} initialFocusRef={nameInput}>
      {editor && <KnowledgeBaseForm key={editor.item?.id || 'new'} item={editor.item} inputRef={nameInput} busy={busy} error={actionError} onSave={mutate} onCancel={() => { if (!pending.current) close() }} />}
    </Modal>
    <ConfirmDialog open={Boolean(deleting)} title="Delete Knowledge Base?" description={<><span>Delete <strong>{deleting?.name}</strong>? This cannot be undone.</span>{actionError && <span role="alert" className="auth-error kb-delete-error">{actionError}</span>}</>} confirmLabel="Confirm deletion" destructive pending={busy} retryVersion={retryVersion} onConfirm={() => mutate()} onCancel={() => { if (!pending.current) close() }} />
    <ManagementControls state={view} disabled={editing || busy} />
    <ManagementPagination meta={meta} loading={loading || waiting} change={change} disabled={editing || busy || Boolean(loadError)} />
    {loading && items.length === 0 ? <section className="panel kb-state" role="status">Loading your Knowledge Bases…</section>
      : loadError ? <section className="panel kb-state"><p role="alert">{loadError}</p><button className="session-button" onClick={load}>Try again</button></section>
      : items.length === 0 ? <section className="panel empty-state">
        <span className="empty-icon"><Icon name="library" size={32} /></span>
        <h2>{query.search ? 'No matching Knowledge Bases' : 'Your knowledge starts with a collection'}</h2>
        <p>{query.search ? 'Try another name or clear your search.' : 'Create your first Knowledge Base for project references, course notes, or research. Then add PDFs from the Documents page.'}</p>
      </section>
      : <div className="kb-grid" aria-busy={loading || waiting}>{items.map(item => <article className="panel kb-card" key={item.id}>
        <span className="feature-icon"><Icon name="library" size={22} /></span>
        <h2><Link className="kb-card-link" to={'/documents?kb=' + encodeURIComponent(item.id)} aria-disabled={editing || busy || loading || waiting} tabIndex={editing || busy || loading || waiting ? -1 : undefined} onClick={event => { if (editing || pending.current || loading || waiting) event.preventDefault() }}>{item.name}</Link></h2>
        <p className="kb-description">{item.description || 'No description added.'}</p>
        <p className="kb-updated">Last edited <time dateTime={Number.isNaN(Date.parse(item.updated_at)) ? undefined : item.updated_at}>{Number.isNaN(Date.parse(item.updated_at)) ? 'Date unavailable' : new Date(item.updated_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time><span aria-hidden="true"> &rarr;</span></p>
        <div className="kb-card-menu"><DropdownMenu triggerLabel={'Actions for ' + item.name} disabled={editing || busy || loading || waiting} items={[
          { id: 'edit', label: 'Edit', onSelect: () => open('edit', item) },
          { id: 'delete', label: 'Delete', destructive: true, onSelect: () => open('delete', item) },
        ]} /></div>
      </article>)}</div>}
  </>
}
