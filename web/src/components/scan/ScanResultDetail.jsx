// KL-82 → KL-89 — Resultado do scan com confiança progressiva + fixes de conversão.
//
// Recebe o payload já FILTRADO por nível de acesso (o backend nunca envia evidência/impacto/
// LGPD a anonymous/unconfirmed) e renderiza conforme a "tabela de visibilidade" do KL-89
// (`lib/scanView.viewFlags`). As MESMAS regras valem para desktop e mobile — a divergência
// desktop-mostra-tudo / mobile-esconde-tudo acabou; o que muda é só o LAYOUT (2 colunas no
// lg), não o conteúdo nem o nível de acesso.
//
// Ordem "above the fold" (KL-89 item 3): score+semáforo → frase contextual → compartilhar+PDF
// → CTA de conta → barras de categoria → 1 risco → (abaixo) checks/LGPD. A linguagem adapta
// pela ORIGEM (visitante do alerta vê "Seu site" + só senha; orgânico vê "Este site" + e-mail+
// senha), nunca pelo dispositivo.
import { useEffect, useState } from 'react';
import {
  viewFlags, scoreHeadline, shareLabel, ctaCopy, maskedEmailOf, reportUrls,
} from '../../lib/scanView.js';
import { safeScanDomain } from '../../lib/scanTitle.js';

const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8';
const btn =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const btnGhost =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl border border-slate-700 px-4 py-3 ' +
  'text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-800 active:scale-[0.98]';
// PDF = entrega de maior valor → destaque com o laranja da marca (brand-500 = #ff6b35).
// `text-[var(--accent-text)]` garante contraste no tema light e no dark (KL-87), não `text-white`.
const btnAccent =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-xl bg-brand-500 px-4 py-3 ' +
  'text-sm font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98]';
const inputCls =
  'h-12 w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';

const SEMA = {
  verde: { dot: '🟢', ring: 'ring-emerald-500/40', text: 'text-emerald-400' },
  amarelo: { dot: '🟡', ring: 'ring-amber-500/40', text: 'text-amber-400' },
  vermelho: { dot: '🔴', ring: 'ring-red-500/40', text: 'text-red-400' },
};

// Data do último scan (timestamp naive do Postgres → adiciona Z antes do Date, KL-51 parseUTC).
function fmtScanDate(s) {
  if (!s) return '';
  let iso = String(s).trim().replace(' ', 'T');
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) iso += 'Z';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const hhmm = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const now = new Date();
  return d.toDateString() === now.toDateString()
    ? `hoje, ${hhmm}`
    : `${d.toLocaleDateString('pt-BR')} ${hhmm}`;
}

export default function ScanResultDetail({ result, url = '', onRefresh = null }) {
  const flags = viewFlags(result);
  // Sanitização defensiva (fix 2026-07-21): NUNCA exibe input cru no corpo — só o hostname limpo.
  // O backend já rejeita input inválido, mas o `domain` usado em todo o card/CTA/breadcrumb passa
  // por `safeScanDomain` (retorna '' se não for domínio → cai nos fallbacks "este site").
  const domain = safeScanDomain(result.domain || result.profile_domain || '');

  // Coluna do relatório. Ordem (KL-89 fix 1): RISCOS primeiro (linguagem de negócio, converte)
  // → benchmark (contextualiza o score) → detalhes técnicos → indicadores de privacidade.
  const details = (
    <div className="space-y-6">
      <RisksSection result={result} />
      <BenchmarkSection result={result} />
      <CategoriesSection result={result} flags={flags} />
      <PrivacySection result={result} flags={flags} url={url} />
    </div>
  );

  // Bloco lateral: CTA de conta (quem não tem) / confirme e-mail (unconfirmed) / monitorar (confirmed).
  let aside = null;
  if (flags.showCTA) aside = <AccountCTA flags={flags} result={result} domain={domain} url={url} />;
  else if (flags.level === 'unconfirmed') aside = <ConfirmEmailCTA />;
  else aside = <MonitorNote domain={domain} />;

  return (
    <div className="space-y-6">
      <ScoreHero result={result} flags={flags} domain={domain} url={url} onRefresh={onRefresh} />

      {/* 2 colunas no desktop (relatório + CTA fixo) — preenche telas largas sem linhas longas.
          No mobile empilha na ordem do card: score → share (no hero) → CTA → detalhes. */}
      <div className="grid gap-6 lg:grid-cols-3">
        <aside className="order-1 lg:order-2 lg:col-span-1">
          <div className="lg:sticky lg:top-24">{aside}</div>
        </aside>
        <div className="order-2 space-y-6 lg:order-1 lg:col-span-2">{details}</div>
      </div>

      <div className="text-center">
        <a href="/" className="inline-flex min-h-[44px] items-center px-1 text-sm text-brand-400 hover:text-brand-300">
          ← Pesquisar outro site
        </a>
      </div>
    </div>
  );
}

