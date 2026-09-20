import { Link } from 'react-router-dom'
import Header from '../components/Header.jsx'
import Icon from '../components/Icon.jsx'
import BackendStatus from '../components/BackendStatus.jsx'
const sections = [
  { to: '/knowledge-bases', icon: 'library', title: 'Knowledge Bases', description: 'Give related documents a shared home. Build a foundation for organized knowledge.', action: 'Explore knowledge bases' },
  { to: '/documents', icon: 'document', title: 'Documents', description: 'A dedicated library for your source material, with everything in one place.', action: 'View document library' },
  { to: '/ask', icon: 'ask', title: 'Ask DeepDocs', description: 'A future space to ask questions and find answers grounded in your documents.', action: 'Preview Ask DeepDocs' },
]
export default function Dashboard() {
  return <><Header eyebrow="YOUR KNOWLEDGE WORKSPACE" title="Welcome to DeepDocs AI" description="A clearer way to organize documents and explore what they know." />
    <section className="welcome-panel"><div><span className="hero-label"><Icon name="spark" size={16} /> DOCUMENT INTELLIGENCE</span><h2>Your documents.<br />A foundation for better answers.</h2><p>DeepDocs AI is taking shape. Explore the workspace designed to bring your documents, knowledge, and questions together.</p><Link className="primary-link" to="/knowledge-bases">Explore your workspace <Icon name="arrow" size={18} /></Link></div><div className="document-art" aria-hidden="true"><div className="art-orbit" /><div className="art-sheet sheet-back" /><div className="art-sheet sheet-front"><Icon name="document" size={36} /><i /><i /><i /><span><Icon name="spark" size={20} /></span></div></div></section>
    <div className="section-heading"><h2>Explore your workspace</h2><span>Built around your documents</span></div>
    <div className="feature-grid">{sections.map(section => <Link className="panel feature-card" to={section.to} key={section.to}><span className={'feature-icon ' + section.icon}><Icon name={section.icon} /></span><h3>{section.title}</h3><p>{section.description}</p><span className="card-state">{section.icon === 'ask' ? 'Q&A coming in a future phase' : 'No content added yet'}</span><span className="card-link">{section.action}<Icon name="arrow" size={17} /></span></Link>)}</div><BackendStatus /></>
}
