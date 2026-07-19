// KL-82 — Resultado do scan com confiança progressiva. Recebe o payload já FILTRADO por
// nível de acesso (o backend nunca envia evidência/detalhe a anonymous/unconfirmed) e
// renderiza condicionalmente. Linguagem neutra ("Este site", não "Seu site") no contexto
// público (KL-82 Bloco 9). Mobile-first: alvos ≥44px, botões w-full sm:w-auto.
import { useEffect, useState } from 'react';
import ShareScore from '../account/ShareScore.jsx';

const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8';
const btn =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-slate-950 transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const btnGhost =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl border border-slate-700 px-5 py-3 ' +
  'text-base font-semibold text-slate-200 transition-colors hover:bg-slate-800 active:scale-[0.98]';

const SEMA = {
  verde: { dot: '🟢', ring: 'ring-emerald-500/40', text: 'text-emerald-400' },
  amarelo: { dot: '🟡', ring: 'ring-amber-500/40', text: 'text-amber-400' },
  vermelho: { dot: '🔴', ring: 'ring-red-500/40', text: 'text-red-400' },
};

export default function ScanResultDetail({ result, url = '' }) {
  const level = result.access_level || 'anonymous';
  const full = level === 'confirmed' || level === 'alert_session';
  const domain = result.domain || result.profile_domain || '';

  return (
    <div className="space-y-8">
      <ScoreHero result={result} domain={domain} url={url} />

      {/* Benchmark — travado no anônimo (mostra que existe, sem revelar) */}
      {level === 'anonymous' ? (
        <LockedSection title="📊 Benchmark do setor"
          cta="Crie uma conta gratuita para comparar com o mercado" url={url} />
      ) : (
        <BenchmarkSection result={result} />
      )}

      {/* Riscos para o negócio */}
      <RisksSection result={result} level={level} url={url} />

      {/* Categorias de verificações */}
      {level === 'anonymous' ? (
        <CategoryBars categories={result.categories_preview || []} url={url} />
      ) : level === 'unconfirmed' ? (
        <CategoriesSummary categories={result.categories || []} />
      ) : (
        <CategoriesFull categories={result.categories || []} checks={result.checks || []} />
      )}

      {/* Extras do nível completo: PDF + compartilhar */}
      {full && <FullExtras result={result} url={url} domain={domain} />}

      {/* CTA final por nível */}
      {level === 'anonymous' && <SignupInline url={url} />}
      {level === 'unconfirmed' && <ConfirmEmailCTA />}
      {level === 'alert_session' && result.alert_signup && (
        <AlertSignup emailHint={result.alert_email_hint} domain={domain} url={url} />
      )}

      <div className="text-center">
        <a href="/" className="inline-flex min-h-[44px] items-center px-1 text-sm text-brand-400 hover:text-brand-300">
          ← Pesquisar outro site
        </a>
      </div>
    </div>
  );
}

// --- Score (hero) ----------------------------------------------------------- #
function ScoreHero({ result, domain, url }) {
  const sema = SEMA[result.semaphore] || SEMA.amarelo;
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const target = result.score ?? 0;
    if (target <= 0) { setShown(target); return; }
    let cur = 0;
    const iv = setInterval(() => {
      cur = Math.min(target, cur + Math.ceil(target / 24));
      setShown(cur);
      if (cur >= target) clearInterval(iv);
    }, 40);
    return () => clearInterval(iv);
  }, [result.score]);

  const hasProfile = !!result.has_profile;
  const profileDomain = result.profile_domain || domain;

  return (
    <div className={`${card} text-center`}>
      <p className="text-sm text-slate-400">{domain}</p>
      <div className={`mx-auto mt-4 flex h-40 w-40 flex-col items-center justify-center rounded-full ring-4 ${sema.ring}`}>
        <span className={`text-6xl font-extrabold ${sema.text}`}>{shown}</span>
        <span className="text-sm text-slate-400">/100</span>
      </div>
      <p className="mt-4 text-2xl">{sema.dot}</p>
      {/* Linguagem neutra (KL-82): "Este site", nunca "Seu site" no público. */}
      <p className="mx-auto mt-3 max-w-md text-lg text-slate-200">
        Este site tem score {result.score}. <a href="/" className="text-brand-400 hover:text-brand-300">E o seu?</a>
      </p>

      <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
        {hasProfile && (
          <a href={`/site/${profileDomain}`} className={`${btn} w-full sm:w-auto`}>Ver perfil completo →</a>
        )}
        <ShareRow domain={profileDomain} score={result.score} url={url} />
      </div>
    </div>
  );
}

