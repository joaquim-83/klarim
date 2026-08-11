import { useEffect, useState, useCallback, Fragment } from 'react'
import { card } from './shared.js'
import { planProgress, DEFAULT_URL } from '../../lib/gate/snippets.js'
import {
  normalizeUrl, categorySummary, kycBannerVisible, usageText, rateLimitMessage,
  formatCountdown, shouldShowWizard, planDetails, canUpgrade, groupChecksByCategory, planName,
  errDetail, reportButton,
} from '../../lib/gate/ux.js'
import GateIntegrationTabs from './GateIntegrationTabs.jsx'
import GateOnboarding from './GateOnboarding.jsx'
import GateVendors from './GateVendors.jsx'
import KycBanner from './KycBanner.jsx'
import DashboardNav from './DashboardNav.jsx'                 // KL-150 P2 (item 2)
import { SetPasswordModal } from './LevelPrompt.jsx'

// KL-153 P2 — portal do dev redesenhado (resolve a Quebra 10). Status bar (score/plano/uso +
// upgrade INLINE), "Novo scan" avulso, resultado filtrado por KYC, integração, histórico. Ativa o
// Gate sozinho quando a conta ainda é owner (Quebra 2). Tokens theme-aware (KL-87). Trata 429 com
// mensagem contextual (../../lib/gate/ux.js). O cookie de sessão (HttpOnly, same-origin) autentica.

const ONBOARDED_KEY = 'klarim_gate_onboarded'

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: 'include',
    headers: opts.body ? { 'Content-Type': 'application/json' } : {}, ...opts,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    // KL-159: `detail` pode ser um OBJETO (ex.: 403 {error:insufficient_level,...}). NUNCA jogar o
    // objeto na `message` (React quebra ao renderizá-lo) — coage p/ string; o objeto fica em `e.data`.
    const e = new Error(errDetail(data, r.status)); e.status = r.status; e.data = data; throw e
  }
  return data
}

const semColor = (s) => (s >= 90 ? '#22c55e' : s >= 50 ? '#eab308' : '#ef4444')
const sema = (s) => (s >= 90 ? '🟢' : s >= 50 ? '🟡' : '🔴')
const ICON = { pass: '✅', fail: '❌', error: '⚠️', skip: '⏭️' }
const smBrand = 'inline-flex min-h-[40px] items-center rounded-lg bg-brand-500 px-3 py-1.5 text-sm font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 disabled:opacity-40'
const smOutline = 'inline-flex min-h-[40px] items-center rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800 disabled:opacity-40'

