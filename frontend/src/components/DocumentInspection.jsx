import { useCallback, useEffect, useRef, useState } from 'react'
import { inspectDocumentOperation } from '../services/api.js'
import { useAuth } from '../context/AuthContext.jsx'

const states = { uploaded: 'Uploaded', processing: 'Processing', processed: 'Processed', indexed: 'Indexed (last confirmed)', failed: 'Failed', unknown: 'Unknown' }
const operations = {
  idle: 'No operation claim was recorded at inspection time.',
  claimed_unknown: 'An operation may still be running, or its outcome may require verification.',
}
const attention = {
  none: 'No attention indicated by this snapshot.',
  needs_processing: 'This document has not completed processing.',
  needs_reindex: 'This document may need indexing before it can be searched.',
  outcome_uncertain: 'Do not retry or delete this document until its state has been verified.',
  requires_review: 'This document requires further review.',
}
const actions = {
  none: 'No action recommended.', process: 'Processing may be needed.', reindex: 'Indexing may be needed.',
  refresh: 'Inspect again to check for an updated state.', contact_support: 'Contact support for review.',
}

export default function DocumentInspection({ document, onClose }) {
  const { logout } = useAuth()
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const request = useRef(null)
  const title = useRef(null)
  const inspect = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setResult(null); setError('')
    try {
      const data = await inspectDocumentOperation(document.id, controller.signal)
      if (!controller.signal.aborted) setResult({ data, receivedAt: new Date() })
    } catch (failure) {
      if (controller.signal.aborted) return
      if (failure.status === 401) logout()
      else setError(failure.message)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [document.id, logout])
  useEffect(() => {
    let mounted = true
    title.current?.focus()
    // Avoid sending the discarded first request during StrictMode's setup probe.
    queueMicrotask(() => { if (mounted) inspect() })
    return () => { mounted = false; request.current?.abort() }
  }, [inspect])
  return <section className="panel kb-editor document-inspection" aria-labelledby="inspection-title">
    <h2 id="inspection-title" ref={title} tabIndex={-1}>Document status inspection</h2>
    <p><strong>{document.filename}</strong></p>
    <p className="kb-muted">Read-only, point-in-time metadata snapshot. This does not verify live vectors, PDF availability, worker activity, or recovery eligibility.</p>
    {loading && <p role="status">Inspecting document status…</p>}
    {error && <p role="alert" className="auth-error">{error}</p>}
    {result && <>
      <p role="status">Inspection received at <time dateTime={result.receivedAt.toISOString()}>{result.receivedAt.toLocaleString()}</time> (your device time). State may have changed since this inspection.</p>
      <dl>
        <div><dt>Document state</dt><dd>{states[result.data.document_state]}</dd></div>
        <div><dt>Operation state</dt><dd>{operations[result.data.operation_state]}</dd></div>
        <div><dt>Attention required</dt><dd>{attention[result.data.attention]}</dd></div>
        <div><dt>Recommended next action</dt><dd>{actions[result.data.recommended_action]}</dd></div>
      </dl>
      <p className="kb-muted">Recommendations are advisory only. Inspection does not unlock, recover, or retry any operation.</p>
    </>}
    <div className="kb-actions">
      <button className="session-button" disabled={loading} onClick={inspect}>Inspect again</button>
      <button className="session-button" onClick={onClose}>Close inspection</button>
    </div>
  </section>
}
