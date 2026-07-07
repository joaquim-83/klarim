import { Navigate } from 'react-router-dom'
import { isAuthed } from '../../lib/auth'

// Bloqueia rotas /painel/* sem token válido — manda para o login.
export default function ProtectedRoute({ children }) {
  return isAuthed() ? children : <Navigate to="/painel/login" replace />
}