// --- Score (hero): score + semáforo + frase contextual + share/PDF -------------------------- #
function ScoreHero({ result, flags, domain, url, onRefresh }) {
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
  const head = scoreHeadline(result.score, flags.alertVisitor);

  return (
    <div className={`${card} text-center`}>
      <p className="text-sm text-slate-400">{domain}</p>
      <div className={`mx-auto mt-4 flex h-40 w-40 flex-col items-center justify-center rounded-full ring-4 ${sema.ring}`}>
        <span className={`text-6xl font-extrabold ${sema.text}`}>{shown}</span>
        <span className="text-sm text-slate-400">/100</span>
      </div>
      <p className="mt-4 text-2xl">{sema.dot}</p>
      {/* Linguagem contextual (KL-89 item 4): alerta = "Seu site"; orgânico = "Este site. E o seu?". */}
      <p className="mx-auto mt-3 max-w-md text-lg text-slate-200">
        {head.lead}{head.tail ? ` ${head.tail}` : ''}{' '}
        {head.question && <a href="/" className="text-brand-400 hover:text-brand-300">{head.question}</a>}
      </p>

      {hasProfile && (
        <div className="mt-5">
          <a href={`/site/${profileDomain}`} className={`${btn} w-full sm:w-auto`}>Ver perfil completo →</a>
        </div>
      )}

      <ShareRow result={result} domain={profileDomain} flags={flags} url={url} />

      {/* KL-89 P0: data do último scan + "Atualizar" (ação secundária, não dominante).
          Quando o resultado é parcial (scan rápido/free), o CTA convida à análise completa. */}
      {result.scan_date && fmtScanDate(result.scan_date) && (
        <p className="mt-4 text-xs text-slate-500">
          {result.partial ? 'Análise rápida' : 'Última análise'}: {fmtScanDate(result.scan_date)}
          {onRefresh && (
            <>
              {' · '}
              <button type="button" onClick={onRefresh}
                className="text-brand-400 underline-offset-2 hover:text-brand-300 hover:underline">
                {result.partial ? 'Ver análise completa (48 verificações) →' : 'Atualizar análise →'}
              </button>
            </>
          )}
        </p>
      )}
    </div>
  );
}

