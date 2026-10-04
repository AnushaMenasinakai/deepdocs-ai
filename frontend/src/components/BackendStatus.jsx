import { useCallback, useEffect, useRef, useState } from 'react'
import { getServiceHealth } from '../services/api.js'

const services = [['api', 'API'], ['database', 'Database'], ['vector', 'Vector store']]
export default function BackendStatus() {
  const [states, setStates] = useState({ api: 'checking', database: 'checking', vector: 'checking' })
  const request = useRef(null)
  const check = useCallback(() => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setStates({ api: 'checking', database: 'checking', vector: 'checking' })
    services.forEach(async ([key]) => {
      const status = await getServiceHealth(key, controller.signal)
      if (!controller.signal.aborted) setStates(current => ({ ...current, [key]: status }))
    })
  }, [])
  useEffect(() => { check(); return () => request.current?.abort() }, [check])
  return <section className="panel dashboard-health" aria-labelledby="system-status-title">
    <div className="dashboard-section-heading"><h2 id="system-status-title">System status</h2><button className="session-button" onClick={check} disabled={Object.values(states).includes('checking')}>Refresh status</button></div>
    <p className="kb-muted">Connectivity checks for this visit, independent of workspace counts.</p>
    <ul>{services.map(([key, label]) => <li key={key}><span>{label}</span><span className={'connection-status ' + (states[key] === 'operational' ? 'connected' : states[key])} role="status" aria-label={label + ': ' + states[key]}><span aria-hidden="true" />{({ operational: 'Operational', unavailable: 'Unavailable', checking: 'Checking' })[states[key]]}</span></li>)}</ul>
  </section>
}
