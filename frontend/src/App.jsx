import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext.jsx'
import { ProtectedRoute, PublicOnlyRoute } from './components/AuthRoutes.jsx'
import AppLayout from './components/AppLayout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import KnowledgeBases from './pages/KnowledgeBases.jsx'
import Documents from './pages/Documents.jsx'
import AskDeepDocs from './pages/AskDeepDocs.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route element={<PublicOnlyRoute />}>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
          </Route>
          <Route element={<ProtectedRoute />}>
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="knowledge-bases" element={<KnowledgeBases />} />
              <Route path="documents" element={<Documents />} />
              <Route path="ask" element={<AskDeepDocs />} />
              <Route path="*" element={<section className="panel empty-state"><h1>Page not found</h1><Link to="/">Return to dashboard</Link></section>} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
