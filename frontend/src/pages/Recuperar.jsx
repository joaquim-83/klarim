import { useState } from 'react'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { recoveryRequest } from '../lib/api'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Recuperar() {
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    if (!EMAIL_RE.test(email)) {
      setError('Digite um e-mail válido.')
      return
    }
    setError('')
    setBusy(true)
    try {
      await recoveryRequest(email)
      setSent(true)
    } catch {
      // Mesmo em erro, mostramos a mensagem genérica (não revela nada).
      setSent(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Layout>
      <div className="mx-auto max-w-md pt-6 text-center">
        <h1 className="text-2xl font-bold">Recuperar relatórios</h1>

        {sent ? (
          <div className="mt-8 rounded-lg border border-klarim-border bg-klarim-surface px-5 py-6">
            <div className="text-3xl">📧</div>
            <p className="mt-3 text-klarim-text">
              Se existirem relatórios associados a este e-mail, enviaremos um link de
              acesso. Verifique sua caixa de entrada.
            </p>
            <Link to="/" className="mt-6 inline-block text-sm text-klarim-muted hover:text-klarim-text">
              ← Voltar ao início
            </Link>
          </div>
        ) : (
          <>
            <p className="mt-2 text-klarim-muted">
              Já pagou por um relatório e não o recebeu? Informe o e-mail usado no
              pagamento e enviaremos um link de acesso.
            </p>
            <form onSubmit={onSubmit} className="mt-8 text-left">
              <label className="text-sm text-klarim-muted">E-mail</label>
              <input
                type="email"
                inputMode="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Digite o e-mail usado no pagamento"
                className="mt-1 w-full rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-alert focus:outline-none"
                aria-label="E-mail usado no pagamento"
              />
              {error && <p className="mt-2 text-sm text-klarim-fail">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60"
              >
                {busy ? <><span className="klarim-spinner h-5 w-5" /> Enviando…</> : 'Enviar link de acesso'}
              </button>
            </form>
          </>
        )}
      </div>
    </Layout>
  )
}
