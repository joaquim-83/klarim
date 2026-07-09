import { useState } from 'react'

const EMAIL = 'scan@klarim.net'
const API = import.meta.env.VITE_API_BASE || '/api'

// Modal de contato: mostra o e-mail (com copiar) + formulário que envia via
// POST /api/contact. Não usa mailto — o visitante não sai do site.
export default function ContactModal({ onClose }) {
  const [copied, setCopied] = useState(false)
  const [form, setForm] = useState({ name: '', email: '', message: '' })
  const [status, setStatus] = useState('idle') // idle | sending | sent | error
  const [error, setError] = useState('')

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }))
  }

  async function copyEmail() {
    try {
      await navigator.clipboard.writeText(EMAIL)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Não foi possível copiar. Copie manualmente: ' + EMAIL)
    }
  }

  async function submit(e) {
    e.preventDefault()
    if (!form.email.trim() || !form.message.trim()) {
      setError('Preencha e-mail e mensagem.')
      return
    }
    setStatus('sending')
    setError('')
    try {
      const resp = await fetch(`${API}/contact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (resp.status === 429) {
        setStatus('error')
        setError('Muitas mensagens. Tente novamente mais tarde.')
        return
      }
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        setStatus('error')
        setError(d.detail || 'Falha ao enviar. Tente novamente.')
        return
      }
      setStatus('sent')
    } catch {
      setStatus('error')
      setError('Falha de conexão. Tente novamente.')
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="relative w-full max-w-md rounded-xl border border-klarim-border bg-klarim-surface p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          aria-label="Fechar"
          className="absolute right-4 top-4 text-klarim-muted hover:text-klarim-text"
        >
          ✕
        </button>

        <h3 className="mb-4 text-lg font-bold text-klarim-text">Entre em contato</h3>

        {/* E-mail + copiar */}
        <div className="mb-5 flex flex-wrap items-center gap-2 rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2">
          <span className="text-klarim-muted">📧</span>
          <span className="flex-1 break-all font-mono text-sm text-klarim-text">{EMAIL}</span>
          <button
            onClick={copyEmail}
            className="rounded border border-klarim-border px-2 py-1 text-xs text-klarim-alert hover:border-klarim-alert"
          >
            {copied ? 'Copiado ✓' : 'Copiar'}
          </button>
        </div>

        {status === 'sent' ? (
          <div className="rounded-lg border border-klarim-ok/40 bg-klarim-ok/10 px-4 py-6 text-center text-sm text-klarim-text">
            ✅ Mensagem enviada! Responderemos em breve.
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <div className="text-center text-xs uppercase tracking-wide text-klarim-muted">
              — ou envie uma mensagem —
            </div>
            <input
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="Nome (opcional)"
              className="w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
            />
            <input
              type="email"
              value={form.email}
              onChange={(e) => set('email', e.target.value)}
              placeholder="Seu e-mail"
              required
              className="w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
            />
            <textarea
              value={form.message}
              onChange={(e) => set('message', e.target.value)}
              placeholder="Sua mensagem"
              rows={4}
              required
              className="w-full resize-y rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
            />
            {error && <div className="text-sm text-klarim-fail">{error}</div>}
            <button
              type="submit"
              disabled={status === 'sending'}
              className="w-full rounded-lg bg-klarim-alert px-4 py-2 font-bold text-klarim-bg disabled:opacity-60"
            >
              {status === 'sending' ? 'Enviando…' : 'Enviar'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
