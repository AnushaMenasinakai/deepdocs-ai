import { useEffect, useId, useRef, useState } from 'react'
import Drawer from './Drawer.jsx'
import SupportingSources, { sourcePages } from './SupportingSources.jsx'

// Both current results and saved snapshots arrive through publicAskResult.
export default function AnswerContent({ result, historical = false }) {
  const prefix = useId()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const selectedSource = useRef(null)
  const wasOpen = useRef(false)
  wasOpen.current = open
  useEffect(() => () => {
    // The original citation can disappear on an answer/KB change. Do not steal
    // focus from another control or an overlay that has since opened.
    if (wasOpen.current) requestAnimationFrame(() => {
      if (!document.querySelector('dialog:modal') && document.activeElement === document.body) document.getElementById('ask-question')?.focus()
    })
  }, [])
  const cited = result.citation_version === 1 && result.status === 'answered'
  const sourceId = id => `${prefix}-source-${id}`
  const sources = new Map(result.sources.map(source => [source.citation_id, source]))
  function showSources(id = null) {
    if (document.querySelector('dialog:modal') || (id !== null && !sources.has(id))) return
    setSelected(id)
    setOpen(true)
  }
  return <>
    {cited ? <div className="answer-claims">{result.claims.map((claim, index) =>
      <p className="answer-text" key={index}>{claim.text}{' '}
        {claim.citation_ids.map(id => {
          const source = sources.get(id)
          if (!source) return null
          return <button type="button" className="citation-marker" key={id}
            aria-label={`Source ${id}: ${source.source_filename}, ${sourcePages(source)}`}
            aria-controls={sourceId(id)}
            aria-haspopup="dialog" onClick={() => showSources(id)}>[{id}]</button>
        })}
      </p>)}</div> : <p className="answer-text">{result.answer}</p>}
    {!!result.sources.length && <>
      <button className="session-button sources-trigger" type="button" aria-haspopup="dialog" aria-expanded={open} onClick={() => showSources()}>Supporting sources</button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Supporting sources" initialFocusRef={selectedSource}>
        {open && historical && <p className="kb-muted history-snapshot-note">Historical snapshot: these source documents may have changed or been deleted.</p>}
        <SupportingSources sources={result.sources} citationVersion={result.citation_version} sourceId={sourceId}
          selectedId={selected} selectedRef={selectedSource} />
      </Drawer>
    </>}
  </>
}
