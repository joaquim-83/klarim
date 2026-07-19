// KL-86 — Dashboard redesenhado: 1 chamada (/account/dashboard-summary) → 6 blocos de valor,
// zero espaço vazio. Responde em 5s: "meu site está bem?" (saúde+riscos), "o que mudou?"
// (tendência+evolução), "o que faço agora?" (checklist). Mobile-first (checklist sobe).
import { useEffect, useState } from 'react';
import { apiGet, apiPost, apiPut } from '../../lib/api.js';
import { field, card, btn } from './ui.js';
import PlanSection from './PlanSection.jsx';

const SEMA_COLOR = { verde: '#22c55e', amarelo: '#eab308', vermelho: '#ef4444' };
const SEMA_DOT = { verde: '🟢', amarelo: '🟡', vermelho: '🔴' };
const CAT_ICON = { ok: '✅', warning: '⚠️', critical: '❌' };

function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(parseUTC(s)).toLocaleDateString('pt-BR'); } catch { return '—'; }
}
// Timestamps naive do Postgres → adiciona Z antes de new Date (padrão KL-51).
function parseUTC(s) {
  if (!s) return s;
  return /[Z+]/.test(s) || s.endsWith('00:00') ? s : `${s}Z`;
}
// KL-44 P3 — sites dos clientes do técnico (dono mascarado, link para o laudo).
function TechnicianClients() {
  const [clients, setClients] = useState(null);
  useEffect(() => {
    apiGet('/account/technician/clients').then(({ ok, data }) => setClients(ok ? (data.clients || []) : []));
  }, []);
  if (clients === null || clients.length === 0) return null;
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <p className="text-lg font-bold text-white">Sites dos meus clientes</p>
      <div className="mt-3 space-y-2">
        {clients.map((c) => (
          <div key={c.link_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
            <span className="font-mono text-slate-200">{c.domain}</span>
            <span className="text-slate-400">{c.last_scan_score ?? '—'}/100</span>
            <span className="text-xs text-slate-500">Dono: {c.owner_email}</span>
            <a href={`/scan?url=${encodeURIComponent(c.domain)}`} className="text-xs text-brand-400 hover:text-brand-300">Ver →</a>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard({ user = {} }) {
  const [data, setData] = useState(null);
  const [toast, setToast] = useState('');
  const [confirmed, setConfirmed] = useState(() => user.email_confirmed !== false);
  const [resendState, setResendState] = useState('');
  const [planUpgradeParam] = useState(() =>
    (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('upgrade') : '') || '');
  const [upgradedFlag] = useState(() =>
    typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('upgraded') === '1');

  async function load() {
    const { ok, data: d } = await apiGet('/account/dashboard-summary');
    setData(ok ? d : { has_site: false, checklist: [] });
  }
  useEffect(() => { load(); }, []);

  async function resendConfirmation() {
    setResendState('sending');
    const { ok, status } = await apiPost('/account/resend-confirmation', {});
    setResendState(ok ? 'sent' : status === 429 ? 'limit' : 'error');
  }

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const claimed = q.get('claimed'); const added = q.get('added');
    const blocked = q.get('blocked'); const conf = q.get('confirmed');
    if (claimed) setToast(`✅ ${claimed} adicionado · ✓ Propriedade verificada automaticamente`);
    else if (added) setToast(`✅ ${added} adicionado ao monitoramento`);
    else if (blocked) setToast('✅ Conta criada! Adicione o domínio do seu site para começar a monitorar.');
    else if (conf === '1') setToast('✅ E-mail confirmado! Acesso completo desbloqueado.');
    else if (conf === 'already') setToast('✅ Seu e-mail já estava confirmado.');
    if (conf) setConfirmed(true);
    if (claimed || added || blocked || conf || q.get('upgrade') || q.get('upgraded')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  if (data === null) {
    return <div className="min-h-screen"><p className="text-slate-400">Carregando seu painel…</p></div>;
  }

  const isTech = user.role === 'technician' || user.role === 'both';
  const greetName = data.profile?.company_name || user.name || '';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Olá{greetName ? `, ${greetName}` : ''}</h1>
        {isTech && (
          <p className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-brand-500/40 bg-brand-500/10 px-3 py-0.5 text-sm font-semibold text-brand-300">
            🔧 Profissional de TI
          </p>
        )}
      </div>

      {toast && (
        <div className="flex items-center justify-between rounded-xl border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-300">
          <span>{toast}</span>
          <button onClick={() => setToast('')} className="text-green-400/70 hover:text-green-300">✕</button>
        </div>
      )}

      {!confirmed && (
        <div className="rounded-xl border border-brand-500/30 bg-brand-500/10 px-4 py-3 text-sm">
          <p className="text-slate-200">
            📧 Confirme seu e-mail para desbloquear o relatório completo. Enviamos um link para <strong className="text-white">{user.email}</strong>.
          </p>
          {resendState === 'sent' ? <p className="mt-1 text-brand-300">Link reenviado ✓</p>
            : resendState === 'limit' ? <p className="mt-1 text-slate-400">Aguarde alguns minutos para reenviar.</p>
            : resendState === 'error' ? <p className="mt-1 text-red-300">Não foi possível reenviar agora.</p>
            : <button onClick={resendConfirmation} disabled={resendState === 'sending'}
                className="mt-1 inline-flex min-h-[44px] items-center text-brand-400 hover:text-brand-300 disabled:opacity-60">
                {resendState === 'sending' ? 'Reenviando…' : 'Reenviar link de confirmação →'}
              </button>}
        </div>
      )}

      {isTech && <TechnicianClients />}

      {data.has_site
        ? <SiteDashboard data={data} user={user} onReload={load}
            planUpgrade={planUpgradeParam} planUpgraded={upgradedFlag} />
        : <NoSiteDashboard data={data} user={user} onResend={resendConfirmation} />}
    </div>
  );
}

// =========================================================================== #
// Dashboard COM site — 6 blocos (grid 2/3 + 1/3; mobile: checklist sobe)
// =========================================================================== #
function SiteDashboard({ data, user, onReload, planUpgrade, planUpgraded }) {
  const site = data.site;
  const siteId = site.target_id;
  const [profileModal, setProfileModal] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [resend, setResend] = useState('');
  const [addOpen, setAddOpen] = useState(false);

  async function onChecklistAction(item) {
    if (item.type === 'link') { window.location.href = item.action; return; }
    if (item.action === '/account/resend-confirmation') {
      setResend('sending');
      const { ok } = await apiPost('/account/resend-confirmation', {});
      setResend(ok ? 'sent' : 'error');
      return;
    }
    if (item.action === 'inline_profile_editor') { setProfileModal(true); return; }
    if (item.action === 'share_modal') { setShareOpen((v) => !v); return; }
    if (item.action === 'add_site') { setAddOpen(true); return; }
  }

  return (
    <div>
      {/* KL-87 Parte 3 — fix do gap desktop: `auto-rows-min` + `items-start` (linhas justas) e
          o Checklist ocupa 3 linhas (`lg:row-span-3`) alinhando com Saúde+Riscos+Evolução, sem
          forçar altura na linha da Saúde. Categorias em largura total no fim. A ordem-fonte é a
          ordem MOBILE (saúde→checklist→riscos→categorias→evolução→plano); no desktop os
          `row-start`/`col-start` reposicionam. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 lg:auto-rows-min lg:items-start">
        {/* mobile 1 · desktop col 1-2 / row 1 */}
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-1"><HealthBlock site={site} profile={data.profile} /></div>
        {/* mobile 2 · desktop col 3 / rows 1-3 */}
        <div className="lg:col-start-3 lg:row-start-1 lg:row-span-3">
          <ChecklistBlock items={data.checklist} onAction={onChecklistAction} resend={resend} />
          {shareOpen && <SharePanel domain={site.domain} score={site.score} />}
        </div>
        {/* mobile 3 · desktop col 1-2 / row 2 */}
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-2"><RisksBlock risks={data.risks} siteId={siteId} /></div>
        {/* mobile 4 · desktop col 1-2 / row 4 */}
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-4"><CategoriesBlock cats={data.check_categories} siteId={siteId} /></div>
        {/* mobile 5 · desktop col 1-2 / row 3 */}
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-3"><EvolutionBlock history={data.score_history} score={site.score} /></div>
        {/* mobile 6 · desktop col 3 / row 4 (ao lado das categorias — sem gap) */}
        <div id="plano" className="lg:col-start-3 lg:row-start-4"><PlanSection initialUpgrade={planUpgrade} showUpgradedToast={planUpgraded} /></div>
      </div>

      <OtherSites data={data} addOpen={addOpen} setAddOpen={setAddOpen} onReload={onReload} />

      {profileModal && (
        <ProfileOnboarding targetId={siteId} profile={data.profile}
          onClose={() => setProfileModal(false)} onSaved={onReload} />
      )}
    </div>
  );
}

// Bloco 1 — Saúde do site (hero)
function HealthBlock({ site, profile }) {
  const color = SEMA_COLOR[site.semaphore] || SEMA_COLOR.amarelo;
  const trend = site.trend === 'up'
    ? <span className="text-sm text-green-400">↑ +{site.trend_diff}</span>
    : site.trend === 'down'
      ? <span className="text-sm text-red-400">↓ {site.trend_diff}</span>
      : <span className="text-sm text-slate-500">→ estável</span>;
  return (
    <div className={card}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="truncate text-sm text-slate-400">{site.domain}</h2>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-5xl font-extrabold text-white">{site.score ?? '—'}</span>
            <span className="text-slate-400">/100</span>
            {trend}
          </div>
          {site.rank_position && site.rank_total ? (
            <p className="mt-2 text-sm text-slate-400">
              {site.rank_position}º de {site.rank_total} {site.sector_label || 'sites'} do setor
            </p>
          ) : (
            <p className="mt-2 text-sm text-slate-400">{site.sector_label || 'Setor não classificado'}</p>
          )}
        </div>
        <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full border-4"
          style={{ borderColor: color }}>
          <span className="text-3xl font-bold text-white">{site.score ?? '—'}</span>
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        {SEMA_DOT[site.semaphore] || '⚪'} Último scan: {fmtDate(site.last_scan)} · Próximo: {fmtDate(site.next_scan)}
      </p>
    </div>
  );
}

// Bloco 2 — Riscos para o negócio
function RisksBlock({ risks, siteId }) {
  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">⚠️ Riscos para o seu negócio</h3>
      {(!risks || risks.length === 0) ? (
        <p className="mt-3 text-green-400">Nenhum risco identificado. Seu site está excelente! 🎉</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {risks.map((r, i) => (
            <li key={r.check_id || i} className="flex gap-3">
              <span aria-hidden="true">{r.icon || '⚠️'}</span>
              <div>
                {r.headline && <p className="text-sm font-semibold text-slate-100">{r.headline}</p>}
                <p className="text-sm text-slate-400">{r.message}</p>
                <a href={`/dashboard/site/${siteId}`} className="mt-0.5 inline-flex min-h-[44px] items-center text-xs text-brand-400 hover:text-brand-300">Como corrigir →</a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Bloco 3 — O que fazer agora (checklist)
function ChecklistBlock({ items, onAction, resend }) {
  const [showAll, setShowAll] = useState(false);
  const list = showAll ? items : items.slice(0, 4);
  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">📋 O que fazer agora</h3>
      <ul className="mt-3 space-y-1">
        {list.map((item) => (
          <li key={item.id}>
            <button type="button" disabled={item.completed || item.type === 'info'}
              onClick={() => onAction(item)}
              className={`flex w-full items-start gap-3 rounded-lg p-2 text-left transition-colors ${item.completed || item.type === 'info' ? '' : 'hover:bg-slate-800/50'}`}>
              <span aria-hidden="true">{item.completed ? '✅' : '☐'}</span>
              <span className={`flex-1 text-sm ${item.completed ? 'text-green-300' : 'text-slate-200'}`}>{item.label}</span>
              {!item.completed && item.type !== 'info' && <span className="text-xs text-brand-400">→</span>}
            </button>
          </li>
        ))}
      </ul>
      {resend === 'sent' && <p className="mt-2 text-xs text-brand-300">Link de confirmação reenviado ✓</p>}
      {resend === 'error' && <p className="mt-2 text-xs text-red-300">Não foi possível reenviar agora.</p>}
      {items.length > 4 && (
        <button onClick={() => setShowAll(!showAll)} className="mt-2 inline-flex min-h-[44px] items-center text-xs text-slate-400 hover:text-slate-200">
          {showAll ? 'Mostrar menos' : `Ver mais ${items.length - 4} item(ns)`}
        </button>
      )}
    </div>
  );
}

// Bloco 4 — Evolução do score
function EvolutionBlock({ history, score }) {
  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">📈 Evolução do score</h3>
      <div className="mt-3 h-40">
        {(!history || history.length <= 1) ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-slate-500">
            <span className="text-3xl font-bold text-slate-300">{score ?? '—'}</span>
            <p className="mt-1">O gráfico será preenchido com os próximos scans.</p>
          </div>
        ) : (
          <ScoreChart data={history} />
        )}
      </div>
    </div>
  );
}

function ScoreChart({ data }) {
  const w = 100, h = 40;
  const pts = data.map((d, i) => ({
    x: (i / Math.max(data.length - 1, 1)) * w,
    y: h - (Math.max(0, Math.min(100, d.score)) / 100) * h,
  }));
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const first = data[0], last = data[data.length - 1];
  return (
    <div className="flex h-full flex-col">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full flex-1" preserveAspectRatio="none">
        <path d={path} fill="none" stroke="#f97316" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {pts.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="1.4" fill="#f97316" />)}
      </svg>
      <div className="mt-2 flex justify-between text-xs text-slate-500">
        <span>{fmtDate(first.date)} · {first.score}</span>
        <span>{fmtDate(last.date)} · {last.score}</span>
      </div>
    </div>
  );
}

// Bloco 5 — Categorias
function CategoriesBlock({ cats, siteId }) {
  if (!cats || cats.length === 0) return null;
  return (
    <div>
      <h3 className="mb-3 text-lg font-bold text-white">Detalhes da análise</h3>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {cats.map((c) => (
          <a key={c.id} href={`/dashboard/site/${siteId}#${c.id}`}
            className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 transition-colors hover:bg-slate-800/50">
            <p className="truncate text-sm font-semibold text-white">{c.name}</p>
            <div className="mt-2 flex items-center justify-between">
              <span className="text-lg font-bold text-white">{c.passed}/{c.total}</span>
              <span aria-hidden="true">{CAT_ICON[c.status] || '⚪'}</span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

// Painel de compartilhar (checklist share_score)
function SharePanel({ domain, score }) {
  const [copied, setCopied] = useState(false);
  const url = `https://klarim.net/site/${domain}`;
  const text = `Este site tem score ${score}/100 de segurança no Klarim.`;
  return (
    <div className={`${card} mt-4`}>
      <p className="text-sm font-semibold text-white">Compartilhar score</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <a href={`https://wa.me/?text=${encodeURIComponent(text + ' ' + url)}`} target="_blank" rel="noopener"
          className="inline-flex min-h-[44px] items-center rounded-lg border border-slate-700 px-4 text-sm text-slate-200 hover:bg-slate-800">WhatsApp</a>
        <a href={`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`} target="_blank" rel="noopener"
          className="inline-flex min-h-[44px] items-center rounded-lg border border-slate-700 px-4 text-sm text-slate-200 hover:bg-slate-800">LinkedIn</a>
        <button type="button" onClick={() => { navigator.clipboard?.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
          className="inline-flex min-h-[44px] items-center rounded-lg border border-slate-700 px-4 text-sm text-slate-200 hover:bg-slate-800">
          {copied ? '✓ Copiado' : '🔗 Copiar'}
        </button>
      </div>
    </div>
  );
}

// Outros sites monitorados + adicionar
function OtherSites({ data, addOpen, setAddOpen, onReload }) {
  const [newUrl, setNewUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const others = data.other_sites || [];

  async function addSite(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    const { ok, status, error: err } = await apiPost('/account/sites', { url: newUrl });
    setBusy(false);
    if (ok) { setNewUrl(''); setAddOpen(false); onReload(); return; }
    setError(status === 403 ? (err || 'Limite do plano atingido.') : (err || 'Não foi possível adicionar.'));
  }

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">
          Sites monitorados ({data.sites_count})
        </h2>
        {!addOpen && (
          <button onClick={() => setAddOpen(true)}
            className="inline-flex min-h-[44px] items-center rounded-lg border border-slate-700 px-3.5 text-sm font-semibold text-slate-200 hover:bg-slate-800">
            + Monitorar outro site
          </button>
        )}
      </div>
      {addOpen && (
        <form onSubmit={addSite} className={`${card} mt-3 flex flex-col gap-3 sm:flex-row`}>
          <input type="text" required value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
            placeholder="seusite.com.br" className={field} />
          <div className="flex gap-2">
            <button type="submit" disabled={busy} className={`${btn} sm:w-auto`}>{busy ? 'Adicionando…' : 'Monitorar'}</button>
            <button type="button" onClick={() => { setAddOpen(false); setError(''); }}
              className="inline-flex min-h-[44px] items-center rounded-xl border border-slate-700 px-5 text-sm text-slate-300 hover:bg-slate-800">Cancelar</button>
          </div>
        </form>
      )}
      {error && <p className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>}
      {others.length > 0 && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {others.map((s) => (
            <a key={s.target_id} href={`/dashboard/site/${s.target_id}`}
              className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900/60 p-4 hover:bg-slate-800/50">
              <span className="truncate font-medium text-white">{s.domain}</span>
              <span className="text-slate-300">{SEMA_DOT[s.semaphore] || '⚪'} {s.score ?? '—'}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

// Onboarding do perfil (checklist "complete_profile")
function ProfileOnboarding({ targetId, profile, onClose, onSaved }) {
  const [companyName, setCompanyName] = useState(profile?.company_name || '');
  const [phone, setPhone] = useState(profile?.phone || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    setBusy(true); setError('');
    const { ok, error: err } = await apiPut('/account/profile-confirm', {
      target_id: targetId, company_name: companyName, phone });
    setBusy(false);
    if (ok) { onSaved(); onClose(); }
    else setError(err || 'Não foi possível salvar.');
  }

  return (
    <div className={`${card} mt-6 border-brand-500/30`}>
      <div className="flex items-center justify-between">
        <p className="text-lg font-bold text-white">Complete o perfil da sua empresa</p>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">✕</button>
      </div>
      <p className="mt-1 text-sm text-slate-400">Confirme ou edite os dados encontrados no seu site.</p>
      {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
      <div className="mt-4 space-y-3">
        <div>
          <label className="mb-1 block text-sm text-slate-300">Nome da empresa</label>
          <input value={companyName} onChange={(e) => setCompanyName(e.target.value)} className={field} placeholder="Sua Empresa Ltda" />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-300">Telefone</label>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} className={field} placeholder="(11) 90000-0000" />
        </div>
      </div>
      <button onClick={save} disabled={busy} className={`${btn} mt-4 w-full sm:w-auto`}>
        {busy ? 'Salvando…' : 'Confirmar dados →'}
      </button>
    </div>
  );
}

// =========================================================================== #
// Dashboard SEM site (KL-86 §8) — buscador + checklist reduzido
// =========================================================================== #
function NoSiteDashboard({ data, user, onResend }) {
  return (
    <div className="space-y-6">
      <form action="/scan" method="GET" className={`${card} border-brand-500/30 bg-brand-500/5 text-center`}>
        <h2 className="text-2xl font-bold text-white">Pesquise qualquer site</h2>
        <div className="mx-auto mt-4 flex max-w-lg flex-col gap-2 sm:flex-row">
          <input type="text" name="url" required placeholder="🔍 digite um domínio..." className={field} />
          <button type="submit" className={`${btn} sm:w-auto`}>Pesquisar →</button>
        </div>
        <p className="mt-3 text-sm text-slate-400">
          Depois de pesquisar, adicione ao monitoramento para acompanhar a segurança do seu site continuamente.
        </p>
      </form>
      {data.checklist && data.checklist.length > 0 && (
        <div className={card}>
          <h3 className="text-lg font-bold text-white">📋 Primeiros passos</h3>
          <ul className="mt-3 space-y-1">
            {data.checklist.map((item) => (
              <li key={item.id}>
                <button type="button"
                  onClick={() => item.action === '/account/resend-confirmation' ? onResend() : null}
                  className="flex w-full items-start gap-3 rounded-lg p-2 text-left hover:bg-slate-800/50">
                  <span aria-hidden="true">☐</span>
                  <span className="flex-1 text-sm text-slate-200">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
