import { useEffect, useRef } from 'react'

const reasons = {
  not_found: 'This document is no longer available in this Knowledge Base.',
  busy: 'Another operation is using this document. It was not changed by this request.',
  requires_processing: 'This document must be processed before it can be re-indexed.',
  service_unavailable: 'The operation could not be confirmed. Cleanup or indexing may be incomplete. Refresh before deciding whether to retry.',
}

export default function DocumentBulk({ count, total, disabled, onSelectPage, onClear, onOpen, confirmation, onCancel, onConfirm, busy, result, error }) {
  const pageCheckbox = useRef(null)
  useEffect(() => {
    if (pageCheckbox.current) pageCheckbox.current.indeterminate = count > 0 && count < total
  }, [count, total])
  return <>
    <section className="document-bulk-controls" aria-label="Page selection">
      <label className="document-check"><input ref={pageCheckbox} type="checkbox" checked={total > 0 && count === total}
        disabled={disabled || total === 0} onChange={event => onSelectPage(event.target.checked)} />Select current page</label>
      <span className="kb-muted">Only documents on this page are selected.</span>
      {count > 0 && <div className="panel document-bulk-bar">
        <p role="status" aria-live="polite">{count} {count === 1 ? 'document' : 'documents'} selected</p>
        <div className="kb-actions">
          <button className="session-button" disabled={disabled || count > 5} aria-describedby="reindex-limit" onClick={event => onOpen('reindex', event)}>Re-index selected</button>
          <button className="session-button kb-delete-link" disabled={disabled} onClick={event => onOpen('delete', event)}>Delete selected</button>
          <button className="session-button" disabled={disabled} onClick={onClear}>Clear selection</button>
        </div>
        <p id="reindex-limit" className="kb-muted">Re-index at most 5 documents at once. Uses existing processed chunks; PDFs are not processed or extracted.</p>
      </div>}
    </section>
    {confirmation && <section className="panel kb-editor kb-delete" aria-labelledby="bulk-confirm-title">
      <h2 id="bulk-confirm-title">{confirmation.operation === 'delete' ? 'Delete selected documents?' : 'Re-index selected documents?'}</h2>
      <p>{confirmation.operation === 'delete'
        ? `Permanently delete ${confirmation.items.length} selected document(s), including their PDFs, chunks, and vectors? This cannot be undone.`
        : `Re-index ${confirmation.items.length} selected document(s) using existing processed chunks? This can take time and temporarily make documents unavailable to retrieval. Unprocessed documents require processing first.`}</p>
      <div className="kb-actions">
        <button autoFocus className="session-button" disabled={busy} onClick={onCancel}>Cancel bulk action</button>
        <button className={confirmation.operation === 'delete' ? 'kb-danger' : 'kb-primary'} disabled={busy} onClick={onConfirm}>
          {busy ? 'Working…' : confirmation.operation === 'delete' ? 'Confirm bulk deletion' : 'Confirm bulk re-index'}</button>
      </div>
      {busy && <p role="status">Processing the selected documents. Leaving this page does not undo completed work.</p>}
    </section>}
    {error && <p className="auth-error" role="alert">{error}</p>}
    {result && <section className="panel document-bulk-result" aria-label="Bulk operation result">
      <p role="status" aria-live="polite">{result.failed === 0 && result.not_attempted === 0
        ? `${result.succeeded} document(s) ${result.operation === 'delete' ? 'deleted' : 're-indexed'} successfully.`
        : `${result.operation === 'delete' ? 'Bulk deletion' : 'Bulk re-index'}: ${result.succeeded} succeeded, ${result.failed} failed${result.not_attempted ? `, ${result.not_attempted} not attempted` : ''}.`}</p>
      {result.results.some(item => item.outcome !== 'succeeded') && <ul>{result.results.filter(item => item.outcome !== 'succeeded').map((item, index) =>
        <li key={index}><strong>{item.filename}</strong>: {item.outcome === 'not_attempted' ? 'Not attempted because the batch stopped after a service failure.' : reasons[item.code]}</li>)}</ul>}
      {(result.failed > 0 || result.not_attempted > 0) && <p className="kb-muted">No automatic retries were made. Review refreshed document states before selecting items again.</p>}
    </section>}
  </>
}
