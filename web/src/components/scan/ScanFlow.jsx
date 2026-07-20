// KL-82 — Fluxo do scan REESCRITO para "result-first" (confiança progressiva): sem gate de
// e-mail. Ao montar, escaneia via GET /api/scan/result (o cookie de sessão, se houver, é
// enviado com credentials:'include' e o backend decide o nível de acesso) e renderiza o
// resultado progressivo. O antigo fluxo de código de 6 dígitos (KL-25) fica DORMENTE ao fim
// do arquivo como fallback (regra 9 do card — não remover, apenas despriorizar).
import { useEffect, useRef, useState } from 'react';
import { CATEGORIES } from './checks.js';
import ScanResultDetail from './ScanResultDetail.jsx';

const TIPS = [
  '62% dos ataques cibernéticos no Brasil miram PMEs.',
  'O custo médio de um incidente de dados no Brasil passa de R$ 6 milhões.',
  'Menos de 5% das empresas brasileiras têm seguro cibernético.',
  'A LGPD pode multar empresas em até 2% do faturamento.',
  'Sites com HTTPS têm prioridade no ranking do Google.',
];

// Resultado sem exigir e-mail (KL-82). credentials:'include' envia o cookie de sessão →
// conta logada recebe o resultado completo; anônimo recebe o preview (filtrado no backend).
async function fetchResult(url) {
  const res = await fetch(`/api/scan/result?url=${encodeURIComponent(url)}`, { credentials: 'include' });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

// Fallback dormente (KL-25): helper de POST usado só pelo fluxo de código legado.
async function apiPost(path, body, token) {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Scan-Token': token } : {}) },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
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
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 text-base font-semibold ' +
  'text-[var(--accent-text)] transition-colors hover:bg-brand-400 disabled:opacity-60 disabled:cursor-not-allowed';
const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8';

// --------------------------------------------------------------------------- #

export default function ScanFlow({ url: initialUrl = '', user = null }) {
  const [url] = useState(() => initialUrl || (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('url') || '' : ''));
  const [step, setStep] = useState('progress'); // result-first: escaneia direto (sem e-mail)
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [limitMsg, setLimitMsg] = useState('');
  const startedRef = useRef(false);

  const domain = domainOf(url);

  useEffect(() => {
    if (url && !startedRef.current) {
      startedRef.current = true;
      window.klarimTrack?.('scan_started', {}, url);
      runScan();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!url) {
    return (
      <div className={card}>
        <p className="text-slate-300">Nenhum site informado.</p>
        <a href="/" className="mt-4 inline-block text-brand-400 hover:text-brand-300">← Voltar ao início</a>
      </div>
    );
  }

  async function runScan(attempt = 0) {
    try {
      const { ok, status, data } = await fetchResult(url);
      if (status === 429) {
        window.klarimTrack?.('scan_limit_reached', {}, url);
        setLimitMsg(data.detail || 'Limite de pesquisas atingido. Crie uma conta gratuita para pesquisas ilimitadas.');
        setStep('limit');
        return;
      }
      if (!ok || !data || data.score === undefined || data.score === null) {
        // Scan lento pode estourar o timeout do proxy (504) — o servidor termina e CACHEIA;
        // uma re-tentativa após pausa pega o cache quente.
        if (attempt < 2) { await new Promise((r) => setTimeout(r, 15000)); return runScan(attempt + 1); }
        setError('A análise está demorando mais que o esperado. Tente novamente em instantes.');
        setStep('error');
        return;
      }
      setResult(data);
      window.klarimTrack?.(data.access_level === 'anonymous' ? 'scan_anonymous' : 'scan_authenticated', { score: data.score }, url);
      window.klarimTrack?.('scan_completed', { score: data.score }, url);
      window.klarimTrack?.('result_viewed', {}, url);
      setStep('result');
    } catch {
      if (attempt < 2) { await new Promise((r) => setTimeout(r, 20000)); return runScan(attempt + 1); }
      setError('A análise está demorando mais que o esperado. Tente novamente em instantes.');
      setStep('error');
    }
  }

  // KL-89: o resultado usa layout de 2 colunas no desktop → preenche o container expandido da
  // página (não fica preso a max-w-3xl). Os demais passos são um card único, centralizado estreito.
  if (step === 'result' && result) {
    return <ScanResultDetail result={result} url={url} />;
  }
  return (
    <div className="mx-auto max-w-2xl">
      {step === 'progress' && <ProgressStep domain={domain} />}
      {step === 'limit' && <LimitStep message={limitMsg} url={url} />}
      {step === 'error' && <ErrorCard message={error} onRetry={() => { setStep('progress'); startedRef.current = false; runScan(); }} />}
    </div>
  );
}

// --- Progresso (simulado durante o scan bloqueante) ------------------------- #
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
      <p className="mt-6 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm text-slate-400">💡 {TIPS[tip]}</p>
    </div>
  );
}

// --- Limite de pesquisas anônimas atingido (KL-82: rate limit 5/h) ---------- #
function LimitStep({ message, url }) {
  return (
    <div className={`${card} text-center`}>
      <p className="text-2xl" aria-hidden="true">🔒</p>
      <p className="mt-2 text-xl font-bold text-white">Limite de pesquisas atingido</p>
      <p className="mx-auto mt-2 max-w-md text-slate-300">{message}</p>
      <form action="/cadastrar" method="GET" className="mt-5">
        {url && <input type="hidden" name="url" value={url} />}
        <button type="submit" className={`${btn} w-full sm:w-auto`}>Criar conta gratuita →</button>
      </form>
      <p className="mt-3 text-xs text-slate-500">Contas têm pesquisas ilimitadas.</p>
    </div>
  );
}

function ErrorCard({ message, onRetry }) {
  return (
    <div className={card}>
      <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{message}</p>
      <button type="button" onClick={onRetry} className={`${btn} mt-4 w-full sm:w-auto`}>Tentar de novo</button>
    </div>
  );
}

// =========================================================================== #
// FALLBACK DORMENTE (KL-25) — fluxo de e-mail + código de 6 dígitos. Mantido por
// decisão do card (regra 9: não remover, apenas desprioritizar). Não é renderizado
// no fluxo padrão; os endpoints /scan/request-code e /scan/verify-code seguem no backend.
// =========================================================================== #
// eslint-disable-next-line no-unused-vars
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
    </div>
  );
}

// eslint-disable-next-line no-unused-vars
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
        <button type="submit" disabled={busy || code.length < 6} className={btn}>{busy ? 'Verificando…' : 'Verificar →'}</button>
      </form>
      <div className="mt-2 flex items-center justify-between text-sm">
        <button onClick={() => setStep('email')} className="inline-flex min-h-[44px] items-center px-1 text-slate-400 hover:text-white">← Trocar e-mail</button>
        {cooldown > 0
          ? <span className="inline-flex min-h-[44px] items-center px-1 text-slate-500">Reenviar em {cooldown}s</span>
          : <button onClick={() => { resendCode(); setCooldown(45); }} className="inline-flex min-h-[44px] items-center px-1 text-brand-400 hover:text-brand-300">Reenviar código</button>}
      </div>
    </div>
  );
}
