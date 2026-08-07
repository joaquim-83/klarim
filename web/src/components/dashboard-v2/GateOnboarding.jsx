import { useEffect, useState } from 'react'
import { card, brandBtn, outlineBtn } from './shared.js'
import { ONBOARDING_PLATFORMS, buildSnippet, secretSteps, DEFAULT_URL, SECRET_NAME }
  from '../../lib/gate/snippets.js'
import GateCodeBlock, { CopyBtn } from './GateCodeBlock.jsx'

// KL-152 P1 — wizard de onboarding (5 steps) que guia o dev até o 1º scan. Aparece quando
// `gate_runs` está vazio; dismissível ("Pular"), reaparece até o 1º scan; some após completar
// (flag em localStorage). Estado 100% no React (a escolha de plataforma não vai ao backend).
// A key crua só é exibida no Step 2 se veio da ativação (sessionStorage) OU se o dev gerar uma nova.

const SESSION_KEY = 'klarim_gate_new_key'   // key crua recém-gerada (ativação/registro/regenerar)

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: opts.body ? { 'Content-Type': 'application/json' } : {}, ...opts })
  if (!r.ok) { let d = `Erro ${r.status}`; try { d = (await r.json()).detail || d } catch { /* */ } throw new Error(d) }
  return r.json()
}

function StepShell({ n, title, children, onSkip }) {
  return (
    <div className={`${card} border-brand-500/40`}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-lg font-bold text-white">{title}</h3>
        <span className="shrink-0 rounded-full bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-300">
          Step {n} de 5
        </span>
      </div>
      {children}
      {onSkip && (
        <div className="mt-5 text-right">
          <button type="button" onClick={onSkip} className="text-sm text-slate-400 hover:text-slate-200">
            Pular wizard →
          </button>
        </div>
      )}
    </div>
  )
}

const sema = (s) => (s >= 90 ? '🟢' : s >= 50 ? '🟡' : '🔴')

