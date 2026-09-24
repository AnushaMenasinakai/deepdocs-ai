import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { register } from '../services/api.js'
import { useAuth } from '../context/AuthContext.jsx'
import { intendedDestination } from './AuthRoutes.jsx'
import Icon from './Icon.jsx'

export default function AuthForm({ mode }) {
  const isRegister = mode === 'register'
  const { authenticate } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const formRef = useRef(null)
  const requestRef = useRef(null)
  const [errors, setErrors] = useState({})
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  useEffect(() => () => requestRef.current?.abort(), [])

  async function submit(event) {
    event.preventDefault()
    if (requestRef.current) return
    const fields = new FormData(event.currentTarget)
    const name = String(fields.get('name') || '').trim()
    const email = String(fields.get('email') || '').trim()
    const password = String(fields.get('password') || '')
    const validation = {}
    if (isRegister && (!name || [...name].length > 100)) validation.name = 'Enter a name between 1 and 100 characters.'
    if (!email || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) validation.email = 'Enter a valid email address.'
    if ([...password].length < 8 || [...password].length > 128) validation.password = 'Use between 8 and 128 characters.'
    if (isRegister && password !== fields.get('confirmPassword')) validation.confirmPassword = 'Passwords do not match.'
    setErrors(validation)
    setMessage('')
    if (Object.keys(validation).length) {
      formRef.current.elements.namedItem(Object.keys(validation)[0]).focus()
      return
    }

    const controller = new AbortController()
    requestRef.current = controller
    setSubmitting(true)
    try {
      if (isRegister) {
        await register({ name, email, password }, controller.signal)
        if (!controller.signal.aborted) navigate('/login', { replace: true, state: { registered: true, from: intendedDestination(location.state?.from) } })
      } else {
        const currentUser = await authenticate({ email, password }, controller.signal)
        if (currentUser && !controller.signal.aborted) {
          navigate(intendedDestination(location.state?.from), { replace: true })
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) setMessage(error.message)
    } finally {
      if (!controller.signal.aborted) {
        requestRef.current = null
        setSubmitting(false)
      }
    }
  }

  function inputProps(field) {
    return {
      id: field,
      name: field,
      'aria-invalid': Boolean(errors[field]),
      'aria-describedby': [errors[field] ? field + '-error' : '', field === 'password' && isRegister ? 'password-hint' : ''].filter(Boolean).join(' ') || undefined,
    }
  }
  function fieldError(field) {
    return errors[field] ? <p className="auth-field-error" id={field + '-error'}>{errors[field]}</p> : null
  }

  return (
    <>
      {!isRegister && location.state?.registered && <p className="auth-success" role="status">Account created. Sign in to continue.</p>}
      <form ref={formRef} onSubmit={submit} noValidate aria-busy={submitting}>
        <div aria-live="polite" aria-atomic="true">
          {message && <p className="auth-error" role="alert">{message}</p>}
          {Object.keys(errors).length > 0 && <p className="auth-validation-summary">Please check the highlighted fields.</p>}
        </div>
        <fieldset disabled={submitting} className="auth-fields">
          <legend className="visually-hidden">{isRegister ? 'Registration details' : 'Login details'}</legend>
          {isRegister && <div className="auth-field">
            <label htmlFor="name">Name</label>
            <input {...inputProps('name')} type="text" autoComplete="name" required maxLength={100} placeholder="Your full name" />
            {fieldError('name')}
          </div>}
          <div className="auth-field">
            <label htmlFor="email">Email</label>
            <input {...inputProps('email')} type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" autoCapitalize="none" spellCheck={false} />
            {fieldError('email')}
          </div>
          <div className="auth-field">
            <label htmlFor="password">Password</label>
            <input {...inputProps('password')} type="password" autoComplete={isRegister ? 'new-password' : 'current-password'} required minLength={8} maxLength={128} placeholder={isRegister ? 'Create a password' : 'Enter your password'} />
            {isRegister && <p className="auth-hint" id="password-hint">Use 8–128 characters. No special combination required.</p>}
            {fieldError('password')}
          </div>
          {isRegister && <div className="auth-field">
            <label htmlFor="confirmPassword">Confirm password</label>
            <input {...inputProps('confirmPassword')} type="password" autoComplete="new-password" required maxLength={128} placeholder="Enter your password again" />
            {fieldError('confirmPassword')}
          </div>}
          <button className="auth-submit" type="submit" disabled={submitting}>
            {submitting ? (isRegister ? 'Creating account…' : 'Signing in…') : (isRegister ? 'Create account' : 'Sign in')}
            {!submitting && <Icon name="arrow" size={18} />}
          </button>
        </fieldset>
        <span className="visually-hidden" role="status">{submitting ? 'Please wait. Your request is being processed.' : ''}</span>
      </form>
      <p className="auth-switch">
        {isRegister ? 'Already have an account? ' : 'New to DeepDocs AI? '}
        <Link to={isRegister ? '/login' : '/register'} state={{ from: intendedDestination(location.state?.from) }}>{isRegister ? 'Sign in' : 'Create an account'}</Link>
      </p>
    </>
  )
}
