import { useId } from 'react'
import Icon from './Icon.jsx'

export default function SupportingSources({ sources }) {
  const titleId = useId()
  if (!sources.length) return null
  return <section className="supporting-sources" aria-labelledby={titleId}>
    <h3 id={titleId}>Supporting sources</h3>
    <p className="kb-muted">These pages were supplied as context for the answer, not matched to individual sentences.</p>
    <ul className="source-grid">
      {sources.map(source => <li className="source-card" key={JSON.stringify([source.document_id, source.page_start, source.page_end])}>
        <Icon name="document" />
        <div><p className="source-filename">{source.source_filename}</p>
          <p className="source-pages">{source.page_start === source.page_end ? `Page ${source.page_start}` : `Pages ${source.page_start}–${source.page_end}`}</p>
        </div>
      </li>)}
    </ul>
  </section>
}
