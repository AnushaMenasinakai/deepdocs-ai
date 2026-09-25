import { useCallback, useEffect, useRef, useState } from 'react'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import KnowledgeBaseForm from '../components/KnowledgeBaseForm.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { listKnowledgeBases, createKnowledgeBase, updateKnowledgeBase, deleteKnowledgeBase } from '../services/api.js'

const ordered = items => [...items].sort((a, b) =>
  Date.parse(b.updated_at) - Date.parse(a.updated_at) || b.id.localeCompare(a.id))

export default function KnowledgeBases() {
  const { logout } = useAuth()
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

  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    setLoadError('')
    try {
      const data = await listKnowledgeBases(controller.signal)
      if (!controller.signal.aborted) setItems(ordered(data))
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else setLoadError(error.message)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [logout])

  useEffect(() => {
    load()
    return () => request.current?.abort()
  }, [load])

  function close() {
    setEditor(null)
    setDeleting(null)
    setActionError('')
    // Return keyboard focus to the originating action, or New after deletion.
    requestAnimationFrame(() => {
      const target = returnFocus.current?.isConnected ? returnFocus.current : createButton.current
      target?.focus()
    })
  }

  function open(kind, item, event) {
    returnFocus.current = event.currentTarget
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
    request.current = controller
    try {
      if (deleting) {
        await deleteKnowledgeBase(deleting.id, controller.signal)
        if (controller.signal.aborted) return
        setItems(current => current.filter(item => item.id !== deleting.id))
        setNotice('Knowledge Base deleted.')
      } else {
        const saved = editor.item
          ? await updateKnowledgeBase(editor.item.id, data, controller.signal)
          : await createKnowledgeBase(data, controller.signal)
        if (controller.signal.aborted) return
        setItems(current => ordered([...current.filter(item => item.id !== saved.id), saved]))
        setNotice(editor.item ? 'Knowledge Base updated.' : 'Knowledge Base created.')
      }
      close()
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else if (error.status === 404) {
        close()
        setNotice('This Knowledge Base is no longer available. Updating your list.')
        setBusy(false)
        await load()
      } else setActionError(error.message)
    } finally {
      pending.current = false
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  const editing = Boolean(editor || deleting)
  return <>
    <Header eyebrow="ORGANIZE YOUR KNOWLEDGE" title="Knowledge Bases" description="Keep related documents together, with room for every topic and project." />
    <div className="kb-toolbar">
      <div><h2>Your collections</h2><p className="kb-muted">A focused space for each topic or project.</p></div>
      <button ref={createButton} className="kb-primary" disabled={editing || loading || Boolean(loadError)} onClick={event => open('edit', null, event)}>New Knowledge Base</button>
    </div>
    <div role="status" aria-live="polite">{notice && <p className="auth-success">{notice}</p>}</div>
    {editor && <KnowledgeBaseForm key={editor.item?.id || 'new'} item={editor.item} busy={busy} error={actionError} onSave={mutate} onCancel={close} />}
    {deleting && <section className="panel kb-editor kb-delete" aria-labelledby="kb-delete-title">
      <h2 id="kb-delete-title">Delete Knowledge Base?</h2>
      <p>Delete <strong>{deleting.name}</strong>? This cannot be undone.</p>
      {actionError && <p role="alert" className="auth-error">{actionError}</p>}
      <div className="kb-actions">
        <button autoFocus className="session-button" disabled={busy} onClick={close}>Cancel</button>
        <button className="kb-danger" disabled={busy} onClick={() => mutate()}>{busy ? 'Deleting…' : 'Confirm deletion'}</button>
      </div>
    </section>}
    {loading ? <section className="panel kb-state" role="status">Loading your Knowledge Bases…</section>
      : loadError ? <section className="panel kb-state"><p role="alert">{loadError}</p><button className="session-button" onClick={load}>Try again</button></section>
      : items.length === 0 ? <section className="panel empty-state">
        <span className="empty-icon"><Icon name="library" size={32} /></span>
        <h2>Your knowledge starts with a collection</h2>
        <p>Create your first Knowledge Base for project references, course notes, or research. Document uploads will come in a later phase.</p>
      </section>
      : <div className="kb-grid">{items.map(item => <article className="panel kb-card" key={item.id}>
        <span className="feature-icon"><Icon name="library" size={22} /></span>
        <h2>{item.name}</h2>
        <p className="kb-description">{item.description || 'No description added.'}</p>
        <p className="kb-updated">Updated <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p>
        <div className="kb-actions">
          <button className="session-button" aria-label={'Edit ' + item.name} disabled={editing || busy} onClick={event => open('edit', item, event)}>Edit</button>
          <button className="session-button kb-delete-link" aria-label={'Delete ' + item.name} disabled={editing || busy} onClick={event => open('delete', item, event)}>Delete</button>
        </div>
      </article>)}</div>}
  </>
}
