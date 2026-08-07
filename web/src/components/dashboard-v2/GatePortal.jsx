import { useEffect, useState, useCallback, Fragment } from 'react'

// KL-151 P3 — portal do dev do Security Gate (dentro do dashboard). Consome /api/gate/* e
// /api/account/gate/* com o cookie de sessão (HttpOnly, enviado automaticamente same-origin —
// os endpoints aceitam JWT além da API key). Seções: API key, projetos, runs, integração, plano.

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    ...opts,
  })
  if (!r.ok) {
    let detail = `Erro ${r.status}`
    try { detail = (await r.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return r.json()
}

const semColor = (s) => (s >= 90 ? '#00D26A' : s >= 50 ? '#F0C000' : '#F85149')
const ICON = { pass: '✅', fail: '❌', error: '⚠️', skip: '⏭️' }

function Section({ title, children, right }) {
  return (
    <section className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-bold text-slate-900">{title}</h2>
        {right}
      </div>
      {children}
    </section>
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
    if (!window.confirm('Regenerar revoga a key atual. Atualize no seu CI/CD. Continuar?')) return
    setBusy(true); setErr('')
    try {
      const r = await api('/api/account/gate/regenerate-key', { method: 'POST' })
      setNewKey(r.api_key)
      load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <Section title="🔑 Sua API Key"
      right={<button onClick={regenerate} disabled={busy}
        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-40">
        {busy ? '…' : 'Regenerar →'}</button>}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      {newKey ? (
        <div className="rounded-lg border border-green-500/40 bg-green-500/10 p-3">
          <p className="text-sm text-green-700">Nova key (copie agora — não será exibida de novo):</p>
          <code className="mt-1 block break-all font-mono text-sm text-slate-900">{newKey}</code>
        </div>
      ) : info?.has_key ? (
        <p className="font-mono text-slate-700">{info.masked}
          <span className="ml-2 text-xs text-slate-500">
            criada {String(info.created_at || '').slice(0, 10)}
            {info.last_used_at ? ` · último uso ${String(info.last_used_at).slice(0, 10)}` : ' · nunca usada'}
          </span>
        </p>
      ) : <p className="text-sm text-slate-500">Nenhuma API key ativa.</p>}
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
  if (!open) return <button onClick={() => setOpen(true)} className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-semibold text-[var(--accent-text)] hover:bg-brand-400">+ Novo projeto</button>
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input placeholder="https://meuapp.com.br" value={url} onChange={(e) => setUrl(e.target.value)}
        className="h-9 rounded-md border border-slate-300 px-2 text-sm" />
      <input placeholder="nome (opcional)" value={name} onChange={(e) => setName(e.target.value)}
        className="h-9 rounded-md border border-slate-300 px-2 text-sm" />
      <button onClick={create} disabled={busy || !url} className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-semibold text-[var(--accent-text)] disabled:opacity-40">Criar</button>
      <button onClick={() => setOpen(false)} className="text-sm text-slate-500">cancelar</button>
      {err && <span className="text-xs text-red-600">{err}</span>}
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
      {err && <p className="text-sm text-red-600">{err}</p>}
      {projects.length === 0 ? <p className="text-sm text-slate-500">Nenhum projeto. Crie um acima.</p> : (
        <ul className="space-y-2">
          {projects.map((p) => (
            <li key={p.id} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white p-3">
              <div>
                <div className="font-semibold text-slate-900">{p.domain}</div>
                <div className="text-xs text-slate-500">
                  {p.verified ? `✅ Verificado (${p.verification_method || '—'})` : '⏳ Não verificado'}
                </div>
              </div>
              <button onClick={() => onSelect(p)} className="text-sm font-medium text-brand-500 hover:underline">Ver histórico →</button>
            </li>
          ))}
        </ul>
      )}
      {data?.plan && (
        <p className="mt-3 text-xs text-slate-500">Plano <strong>{data.plan.name}</strong> · {(data.allowed_checks || []).length} checks incluídos</p>
      )}
    </Section>
  )
}

// ---- Runs ---- //
function RunDetail({ runId }) {
  const [run, setRun] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api(`/api/gate/runs/${runId}`).then((r) => setRun(r.run)).catch((e) => setErr(e.message)) }, [runId])
  if (err) return <p className="text-sm text-red-600">{err}</p>
  if (!run) return <p className="text-sm text-slate-500">Carregando…</p>
  const results = run.results || []
  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-white p-3">
      <ul className="space-y-1 text-sm">
        {results.map((r, i) => (
          <li key={i}><span>{ICON[r.status] || '•'}</span> <span className="text-slate-500">[{r.severity}]</span> <strong>{r.check}</strong>: {r.detail}</li>
        ))}
      </ul>
      {(run.checks_blocked || []).length > 0 && (
        <div className="mt-3 rounded-md bg-slate-100 p-2 text-sm text-slate-600">
          🔒 {run.checks_blocked.length} checks bloqueados no seu plano:
          {' '}{run.checks_blocked.slice(0, 5).join(', ')}{run.checks_blocked.length > 5 ? '…' : ''}
          {' '}<a href="/security-gate#planos" className="font-medium text-brand-500">Fazer upgrade →</a>
        </div>
      )}
      {run.metadata && Object.keys(run.metadata).length > 0 && (
        <p className="mt-2 text-xs text-slate-400">Metadata: {Object.entries(run.metadata).map(([k, v]) => `${k}=${v}`).join(' · ')}</p>
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
    <Section title={`${project.domain} — Histórico`} right={<button onClick={onBack} className="text-sm text-slate-500">← Voltar</button>}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      {!runs ? <p className="text-sm text-slate-500">Carregando…</p> : runs.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhum run ainda. Rode <code>klarim-gate scan {project.domain}</code>.</p>
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-400"><th className="py-1">Data</th><th>Score</th><th>Status</th><th>CI</th><th></th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.id}>
                <tr className="border-t border-slate-200 text-slate-700">
                  <td className="py-1.5">{String(r.created_at || '').slice(0, 16).replace('T', ' ')}</td>
                  <td><span style={{ color: semColor(r.score) }} className="font-bold">{r.score}</span></td>
                  <td>{r.passed ? '✅ PASS' : '❌ FAIL'}</td>
                  <td className="text-xs text-slate-500">{r.metadata?.ci || 'manual'}</td>
                  <td><button onClick={() => setOpenRun(openRun === r.id ? null : r.id)} className="text-brand-500">{openRun === r.id ? 'fechar' : 'ver'}</button></td>
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

// ---- Integração ---- //
function Integration() {
  const snippet = `- name: Security Gate
  run: |
    pip install httpx
    python -c "
    import httpx, sys
    r = httpx.post('https://klarim.net/api/gate/scan',
        headers={'X-API-Key': '\${{ secrets.KLARIM_API_KEY }}'},
        json={'url': 'https://meuapp.com.br', 'fail_on': 'critical'}, timeout=120)
    d = r.json(); print('Score', d['score']); sys.exit(0 if d['passed'] else 1)"`
  return (
    <Section title="⚙️ Integração no CI/CD">
      <p className="mb-2 text-sm text-slate-600">Guarde sua API key como o secret <code>KLARIM_API_KEY</code> e adicione ao pipeline:</p>
      <pre className="overflow-x-auto rounded-lg bg-slate-900 p-4 text-xs text-slate-100"><code>{snippet}</code></pre>
      <p className="mt-2 text-xs text-slate-500">Mais exemplos (GitLab, Bitbucket, curl) em <a href="/security-gate#como-funciona" className="text-brand-500">/security-gate</a>.</p>
    </Section>
  )
}

export default function GatePortal() {
  const [selected, setSelected] = useState(null)
  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Security Gate</h1>
        <p className="text-sm text-slate-500">Scan de segurança pós-deploy no seu CI/CD.</p>
      </div>
      <ApiKeyCard />
      {selected
        ? <Runs project={selected} onBack={() => setSelected(null)} />
        : <Projects onSelect={setSelected} />}
      <Integration />
    </div>
  )
}