export default function GateOnboarding({ onDone, onSkip }) {
  const [step, setStep] = useState(1)
  const [platform, setPlatform] = useState(null)
  const [url, setUrl] = useState(DEFAULT_URL)
  const [fullKey, setFullKey] = useState('')
  const [keyInfo, setKeyInfo] = useState(null)
  const [genBusy, setGenBusy] = useState(false)
  const [firstRun, setFirstRun] = useState(null)

  // Dados no mount: key crua (sessionStorage), metadados da key e a URL do 1º projeto.
  useEffect(() => {
    try { const k = sessionStorage.getItem(SESSION_KEY); if (k) setFullKey(k) } catch { /* */ }
    api('/api/account/gate/key-info').then(setKeyInfo).catch(() => {})
    api('/api/gate/projects').then((r) => {
      const p = (r.projects || [])[0]
      if (p) setUrl(p.url || `https://${p.domain}`)
    }).catch(() => {})
  }, [])

  // Step 4: polling do 1º run a cada 10s.
  useEffect(() => {
    if (step !== 4 || firstRun) return undefined
    let alive = true
    const tick = () => api('/api/gate/runs?limit=1')
      .then((r) => { const run = (r.runs || [])[0]; if (alive && run) setFirstRun(run) })
      .catch(() => {})
    tick()
    const iv = setInterval(tick, 10000)
    return () => { alive = false; clearInterval(iv) }
  }, [step, firstRun])

  async function generateKey() {
    setGenBusy(true)
    try {
      const r = await api('/api/account/gate/regenerate-key', { method: 'POST' })
      if (r.api_key) {
        setFullKey(r.api_key)
        try { sessionStorage.setItem(SESSION_KEY, r.api_key) } catch { /* */ }
      }
    } catch { /* mantém o masked */ } finally { setGenBusy(false) }
  }

  const finish = () => {
    try { sessionStorage.removeItem(SESSION_KEY) } catch { /* */ }
    onDone?.()
  }

  // ---- Step 1: escolher CI/CD ---- //
  if (step === 1) {
    return (
      <StepShell n={1} title="Configurar o Security Gate" onSkip={onSkip}>
        <p className="mb-4 text-sm text-slate-300">Escolha onde roda o seu CI/CD:</p>
        <div className="flex flex-wrap gap-2">
          {ONBOARDING_PLATFORMS.map((p) => (
            <button key={p.id} type="button"
              onClick={() => { setPlatform(p.id); setStep(2) }}
              className="min-h-[44px] rounded-xl border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:border-brand-500 hover:bg-slate-800">
              {p.label}
            </button>
          ))}
        </div>
      </StepShell>
    )
  }

  const plat = platform || 'github'
  const sec = secretSteps(plat)

  // ---- Step 2: adicionar o secret ---- //
  if (step === 2) {
    return (
      <StepShell n={2} title="Adicione a API key como secret" onSkip={onSkip}>
        <ol className="space-y-2 text-sm text-slate-300">
          <li><span className="text-slate-400">1.</span> Vá em <strong className="text-white">{sec.where}</strong></li>
          <li><span className="text-slate-400">2.</span> Nome do secret: <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">{sec.name}</code></li>
          {sec.flags.length > 0 && (
            <li><span className="text-slate-400">3.</span> Marque: {sec.flags.map((f) => (
              <span key={f} className="mr-1 rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-100">☑ {f}</span>))}</li>
          )}
        </ol>
        <div className="mt-4">
          <p className="mb-1 text-sm font-medium text-slate-200">Valor:</p>
          {fullKey ? (
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 font-mono text-sm text-slate-100">{fullKey}</code>
              <CopyBtn text={fullKey} />
            </div>
          ) : (
            <div className="rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm text-slate-300">
              <p>Sua key foi exibida <strong className="text-white">uma única vez</strong> ao ativar
                {keyInfo?.prefix ? <> (prefixo <code className="text-slate-100">{keyInfo.prefix}…</code>)</> : null}.
                Se não a guardou, gere uma nova:</p>
              <button type="button" onClick={generateKey} disabled={genBusy}
                className={`mt-3 ${outlineBtn}`}>
                {genBusy ? 'Gerando…' : 'Gerar nova key'}
              </button>
              <p className="mt-2 text-xs text-slate-500">A anterior continua válida por 1h (nenhum pipeline em andamento quebra).</p>
            </div>
          )}
          {sec.exportLine && (
            <p className="mt-2 text-xs text-slate-400">No terminal: <code className="text-slate-200">{sec.exportLine}{fullKey || 'KLM_…'}</code></p>
          )}
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          <button type="button" onClick={() => setStep(1)} className={outlineBtn}>← Voltar</button>
          <button type="button" onClick={() => setStep(3)} className={brandBtn}>Já adicionei ✓</button>
        </div>
      </StepShell>
    )
  }

  // ---- Step 3: colar o YAML ---- //
  if (step === 3) {
    return (
      <StepShell n={3} title="Cole no seu pipeline" onSkip={onSkip}>
        <p className="mb-3 text-sm text-slate-300">
          Cole este trecho <strong className="text-white">após o deploy</strong> no seu workflow — a URL já
          está preenchida ({url}) e a key vem do secret <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">{SECRET_NAME}</code>:
        </p>
        <GateCodeBlock code={buildSnippet(plat, url)} filename={plat} />
        <div className="mt-6 flex flex-wrap gap-2">
          <button type="button" onClick={() => setStep(2)} className={outlineBtn}>← Voltar</button>
          <button type="button" onClick={() => setStep(4)} className={brandBtn}>Já colei ✓</button>
        </div>
      </StepShell>
    )
  }

  // ---- Step 4: fazer deploy + polling ---- //
  if (step === 4) {
    return (
      <StepShell n={4} title="Faça o deploy" onSkip={onSkip}>
        {!firstRun ? (
          <div className="py-4 text-center">
            <p className="text-sm text-slate-300">Faça push de qualquer mudança e aguarde o pipeline rodar.</p>
            <div className="mt-4 inline-flex items-center gap-3 text-slate-300">
              <span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-600 border-t-brand-500" aria-hidden="true" />
              <span className="text-sm">Aguardando o primeiro scan…</span>
            </div>
            <p className="mt-2 text-xs text-slate-500">Verificando a cada 10 segundos.</p>
          </div>
        ) : (
          <div className="rounded-xl border border-green-500/40 bg-green-500/10 p-4">
            <p className="font-semibold text-white">✅ Primeiro scan recebido!</p>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-200">
              <span>Score: <strong>{firstRun.score}/100</strong> {sema(firstRun.score)}</span>
              {firstRun.duration_ms != null && <span>Duração: {Math.round(firstRun.duration_ms / 1000)}s</span>}
              <span>Checks: {(firstRun.checks_run || []).length} rodados</span>
              <span>{firstRun.passed ? 'Passou ✅' : 'Reprovou ❌'}</span>
            </div>
            <button type="button" onClick={() => setStep(5)} className={`mt-4 ${brandBtn}`}>
              Ver próximos passos →
            </button>
          </div>
        )}
      </StepShell>
    )
  }

  // ---- Step 5: pronto ---- //
  return (
    <StepShell n={5} title="🎉 Security Gate configurado!">
      <p className="text-sm text-slate-300">Seu site será escaneado a cada deploy. Próximos passos:</p>
      <ul className="mt-3 space-y-2 text-sm text-slate-200">
        <li>👥 Convide um colega do time (Convites, no dashboard)</li>
        <li>⚙️ Personalize os checks no <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">security-gate.yml</code></li>
        <li>➕ Adicione outro projeto</li>
        <li>📈 Acompanhe o histórico de scores por projeto</li>
      </ul>
      <button type="button" onClick={finish} className={`mt-6 ${brandBtn}`}>Ir para o dashboard →</button>
    </StepShell>
  )
}
