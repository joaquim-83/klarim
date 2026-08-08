import { useEffect, useState } from 'react'
import { card, brandBtn } from './shared.js'
import {
  normalizeUrl, maskCPF, isValidCPF, categorySummary, groupChecksByCategory, wizardNext,
} from '../../lib/gate/ux.js'
import GateIntegrationTabs from './GateIntegrationTabs.jsx'

// KL-153 P2 — wizard SCAN-FIRST (6 steps): 1 URL → 2 scanning → 3 resumo → 4 KYC → 5 completo →
// 6 CI/CD. Consome o scan AVULSO (`POST /api/gate/scan` sem project_id, cookie de sessão) e o KYC
// (`POST /api/account/kyc`). O resultado COMPLETO (step 5) vem do run persistido
// (`GET /api/gate/runs/{id}`) — não re-escaneia (o rate limit por domínio barraria). A lógica pura
// (normalização de URL, CPF, agregação por categoria, transições) vive em ../../lib/gate/ux.js.

const SESSION_KEY = 'klarim_gate_new_key'
const sema = (s) => (s >= 90 ? '🟢' : s >= 50 ? '🟡' : '🔴')

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: 'include',
    headers: opts.body ? { 'Content-Type': 'application/json' } : {}, ...opts,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) { const e = new Error(data.detail || `Erro ${r.status}`); e.status = r.status; e.data = data; throw e }
  return data
}

function Shell({ n, title, children }) {
  return (
    <div className={`${card} border-brand-500/40`}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-lg font-bold text-white">{title}</h3>
        <span className="shrink-0 rounded-full bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-300">Passo {n} de 6</span>
      </div>
      {children}
    </div>
  )
}

