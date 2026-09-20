import Icon from './Icon.jsx'
export default function EmptyState({ icon, title, description, note }) {
  return <section className="panel empty-state"><span className="empty-icon"><Icon name={icon} size={32} /></span><span className="small-label">READY FOR WHAT’S NEXT</span><h2>{title}</h2><p>{description}</p><div className="availability-note">{note}</div></section>
}
