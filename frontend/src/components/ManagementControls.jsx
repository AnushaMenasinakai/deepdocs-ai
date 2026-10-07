import { useEffect, useMemo, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export function useManagementQuery(defaultSort, sorts, documents = false) {
  const [url, setUrl] = useSearchParams()
  const text = url.toString()
  const urlSearch = (url.get('search') || '').slice(0, 200)
  const [search, setSearch] = useState(urlSearch)
  useEffect(() => setSearch(urlSearch), [urlSearch])
  const change = useCallback((values, replace = false) => {
    setUrl(current => {
      const next = new URLSearchParams(current)
      Object.entries(values).forEach(([key, value]) => {
        if (value === '' || value === null) next.delete(key)
        else next.set(key, String(value))
      })
      return next
    }, { replace })
  }, [setUrl])
  useEffect(() => {
    if (search.trim() === urlSearch.trim()) return
    const timer = setTimeout(() => change({ search: search.trim(), page: 1 }), 300)
    return () => clearTimeout(timer)
  }, [search, urlSearch, change])
  const query = useMemo(() => {
    const params = new URLSearchParams(text)
    const integer = (key, fallback, max) => /^\d+$/.test(params.get(key) || '') && Number(params.get(key)) >= 1 && Number(params.get(key)) <= max ? Number(params.get(key)) : fallback
    return { search: (params.get('search') || '').trim().slice(0, 200), page: integer('page', 1, 1000000), limit: integer('limit', 20, 100),
      sort: sorts.includes(params.get('sort')) ? params.get('sort') : defaultSort,
      order: ['asc', 'desc'].includes(params.get('order')) ? params.get('order') : 'desc',
      ...(documents ? { status: ['indexed', 'processing', 'failed'].includes(params.get('status')) ? params.get('status') : 'all' } : {}) }
  }, [text, defaultSort, sorts, documents])
  const key = JSON.stringify(query)
  const stableQuery = useMemo(() => JSON.parse(key), [key])
  return { query: stableQuery, search, setSearch, waiting: search.trim() !== query.search, change,
    clear: () => { setSearch(''); change({ search: '', status: '', page: 1 }) } }
}

export function ManagementControls({ state, documents = false, disabled = false }) {
  const { query, search, setSearch, change, clear } = state
  const prefix = documents ? 'document' : 'kb'
  return <section className="panel management-controls" aria-label={documents ? 'Document browsing controls' : 'Knowledge Base browsing controls'}>
    <div className="management-search"><label htmlFor={prefix + '-search'}>{documents ? 'Search filenames' : 'Search Knowledge Bases'}</label>
      <input id={prefix + '-search'} type="search" maxLength={200} value={search} disabled={disabled} onChange={event => setSearch(event.target.value)} placeholder={documents ? 'Find a PDF by filename' : 'Find a collection by name'} /></div>
    {documents && <div><label htmlFor="document-filter">Status</label><select id="document-filter" value={query.status} disabled={disabled} onChange={event => change({ status: event.target.value, page: 1 })}>
      <option value="all">All</option><option value="indexed">Indexed</option><option value="processing">Processing</option><option value="failed">Failed</option>
    </select></div>}
    <div><label htmlFor={prefix + '-sort'}>Sort by</label><select id={prefix + '-sort'} value={query.sort} disabled={disabled} onChange={event => change({ sort: event.target.value, page: 1 })}>
      <option value="updated_at">Last updated</option><option value="created_at">Date created</option><option value={documents ? 'filename' : 'name'}>{documents ? 'Filename' : 'Name'}</option>
    </select></div>
    <div><label htmlFor={prefix + '-order'}>Order</label><select id={prefix + '-order'} value={query.order} disabled={disabled} onChange={event => change({ order: event.target.value, page: 1 })}>
      <option value="desc">Descending</option><option value="asc">Ascending</option>
    </select></div>
    <button className="session-button" disabled={disabled || (!search && (!documents || query.status === 'all'))} onClick={clear}>Clear filters</button>
  </section>
}

export function ManagementPagination({ meta, loading, change, disabled }) {
  return <nav className="management-pagination" aria-label="Results pages">
    <p role="status" aria-live="polite">{loading ? 'Updating results…' : `${meta.total} results · Page ${meta.page} of ${Math.max(1, meta.total_pages)}`}</p>
    <div className="kb-actions"><button className="session-button" disabled={disabled || loading || meta.page <= 1} onClick={() => change({ page: meta.page - 1 })}>Previous</button>
      <button className="session-button" disabled={disabled || loading || meta.page >= meta.total_pages} onClick={() => change({ page: meta.page + 1 })}>Next</button></div>
  </nav>
}
