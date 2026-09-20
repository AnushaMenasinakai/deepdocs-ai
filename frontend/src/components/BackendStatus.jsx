import { useEffect, useState } from 'react'
export default function BackendStatus() {
  const [status, setStatus] = useState('checking')
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const timeout = setTimeout(() => controller.abort(), 5000)
    async function checkHealth() {
      try {
        const response = await fetch('http://127.0.0.1:8000/api/health', { signal: controller.signal })
        if (!response.ok) throw new Error('Health request failed')
        const data = await response.json()
        if (data.status !== 'ok') throw new Error('API is not healthy')
        if (active) setStatus('connected')
      } catch {
        if (active) setStatus('unavailable')
      } finally { clearTimeout(timeout) }
    }
    checkHealth()
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [])
  return <section className="panel backend-panel" aria-label="Backend connectivity"><div><h2>Backend connection</h2><p>{status === 'connected' ? 'The health endpoint responded successfully.' : status === 'checking' ? 'Checking the local API connection…' : 'The backend is unavailable. You can still explore the workspace. Refresh to check again.'}</p></div><span className={'connection-status ' + status} role="status"><span />{status === 'connected' ? 'Connected' : status === 'checking' ? 'Checking…' : 'Unavailable'}</span></section>
}
