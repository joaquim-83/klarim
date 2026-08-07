import { useEffect, useState, useCallback, Fragment } from 'react'
import { card, brandBtn, outlineBtn } from './shared.js'
import { planProgress, DEFAULT_URL } from '../../lib/gate/snippets.js'
import GateIntegrationTabs from './GateIntegrationTabs.jsx'
import GateOnboarding from './GateOnboarding.jsx'

// KL-151 P3 / KL-152 P1 — portal do dev do Security Gate. Consome /api/gate/* e /api/account/gate/*
// com o cookie de sessão (HttpOnly, same-origin). KL-152: visual alinhado ao dashboard (tokens
// theme-aware do KL-87 — títulos text-white, cards `card` de shared.js), inputs com label, badge de
// plano com barra, abas de integração e wizard de onboarding (aparece até o 1º scan).

const ONBOARDED_KEY = 'klarim_gate_onboarded'

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: opts.body ? { 'Content-Type': 'application/json' } : {}, ...opts })
  if (!r.ok) {
    let detail = `Erro ${r.status}`
    try { detail = (await r.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return r.json()
}

const semColor = (s) => (s >= 90 ? '#22c55e' : s >= 50 ? '#eab308' : '#ef4444')
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

function Field({ label, ...props }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-200">{label}</span>
      <input {...props}
        className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none" />
    </label>
  )
}

// ---- API key ---- //
function ApiKeyCard() {
  const [info, setInfo] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api('/api/account/gate/key-info').then(setInfo).catch((e) => setErr(e.message))
  }, [])
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
    <Section title="🔑 Sua API Key"
      right={<button onClick={regenerate} disabled={busy} className={smOutline}>{busy ? '…' : 'Regenerar →'}</button>}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {newKey ? (
        <div className="rounded-lg border border-green-500/40 bg-green-500/10 p-3">
          <p className="text-sm text-green-300">Nova key (copie agora — não será exibida de novo):</p>
          <code className="mt-1 block break-all font-mono text-sm text-slate-100">{newKey}</code>
        </div>
      ) : info?.has_key ? (
        <p className="font-mono text-slate-200">{info.masked}
          <span className="ml-2 font-sans text-xs text-slate-400">
            criada {String(info.created_at || '').slice(0, 10)}
            {info.last_used_at ? ` · último uso ${String(info.last_used_at).slice(0, 10)}` : ' · nunca usada'}
          </span>
        </p>
      ) : <p className="text-sm text-slate-400">Nenhuma API key ativa.</p>}
    </Section>
  )
}