function CategoryRows({ cats }) {
  return (
    <ul className="mt-4 space-y-1.5">
      {cats.map((c) => {
        const ok = (c.status || (c.checks_failed > 0 ? 'fail' : 'pass')) === 'pass'
        return (
          <li key={c.name} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-sm">
            <span className="font-medium capitalize text-slate-100">{c.name.replace(/_/g, ' ')}</span>
            <span className={ok ? 'text-green-400' : 'text-red-400'}>
              {c.checks_passed} de {c.checks_total} {ok ? '✅' : '❌'}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export default function GateOnboarding({ onDone, onSkip, kycCompleted = false }) {
  const [step, setStep] = useState(1)
  const [url, setUrl] = useState('')
  const [result, setResult] = useState(null)   // resposta do /gate/scan (basic OU complete)
  const [fullRun, setFullRun] = useState(null)  // run detalhado (results completos) p/ o step 5
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  // KYC
  const [cpf, setCpf] = useState('')
  const [address, setAddress] = useState('')
  const [phone, setPhone] = useState('')
  const [fullKey, setFullKey] = useState('')

  useEffect(() => { try { const k = sessionStorage.getItem(SESSION_KEY); if (k) setFullKey(k) } catch { /* */ } }, [])

  async function runScan() {
    const scanUrl = normalizeUrl(url)
    if (!scanUrl) { setErr('Digite a URL do seu projeto.'); return }
    setErr(''); setBusy(true); setStep(2)
    try {
      const r = await api('/api/gate/scan', { method: 'POST', body: JSON.stringify({ url: scanUrl }) })
      setResult(r)
      setStep(3)
    } catch (e) {
      setErr(e.status === 429 ? (e.data?.detail || 'Limite de consultas. Aguarde um pouco.') : e.message)
      setStep(1)
    } finally { setBusy(false) }
  }

  async function loadFullRun() {
    if (!result?.run_id) { setStep(5); return }
    try { const r = await api(`/api/gate/runs/${result.run_id}`); setFullRun(r.run) } catch { /* usa o resumo */ }
    setStep(5)
  }

  async function submitKyc() {
    if (!isValidCPF(cpf)) { setErr('CPF inválido. Verifique os dígitos.'); return }
    setErr(''); setBusy(true)
    try {
      await api('/api/account/kyc', { method: 'POST', body: JSON.stringify({ cpf, address, phone }) })
      await loadFullRun()   // step 5 com o resultado completo
    } catch (e) {
      setErr(e.status === 409 ? 'Este CPF já está vinculado a outra conta.'
        : e.status === 422 ? 'CPF inválido. Verifique os dígitos e tente novamente.'
        : e.message)
    } finally { setBusy(false) }
  }

  const skipKyc = () => setStep(wizardNext(4, { skip: true }))   // → 6 (CI/CD com o resumo)
  const finish = () => { try { sessionStorage.removeItem(SESSION_KEY) } catch { /* */ } onDone?.() }

  // ---- Step 1: URL ---- //
  if (step === 1) {
    return (
      <Shell n={1} title="Digite a URL do seu projeto">
        <p className="mb-3 text-sm text-slate-300">Qualquer formato — o Gate escaneia o deploy no ar (sem verificar domínio).</p>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-200">URL do projeto</span>
          <input type="text" value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') runScan() }}
            placeholder="https://meuapp.com  ·  meuapp.com  ·  http://localhost:3000"
            className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none" />
        </label>
        {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" onClick={runScan} disabled={busy} className={brandBtn}>Escanear →</button>
          {onSkip && <button type="button" onClick={onSkip} className="text-sm text-slate-400 hover:text-slate-200">Pular wizard →</button>}
        </div>
      </Shell>
    )
  }

  // ---- Step 2: scanning ---- //
  if (step === 2) {
    return (
      <Shell n={2} title="Escaneando…">
        <div className="py-6 text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-brand-500" aria-hidden="true" />
          <p className="mt-3 text-sm text-slate-300">Rodando as verificações de segurança em {normalizeUrl(url)}…</p>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div className="h-full w-2/3 animate-pulse rounded-full bg-brand-500" />
          </div>
        </div>
      </Shell>
    )
  }

  // ---- Step 3: resumo (basic) ---- //
  if (step === 3 && result) {
    const cats = categorySummary(result)
    return (
      <Shell n={3} title="Resultado resumido">
        <div className="flex items-center gap-4">
          <div className="text-4xl font-extrabold text-white">{result.score}<span className="text-lg text-slate-400">/100</span></div>
          <div className="text-3xl" aria-hidden="true">{sema(result.score)}</div>
          <div className="text-sm text-slate-300">{result.passed ? 'Passou ✅' : 'Reprovou ❌'}</div>
        </div>
        <CategoryRows cats={cats} />
        <div className="mt-5 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200">
          🔒 Para ver os detalhes e as recomendações de cada verificação, confirme sua identidade.
        </div>
        <div className="mt-5">
          <button type="button" onClick={() => setStep(wizardNext(3, { kycCompleted }))} className={brandBtn}>
            Ver detalhes completos →
          </button>
        </div>
      </Shell>
    )
  }

  // ---- Step 4: KYC inline ---- //
  if (step === 4) {
    return (
      <Shell n={4} title="Confirme sua identidade">
        <p className="text-sm text-slate-300">
          Para proteger domínios contra uso indevido, os detalhes das verificações exigem confirmação de
          identidade. Seus dados são protegidos pela LGPD e usados exclusivamente para vincular consultas à
          sua conta.
        </p>
        <div className="mt-4 grid gap-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-200">CPF</span>
            <input inputMode="numeric" value={cpf} onChange={(e) => setCpf(maskCPF(e.target.value))}
              placeholder="000.000.000-00"
              className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
            {cpf && !isValidCPF(cpf) && <span className="mt-1 block text-xs text-red-400">CPF incompleto ou inválido.</span>}
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-200">Endereço completo</span>
            <textarea value={address} onChange={(e) => setAddress(e.target.value)} rows={2}
              placeholder="Rua, número, bairro, cidade/UF, CEP"
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-200">Telefone</span>
            <input inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
              placeholder="+55 (00) 00000-0000"
              className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
          </label>
        </div>
        {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" onClick={submitKyc} disabled={busy || !isValidCPF(cpf)} className={brandBtn}>
            {busy ? 'Confirmando…' : 'Confirmar'}
          </button>
          <button type="button" onClick={skipKyc} className="text-sm text-slate-400 hover:text-slate-200">Pular por agora →</button>
        </div>
      </Shell>
    )
  }

  // ---- Step 5: resultado completo ---- //
  if (step === 5) {
    const groups = groupChecksByCategory(fullRun?.results || result?.results || [])
    return (
      <Shell n={5} title="Resultado completo">
        {groups.length === 0 ? (
          <p className="text-sm text-slate-400">Detalhes indisponíveis. Veja o histórico no dashboard.</p>
        ) : (
          <div className="space-y-3">
            {groups.map((g) => (
              <details key={g.name} className="rounded-lg border border-slate-800 bg-slate-900/40">
                <summary className="cursor-pointer list-none px-3 py-2 text-sm font-semibold capitalize text-slate-100">
                  {g.name.replace(/_/g, ' ')} ({g.checks.length})
                </summary>
                <ul className="space-y-1 px-3 pb-3 text-sm text-slate-200">
                  {g.checks.map((c, i) => (
                    <li key={i} className="border-t border-slate-800 pt-1">
                      <span>{c.status === 'pass' ? '✅' : c.status === 'fail' ? '❌' : '⚠️'}</span>{' '}
                      <span className="text-slate-400">[{c.severity}]</span> <strong>{c.check}</strong>: {c.detail}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        )}
        <div className="mt-5">
          <button type="button" onClick={() => setStep(6)} className={brandBtn}>Integrar no CI/CD →</button>
        </div>
      </Shell>
    )
  }

  // ---- Step 6: integração CI/CD ---- //
  return (
    <Shell n={6} title="Integre no seu CI/CD">
      <p className="mb-2 text-sm text-slate-300">Rode o Gate a cada deploy. A URL já vem preenchida; a key vem do secret <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-100">KLARIM_KEY</code>.</p>
      {fullKey && (
        <div className="mb-3 rounded-lg border border-green-500/40 bg-green-500/10 p-3 text-sm">
          <p className="text-green-300">Sua API key (guarde — exibida uma vez):</p>
          <code className="mt-1 block break-all font-mono text-slate-100">{fullKey}</code>
        </div>
      )}
      <GateIntegrationTabs url={normalizeUrl(url)} />
      <div className="mt-5">
        <button type="button" onClick={finish} className={brandBtn}>Ir para o dashboard →</button>
      </div>
    </Shell>
  )
}