function Section({ title, children, right }) {
  return (
    <section className={`mb-6 ${card}`}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-bold text-white">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

// ---- Modal de pagamento PIX (KL-156) — mostra o QR + copia-e-cola e faz polling do status ---- //
function PixUpgradeModal({ charge, onDone, onClose }) {
  const [copied, setCopied] = useState(false)
  const [paid, setPaid] = useState(false)
  const qr = charge.br_code_base64
    ? (String(charge.br_code_base64).startsWith('data:') ? charge.br_code_base64 : `data:image/png;base64,${charge.br_code_base64}`)
    : null
  useEffect(() => {
    if (!charge.charge_id) return undefined
    let alive = true
    const iv = setInterval(async () => {
      try {
        const r = await api(`/api/account/upgrade/status?charge_id=${encodeURIComponent(charge.charge_id)}`)
        if (alive && r.paid) { setPaid(true); clearInterval(iv); setTimeout(() => onDone?.(), 1200) }
      } catch { /* best-effort; o webhook confirma de qualquer forma */ }
    }, 4000)
    return () => { alive = false; clearInterval(iv) }
  }, [charge.charge_id])
  function copy() {
    if (charge.br_code) { navigator.clipboard?.writeText(charge.br_code); setCopied(true); setTimeout(() => setCopied(false), 2000) }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Pagamento PIX">
      <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-6 text-center shadow-2xl">
        {paid ? (
          <div><p className="text-2xl">✅</p><p className="mt-2 font-bold text-white">Pagamento confirmado!</p><p className="mt-1 text-sm text-slate-300">Ativando o plano {charge.plan}…</p></div>
        ) : (
          <>
            <h3 className="text-lg font-bold text-white">Assinar {charge.plan} — {charge.price_display}</h3>
            <p className="mt-1 text-sm text-slate-300">Pague com PIX para ativar. A confirmação é automática.</p>
            {qr && <img src={qr} alt="QR Code PIX" className="mx-auto mt-4 h-52 w-52 rounded-lg bg-[#ffffff] p-2" />}
            {charge.br_code && (
              <div className="mt-3">
                <p className="text-xs text-slate-500">PIX copia-e-cola:</p>
                <div className="mt-1 flex items-center gap-2">
                  <code className="flex-1 truncate rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300">{charge.br_code}</code>
                  <button onClick={copy} className="shrink-0 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800">{copied ? 'Copiado ✓' : 'Copiar'}</button>
                </div>
              </div>
            )}
            <div className="mt-4 inline-flex items-center gap-2 text-xs text-slate-400">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-brand-500" aria-hidden="true" /> aguardando pagamento…
            </div>
            <button onClick={onClose} className="mt-4 block w-full text-sm text-slate-400 hover:text-slate-200">Fechar</button>
          </>
        )}
      </div>
    </div>
  )
}

// ---- Barra de status: score + plano + uso + upgrade inline (KL-153/156/159) ---- //
function StatusBar({ status, lastRun, onUpgraded, autoUpgrade }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [fallback, setFallback] = useState(null)   // {message, contact_email}
  const [charge, setCharge] = useState(null)        // dados do PIX p/ o modal
  const [pwFor, setPwFor] = useState(null)          // KL-159: slug aguardando "definir senha" (nível 1→2)
  const [autoRan, setAutoRan] = useState(false)
  const slug = status.plan_slug || 'free'
  const details = planDetails(slug)
  const next = details.next

  // KL-159 — `upgrade(planSlug)`: aceita o plano ALVO (do botão = próximo; ou de ?upgrade=). Trata:
  // fallback (sem pagamento) · PIX (modal+polling) · 403 insufficient_level (pede senha e re-tenta)
  // · 409/429/erro (mensagem inline). NUNCA fica em silêncio.
  async function upgrade(planSlug) {
    const target = planSlug || next?.slug
    if (!target) return
    setBusy(true); setErr(''); setFallback(null)
    try {
      const r = await api('/api/account/gate/upgrade', { method: 'POST', body: JSON.stringify({ plan: target }) })
      if (r.fallback) setFallback({ message: r.message, contact_email: r.contact_email })
      else if (r.br_code_base64 || r.charge_id) setCharge({ ...r, plan: planName(target) })
      else onUpgraded?.()
    } catch (e) {
      if (e.data?.detail?.error === 'insufficient_level') {
        setPwFor(target)   // conta sem senha → abre o modal de senha e re-tenta ao concluir
      } else {
        setErr(e.message || 'Não foi possível iniciar o upgrade. Tente novamente.')
      }
    } finally { setBusy(false) }
  }

  // KL-159 — auto-upgrade vindo da página de Planos (`/dashboard/gate?upgrade=pro`).
  useEffect(() => {
    if (autoUpgrade && !autoRan && ['pro', 'team'].includes(autoUpgrade)) {
      setAutoRan(true); upgrade(autoUpgrade)
    }
  }, [autoUpgrade, autoRan])

  return (
    <div className={`mb-6 ${card}`}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          {lastRun ? (
            <>
              <div className="text-3xl font-extrabold" style={{ color: semColor(lastRun.score) }}>{lastRun.score}<span className="text-sm text-slate-400">/100</span></div>
              <div className="text-2xl" aria-hidden="true">{sema(lastRun.score)}</div>
              <div className="text-xs text-slate-400">último scan</div>
            </>
          ) : <div className="text-sm text-slate-400">Nenhum scan ainda — rode o primeiro abaixo.</div>}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-right">
            <div className="text-sm font-semibold text-white">Plano {status.plan || details.name}</div>
            <div className="text-xs text-slate-400">{usageText(status.scans_used_hour, status.scans_limit_hour)}</div>
          </div>
          {canUpgrade(slug) && (
            <button type="button" onClick={() => upgrade()} disabled={busy} className={smBrand}>
              {busy ? '…' : `Upgrade → ${next.label} (${next.price_display})`}
            </button>
          )}
        </div>
      </div>

      {/* KL-156 (Fix 5) — bloco "Seu plano" com limites explícitos + próximo plano. */}
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-sm sm:grid-cols-4">
        <div><dt className="text-xs text-slate-500">Plano</dt><dd className="font-semibold text-white">{details.name} <span className="font-normal text-slate-400">(Security Gate)</span></dd></div>
        <div><dt className="text-xs text-slate-500">Scans por hora</dt><dd className="text-slate-200">{details.scansHour}</dd></div>
        <div><dt className="text-xs text-slate-500">Cooldown por domínio</dt><dd className="text-slate-200">{details.cooldownLabel}</dd></div>
        <div><dt className="text-xs text-slate-500">Nível de acesso</dt><dd className="text-slate-200">{status.access_level === 'complete' ? 'Completo (KYC)' : 'Básico'}</dd></div>
        {next && (
          <div className="col-span-2 sm:col-span-4"><dt className="sr-only">Próximo plano</dt>
            <dd className="text-xs text-slate-400">Próximo plano: <span className="font-medium text-slate-200">{next.label} ({next.price_display})</span> — {planDetails(next.slug).scansHour} scans/hora, cooldown {planDetails(next.slug).cooldownLabel}.</dd></div>
        )}
      </dl>

      {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
      {fallback && (
        <div className="mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          {fallback.message || 'Assinatura indisponível no momento.'}{' '}
          {fallback.contact_email && <a href={`mailto:${fallback.contact_email}`} className="font-semibold text-brand-400 hover:underline">{fallback.contact_email}</a>}
        </div>
      )}
      {charge && <PixUpgradeModal charge={charge} onDone={() => { setCharge(null); onUpgraded?.() }} onClose={() => setCharge(null)} />}
      {/* KL-159 — conta sem senha (nível 1) → define a senha e re-tenta o upgrade automaticamente. */}
      {pwFor && <SetPasswordModal onClose={() => setPwFor(null)}
        onDone={() => { const s = pwFor; setPwFor(null); upgrade(s) }} />}
    </div>
  )
}

// ---- Novo scan avulso (modal) ---- //
function NewScan({ onScanned }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [rl, setRl] = useState(null)    // rate-limit
  const [err, setErr] = useState('')

  async function scan() {
    const scanUrl = normalizeUrl(url)
    if (!scanUrl) { setErr('Digite a URL.'); return }
    setBusy(true); setErr(''); setRl(null); setRes(null)
    try {
      const r = await api('/api/gate/scan', { method: 'POST', body: JSON.stringify({ url: scanUrl }) })
      setRes(r); onScanned?.()
    } catch (e) {
      if (e.status === 429) setRl(rateLimitMessage(e.data?.limit_type, e.data?.retry_after_seconds))
      else setErr(e.message)
    } finally { setBusy(false) }
  }

  if (!open) return <button type="button" onClick={() => setOpen(true)} className={smBrand}>+ Novo scan</button>
  return (
    <div className="mt-1 w-full">
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1">
          <span className="mb-1 block text-sm font-medium text-slate-200">URL do site</span>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://meuapp.com"
            className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
        </label>
        <button type="button" onClick={scan} disabled={busy} className={smBrand}>{busy ? 'Escaneando…' : 'Escanear'}</button>
        <button type="button" onClick={() => { setOpen(false); setRes(null); setRl(null) }} className="text-sm text-slate-400 hover:text-slate-200">fechar</button>
      </div>
      {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
      {rl && <RateLimitNote note={rl} />}
      {res && <ScanResultCard res={res} onKycDone={loadFullResult} />}
    </div>
  )

  // KL-158: após o KYC, busca o run persistido (results completos) e mostra o detalhe — NÃO
  // re-escaneia (o rate limit por domínio barraria).
  async function loadFullResult() {
    if (!res?.run_id) return
    try {
      const r = await api(`/api/gate/runs/${res.run_id}`)
      setRes((prev) => ({ ...prev, access_level: 'complete', results: r.run?.results || [] }))
    } catch { /* mantém o resumo */ }
    onScanned?.()   // KL-158: re-busca o status (Nível de acesso → Completo) e o último run
  }
}

function RateLimitNote({ note }) {
  const [left, setLeft] = useState(Number(note.retryAfterSeconds) || 0)
  useEffect(() => {
    setLeft(Number(note.retryAfterSeconds) || 0)
    const iv = setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(iv)
  }, [note])
  return (
    <div className="mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
      <p className="font-semibold text-amber-200">{note.title}</p>
      <p className="mt-1 text-amber-100">{note.message}</p>
      {left > 0 && <p className="mt-1 font-mono text-amber-300">⏳ {formatCountdown(left)}</p>}
      {note.showUpgrade && <a href="/security-gate#planos" className="mt-2 inline-block font-semibold text-brand-400 hover:underline">Fazer upgrade →</a>}
    </div>
  )
}

function ScanResultCard({ res, onKycDone }) {
  const cats = categorySummary(res)
  const groups = (res.results || []).length ? groupChecksByCategory(res.results) : null
  return (
    <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex items-center gap-3">
        <span className="text-2xl font-extrabold" style={{ color: semColor(res.score) }}>{res.score}/100</span>
        <span className="text-xl" aria-hidden="true">{sema(res.score)}</span>
        <span className="text-sm text-slate-300">{res.passed ? 'Passou ✅' : 'Reprovou ❌'}</span>
      </div>
      <ul className="mt-3 space-y-1 text-sm text-slate-200">
        {cats.map((c) => (
          <li key={c.name} className="flex justify-between"><span className="capitalize">{c.name.replace(/_/g, ' ')}</span>
            <span className={c.status === 'pass' ? 'text-green-400' : 'text-red-400'}>{c.checks_passed}/{c.checks_total}</span></li>
        ))}
      </ul>
      {/* KL-158: com KYC → detalhes completos por categoria (do run persistido). */}
      {groups && (
        <div className="mt-3 space-y-2">
          {groups.map((g) => (
            <details key={g.name} className="rounded-lg border border-slate-800 bg-slate-900/40">
              <summary className="cursor-pointer list-none px-3 py-2 text-sm font-semibold capitalize text-slate-100">{g.name.replace(/_/g, ' ')} ({g.checks.length})</summary>
              <ul className="space-y-1 px-3 pb-3 text-sm text-slate-200">
                {g.checks.map((c, i) => (
                  <li key={i} className="border-t border-slate-800 pt-1"><span>{ICON[c.status] || '•'}</span> <span className="text-slate-400">[{c.severity}]</span> <strong>{c.check}</strong>: {c.detail}</li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
      {/* KL-158: banner CLICÁVEL (abre o modal de KYC) — antes era texto puro sem ação. */}
      {kycBannerVisible(res.access_level) && (
        <KycBanner message={res.kyc_message} onCompleted={onKycDone} />
      )}
      {/* KL-163: com KYC (access_level=complete) o dev exporta o relatório PDF do run. */}
      {res.run_id && res.access_level === 'complete' && (
        <ReportButton runId={res.run_id} kycCompleted domain={String(res.url || '').replace(/^https?:\/\//, '').replace(/\/.*$/, '')} />
      )}
    </div>
  )
}

// ---- API key ---- //
function ApiKeyCard() {
  const [info, setInfo] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = useCallback(() => { api('/api/account/gate/key-info').then(setInfo).catch((e) => setErr(e.message)) }, [])
  useEffect(load, [load])
  async function regenerate() {
    if (!window.confirm('Regenerar emite uma nova key. A atual vale por mais 1h (o CI em andamento não quebra). Continuar?')) return
    setBusy(true); setErr('')
    try {
      const r = await api('/api/account/gate/regenerate-key', { method: 'POST' })
      setNewKey(r.api_key)
      try { sessionStorage.setItem('klarim_gate_new_key', r.api_key) } catch { /* */ }
      load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <Section title="🔑 Sua API Key" right={<button onClick={regenerate} disabled={busy} className={smOutline}>{busy ? '…' : 'Regenerar →'}</button>}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {newKey ? (
        <div className="rounded-lg border border-green-500/40 bg-green-500/10 p-3">
          <p className="text-sm text-green-300">Nova key (copie agora — não será exibida de novo):</p>
          <code className="mt-1 block break-all font-mono text-sm text-slate-100">{newKey}</code>
        </div>
      ) : info?.has_key ? (
        <p className="font-mono text-slate-200">{info.masked}
          <span className="ml-2 font-sans text-xs text-slate-400">criada {String(info.created_at || '').slice(0, 10)}
            {info.last_used_at ? ` · último uso ${String(info.last_used_at).slice(0, 10)}` : ' · nunca usada'}</span>
        </p>
      ) : <p className="text-sm text-slate-400">Nenhuma API key ativa.</p>}
    </Section>
  )
}

// ---- Projetos + planos ---- //
function NewProject({ onDone }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState(''); const [name, setName] = useState('')
  const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  async function create() {
    setBusy(true); setErr('')
    try { await api('/api/gate/projects', { method: 'POST', body: JSON.stringify({ url, name }) }); setOpen(false); setUrl(''); setName(''); onDone() }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  if (!open) return <button onClick={() => setOpen(true)} className={smOutline}>+ Novo projeto</button>
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block"><span className="mb-1 block text-sm font-medium text-slate-200">Nome do projeto</span>
        <input placeholder="Ex: Meu App" value={name} onChange={(e) => setName(e.target.value)} className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" /></label>
      <label className="block"><span className="mb-1 block text-sm font-medium text-slate-200">URL do site (domínio verificável)</span>
        <input placeholder="https://meuapp.com.br" value={url} onChange={(e) => setUrl(e.target.value)} className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" /></label>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <button onClick={create} disabled={busy || !url} className={smBrand}>{busy ? 'Criando…' : 'Criar projeto'}</button>
        <button onClick={() => setOpen(false)} className="text-sm text-slate-400 hover:text-slate-200">cancelar</button>
        {err && <span className="text-xs text-red-400">{err}</span>}
      </div>
    </div>
  )
}

function PlanBadge({ plan, allowedCount }) {
  const pr = planProgress(plan.slug, allowedCount)
  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-white">Plano {plan.name} <span className="font-normal text-slate-400">· {pr.count} checks incluídos</span></span>
        {pr.next && <a href="/security-gate#planos" className="text-xs font-semibold text-brand-400 hover:underline">Upgrade → {pr.next.label} ({pr.next.checks} checks)</a>}
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${pr.pct}%` }} /></div>
      <p className="mt-1 text-xs text-slate-400">{pr.count}/{pr.total} checks</p>
    </div>
  )
}

// ---- KL-150 P2 (item 1): verificação de domínio do projeto (o portal não tinha UI p/ isso) ---- //
const _VERIFY_METHODS = [
  { key: 'dns_txt', label: 'DNS TXT' },
  { key: 'meta_tag', label: 'Meta tag' },
  { key: 'html_file', label: 'Arquivo HTML' },
]
function VerifyProjectModal({ project, onClose, onVerified }) {
  const [method, setMethod] = useState('dns_txt')
  const [instr, setInstr] = useState(null)   // instruções do /verify/start
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  async function start(m) {
    setBusy(true); setErr(''); setMsg(''); setInstr(null)
    try {
      const r = await api(`/api/gate/projects/${project.id}/verify/start`, { method: 'POST', body: JSON.stringify({ method: m }) })
      setInstr(r.instructions || null)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function check() {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await api(`/api/gate/projects/${project.id}/verify/check`, { method: 'POST' })
      if (r.status === 'verified') { onVerified(); return }
      if (r.status === 'not_found') setMsg('Ainda não encontramos o desafio no domínio. Confira a configuração e a propagação, e tente de novo.')
      else if (r.status === 'no_pending') setMsg('Gere o desafio abaixo antes de verificar.')
      else setMsg('Não foi possível verificar. Tente novamente.')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const box = 'rounded-lg border border-slate-700 bg-slate-950 p-2 font-mono text-xs text-slate-200 break-all'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Verificar domínio">
      <div className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <h3 className="text-lg font-bold text-white">Verificar domínio — {project.domain}</h3>
        <p className="mt-1 text-sm text-slate-300">Comprove que o domínio é seu (escolha um método). Necessário para rodar scans do domínio registrado no CI/CD.</p>

        <div className="mt-4 flex flex-wrap gap-2">
          {_VERIFY_METHODS.map((m) => (
            <button key={m.key} type="button" onClick={() => { setMethod(m.key); start(m.key) }}
              className={`rounded-lg border px-3 py-1.5 text-sm ${method === m.key ? 'border-brand-500 bg-brand-500/10 text-brand-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
              {m.label}
            </button>
          ))}
        </div>

        {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
        {!instr && !busy && <p className="mt-4 text-sm text-slate-400">Escolha um método acima para ver as instruções.</p>}
        {busy && !instr && <p className="mt-4 text-sm text-slate-400">Gerando desafio…</p>}

        {instr && (
          <div className="mt-4 space-y-2 text-sm text-slate-300">
            <p className="font-semibold text-white">{instr.title}</p>
            {instr.snippet && <code className={box}>{instr.snippet}</code>}
            {instr.record_type && <div className={box}>Tipo: {instr.record_type} · Host: {instr.host} · Valor: {instr.value}</div>}
            {instr.path && <div className={box}>Arquivo: {instr.path}<br />Conteúdo: {instr.content}</div>}
            <p className="text-slate-400">{instr.help}</p>
          </div>
        )}
        {msg && <p className="mt-3 text-sm text-amber-300">{msg}</p>}

        <div className="mt-6 flex items-center gap-3">
          <button type="button" onClick={check} disabled={busy || !instr} className={smBrand}>{busy ? 'Verificando…' : 'Verificar agora'}</button>
          <button type="button" onClick={onClose} className="text-sm text-slate-400 hover:text-slate-200">Fechar</button>
        </div>
      </div>
    </div>
  )
}

function Projects({ onSelect, refreshKey }) {
  const [data, setData] = useState(null); const [err, setErr] = useState('')
  const [verifyProj, setVerifyProj] = useState(null)
  const load = useCallback(() => api('/api/gate/projects').then(setData).catch((e) => setErr(e.message)), [])
  useEffect(() => { load() }, [load, refreshKey])
  const projects = data?.projects || []
  return (
    <Section title={`Meus Projetos (${projects.length})`} right={<NewProject onDone={load} />}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {projects.length === 0 ? <p className="text-sm text-slate-400">Nenhum projeto verificado. O “Novo scan” acima funciona sem projeto (scan avulso).</p> : (
        <ul className="space-y-2">
          {projects.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
              <div className="min-w-0"><div className="truncate font-semibold text-white">{p.domain}</div>
                <div className="text-xs text-slate-400">{p.verified ? `✅ Verificado (${p.verification_method || '—'})` : '⏳ Não verificado'}</div></div>
              <div className="flex shrink-0 items-center gap-3">
                {!p.verified && <button onClick={() => setVerifyProj(p)} className="text-sm font-medium text-brand-400 hover:underline">Verificar →</button>}
                <button onClick={() => onSelect(p)} className="text-sm font-medium text-slate-300 hover:underline">Histórico →</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {data?.plan && <PlanBadge plan={data.plan} allowedCount={(data.allowed_checks || []).length} />}
      {verifyProj && <VerifyProjectModal project={verifyProj} onClose={() => setVerifyProj(null)}
        onVerified={() => { setVerifyProj(null); load() }} />}
    </Section>
  )
}

// ---- Exportar relatório PDF (KL-163) ---- //
// Sem KYC → mensagem de bloqueio (não botão). Com KYC → baixa o PDF via blob (o endpoint devolve
// application/pdf com Content-Disposition attachment). Loading enquanto o PDF é gerado (alguns
// segundos). 403 → o backend também gateia por KYC (defesa-em-profundidade).
function ReportButton({ runId, kycCompleted, domain }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const btn = reportButton(kycCompleted)
  if (!btn.show) {
    return <p className="mt-3 text-xs text-slate-400">🔒 {btn.message} <a href="/dashboard/gate" className="font-medium text-brand-400 hover:underline">Complete o cadastro</a></p>
  }
  async function download() {
    setBusy(true); setErr('')
    try {
      const r = await fetch(`/api/gate/runs/${runId}/report`, { credentials: 'include' })
      if (!r.ok) {
        const data = await r.json().catch(() => ({}))
        throw new Error(errDetail(data, r.status))
      }
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `klarim-gate-${(domain || 'site').replace(/[^a-z0-9.-]/gi, '-')}.pdf`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="mt-3">
      <button onClick={download} disabled={busy} className={smOutline}>{busy ? 'Gerando PDF…' : btn.label}</button>
      {err && <p className="mt-1 text-xs text-red-400">{err}</p>}
    </div>
  )
}

// ---- Runs ---- //
function RunDetail({ runId, kycCompleted }) {
  const [run, setRun] = useState(null); const [err, setErr] = useState('')
  useEffect(() => { api(`/api/gate/runs/${runId}`).then((r) => setRun(r.run)).catch((e) => setErr(e.message)) }, [runId])
  if (err) return <p className="text-sm text-red-400">{err}</p>
  if (!run) return <p className="text-sm text-slate-400">Carregando…</p>
  const results = run.results || []
  return (
    <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <ul className="space-y-1 text-sm text-slate-200">
        {results.map((r, i) => (<li key={i}><span>{ICON[r.status] || '•'}</span> <span className="text-slate-400">[{r.severity}]</span> <strong>{r.check}</strong>: {r.detail}</li>))}
      </ul>
      {(run.checks_blocked || []).length > 0 && (
        <div className="mt-3 rounded-md bg-slate-800/60 p-2 text-sm text-slate-300">🔒 {run.checks_blocked.length} checks bloqueados no seu plano:{' '}{run.checks_blocked.slice(0, 5).join(', ')}{run.checks_blocked.length > 5 ? '…' : ''}{' '}<a href="/security-gate#planos" className="font-medium text-brand-400">Fazer upgrade →</a></div>
      )}
      <ReportButton runId={runId} kycCompleted={kycCompleted} domain={String(run.url || '').replace(/^https?:\/\//, '').replace(/\/.*$/, '')} />
    </div>
  )
}

function Runs({ project, onBack, kycCompleted }) {
  const [runs, setRuns] = useState(null); const [err, setErr] = useState(''); const [openRun, setOpenRun] = useState(null)
  useEffect(() => { api(`/api/gate/runs?project_id=${project.id}`).then((r) => setRuns(r.runs)).catch((e) => setErr(e.message)) }, [project.id])
  return (
    <Section title={`${project.domain} — Histórico`} right={<button onClick={onBack} className="text-sm text-slate-400 hover:text-slate-200">← Voltar</button>}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {!runs ? <p className="text-sm text-slate-400">Carregando…</p> : runs.length === 0 ? <p className="text-sm text-slate-400">Nenhum run ainda.</p> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-400"><th className="py-1">Data</th><th>Score</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.id}>
                <tr className="border-t border-slate-800 text-slate-200">
                  <td className="py-1.5">{String(r.created_at || '').slice(0, 16).replace('T', ' ')}</td>
                  <td><span style={{ color: semColor(r.score) }} className="font-bold">{r.score}</span></td>
                  <td>{r.passed ? '✅ PASS' : '❌ FAIL'}</td>
                  <td><button onClick={() => setOpenRun(openRun === r.id ? null : r.id)} className="text-brand-400">{openRun === r.id ? 'fechar' : 'ver'}</button></td>
                </tr>
                {openRun === r.id && <tr><td colSpan={4}><RunDetail runId={r.id} kycCompleted={kycCompleted} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}

// ---- Histórico geral (todos os runs da conta) ---- //
function AllRuns({ refreshKey }) {
  const [runs, setRuns] = useState(null); const [err, setErr] = useState('')
  useEffect(() => { api('/api/gate/runs?limit=20').then((r) => setRuns(r.runs)).catch((e) => setErr(e.message)) }, [refreshKey])
  return (
    <Section title="Histórico de scans">
      {err && <p className="text-sm text-red-400">{err}</p>}
      {!runs ? <p className="text-sm text-slate-400">Carregando…</p> : runs.length === 0 ? <p className="text-sm text-slate-400">Nenhum scan ainda.</p> : (
        <ul className="divide-y divide-slate-800">
          {runs.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0 truncate text-slate-300">{String(r.url || '').replace(/^https?:\/\//, '')}</span>
              <span className="flex shrink-0 items-center gap-3">
                <span style={{ color: semColor(r.score) }} className="font-bold">{r.score}</span>
                <span>{r.passed ? '✅' : '❌'}</span>
                <span className="text-xs text-slate-500">{String(r.created_at || '').slice(0, 10)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

export default function GatePortal() {
  const [status, setStatus] = useState(null)     // null=carregando
  const [selected, setSelected] = useState(null)
  const [firstUrl, setFirstUrl] = useState(DEFAULT_URL)
  const [lastRun, setLastRun] = useState(null)
  const [showOnboard, setShowOnboard] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  // KL-159 — auto-upgrade vindo da página de Planos (`/dashboard/gate?upgrade=pro`).
  const [autoUpgrade] = useState(() => {
    try { return new URLSearchParams(window.location.search).get('upgrade') } catch { return null }
  })

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), [])
  const reloadStatus = useCallback(() => api('/api/account/gate/status').then(setStatus).catch(() => setStatus({ error: true })), [])

  useEffect(() => {
    let alive = true
    ;(async () => {
      let st = await api('/api/account/gate/status').catch(() => null)
      // Quebra 2: conta owner sem Gate → ativa automaticamente e revela o dashboard.
      if (st && !st.is_developer) {
        try {
          const act = await api('/api/account/gate/activate', { method: 'POST' })
          if (act.api_key) { try { sessionStorage.setItem('klarim_gate_new_key', act.api_key) } catch { /* */ } }
          st = await api('/api/account/gate/status').catch(() => st)
        } catch { /* segue com o status atual */ }
      }
      if (!alive) return
      setStatus(st || { error: true })
      const runsRes = await api('/api/gate/runs?limit=1').catch(() => ({ runs: [] }))
      const first = (runsRes.runs || [])[0]
      if (alive && first) setLastRun(first)
      let onboarded = false
      try { onboarded = !!localStorage.getItem(ONBOARDED_KEY) } catch { /* */ }
      if (alive) setShowOnboard(!onboarded && shouldShowWizard(st, (runsRes.runs || []).length))
      const proj = await api('/api/gate/projects').catch(() => ({ projects: [] }))
      const p = (proj.projects || [])[0]
      if (alive && p) setFirstUrl(p.url || `https://${p.domain}`)
    })()
    return () => { alive = false }
  }, [])

  const dismissOnboard = () => setShowOnboard(false)
  const completeOnboard = () => { try { localStorage.setItem(ONBOARDED_KEY, '1') } catch { /* */ } setShowOnboard(false); refresh() }
  const onScanned = () => { api('/api/gate/runs?limit=1').then((r) => setLastRun((r.runs || [])[0] || null)).catch(() => {}); reloadStatus(); refresh() }

  if (!status) {
    return <div className="py-10 text-center text-sm text-slate-400">Carregando o Security Gate…</div>
  }

  return (
    <div>
      {/* KL-150 P2 (item 2) — nav para não ficar preso em /dashboard/gate */}
      <DashboardNav accountType={status.account_type} />
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Security Gate</h1>
        <p className="mt-1 text-sm text-slate-300">Scan de segurança pós-deploy — 86 verificações no seu CI/CD.</p>
      </div>

      {!status.error && <StatusBar status={status} lastRun={lastRun} onUpgraded={reloadStatus} autoUpgrade={autoUpgrade} />}

      {showOnboard ? (
        <GateOnboarding onDone={completeOnboard} onSkip={dismissOnboard} kycCompleted={!!status.kyc_completed} />
      ) : (
        <>
          <Section title="🔎 Novo scan" right={null}>
            <p className="mb-2 text-sm text-slate-300">Escaneie qualquer URL na hora (scan avulso, sem verificar domínio).</p>
            <NewScan onScanned={onScanned} />
          </Section>

          <ApiKeyCard />
          {selected ? <Runs project={selected} onBack={() => setSelected(null)} kycCompleted={!!status.kyc_completed} /> : <Projects onSelect={setSelected} refreshKey={refreshKey} />}
          <GateVendors />

          <Section title="⚙️ Integração no CI/CD" right={<a href="/docs/gate/github-actions" className="text-sm font-medium text-brand-400 hover:underline">Ver documentação →</a>}>
            <p className="mb-3 text-sm text-slate-300">Guarde a API key como o secret <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">KLARIM_KEY</code> e adicione ao seu pipeline (URL já preenchida):</p>
            <GateIntegrationTabs url={firstUrl} />
          </Section>

          <AllRuns refreshKey={refreshKey} />
        </>
      )}
    </div>
  )
}