// ---- Projetos ---- //
function NewProject({ onDone }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function create() {
    setBusy(true); setErr('')
    try { await api('/api/gate/projects', { method: 'POST', body: JSON.stringify({ url, name }) }); setOpen(false); setUrl(''); setName(''); onDone() }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  if (!open) return <button onClick={() => setOpen(true)} className={smBrand}>+ Novo projeto</button>
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Nome do projeto" placeholder="Ex: Meu App" value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="URL do site" placeholder="https://meuapp.com.br" value={url} onChange={(e) => setUrl(e.target.value)} />
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
        <span className="text-sm font-semibold text-white">
          Plano {plan.name} <span className="font-normal text-slate-400">· {pr.count} checks incluídos</span>
        </span>
        {pr.next && (
          <a href="/security-gate#planos" className="text-xs font-semibold text-brand-400 hover:underline">
            Upgrade → {pr.next.label} ({pr.next.checks} checks)
          </a>
        )}
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${pr.pct}%` }} />
      </div>
      <p className="mt-1 text-xs text-slate-400">{pr.count}/{pr.total} checks</p>
    </div>
  )
}

function Projects({ onSelect }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => api('/api/gate/projects').then(setData).catch((e) => setErr(e.message)), [])
  useEffect(load, [load])
  const projects = data?.projects || []

  return (
    <Section title={`Meus Projetos (${projects.length})`} right={<NewProject onDone={load} />}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {projects.length === 0 ? <p className="text-sm text-slate-400">Nenhum projeto ainda. Crie um acima.</p> : (
        <ul className="space-y-2">
          {projects.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
              <div className="min-w-0">
                <div className="truncate font-semibold text-white">{p.domain}</div>
                <div className="text-xs text-slate-400">
                  {p.verified ? `✅ Verificado (${p.verification_method || '—'})` : '⏳ Não verificado'}
                </div>
              </div>
              <button onClick={() => onSelect(p)} className="shrink-0 text-sm font-medium text-brand-400 hover:underline">Ver histórico →</button>
            </li>
          ))}
        </ul>
      )}
      {data?.plan && <PlanBadge plan={data.plan} allowedCount={(data.allowed_checks || []).length} />}
    </Section>
  )
}

// ---- Runs ---- //
function RunDetail({ runId }) {
  const [run, setRun] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api(`/api/gate/runs/${runId}`).then((r) => setRun(r.run)).catch((e) => setErr(e.message)) }, [runId])
  if (err) return <p className="text-sm text-red-400">{err}</p>
  if (!run) return <p className="text-sm text-slate-400">Carregando…</p>
  const results = run.results || []
  return (
    <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <ul className="space-y-1 text-sm text-slate-200">
        {results.map((r, i) => (
          <li key={i}><span>{ICON[r.status] || '•'}</span> <span className="text-slate-400">[{r.severity}]</span> <strong>{r.check}</strong>: {r.detail}</li>
        ))}
      </ul>
      {(run.checks_blocked || []).length > 0 && (
        <div className="mt-3 rounded-md bg-slate-800/60 p-2 text-sm text-slate-300">
          🔒 {run.checks_blocked.length} checks bloqueados no seu plano:
          {' '}{run.checks_blocked.slice(0, 5).join(', ')}{run.checks_blocked.length > 5 ? '…' : ''}
          {' '}<a href="/security-gate#planos" className="font-medium text-brand-400">Fazer upgrade →</a>
        </div>
      )}
      {run.metadata && Object.keys(run.metadata).length > 0 && (
        <p className="mt-2 text-xs text-slate-500">Metadata: {Object.entries(run.metadata).map(([k, v]) => `${k}=${v}`).join(' · ')}</p>
      )}
    </div>
  )
}

function Runs({ project, onBack }) {
  const [runs, setRuns] = useState(null)
  const [err, setErr] = useState('')
  const [openRun, setOpenRun] = useState(null)
  useEffect(() => {
    api(`/api/gate/runs?project_id=${project.id}`).then((r) => setRuns(r.runs)).catch((e) => setErr(e.message))
  }, [project.id])

  return (
    <Section title={`${project.domain} — Histórico`} right={<button onClick={onBack} className="text-sm text-slate-400 hover:text-slate-200">← Voltar</button>}>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {!runs ? <p className="text-sm text-slate-400">Carregando…</p> : runs.length === 0 ? (
        <p className="text-sm text-slate-400">Nenhum run ainda. Rode o Gate no seu CI/CD (veja a integração abaixo).</p>
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-400"><th className="py-1">Data</th><th>Score</th><th>Status</th><th>CI</th><th></th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.id}>
                <tr className="border-t border-slate-800 text-slate-200">
                  <td className="py-1.5">{String(r.created_at || '').slice(0, 16).replace('T', ' ')}</td>
                  <td><span style={{ color: semColor(r.score) }} className="font-bold">{r.score}</span></td>
                  <td>{r.passed ? '✅ PASS' : '❌ FAIL'}</td>
                  <td className="text-xs text-slate-400">{r.metadata?.ci || 'manual'}</td>
                  <td><button onClick={() => setOpenRun(openRun === r.id ? null : r.id)} className="text-brand-400">{openRun === r.id ? 'fechar' : 'ver'}</button></td>
                </tr>
                {openRun === r.id && <tr><td colSpan={5}><RunDetail runId={r.id} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}

export default function GatePortal() {
  const [selected, setSelected] = useState(null)
  const [firstUrl, setFirstUrl] = useState(DEFAULT_URL)
  const [showOnboard, setShowOnboard] = useState(null)   // null=carregando · bool

  useEffect(() => {
    api('/api/gate/projects').then((r) => {
      const p = (r.projects || [])[0]
      if (p) setFirstUrl(p.url || `https://${p.domain}`)
    }).catch(() => {})
    let done = false
    try { done = !!localStorage.getItem(ONBOARDED_KEY) } catch { /* */ }
    if (done) { setShowOnboard(false); return }
    api('/api/gate/runs?limit=1')
      .then((r) => setShowOnboard((r.runs || []).length === 0))
      .catch(() => setShowOnboard(false))
  }, [])

  const dismissOnboard = () => setShowOnboard(false)
  const completeOnboard = () => {
    try { localStorage.setItem(ONBOARDED_KEY, '1') } catch { /* */ }
    setShowOnboard(false)
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Security Gate</h1>
        <p className="mt-1 text-sm text-slate-300">Scan de segurança pós-deploy no seu CI/CD.</p>
      </div>

      {showOnboard && (
        <div className="mb-6">
          <GateOnboarding onDone={completeOnboard} onSkip={dismissOnboard} />
        </div>
      )}

      <ApiKeyCard />
      {selected
        ? <Runs project={selected} onBack={() => setSelected(null)} />
        : <Projects onSelect={setSelected} />}

      <Section title="⚙️ Integração no CI/CD">
        <p className="mb-3 text-sm text-slate-300">
          Guarde a API key como o secret <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">KLARIM_KEY</code> e
          adicione ao seu pipeline (a URL do projeto já vem preenchida):
        </p>
        <GateIntegrationTabs url={firstUrl} />
      </Section>
    </div>
  )
}
