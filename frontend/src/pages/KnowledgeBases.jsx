import Header from '../components/Header.jsx'
import EmptyState from '../components/EmptyState.jsx'
export default function KnowledgeBases() {
  return <><Header eyebrow="ORGANIZE YOUR KNOWLEDGE" title="Knowledge Bases" description="Keep related documents together, with room for every topic and project." /><EmptyState icon="library" title="Your knowledge starts with a collection" description="Knowledge bases will organize related documents into focused collections, helping you keep source material in context." note="Knowledge base creation and saving will be available in a future phase." /><div className="context-note"><h2>A place for the bigger picture</h2><p>Think project references, research collections, or team documentation. This workspace is ready for the next stage; no knowledge bases have been created here.</p></div></>
}
