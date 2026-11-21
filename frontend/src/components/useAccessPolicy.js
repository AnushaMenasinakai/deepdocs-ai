import { useEffect, useState } from 'react'
import { accessPolicy } from '../services/api.js'

export default function useAccessPolicy() {
  const [state, setState] = useState({ loading: true, publicRegistration: false })
  useEffect(() => {
    const controller = new AbortController()
    accessPolicy(controller.signal).then(value => {
      if (!controller.signal.aborted) setState({ loading: false, publicRegistration: value.public_registration === true })
    }).catch(() => {
      if (!controller.signal.aborted) setState({ loading: false, publicRegistration: false, unavailable: true })
    })
    return () => controller.abort()
  }, [])
  return state
}
