import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import SupportingSources from '../components/SupportingSources.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { askKnowledgeBase, listKnowledgeBases } from '../services/api.js'

function QuestionForm({ base, refreshBases }) {
  const { logout } = useAuth()
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = useRef(null)
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => { active.current = false; pending.current?.abort() }
  }, [])
  const valid = question.trim().length > 0 && question.length <= 1000
  async function submit(event) {
    event.preventDefault()
    if (!valid || pending.current || missing) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true); setError(''); setResult(null)
    try {
      const answer = await askKnowledgeBase(base.id, question, controller.signal)
      if (active.current && !controller.signal.aborted) setResult(answer)
    } catch (failure) {
      if (!active.current || controller.signal.aborted) return
      if (failure.status === 401) logout()
      else { setError(failure.message); setMissing(failure.status === 404) }
    } finally {
      if (active.current && !controller.signal.aborted) { pending.current = null; setBusy(false) }
    }
  }
  return <div className="ask-workspace">
    <section className="panel ask-composer">
      <div className="ask-composer-heading"><span className="empty-icon"><Icon name="ask" /></span>
        <div><h2>Explore your documents</h2><p className="kb-muted">Ask about indexed PDFs in {base.name}.</p></div>
      </div>
      <form onSubmit={submit}>
        <label htmlFor="ask-question">Your question</label>
        <textarea id="ask-question" value={question} maxLength={1000} required disabled={busy || missing}
          placeholder="What would you like to understand?" aria-describedby="ask-hint ask-count"
          onChange={event => { setQuestion(event.target.value); setResult(null); setError('') }} />
        <div className="ask-input-footer"><p id="ask-hint">One question at a time. Answers use your retrieved document context.</p>
          <span id="ask-count">{question.length}/1,000</span></div>
        <button className="kb-primary" type="submit" disabled={!valid || busy || missing}>{busy ? 'Finding an answer…' : 'Ask DeepDocs'}</button>
      </form>
    </section>
    <div aria-live="polite" aria-atomic="true">
      {busy && <section className="panel ask-loading" role="status">
        <span className="ask-loading-icon" aria-hidden="true"><Icon name="spark" size={24} /></span>
        <h2>Finding your answer</h2>
        <p>Searching your documents and preparing a grounded response...</p>
        <span className="ask-loading-dots" aria-hidden="true"><span /><span /><span /></span>
      </section>}
      {error && <div className="panel kb-state" role="alert"><p>{error}</p>{missing && <button className="session-button" onClick={refreshBases}>Refresh Knowledge Bases</button>}</div>}
      {result && <section className="panel ask-answer" aria-labelledby="answer-title">
        <p className="eyebrow">{result.status === 'answered' ? 'FROM YOUR DOCUMENTS' : 'MORE CONTEXT NEEDED'}</p>
        <h2 id="answer-title">{result.status === 'answered' ? 'Answer' : 'Not enough relevant information'}</h2>
        <p className="answer-text">{result.answer}</p>
        {result.status === 'answered' && <SupportingSources sources={result.sources} />}
      </section>}
    </div>
  </div>
}

export default function AskDeepDocs() {
  const { logout } = useAuth()
  const [bases, setBases] = useState([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const request = useRef(null)
  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError('')
    try {
      const values = await listKnowledgeBases(controller.signal)
      if (controller.signal.aborted) return
      if (!Array.isArray(values) || values.some(base => !base || typeof base.id !== 'string' || !base.id || typeof base.name !== 'string')) {
        throw new Error('We could not load your Knowledge Bases. Please try again.')
      }
      setBases(values)
      setSelected(current => values.some(base => base.id === current) ? current : values[0]?.id || '')
    } catch (failure) {
      if (controller.signal.aborted) return
      if (failure.status === 401) logout()
      else setError(failure.message)
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }, [logout])
  useEffect(() => { load(); return () => request.current?.abort() }, [load])
  const base = bases.find(item => item.id === selected)
  return <>
    <Header eyebrow="FROM DOCUMENTS TO UNDERSTANDING" title="Ask DeepDocs" description="Ask questions about your indexed Knowledge Base documents, with supporting PDF pages for reference." />
    {loading ? <section className="panel kb-state" role="status">Loading Knowledge Bases…</section>
      : error ? <section className="panel kb-state" role="alert"><p>{error}</p><button className="session-button" onClick={load}>Try again</button></section>
      : !bases.length ? <section className="panel empty-state"><span className="empty-icon"><Icon name="library" size={32} /></span><h2>Create a Knowledge Base first</h2><p>Add your PDFs to a Knowledge Base, then process and index them before asking questions.</p><Link className="primary-link" to="/knowledge-bases">Go to Knowledge Bases <Icon name="arrow" size={17} /></Link></section>
      : <><div className="document-selector"><label htmlFor="ask-base">Knowledge Base</label><select id="ask-base" value={selected} onChange={event => setSelected(event.target.value)}>{bases.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
        {base && <QuestionForm key={base.id} base={base} refreshBases={load} />}</>}
  </>
}
