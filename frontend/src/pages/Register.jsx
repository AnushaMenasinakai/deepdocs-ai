import AuthLayout from '../components/AuthLayout.jsx'
import AuthForm from '../components/AuthForm.jsx'

export default function Register() {
  return <AuthLayout title="Create your account" description="Start your journey with DeepDocs AI."><AuthForm mode="register" /></AuthLayout>
}
