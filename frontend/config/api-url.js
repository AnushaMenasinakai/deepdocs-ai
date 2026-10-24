const ERROR = 'Invalid VITE_API_URL: builds require an explicit HTTPS API origin (HTTP is allowed only for loopback testing), with no credentials, path, query, fragment or wildcard.'

export function resolveApiUrl(value, production) {
  if (value === undefined || (typeof value === 'string' && !value.trim())) {
    if (!production) return 'http://127.0.0.1:8000'
    throw new Error(ERROR)
  }
  try {
    // Validate before URL parsing can repair malformed or ambiguous syntax.
    const match = /^(https?):\/\/(\[[0-9a-f:]+\]|[a-z0-9.-]+)(?::([0-9]+))?\/?$/i.exec(value.trim())
    if (!match || (match[3] && (+match[3] < 1 || +match[3] > 65535))) throw new Error()
    const url = new URL(value.trim())
    const host = url.hostname
    if (!host.startsWith('[') && (host.length > 253 || host.split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)))) throw new Error()
    // Reject numeric shorthand/octal IPv4 representations normalized by URL.
    if (!match[2].startsWith('[') && host !== match[2].toLowerCase()) throw new Error()
    const loopback = host === 'localhost' || host === '127.0.0.1' || host === '[::1]'
    if (production && url.protocol === 'http:' && !loopback) throw new Error()
    return url.origin
  } catch {
    throw new Error(ERROR)
  }
}
