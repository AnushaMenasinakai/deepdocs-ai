import { useId } from 'react'
import SupportingSources, { sourcePages } from './SupportingSources.jsx'

// Both current results and saved snapshots arrive through publicAskResult.
export default function AnswerContent({ result }) {
  const prefix = useId()
  const cited = result.citation_version === 1 && result.status === 'answered'
  const sourceId = id => `${prefix}-source-${id}`
  const sources = new Map(result.sources.map(source => [source.citation_id, source]))
  return <>
    {cited ? <div className="answer-claims">{result.claims.map((claim, index) =>
      <p className="answer-text" key={index}>{claim.text}{' '}
        {claim.citation_ids.map(id => {
          const source = sources.get(id)
          return <button type="button" className="citation-marker" key={id}
            aria-label={`Source ${id}: ${source.source_filename}, ${sourcePages(source)}`}
            aria-controls={sourceId(id)}
            onClick={() => document.getElementById(sourceId(id))?.focus()}>[{id}]</button>
        })}
      </p>)}</div> : <p className="answer-text">{result.answer}</p>}
    {!!result.sources.length && <SupportingSources sources={result.sources}
      citationVersion={result.citation_version} sourceId={sourceId} />}
  </>
}
