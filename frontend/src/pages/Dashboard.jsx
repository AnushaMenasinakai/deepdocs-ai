import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import RecentDocuments from '../components/RecentDocuments.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { getDashboardSummary, getRecentDocuments } from '../services/api.js'

const stats = [['knowledge_base_count', 'Knowledge Bases', 'library'], ['document_count', 'Documents', 'document'], ['indexed_document_count', 'Indexed documents', 'spark'], ['ask_history_count', 'Saved questions', 'ask']]
export default function Dashboard() {
  const { logout } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [documents, setDocuments] = useState([])
  const [documentsLoading, setDocumentsLoading] = useState(true)
  const [documentsError, setDocumentsError] = useState('')
  const request = useRef(null)
  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError('')
    setDocumentsLoading(true); setDocumentsError('')
    await Promise.all([(async () => {
      try {
        const summary = await getDashboardSummary(controller.signal)
        if (!controller.signal.aborted) setData(summary)
      } catch (failure) {
        if (controller.signal.aborted) return
        if (failure.status === 401) logout()
        else setError(failure.message)
      } finally { if (!controller.signal.aborted) setLoading(false) }
    })(), (async () => {
      try {
        const recent = await getRecentDocuments(controller.signal)
        if (!controller.signal.aborted) setDocuments(recent)
      } catch (failure) {
        if (controller.signal.aborted) return
        if (failure.status === 401) logout()
        else setDocumentsError(failure.message)
      } finally { if (!controller.signal.aborted) setDocumentsLoading(false) }
    })()])
  }, [logout])
  useEffect(() => { load(); return () => request.current?.abort() }, [load])
  return <div className="dashboard-page">
    <Header eyebrow="YOUR KNOWLEDGE WORKSPACE" title="Welcome back" description="Keep track of your knowledge, documents and saved questions." />
    <div className="dashboard-section-heading"><h2>Workspace overview</h2><button className="session-button" onClick={load} disabled={loading}>Refresh overview</button></div>
    {loading ? <section role="status" aria-label="Loading workspace overview"><p className="visually-hidden">Loading workspace overview…</p><div className="dashboard-stats" aria-hidden="true">{stats.map(([key]) => <div className="panel dashboard-stat dashboard-skeleton" key={key}><span /><span /></div>)}</div></section>
      : error ? <section className="panel kb-state" role="alert"><p>{error}</p><button className="session-button" onClick={load}>Retry overview</button></section>
      : data && <>
        <dl className="dashboard-stats">{stats.map(([key, label, icon]) => <div className="panel dashboard-stat" key={key}><dt><Icon name={icon} size={20} />{label}</dt><dd>{data[key].toLocaleString()}</dd></div>)}</dl>
        <p className="dashboard-count-note">{data.processing_document_count} processing · {data.failed_document_count} failed. Indexed counts reflect the current generation’s last confirmed synchronization, not a live vector audit. Saved questions include insufficient-context results.</p>
        {data.knowledge_base_count === 0 && <section className="panel dashboard-onboarding" aria-labelledby="onboarding-title">
          <svg className="dashboard-onboarding-art" viewBox="0 0 180 140" aria-hidden="true" focusable="false" fill="none">
            <rect x="38" y="12" width="78" height="100" rx="8" fill="var(--surface-subtle)" stroke="var(--border)" />
            <rect x="52" y="22" width="78" height="100" rx="8" fill="var(--surface)" stroke="var(--accent-border)" />
            <path d="M66 42h42M66 54h28M66 66h36" stroke="var(--text-muted)" strokeWidth="3" strokeLinecap="round" />
            <path d="M22 76h42l10 10h72v39a7 7 0 0 1-7 7H29a7 7 0 0 1-7-7Z" fill="var(--accent-surface)" stroke="var(--accent-border)" strokeWidth="2" />
            <circle cx="132" cy="60" r="21" fill="var(--surface-subtle)" stroke="var(--accent-text)" strokeWidth="3" />
            <path d="m148 76 15 15M123 60h18M132 51v18" stroke="var(--accent-text)" strokeWidth="3" strokeLinecap="round" />
          </svg>
          <div><h2 id="onboarding-title">Your knowledge workspace starts here</h2><p>Create your first Knowledge Base to organize documents and start asking grounded questions.</p><Link className="primary-link" to="/knowledge-bases">Create Knowledge Base <Icon name="arrow" size={17} /></Link></div>
        </section>}
      </>}
    <RecentDocuments documents={documents} loading={documentsLoading} error={documentsError} />
    {!loading && !error && data && <section className="panel dashboard-recent" aria-labelledby="recent-kb-title"><div className="dashboard-section-heading"><h2 id="recent-kb-title">Recent Knowledge Bases</h2><Link className="dashboard-text-link" to="/knowledge-bases">Manage Knowledge Bases</Link></div><p className="kb-muted">Latest Knowledge Base edits. Document activity does not change this date.</p>
          {!data.recent_knowledge_bases.length ? <p className="dashboard-recent-empty">No Knowledge Bases yet. Create one to give related documents a home.</p> : <ul>{data.recent_knowledge_bases.slice(0, 3).map(base => <li key={base.id}><div><h3>{base.name}</h3><p>{base.document_count} {base.document_count === 1 ? 'document' : 'documents'} · Edited <time dateTime={base.updated_at}>{new Date(base.updated_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p></div><Link className="session-button" to="/knowledge-bases" aria-label={'Manage Knowledge Bases for ' + base.name}>Manage <Icon name="arrow" size={15} /></Link></li>)}</ul>}
        </section>}
    <div className="dashboard-bottom"><section className="panel dashboard-actions" aria-labelledby="quick-actions-title"><h2 id="quick-actions-title">Quick actions</h2><p className="kb-muted">Continue working with your documents.</p><nav aria-label="Quick actions"><Link to="/knowledge-bases" aria-labelledby="quick-bases-title" aria-describedby="quick-bases-description"><Icon name="library" /><span className="dashboard-action-copy"><span className="dashboard-action-title" id="quick-bases-title">Manage Knowledge Bases</span><span className="dashboard-action-description" id="quick-bases-description">Organize your collections</span></span><Icon name="arrow" size={17} /></Link><Link to="/documents" aria-labelledby="quick-documents-title" aria-describedby="quick-documents-description"><Icon name="document" /><span className="dashboard-action-copy"><span className="dashboard-action-title" id="quick-documents-title">View Documents</span><span className="dashboard-action-description" id="quick-documents-description">Browse uploaded files</span></span><Icon name="arrow" size={17} /></Link><Link to="/ask" aria-labelledby="quick-ask-title" aria-describedby="quick-ask-description"><Icon name="ask" /><span className="dashboard-action-copy"><span className="dashboard-action-title" id="quick-ask-title">Ask DeepDocs</span><span className="dashboard-action-description" id="quick-ask-description">Ask grounded questions</span></span><Icon name="arrow" size={17} /></Link></nav></section></div>
  </div>
}
