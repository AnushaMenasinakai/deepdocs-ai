import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { listAskHistory, deleteAskHistory } from '../services/api.js'
import SupportingSources from './SupportingSources.jsx'

export default function AskHistory({ knowledgeBaseId, revision }) {
  const { logout } = useAuth()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [confirming, setConfirming] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const read = useRef(null)
  const mutation = useRef(null)
  const title = useRef(null)
  const confirmButton = useRef(null)
  useEffect(() => { if (confirming) confirmButton.current?.focus() }, [confirming])
  const returnFocus = useRef(null)
  const load = useCallback(async () => {
    read.current?.abort()
    const controller = new AbortController()
    read.current = controller
    setLoading(true); setLoadError('')
    try {
      const values = await listAskHistory(knowledgeBaseId, controller.signal)
      if (!controller.signal.aborted) setItems(values)
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else setLoadError(error.message)
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }, [knowledgeBaseId, logout])
  useEffect(() => { load(); return () => read.current?.abort() }, [load, revision])
  useEffect(() => () => mutation.current?.abort(), [])
  function cancel() {
    setConfirming(null); setActionError('')
    returnFocus.current?.focus()
  }
  async function remove() {
    if (mutation.current || !confirming) return
    const id = confirming.id
    const controller = new AbortController()
    mutation.current = controller
    setBusy(true); setActionError(''); setNotice('')
    try {
      await deleteAskHistory(knowledgeBaseId, id, controller.signal)
      if (controller.signal.aborted) return
      // A read started before deletion must not restore the deleted snapshot.
      read.current?.abort(); setLoading(false)
      setItems(current => current.filter(item => item.id !== id))
      setConfirming(null); setNotice('Previous question deleted.')
      title.current?.focus()
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else if (error.status === 404) {
        setConfirming(null); setNotice('This entry is no longer available. Refreshing history.'); load()
      } else setActionError(error.message)
    } finally {
      if (!controller.signal.aborted) { mutation.current = null; setBusy(false) }
    }
  }
  return <section className="ask-history" aria-labelledby="history-title">
    <h2 id="history-title" tabIndex={-1} ref={title}>Previous questions</h2>
    <p className="kb-muted">Your latest 20 saved questions for this Knowledge Base. Each new question starts fresh.</p>
    <p className="visually-hidden" role="status">{notice}</p>
    {loading && <p className="panel kb-state" role="status">Loading previous questions…</p>}
    {!loading && loadError && <div className="panel kb-state" role="alert"><p>{loadError}</p><button className="session-button" onClick={load}>Retry history</button></div>}
    {!loading && !loadError && !items.length && <div className="panel kb-state"><h3>No previous questions yet</h3><p>Ask your first question about this Knowledge Base.</p></div>}
    {confirming && <section className="panel kb-editor kb-delete" aria-label="Confirm history deletion">
      <h3>Delete this previous question?</h3><p>{confirming.question}</p>
      <p>Only this saved question and answer will be removed. Your documents stay unchanged.</p>
      {actionError && <p role="alert" className="auth-error">{actionError}</p>}
      <div className="kb-actions"><button ref={confirmButton} className="kb-danger" disabled={busy} onClick={remove}>{busy ? 'Deleting…' : 'Confirm delete'}</button><button className="session-button" disabled={busy} onClick={cancel}>Cancel</button></div>
    </section>}
    {!loading && !loadError && <ul className="history-list">
      {items.map(item => <li className="panel history-item" key={item.id}>
        <details>
          <summary><span className="history-question">{item.question}</span><span className="history-meta"><span>{item.status === 'answered' ? 'Answered' : 'Insufficient context'}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></span></summary>
          <div className="history-answer"><p className="small-label">SAVED ANSWER</p><p className="answer-text">{item.answer}</p>
            {!!item.sources.length && <><p className="kb-muted history-snapshot-note">Historical snapshot: these source documents may have changed or been deleted.</p><SupportingSources sources={item.sources} /></>}
          </div>
        </details>
        <button className="session-button kb-delete-link" disabled={busy} aria-label={'Delete previous question: ' + item.question}
          onClick={event => { returnFocus.current = event.currentTarget; setConfirming(item); setActionError('') }}>Delete</button>
      </li>)}
    </ul>}
  </section>
}
