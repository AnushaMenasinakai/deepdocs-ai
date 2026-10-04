import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import BackendStatus from '../components/BackendStatus.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { getDashboardSummary } from '../services/api.js'

const stats = [['knowledge_base_count', 'Knowledge Bases', 'library'], ['document_count', 'Documents', 'document'], ['indexed_document_count', 'Indexed documents', 'spark'], ['ask_history_count', 'Saved questions', 'ask']]
export default function Dashboard() {
  const { logout } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const request = useRef(null)
  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError('')
    try {
      const summary = await getDashboardSummary(controller.signal)
      if (!controller.signal.aborted) setData(summary)
    } catch (failure) {
      if (controller.signal.aborted) return
      if (failure.status === 401) logout()
      else setError(failure.message)
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }, [logout])
  useEffect(() => { load(); return () => request.current?.abort() }, [load])
  return <>
    <Header eyebrow="YOUR KNOWLEDGE WORKSPACE" title="Dashboard" description="Overview of your DeepDocs workspace." />
    <div className="dashboard-section-heading"><h2>Workspace overview</h2><button className="session-button" onClick={load} disabled={loading}>Refresh overview</button></div>
    {loading ? <section role="status" aria-label="Loading workspace overview"><p className="visually-hidden">Loading workspace overview…</p><div className="dashboard-stats" aria-hidden="true">{stats.map(([key]) => <div className="panel dashboard-stat dashboard-skeleton" key={key}><span /><span /></div>)}</div></section>
      : error ? <section className="panel kb-state" role="alert"><p>{error}</p><button className="session-button" onClick={load}>Retry overview</button></section>
      : data && <>
        <dl className="dashboard-stats">{stats.map(([key, label, icon]) => <div className="panel dashboard-stat" key={key}><dt><Icon name={icon} size={20} />{label}</dt><dd>{data[key].toLocaleString()}</dd></div>)}</dl>
        <p className="dashboard-count-note">{data.processing_document_count} processing · {data.failed_document_count} failed. Indexed counts reflect the current generation’s last confirmed synchronization, not a live vector audit. Saved questions include insufficient-context results.</p>
        {data.knowledge_base_count === 0 && data.document_count === 0 && data.ask_history_count === 0 && <section className="panel dashboard-onboarding"><span className="feature-icon library"><Icon name="library" /></span><div><h2>Create your first Knowledge Base</h2><p>Start organizing your PDFs, then process and index them to ask grounded questions.</p><Link className="primary-link" to="/knowledge-bases">Open Knowledge Bases <Icon name="arrow" size={17} /></Link></div></section>}
        <section className="panel dashboard-recent" aria-labelledby="recent-kb-title"><div className="dashboard-section-heading"><h2 id="recent-kb-title">Recent Knowledge Bases</h2><Link className="dashboard-text-link" to="/knowledge-bases">Manage Knowledge Bases</Link></div><p className="kb-muted">Latest Knowledge Base edits. Document activity does not change this date.</p>
          {!data.recent_knowledge_bases.length ? <p className="dashboard-recent-empty">No Knowledge Bases yet. Create one to give related documents a home.</p> : <ul>{data.recent_knowledge_bases.map(base => <li key={base.id}><div><h3>{base.name}</h3><p>{base.document_count} {base.document_count === 1 ? 'document' : 'documents'} · Edited <time dateTime={base.updated_at}>{new Date(base.updated_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</time></p></div><Link className="session-button" to="/knowledge-bases" aria-label={'Manage Knowledge Bases for ' + base.name}>Manage <Icon name="arrow" size={15} /></Link></li>)}</ul>}
        </section>
      </>}
    <div className="dashboard-bottom"><BackendStatus /><section className="panel dashboard-actions" aria-labelledby="quick-actions-title"><h2 id="quick-actions-title">Quick actions</h2><p className="kb-muted">Continue working with your documents.</p><nav aria-label="Quick actions"><Link to="/knowledge-bases"><Icon name="library" />Manage Knowledge Bases<Icon name="arrow" size={17} /></Link><Link to="/documents"><Icon name="document" />View Documents<Icon name="arrow" size={17} /></Link><Link to="/ask"><Icon name="ask" />Ask DeepDocs<Icon name="arrow" size={17} /></Link></nav></section></div>
  </>
}
