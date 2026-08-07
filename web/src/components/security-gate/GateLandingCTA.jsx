import { useEffect, useState } from 'react'

// Fix (ativação p/ conta existente) — CTA dinâmico do hero da landing /security-gate.
// No mount consulta GET /api/account/gate/status (cookie de sessão HttpOnly, same-origin):
//   401/erro → não logado    → "Começar grátis →"  (link p/ o registro)
//   logado + Gate não ativo   → "Ativar Security Gate →"  → POST /api/account/gate/activate
//   logado + Gate já ativo    → "Abrir dashboard →"  (link p/ /dashboard/gate)
// A ativação numa conta com key nova exibe a key UMA VEZ num modal antes de ir ao dashboard.
// SSR-safe: o 1º paint é o estado "não logado" (link), evitando salto de layout e cache do CF.

const BTN =
  'inline-flex min-h-[48px] w-full items-center justify-center rounded-lg bg-brand-500 px-6 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 ' +
  'active:scale-95 sm:w-auto'

async function getJSON(path, opts = {}) {
  const r = await fetch(path, { credentials: 'include', ...opts })
  const data = await r.json().catch(() => ({}))
  return { ok: r.ok, status: r.status, data }
}

function KeyModal({ apiKey, plan }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(apiKey)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard bloqueado → o usuário copia manualmente */ }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog" aria-modal="true" aria-label="Security Gate ativado">
      <div className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-6 text-left shadow-2xl">
        <h2 className="text-xl font-bold text-white">✅ Security Gate ativado!</h2>
        <p className="mt-1 text-sm text-slate-300">
          Gate ativo com plano <strong className="text-white">{plan || 'Pro'}</strong> (trial de 14 dias).
        </p>
        <p className="mt-4 text-sm font-medium text-slate-200">Sua API Key:</p>
        <code className="mt-1 block break-all rounded-lg border border-slate-700 bg-slate-800 p-3 font-mono text-sm text-slate-100">
          {apiKey}
        </code>
        <div className="mt-2 flex items-center gap-3">
          <button onClick={copy}
            className="rounded-md border border-slate-600 px-3 py-1.5 text-sm font-medium text-slate-200 hover:bg-slate-800">
            {copied ? 'Copiado ✓' : 'Copiar'}
          </button>
          <span className="text-xs text-amber-400">⚠️ Exibida apenas uma vez. Guarde-a em local seguro.</span>
        </div>
        <a href="/dashboard/gate" className={`mt-6 ${BTN}`}>Ir para o dashboard →</a>
      </div>
    </div>
  )
}

export default function GateLandingCTA() {
  const [state, setState] = useState('loading')   // loading | logged_out | inactive | active
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [modal, setModal] = useState(null)         // {apiKey, plan}

  useEffect(() => {
    let alive = true
    getJSON('/api/account/gate/status')
      .then(({ ok, data }) => {
        if (!alive) return
        if (!ok) return setState('logged_out')
        setState(data.gate_active ? 'active' : 'inactive')
      })
      .catch(() => { if (alive) setState('logged_out') })
    return () => { alive = false }
  }, [])

  async function activate() {
    setBusy(true); setErr('')
    try {
      const { ok, data } = await getJSON('/api/account/gate/activate', { method: 'POST' })
      if (!ok) throw new Error(data.detail || 'Não foi possível ativar. Tente novamente.')
      if (data.api_key) {
        // guarda p/ o wizard de onboarding do dashboard pré-preencher o secret (KL-152 P1)
        try { sessionStorage.setItem('klarim_gate_new_key', data.api_key) } catch { /* */ }
        setModal({ apiKey: data.api_key, plan: data.plan })   // key nova → mostra 1x
      } else {
        window.location.href = data.dashboard_url || '/dashboard/gate'   // já tinha key
      }
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  // 1º paint (SSR/loading) e não logado: link para o registro (não-JS friendly).
  if (state === 'loading' || state === 'logged_out') {
    return <a href="/cadastrar?type=developer" className={BTN}>Começar grátis →</a>
  }
  if (state === 'active') {
    return <a href="/dashboard/gate" className={BTN}>Abrir dashboard →</a>
  }
  // logado, Gate não ativo.
  return (
    <div className="flex w-full flex-col items-center gap-2 sm:w-auto">
      <button onClick={activate} disabled={busy} className={`${BTN} disabled:opacity-60`}>
        {busy ? 'Ativando…' : 'Ativar Security Gate →'}
      </button>
      {err && <span className="text-sm text-red-400">{err}</span>}
      {modal && <KeyModal apiKey={modal.apiKey} plan={modal.plan} />}
    </div>
  )
}
