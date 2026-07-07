import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Beacon } from '../../components/Logo'
import { login } from '../../lib/adminApi'
import { isAuthed } from '../../lib/auth'
import { Button, ErrorBox } from '../../components/admin/ui'

export default function Login() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Já autenticado? vai direto para o painel.
  if (isAuthed()) {
    navigate('/painel', { replace: true })
  }

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(username.trim(), password)
      navigate('/painel', { replace: true })
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