// Compartilhar: WhatsApp/LinkedIn são <a href> (sem JS, CSP-safe); copiar usa a ilha React.
function ShareRow({ domain, score, url }) {
  const [copied, setCopied] = useState(false);
  const shareUrl = `https://klarim.net/site/${domain}`;
  const text = `Este site tem score ${score}/100 de segurança no Klarim. Pesquise qualquer site em klarim.net`;
  const wa = `https://wa.me/?text=${encodeURIComponent(text + ' ' + shareUrl)}`;
  const li = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}`;
  function copy() {
    navigator.clipboard?.writeText(shareUrl).then(() => {
      setCopied(true);
      window.klarimTrack?.('share_clicked', { via: 'copy', domain }, url);
      setTimeout(() => setCopied(false), 2000);
    });
  }
  return (
    <div className="flex w-full items-center justify-center gap-2 sm:w-auto">
      <a href={wa} target="_blank" rel="noopener" className={btnGhost}
        onClick={() => window.klarimTrack?.('share_clicked', { via: 'whatsapp', domain }, url)}>WhatsApp</a>
      <a href={li} target="_blank" rel="noopener" className={btnGhost}
        onClick={() => window.klarimTrack?.('share_clicked', { via: 'linkedin', domain }, url)}>LinkedIn</a>
      <button type="button" onClick={copy} className={btnGhost}>{copied ? '✓ Copiado' : '🔗 Copiar'}</button>
    </div>
  );
}

// --- Benchmark -------------------------------------------------------------- #
function BenchmarkSection({ result }) {
  const b = result.benchmark;
  if (!b || !b.count) return null;
  const above = (result.score ?? 0) >= b.avg_score;
  return (
    <div className={card}>
      <p className="text-sm font-medium uppercase tracking-wide text-brand-400/80">Benchmark</p>
      <p className="mt-2 text-slate-300">
        Score: <span className="font-bold text-white">{result.score}</span> · Média dos sites brasileiros:{' '}
        <span className="font-bold text-white">{b.avg_score}</span>
      </p>
      <p className="mt-1 text-sm text-slate-400">
        {above ? 'Este site está acima da média nacional. 👏'
               : 'Este site está abaixo da média nacional — há espaço para melhorar.'}
      </p>
    </div>
  );
}

// --- Riscos ----------------------------------------------------------------- #
function RisksSection({ result, level, url }) {
  const risks = level === 'confirmed' || level === 'alert_session'
    ? (result.risk_summary?.risks || [])
    : (result.risks_preview || []);
  const total = result.risks_total ?? risks.length;
  if (!risks.length && !total) return null;
  const hidden = Math.max(0, total - risks.length);
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Riscos para o negócio</h2>
      <ul className="mt-4 space-y-3">
        {risks.map((r, i) => (
          <li key={r.check_id || i} className="flex gap-3">
            <span aria-hidden="true">{r.icon || '⚠️'}</span>
            <div>
              {r.headline && <p className="font-semibold text-slate-100">{r.headline}</p>}
              <p className="text-sm text-slate-400">{r.message}</p>
            </div>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm text-slate-400">
          🔒 Mais {hidden} risco(s) identificado(s).
          <a href={`/cadastrar${url ? `?url=${encodeURIComponent(url)}` : ''}`}
            className="ml-1 text-brand-400 hover:text-brand-300">Crie conta para ver todos.</a>
        </div>
      )}
    </div>
  );
}

// --- Categorias: barras (anônimo) ------------------------------------------- #
function CategoryBars({ categories, url }) {
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">📋 Detalhes da análise</h2>
      <div className="mt-4 space-y-3">
        {categories.map((cat) => (
          <div key={cat.name} className="flex items-center gap-3">
            <span className="w-36 shrink-0 truncate text-sm text-slate-300 sm:w-44">{cat.name}</span>
            <div className="h-3 flex-1 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.round((cat.pass_ratio || 0) * 100)}%` }} />
            </div>
            <span className="text-slate-500" aria-hidden="true">🔒</span>
          </div>
        ))}
      </div>
      <p className="mt-4 text-sm text-slate-400">
        48 verificações realizadas.
        <a href={`/cadastrar${url ? `?url=${encodeURIComponent(url)}` : ''}`}
          className="ml-1 text-brand-400 hover:text-brand-300">Crie conta para ver os detalhes.</a>
      </p>
    </div>
  );
}

