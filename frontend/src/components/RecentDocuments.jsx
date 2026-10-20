import { Link } from 'react-router-dom'

const labels = { uploaded: 'Uploaded', processing: 'Processing', processed: 'Processed', failed: 'Failed' }

export default function RecentDocuments({ documents, loading, error, emptyMessage = 'No recent documents to display.' }) {
  return <section className="panel dashboard-documents" aria-labelledby="recent-documents-title">
    <h2 id="recent-documents-title">Recent Documents</h2>
    {loading ? <p className="kb-muted" role="status">Loading recent documents…</p>
      : error ? <p className="auth-error" role="alert">{error}</p>
      : documents.length === 0 ? <p className="kb-muted">{emptyMessage}</p>
      : <ul>{documents.map(item => {
        // Match the Documents page: failure takes precedence over indexed metadata.
        const status = item.failed ? 'Failed' : item.indexed ? 'Indexed' : labels[item.status]
        return <li key={item.id}>
          <div className="dashboard-document-info">
            <h3>{item.filename}</h3>
            <p>{item.knowledge_base_name && <span>{item.knowledge_base_name} · </span>}Uploaded <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p>
          </div>
          <span className={'dashboard-document-status ' + status.toLowerCase()}>{status}</span>
          <Link className="session-button" to={'/documents?kb=' + encodeURIComponent(item.knowledge_base_id)} aria-label={'View Documents in Knowledge Base for ' + item.filename}>View Documents</Link>
        </li>
      })}</ul>}
  </section>
}
