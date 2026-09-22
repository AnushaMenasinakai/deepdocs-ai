import AuthLayout from '../components/AuthLayout.jsx'
import AuthForm from '../components/AuthForm.jsx'

export default function Login() {
  return <AuthLayout title="Welcome back" description="Sign in to your DeepDocs AI account."><AuthForm mode="login" /></AuthLayout>
}
