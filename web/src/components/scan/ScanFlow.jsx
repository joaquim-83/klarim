import { useEffect, useRef, useState } from 'react';
import { CATEGORIES, groupByCategory } from './checks.js';

const TIPS = [
  '62% dos ataques cibernéticos no Brasil miram PMEs.',
  'O custo médio de um incidente de dados no Brasil passa de R$ 6 milhões.',
  'Menos de 5% das empresas brasileiras têm seguro cibernético.',
  'A LGPD pode multar empresas em até 2% do faturamento.',
  'Sites com HTTPS têm prioridade no ranking do Google.',
];

// --- helpers de API (mesma origem; o Nginx roteia /api → FastAPI) ------------ #
async function apiPost(path, body, token) {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Scan-Token': token } : {}) },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

async function fetchSummary(url, token) {
  const res = await fetch(`/api/scan/summary?url=${encodeURIComponent(url)}`, {
    headers: token ? { 'X-Scan-Token': token } : {},
  });
  return res.json();
}

function domainOf(url) {
  try {
    return new URL(url.includes('://') ? url : `https://${url}`).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function maskEmail(e) {
  const [u, d] = (e || '').split('@');
  if (!u || !d) return e;
  const mu = u.length <= 2 ? u[0] + '*' : u.slice(0, 2) + '*'.repeat(Math.max(1, u.length - 2));
  return `${mu}@${d}`;
}

const field =
  'w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-3.5 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';
const btn =
  'inline-flex items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 text-base font-semibold ' +
  'text-slate-950 transition-colors hover:bg-brand-400 disabled:opacity-60 disabled:cursor-not-allowed';
const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8';

// --------------------------------------------------------------------------- #

export default function ScanFlow({ url: initialUrl = '' }) {
  const [url] = useState(() => initialUrl || (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('url') || '' : ''));
  const [step, setStep] = useState('email'); // email | code | progress | result | limit
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [limitMsg, setLimitMsg] = useState('');
  const [result, setResult] = useState(null);
  const tokenRef = useRef('');

  const domain = domainOf(url);

  if (!url) {
    return (
      <div className={card}>
        <p className="text-slate-300">Nenhum site informado.</p>
        <a href="/" className="mt-4 inline-block text-brand-400 hover:text-brand-300">← Voltar ao início</a>
      </div>
    );
  }

  async function submitEmail(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const { data } = await apiPost('/scan/request-code', { email, url });
      if (data.status === 'code_sent') setStep('code');
      else if (data.status === 'limit_reached' || data.status === 'already_scanned') {
        setLimitMsg(data.message || 'Limite atingido.');
        setStep('limit');
      } else setError(data.detail || data.message || 'Não foi possível enviar o código.');
    } catch {
      setError('Erro de conexão. Tente novamente.');
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const { data } = await apiPost('/scan/verify-code', { email, code, url });
      if (data.status === 'verified' && data.scan_token) {
        tokenRef.current = data.scan_token;
        setStep('progress');
        runScan(data.scan_token);
      } else setError(data.message || 'Código inválido ou expirado.');
    } catch {
      setError('Erro de conexão. Tente novamente.');
    } finally {
      setBusy(false);
    }
  }

  async function resendCode() {
    setError('');
    await apiPost('/scan/request-code', { email, url });
  }

  async function runScan(token) {
    try {
      const data = await fetchSummary(url, token);
      if (data && data.status === 'auth_required') {
        setError('Sessão expirada. Verifique o e-mail novamente.');
        setStep('email');
        return;
      }
      setResult(data);
      setStep('result');
    } catch {
      setError('Não foi possível concluir o scan. Tente novamente.');
      setStep('email');
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      {error && step !== 'progress' && (
        <p className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>
      )}
      {step === 'email' && <EmailStep {...{ domain, email, setEmail, busy, submitEmail }} />}
      {step === 'code' && <CodeStep {...{ email, code, setCode, busy, submitCode, resendCode, setStep }} />}
      {step === 'progress' && <ProgressStep domain={domain} />}
      {step === 'result' && result && <ResultView data={result} domain={domain} email={email} url={url} />}
      {step === 'limit' && <LimitStep message={limitMsg} />}
    </div>
  );
}

// --- Etapa 1: e-mail -------------------------------------------------------- #
function EmailStep({ domain, email, setEmail, busy, submitEmail }) {
  return (
    <div className={card}>
      <p className="text-sm text-slate-400">Analisar</p>
      <p className="text-xl font-bold text-white">{domain}</p>
      <p className="mt-4 text-slate-300">Para receber o relatório completo, confirme seu e-mail:</p>
      <form onSubmit={submitEmail} className="mt-4 flex flex-col gap-3">
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="voce@empresa.com.br" autoComplete="email" className={field} />
        <button type="submit" disabled={busy} className={btn}>{busy ? 'Enviando…' : 'Continuar →'}</button>
      </form>
      <p className="mt-4 text-sm text-slate-500">Usamos apenas para enviar o relatório. Sem spam, sem cadastro.</p>
    </div>
  );
}

// --- Etapa 2: código -------------------------------------------------------- #
function CodeStep({ email, code, setCode, busy, submitCode, resendCode, setStep }) {
  const [cooldown, setCooldown] = useState(45);
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);
  return (
    <div className={card}>
      <p className="text-xl font-bold text-white">Código de verificação</p>
      <p className="mt-2 text-slate-300">
        Enviamos um código de 6 dígitos para <span className="font-medium text-white">{maskEmail(email)}</span>
      </p>
      <form onSubmit={submitCode} className="mt-4 flex flex-col gap-3">
        <input inputMode="numeric" pattern="[0-9]*" maxLength={6} required value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          placeholder="______" className={`${field} text-center text-2xl tracking-[0.5em]`} />
        <button type="submit" disabled={busy || code.length < 6} className={btn}>
          {busy ? 'Verificando…' : 'Verificar →'}
        </button>
      </form>
      <div className="mt-4 flex items-center justify-between text-sm">
        <button onClick={() => setStep('email')} className="text-slate-400 hover:text-white">← Trocar e-mail</button>
        {cooldown > 0 ? (
          <span className="text-slate-500">Reenviar em {cooldown}s</span>
        ) : (
          <button onClick={() => { resendCode(); setCooldown(45); }} className="text-brand-400 hover:text-brand-300">
            Reenviar código
          </button>
        )}
      </div>
    </div>
  );
}

// --- Etapa 3: progresso (simulado durante o scan bloqueante) ---------------- #
function ProgressStep({ domain }) {
  const [pct, setPct] = useState(4);
  const [tip, setTip] = useState(0);
  const [catIdx, setCatIdx] = useState(0);
  useEffect(() => {
    const p = setInterval(() => setPct((v) => Math.min(94, v + Math.random() * 6 + 2)), 1400);
    const t = setInterval(() => setTip((i) => (i + 1) % TIPS.length), 5000);
    const c = setInterval(() => setCatIdx((i) => Math.min(CATEGORIES.length, i + 1)), 3500);
    return () => { clearInterval(p); clearInterval(t); clearInterval(c); };
  }, []);
  return (
    <div className={card}>
      <p className="text-sm text-slate-400">Analisando</p>
      <p className="text-xl font-bold text-white">{domain}</p>

      <div className="mt-6 h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-brand-500 transition-all duration-700 ease-out" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-sm text-slate-400">{Math.round(pct)}% — 48 verificações de segurança</p>

      <ul className="mt-6 space-y-2">
        {CATEGORIES.map((cat, i) => (
          <li key={cat} className="flex items-center gap-2 text-sm">
            <span className={i < catIdx ? 'text-brand-400' : 'text-slate-600'}>{i < catIdx ? '✓' : '○'}</span>
            <span className={i < catIdx ? 'text-slate-200' : 'text-slate-500'}>{cat}</span>
            {i === catIdx && <span className="text-slate-500">analisando…</span>}
          </li>
        ))}
      </ul>

      <p className="mt-6 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm text-slate-400">
        💡 {TIPS[tip]}
      </p>
    </div>
  );
}

// --- Etapa 4: resultado ----------------------------------------------------- #
const SEMA = {
  // semáforo de score: verde/amarelo/vermelho são indicadores funcionais (não branding).
  verde: { dot: '🟢', ring: 'ring-emerald-500/40', text: 'text-emerald-400' },
  amarelo: { dot: '🟡', ring: 'ring-amber-500/40', text: 'text-amber-400' },
  vermelho: { dot: '🔴', ring: 'ring-red-500/40', text: 'text-red-400' },
};

function phraseFor(sema, fails) {
  if (sema === 'verde') return 'Parabéns! Seu site está entre os mais seguros.';
  if (sema === 'vermelho') return `Seu site precisa de atenção urgente: ${fails} ponto(s) de risco.`;
  return `Seu site tem ${fails} ponto(s) de atenção. A maioria é simples de corrigir.`;
}

function ResultView({ data, domain, email = '', url = '' }) {
  const sema = SEMA[data.semaphore] || SEMA.amarelo;
  const checks = [...(data.free_checks || []), ...(data.paid_checks || [])];
  const groups = groupByCategory(checks);
  const [benchmark, setBenchmark] = useState(null);
  const [shown, setShown] = useState(0);

  // reveal do score (0 → score em ~1s)
  useEffect(() => {
    const target = data.score ?? 0;
    if (target <= 0) { setShown(target); return; }
    let cur = 0;
    const iv = setInterval(() => { cur = Math.min(target, cur + Math.ceil(target / 24)); setShown(cur); if (cur >= target) clearInterval(iv); }, 40);
    return () => clearInterval(iv);
  }, [data.score]);

  useEffect(() => {
    fetch('/api/benchmark').then((r) => r.json()).then(setBenchmark).catch(() => {});
  }, []);

  const reportUrl = (kind) => {
    const u = data.report_urls?.[kind];
    return u ? `/api${u}` : null;
  };
  const exec = reportUrl('executive');
  const tech = reportUrl('technical');

  return (
    <div className="space-y-8">
      {/* Score */}
      <div className={`${card} text-center`}>
        <p className="text-lg font-semibold text-white">{domain}</p>
        <div className={`mx-auto mt-4 flex h-40 w-40 flex-col items-center justify-center rounded-full ring-4 ${sema.ring}`}>
          <span className={`text-5xl font-extrabold ${sema.text}`}>{shown}</span>
          <span className="text-sm text-slate-400">/100</span>
        </div>
        <p className="mt-4 text-2xl">{sema.dot}</p>
        <p className="mx-auto mt-2 max-w-md text-slate-300">{phraseFor(data.semaphore, data.fail_count ?? 0)}</p>
      </div>

      {/* Benchmark */}
      {benchmark && benchmark.count > 0 && (
        <div className={card}>
          <p className="text-sm font-medium uppercase tracking-wide text-brand-400/80">Benchmark</p>
          <p className="mt-2 text-slate-300">
            Seu score: <span className="font-bold text-white">{data.score}</span> · Média dos sites brasileiros:{' '}
            <span className="font-bold text-white">{benchmark.avg_score}</span>
          </p>
          <p className="mt-1 text-sm text-slate-400">
            {data.score >= benchmark.avg_score
              ? 'Seu site está acima da média nacional. 👏'
              : 'Seu site está abaixo da média nacional — há espaço para melhorar.'}
          </p>
        </div>
      )}

      {/* CTAs */}
      <div className="flex flex-col gap-3 sm:flex-row">
        {exec && <a href={exec} className={btn}>📄 Baixar relatório (executivo)</a>}
        {tech && (
          <a href={tech} className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700 px-6 py-3.5 text-base font-semibold text-slate-200 transition-colors hover:bg-slate-800">
            📑 Relatório técnico
          </a>
        )}
      </div>

      {/* Detalhes por categoria */}
      <div>
        <h2 className="text-lg font-bold text-white">Detalhes da análise</h2>
        <div className="mt-4 space-y-4">
          {groups.map(([cat, list]) => {
            const ok = list.filter((c) => c.status === 'PASS').length;
            return (
              <div key={cat} className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="font-semibold text-white">
                  {cat} <span className="text-sm font-normal text-slate-400">({ok}/{list.length} ✅)</span>
                </p>
                <ul className="mt-3 space-y-1.5">
                  {list.map((c) => <CheckRow key={c.check_id} check={c} />)}
                </ul>
              </div>
            );
          })}
        </div>
      </div>

      {/* CTA: criar conta (o e-mail já foi verificado no scan) */}
      <div className={`${card} border-brand-500/30 bg-brand-500/5 text-center`}>
        <h3 className="text-lg font-bold text-white">Quer monitorar seu site gratuitamente?</h3>
        <p className="mt-1 text-sm text-slate-300">
          Crie sua conta em 10 segundos. O e-mail já foi verificado — só falta uma senha.
        </p>
        <form action="/cadastrar" method="GET" className="mt-4">
          {email && <input type="hidden" name="email" value={email} />}
          {url && <input type="hidden" name="url" value={url} />}
          <button type="submit" className={`${btn} sm:w-auto`}>Criar conta grátis →</button>
        </form>
      </div>

      <div className="text-center">
        <a href="/" className="text-sm text-brand-400 hover:text-brand-300">← Escanear outro site</a>
      </div>
    </div>
  );
}

function CheckRow({ check }) {
  const [open, setOpen] = useState(false);
  const isFail = check.status === 'FAIL';
  const isLocked = check.status === 'locked';
  const icon = isFail ? '❌' : isLocked ? '🔒' : check.status === 'PASS' ? '✅' : '⚪';
  const canExpand = isFail && (check.evidence || check.impact || check.fix);
  return (
    <li className="text-sm">
      <div className={`flex items-start gap-2 ${canExpand ? 'cursor-pointer' : ''}`} onClick={() => canExpand && setOpen((o) => !o)}>
        <span aria-hidden="true">{icon}</span>
        <span className={isFail ? 'text-slate-100' : 'text-slate-300'}>{check.name}</span>
        {canExpand && <span className="ml-auto text-xs text-slate-500">{open ? 'ocultar' : 'detalhes'}</span>}
      </div>
      {open && canExpand && (
        <div className="ml-6 mt-1.5 space-y-1.5 rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-slate-400">
          {check.impact && <p><span className="text-slate-300">Impacto:</span> {check.impact}</p>}
          {check.evidence && <p><span className="text-slate-300">Evidência:</span> {check.evidence}</p>}
          {check.fix && <p><span className="text-slate-300">Correção:</span> {check.fix}</p>}
          {(check.owasp || check.cwe || check.lgpd) && (
            <p className="text-xs text-slate-500">
              {[check.owasp, check.cwe, check.lgpd].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

// --- Etapa alternativa: limite (paywall ligado) ----------------------------- #
function LimitStep({ message }) {
  return (
    <div className={card}>
      <p className="text-xl font-bold text-white">Limite atingido</p>
      <p className="mt-2 text-slate-300">{message}</p>
      <a href="/" className="mt-4 inline-block text-brand-400 hover:text-brand-300">← Voltar ao início</a>
    </div>
  );
}