// --- Categorias: resumo com números, sem detalhe (unconfirmed) -------------- #
function CategoriesSummary({ categories }) {
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">📋 Detalhes da análise</h2>
      <div className="mt-4 space-y-2">
        {categories.map((cat) => (
          <div key={cat.name} className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3">
            <span className="text-sm font-medium text-slate-200">{cat.name}</span>
            <span className={cat.fail_count > 0 ? 'text-red-400' : 'text-emerald-400'}>
              {cat.pass_count}/{cat.total} {cat.fail_count > 0 ? '⚠️' : '✅'}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-4 text-sm text-slate-400">
        Confirme seu e-mail para ver o detalhe de cada verificação e baixar o relatório em PDF.
      </p>
    </div>
  );
}

// --- Categorias: completo com accordion (confirmed / alert_session) --------- #
function CategoriesFull({ categories, checks }) {
  return (
    <div>
      <h2 className="text-lg font-bold text-white">Detalhes da análise</h2>
      <div className="mt-4 space-y-3">
        {categories.map((cat) => {
          const list = checks.filter((c) => c.category === cat.name);
          return (
            <details key={cat.name} open={cat.has_high_fails}>
              <summary className="flex min-h-[44px] cursor-pointer items-center justify-between rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3">
                <span className="font-semibold text-white">{cat.name}</span>
                <span className={cat.fail_count > 0 ? 'text-red-400' : 'text-emerald-400'}>
                  {cat.pass_count}/{cat.total} {cat.fail_count > 0 ? '⚠️' : '✅'}
                </span>
              </summary>
              <ul className="mt-2 space-y-1.5 pl-4">
                {list.map((c) => <CheckRow key={c.check_id} check={c} />)}
              </ul>
            </details>
          );
        })}
      </div>
    </div>
  );
}

function CheckRow({ check }) {
  const [open, setOpen] = useState(false);
  const isFail = check.status === 'FAIL';
  const icon = isFail ? '❌' : check.status === 'PASS' ? '✅' : check.status === 'locked' ? '🔒' : '⚪';
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
            <p className="text-xs text-slate-500">{[check.owasp, check.cwe, check.lgpd].filter(Boolean).join(' · ')}</p>
          )}
        </div>
      )}
    </li>
  );
}

// --- Extras do nível completo: PDF + compartilhar --------------------------- #
function FullExtras({ result, url, domain }) {
  const exec = result.report_urls?.executive;
  const tech = result.report_urls?.technical;
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-4">
      {(exec || tech) && (
        <div className="relative w-full sm:w-auto">
          <button type="button" onClick={() => setOpen((o) => !o)} className={`${btn} w-full sm:w-auto`}>
            📄 Baixar PDF <span className="text-sm">▾</span>
          </button>
          {open && (
            <div className="absolute z-10 mt-2 w-full overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-xl sm:w-64">
              {exec && (
                <a href={`/api${exec}`} className="block px-4 py-3 text-sm text-slate-200 hover:bg-slate-800" onClick={() => setOpen(false)}>
                  📋 Relatório Executivo<br /><span className="text-xs text-slate-500">linguagem acessível</span>
                </a>
              )}
              {tech && (
                <a href={`/api${tech}`} className="block border-t border-slate-800 px-4 py-3 text-sm text-slate-200 hover:bg-slate-800" onClick={() => setOpen(false)}>
                  📊 Relatório Técnico<br /><span className="text-xs text-slate-500">OWASP / CWE / LGPD</span>
                </a>
              )}
            </div>
          )}
        </div>
      )}
      {result.has_profile && <ShareScore domain={result.profile_domain || domain} score={result.score} />}
    </div>
  );
}

