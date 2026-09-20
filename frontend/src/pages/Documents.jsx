import Header from '../components/Header.jsx'
import EmptyState from '../components/EmptyState.jsx'
export default function Documents() {
  return <><Header eyebrow="YOUR SOURCE OF KNOWLEDGE" title="Documents" description="A central home for the documents behind your knowledge." /><EmptyState icon="document" title="Your document library is waiting" description="This will be the place to browse and organize your source documents. No documents have been added to this workspace yet." note="Document uploads and processing are not available in this phase." /><div className="context-note"><h2>From source material to understanding</h2><p>Your library will provide the source material for knowledge collections and document-based answers as DeepDocs AI develops.</p></div></>
}
