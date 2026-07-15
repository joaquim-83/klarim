import { useState, useEffect } from 'react'
import { login } from '../../lib/admin/adminApi'
import { isAuthed } from '../../lib/admin/auth'
import { Button, ErrorBox } from './ui'

// Login do painel (KL-14), portado de frontend/src/pages/admin/Login.jsx (KL-51 fase 1).
// Sem react-router: useNavigate → window.location.href. O check de já-autenticado roda
// em useEffect (client-only; localStorage não existe no SSR).

function Beacon({ size = 40 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <g stroke="#FF6B35" strokeWidth="3.5" strokeLinecap="round">
        <line x1="32" y1="4" x2="32" y2="13" />
        <line x1="12" y1="12" x2="18" y2="18" />
        <line x1="52" y1="12" x2="46" y2="18" />
        <line x1="6" y1="30" x2="15" y2="30" />
        <line x1="58" y1="30" x2="49" y2="30" />
      </g>
      <circle cx="32" cy="38" r="17" fill="#FF6B35" />
      <circle cx="32" cy="38" r="7.5" fill="#0D1117" />
    </svg>
  )
}

export default function LoginIsland() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Já autenticado? vai direto para o painel.
  useEffect(() => {
    if (isAuthed()) window.location.href = '/painel'
  }, [])

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(username.trim(), password)
      window.location.href = '/painel'
    } catch (err) {
      setError(err.message || 'Falha no login.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-klarim-bg px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-2xl border border-klarim-border bg-klarim-surface p-8"
      >
        <div className="mb-6 flex flex-col items-center gap-2">
          <Beacon size={40} />
          <span className="text-2xl font-extrabold tracking-widest">
            KLA<span className="text-klarim-alert">R</span>IM
          </span>
          <span className="text-sm text-klarim-muted">Painel do operador</span>
        </div>

        <label className="mb-1 block text-xs font-semibold uppercase text-klarim-muted">Usuário</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          autoComplete="username"
          className="mb-4 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-klarim-text outline-none focus:border-klarim-alert"
        />

        <label className="mb-1 block text-xs font-semibold uppercase text-klarim-muted">Senha</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          className="mb-4 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-klarim-text outline-none focus:border-klarim-alert"
        />

        {error && <div className="mb-4"><ErrorBox message={error} /></div>}

        <Button type="submit" variant="primary" size="md" disabled={busy} className="w-full">
          {busy ? 'Entrando…' : 'Entrar'}
        </Button>
      </form>
    </div>
  )
}