// --- CTAs por nível --------------------------------------------------------- #
function SignupInline({ url }) {
  const box = `${card} border-brand-500/30 bg-brand-500/5 text-center`;
  return (
    <div className={box}>
      <h3 className="text-lg font-bold text-white">Veja a análise completa, grátis</h3>
      <p className="mt-1 text-sm text-slate-300">
        Crie sua conta em 10 segundos e desbloqueie o detalhe das 48 verificações, o benchmark
        do setor e o relatório em PDF.
      </p>
      <form action="/cadastrar" method="GET" className="mt-4">
        {url && <input type="hidden" name="url" value={url} />}
        <button type="submit" className={`${btn} w-full sm:w-auto`}
          onClick={() => window.klarimTrack?.('signup_inline_clicked', {}, url)}>
          Criar conta gratuita →
        </button>
      </form>
      <p className="mt-3 text-xs text-slate-500">Sem cartão. Pesquisas ilimitadas.</p>
    </div>
  );
}

// Fluxo 2 (KL-82 Slice 3): quem chegou pelo link do alerta cria conta só com SENHA (o e-mail
// vem do cookie HMAC-validado no backend). Sucesso → dashboard; e-mail já com conta → login.
function AlertSignup({ emailHint, domain, url }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const box = `${card} border-brand-500/30 bg-brand-500/5`;

  async function submit(e) {
    e.preventDefault();
    setError('');
    if (password.length < 8) return setError('A senha precisa ter ao menos 8 caracteres.');
    setBusy(true);
    const res = await fetch('/api/account/signup-from-alert', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (res.ok && data.existing_account) {
      window.location.href = `/entrar?redirect=${encodeURIComponent('/dashboard')}`;
      return;
    }
    if (res.ok) {
      window.klarimTrack?.('account_created_alert', {}, url);
      window.klarimTrack?.('alert_session_converted', {}, url);
      window.location.href = `/dashboard?claimed=${encodeURIComponent(domain)}`;
      return;
    }
    setError(data.detail || 'Não foi possível criar a conta.');
  }

  return (
    <div className={box}>
      <h3 className="text-lg font-bold text-white">Monitore {domain} — crie sua conta</h3>
      <p className="mt-1 text-sm text-slate-300">
        Seu e-mail{emailHint ? ` (${emailHint})` : ''} já está confirmado. Defina uma senha para
        acompanhar a evolução do score e receber alertas.
      </p>
      {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
      <form onSubmit={submit} className="mt-4 flex flex-col gap-3 sm:flex-row">
        <input type="password" required minLength={8} value={password}
          onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
          placeholder="crie uma senha (mín. 8)"
          className="h-12 w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white placeholder:text-slate-500 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 sm:flex-1" />
        <button type="submit" disabled={busy} className={`${btn} w-full sm:w-auto`}>
          {busy ? 'Criando…' : 'Criar conta →'}
        </button>
      </form>
    </div>
  );
}

function ConfirmEmailCTA() {
  const box = `${card} border-brand-500/30 bg-brand-500/5 text-center`;
  return (
    <div className={box}>
      <h3 className="text-lg font-bold text-white">Confirme seu e-mail para o relatório completo</h3>
      <p className="mt-1 text-sm text-slate-300">
        Enviamos um link de confirmação para o seu e-mail. Confirme para ver o detalhe de cada
        verificação e baixar o PDF.
      </p>
    </div>
  );
}

// --- Seção travada com blur + cadeado (preview, não punição — KL-82 regra 8) - #
function LockedSection({ title, cta, url }) {
  return (
    <div className={`relative overflow-hidden ${card}`}>
      <h3 className="text-lg font-bold text-white">{title}</h3>
      <div className="mt-3 select-none blur-sm pointer-events-none" aria-hidden="true">
        <div className="h-3 w-3/4 rounded-full bg-slate-700" />
        <div className="mt-3 h-3 w-2/3 rounded-full bg-slate-800" />
        <div className="mt-3 h-3 w-1/2 rounded-full bg-slate-700" />
      </div>
      <div className="absolute inset-0 flex items-center justify-center rounded-2xl bg-slate-950/60">
        <div className="text-center">
          <span className="text-2xl" aria-hidden="true">🔒</span>
          <p className="mt-2 text-sm text-slate-300">{cta}</p>
          <a href={`/cadastrar${url ? `?url=${encodeURIComponent(url)}` : ''}`} className={`${btn} mt-3 text-sm`}>
            Criar conta gratuita →
          </a>
        </div>
      </div>
    </div>
  );
}