// Compartilhar (WhatsApp/LinkedIn = <a href>, CSP-safe) + Copiar + PDF na MESMA linha (KL-89
// item 3): mesmo peso visual, sem botão de PDF isolado. O PDF é público (paywall off).
function ShareRow({ result, domain, flags, url }) {
  const [copied, setCopied] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);
  const shareUrl = `https://klarim.net/site/${domain}`;
  const text = `${flags.alertVisitor ? 'Meu' : 'Este'} site tem score ${result.score}/100 de segurança no Klarim. Pesquise qualquer site em klarim.net`;
  const wa = `https://wa.me/?text=${encodeURIComponent(text + ' ' + shareUrl)}`;
  const li = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}`;
  const reports = flags.showPdf ? reportUrls(result, url) : null;

  function copy() {
    navigator.clipboard?.writeText(shareUrl).then(() => {
      setCopied(true);
      window.klarimTrack?.('share_clicked', { via: 'copy', domain }, url);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="mt-6">
      <p className="mb-2 text-center text-sm text-slate-500">{shareLabel(flags.alertVisitor)}</p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <a href={wa} target="_blank" rel="noopener" className={btnGhost}
          onClick={() => window.klarimTrack?.('share_clicked', { via: 'whatsapp', domain }, url)}>WhatsApp</a>
        <a href={li} target="_blank" rel="noopener" className={btnGhost}
          onClick={() => window.klarimTrack?.('share_clicked', { via: 'linkedin', domain }, url)}>LinkedIn</a>
        <button type="button" onClick={copy} className={btnGhost}>{copied ? '✓ Copiado' : '🔗 Copiar'}</button>
        {reports && (
          <div className="relative">
            <button type="button" onClick={() => setPdfOpen((o) => !o)} className={btnAccent}
              aria-expanded={pdfOpen}>📄 Baixar PDF <span className="text-xs">▾</span></button>
            {pdfOpen && (
              <div className="absolute left-1/2 z-10 mt-2 w-64 -translate-x-1/2 overflow-hidden rounded-xl border border-slate-700 bg-slate-900 text-left shadow-xl">
                <a href={`/api${reports.executive}`} className="block px-4 py-3 text-sm text-slate-200 hover:bg-slate-800"
                  onClick={() => { setPdfOpen(false); window.klarimTrack?.('pdf_downloaded', { kind: 'executive', domain }, url); }}>
                  📋 Relatório Executivo<br /><span className="text-xs text-slate-500">linguagem acessível</span>
                </a>
                <a href={`/api${reports.technical}`} className="block border-t border-slate-800 px-4 py-3 text-sm text-slate-200 hover:bg-slate-800"
                  onClick={() => { setPdfOpen(false); window.klarimTrack?.('pdf_downloaded', { kind: 'technical', domain }, url); }}>
                  📊 Relatório Técnico<br /><span className="text-xs text-slate-500">OWASP / CWE / LGPD</span>
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// --- Bloco CTA de conta (some para quem já tem conta) ---------------------------------------- #
// Orgânico: e-mail + senha (signup inline). Alerta: só senha (e-mail confirmado via HMAC no
// backend; mostrado mascarado). Benefícios em linguagem humana (KL-89 item 3/4).
function AccountCTA({ flags, result, domain, url }) {
  const copy = ctaCopy(flags.alertVisitor, domain);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [existing, setExisting] = useState(false);
  const maskedEmail = maskedEmailOf(result);

  async function submit(e) {
    e.preventDefault();
    setError(''); setExisting(false);
    if (password.length < 8) return setError('A senha precisa ter ao menos 8 caracteres.');
    window.klarimTrack?.('signup_inline_clicked', { via: flags.alertVisitor ? 'alert' : 'organic' }, url);
    setBusy(true);
    try {
      if (flags.passwordOnly) {
        // Fluxo 2 (KL-82 Slice 3): e-mail vem do cookie HMAC → só senha.
        const res = await fetch('/api/account/signup-from-alert', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password }),
        });
        const data = await res.json().catch(() => ({}));
        setBusy(false);
        if (res.ok && data.existing_account) { setExisting(true); return; }
        if (res.ok) {
          window.klarimTrack?.('account_created_alert', {}, url);
          window.klarimTrack?.('alert_session_converted', {}, url);
          window.location.href = `/dashboard?claimed=${encodeURIComponent(domain)}`;
          return;
        }
        setError(data.detail || 'Não foi possível criar a conta.');
        return;
      }
      // Orgânico: e-mail + senha (KL-82 Slice 2). Sem código; conta na hora + e-mail de confirmação.
      const res = await fetch('/api/account/signup', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, url: url || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      setBusy(false);
      if (res.ok) {
        window.klarimTrack?.('account_created', {}, url);
        const c = data.claim;
        if (c?.site_added && c?.domain) {
          window.location.href = `/dashboard?${c.is_owner ? 'claimed' : 'added'}=${encodeURIComponent(c.domain)}`;
        } else {
          window.location.href = '/dashboard';
        }
        return;
      }
      if (res.status === 409) { setExisting(true); return; }
      if (res.status === 400 && /descart|permanente/i.test(data.detail || '')) {
        return setError('Use um e-mail permanente para criar sua conta.');
      }
      if (res.status === 429) return setError('Limite de cadastros atingido. Tente novamente mais tarde.');
      setError(data.detail || 'Não foi possível criar a conta.');
    } catch {
      setBusy(false);
      setError('Falha de conexão. Tente novamente.');
    }
  }

  const loginHref = `/entrar?redirect=${encodeURIComponent('/dashboard')}${!flags.passwordOnly && email ? `&email=${encodeURIComponent(email)}` : ''}`;

  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <h3 className="text-lg font-bold text-white">📊 {copy.title}</h3>
      <ul className="mt-3 space-y-1.5 text-sm text-slate-300">
        {copy.benefits.map((b) => (
          <li key={b} className="flex items-start gap-2"><span className="text-brand-400" aria-hidden="true">·</span>{b}</li>
        ))}
      </ul>

      {existing ? (
        <p className="mt-4 text-sm text-slate-300">
          Já existe uma conta com este e-mail.{' '}
          <a href={loginHref} className="text-brand-400 hover:text-brand-300">Entrar →</a>
        </p>
      ) : (
        <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
          {flags.passwordOnly ? (
            // E-mail read-only (mascarado) num hidden input — o valor real fica no cookie, nunca no HTML.
            maskedEmail && (
              <p className="text-sm text-slate-400">
                Seu e-mail (<span className="font-medium text-slate-200">{maskedEmail}</span>) já está confirmado.
              </p>
            )
          ) : (
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              autoComplete="email" placeholder="seu@email.com.br" className={inputCls} />
          )}
          <input type="password" required minLength={8} value={password}
            onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
            placeholder="crie uma senha (mín. 8)" className={inputCls} />
          {error && <p className="text-sm text-red-300">{error}</p>}
          <button type="submit" disabled={busy} className={`${btn} w-full`}>
            {busy ? 'Criando…' : copy.button}
          </button>
        </form>
      )}
      <p className="mt-3 text-xs text-slate-500">Sem cartão. Pesquisas ilimitadas.</p>
    </div>
  );
}

// unconfirmed — já tem conta, falta confirmar o e-mail (sem CTA de "criar conta").
function ConfirmEmailCTA() {
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <h3 className="text-lg font-bold text-white">Confirme seu e-mail</h3>
      <p className="mt-1 text-sm text-slate-300">
        Enviamos um link de confirmação. Confirme para ver o detalhe de cada verificação, os
        indicadores de LGPD e baixar o relatório completo.
      </p>
      <a href="/dashboard" className={`${btn} mt-4 w-full`}>Ir para o painel →</a>
    </div>
  );
}

// confirmed — já tem conta e acesso completo; oferece adicionar o site ao monitoramento.
function MonitorNote({ domain }) {
  return (
    <div className={card}>
      <h3 className="text-base font-bold text-white">✅ Você tem acesso completo</h3>
      <p className="mt-1 text-sm text-slate-400">
        Adicione {domain || 'este site'} ao monitoramento para acompanhar a evolução do score e
        receber alertas.
      </p>
      <a href={`/dashboard${domain ? `?add=${encodeURIComponent(domain)}` : ''}`} className={`${btn} mt-4 w-full`}>
        + Adicionar ao monitoramento
      </a>
    </div>
  );
}

// --- Benchmark (PÚBLICO — sempre visível, sem cadeado, KL-89 fix 5) -------------------------- #
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

// --- Riscos (linguagem de negócio, KL-20) — TODOS os riscos p/ TODOS (sem gate) --------------- #
function RisksSection({ result }) {
  const risks = result.risk_summary?.risks || result.risks_preview || [];
  if (!risks.length) return null;
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
    </div>
  );
}

// --- Categorias: barras de proporção + accordion de checks (todos os níveis) ------------------ #
// Evidência técnica só aparece no acesso completo (`flags.showEvidence`); sem ela, o check mostra
// só nome/status. As barras (proporção PASS/FAIL) dão a visão rápida; o accordion, o detalhe.
function CategoriesSection({ result, flags }) {
  const categories = result.categories || [];
  const checks = result.checks || [];
  if (!categories.length) return null;
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Detalhes da análise</h2>

      {/* Barras de proporção por categoria (visão rápida) */}
      <div className="mt-4 space-y-2.5">
        {categories.map((cat) => (
          <div key={cat.name} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-sm text-slate-300 sm:w-44">{cat.name}</span>
            <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.round((cat.pass_ratio || 0) * 100)}%` }} />
            </div>
            <span className="w-10 shrink-0 text-right text-xs text-slate-400">{cat.pass_count}/{cat.total}</span>
          </div>
        ))}
      </div>

      {/* Checks detalhados por categoria (expandíveis; evidência só no acesso completo) */}
      {checks.length > 0 && (
        <div className="mt-6 space-y-3">
          {categories.map((cat) => {
            const list = checks.filter((c) => c.category === cat.name);
            if (!list.length) return null;
            return (
              <details key={cat.name} open={cat.has_high_fails}>
                <summary className="flex min-h-[44px] cursor-pointer items-center justify-between rounded-xl border border-slate-800 bg-slate-950/40 px-4 py-3">
                  <span className="font-semibold text-white">{cat.name}</span>
                  <span className={cat.fail_count > 0 ? 'text-red-400' : 'text-emerald-400'}>
                    {cat.pass_count}/{cat.total} {cat.fail_count > 0 ? '⚠️' : '✅'}
                  </span>
                </summary>
                <ul className="mt-2 space-y-1.5 pl-4">
                  {list.map((c) => <CheckRow key={c.check_id} check={c} showEvidence={flags.showEvidence} />)}
                </ul>
              </details>
            );
          })}
        </div>
      )}

      {!flags.showEvidence && (
        <p className="mt-4 text-sm text-slate-400">
          Crie uma conta gratuita para ver a evidência técnica e como corrigir cada verificação.
        </p>
      )}
    </div>
  );
}

