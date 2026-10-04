import { useId } from 'react'
import Icon from './Icon.jsx'

export const sourcePages = source => source.page_start === source.page_end
  ? `Page ${source.page_start}` : `Pages ${source.page_start}–${source.page_end}`

export default function SupportingSources({ sources, citationVersion = 0, sourceId }) {
  const titleId = useId()
  if (!sources.length) return null
  return <section className="supporting-sources" aria-labelledby={titleId}>
    <h3 id={titleId}>Supporting sources</h3>
    <p className="kb-muted">{citationVersion === 1
      ? 'References link claims to supplied pages selected by the model. They do not independently verify that each claim is correct.'
      : 'These pages were supplied as context for the answer, not matched to individual sentences.'}</p>
    <ul className="source-grid">
      {sources.map(source => <li className="source-card" id={citationVersion === 1 ? sourceId(source.citation_id) : undefined}
        tabIndex={citationVersion === 1 ? -1 : undefined} key={JSON.stringify([source.document_id, source.page_start, source.page_end])}>
        {citationVersion === 1 ? <span className="source-number" aria-label={`Source ${source.citation_id}`}>[{source.citation_id}]</span> : <Icon name="document" />}
        <div><p className="source-filename">{source.source_filename}</p>
          <p className="source-pages">{sourcePages(source)}</p>
        </div>
      </li>)}
    </ul>
  </section>
}
