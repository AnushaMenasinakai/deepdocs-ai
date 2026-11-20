import AuthLayout from '../components/AuthLayout.jsx'
import AuthForm from '../components/AuthForm.jsx'
import useAccessPolicy from '../components/useAccessPolicy.js'

export default function Register() {
  const policy = useAccessPolicy()
  if (!policy.publicRegistration) return <AuthLayout title="Private access" description="Access requires operator approval."><p role="status">{policy.loading ? 'Checking registration availability…' : policy.unavailable ? 'Registration availability could not be checked. Please try again later.' : 'Public registration is disabled. Contact the operator for an account.'}</p></AuthLayout>
  return <AuthLayout title="Create your account" description="Start your journey with DeepDocs AI."><AuthForm mode="register" /></AuthLayout>
}