function CheckRow({ check, showEvidence }) {
  const [open, setOpen] = useState(false);
  const isFail = check.status === 'FAIL';
  const icon = isFail ? '❌' : check.status === 'PASS' ? '✅' : check.status === 'locked' ? '🔒' : '⚪';
  // Só expande se há ACESSO à evidência E o check de fato traz detalhe (backend só envia no full).
  const canExpand = isFail && showEvidence && (check.evidence || check.impact || check.fix);
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

// --- Indicadores de privacidade / LGPD (só com acesso completo) ------------------------------ #
function PrivacySection({ result, flags, url }) {
  // Travado p/ anonymous E unconfirmed (desktop e mobile) — deriva só do nível (KL-89 fix 2).
  if (!flags.showPrivacy) {
    return (
      <LockedSection title="⚖️ Indicadores de privacidade (LGPD)"
        cta="Crie uma conta gratuita para ver os 8 indicadores de privacidade" url={url} />
    );
  }
  const p = result.privacy_indicators;
  if (!p || !Array.isArray(p.checks) || !p.checks.length) return null;
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Indicadores de privacidade: {p.score}/{p.total}</h2>
      <p className="mt-1 text-sm text-slate-400">Fatos técnicos observáveis por varredura passiva. Referência LGPD por indicador.</p>
      <ul className="mt-4 space-y-2">
        {p.checks.map((c, i) => (
          <li key={c.id || i} className="flex items-start gap-2 text-sm">
            <span aria-hidden="true">{c.status === 'PASS' ? '✅' : '❌'}</span>
            <span className="text-slate-200">{c.name}</span>
            {c.lgpd_ref && <span className="ml-auto shrink-0 text-xs text-slate-500">{c.lgpd_ref}</span>}
          </li>
        ))}
      </ul>
      {p.disclaimer && (
        <p className="mt-4 border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-500">⚖️ {p.disclaimer}</p>
      )}
    </div>
  );
}

// --- Seção travada com teaser (preview, não punição — KL-82 regra 8 / KL-89 regra 9) --------- #
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

// KL-82 Slice 3 mantido como export secundário para compatibilidade — o AccountCTA já cobre o
// fluxo do alerta (só senha). Se algum ponto ainda importar AlertSignup, ele continua válido.
export function AlertSignup({ emailHint, domain, url }) {
  return <AccountCTA
    flags={{ alertVisitor: true, passwordOnly: true, showPdf: true }}
    result={{ alert_email_hint: emailHint }} domain={domain} url={url} />;
}
