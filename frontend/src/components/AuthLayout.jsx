import { Link } from 'react-router-dom'
import Icon from './Icon.jsx'

export default function AuthLayout({ title, description, children }) {
  return (
    <div className="auth-shell">
      <section className="auth-story" aria-label="About DeepDocs AI">
        <Link className="brand" to="/" aria-label="DeepDocs AI home">
          <span className="brand-mark"><Icon name="document" /></span>
          DeepDocs <span className="brand-ai">AI</span>
        </Link>
        <div className="auth-story-content">
          <span className="hero-label"><Icon name="spark" size={17} /> YOUR KNOWLEDGE WORKSPACE</span>
          <h2>Your documents.<br />A clearer perspective.</h2>
          <p>A thoughtfully organized space for your documents, knowledge, and the questions that come next.</p>
          <div className="auth-illustration" aria-hidden="true">
            <div className="auth-orbit" />
            <div className="auth-document"><Icon name="document" size={42} /><i /><i /><i /><span><Icon name="spark" size={24} /></span></div>
            <span className="auth-floating-icon"><Icon name="library" size={26} /></span>
          </div>
          <div className="auth-story-note"><Icon name="library" size={19} /><span>A foundation for document intelligence.</span></div>
        </div>
        <p className="auth-story-footer">DeepDocs AI · Built around your knowledge</p>
      </section>
      <main className="auth-main">
        <Link className="auth-back" to="/">← Back to workspace</Link>
        <section className="auth-card" aria-labelledby="auth-title">
          <span className="auth-card-icon"><Icon name="document" size={25} /></span>
          <h1 id="auth-title">{title}</h1>
          <p className="auth-description">{description}</p>
          {children}
        </section>
        <p className="auth-footer">Your next chapter starts with what you know.</p>
      </main>
    </div>
  )
}
