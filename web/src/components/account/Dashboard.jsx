import { useEffect, useState } from 'react';
import { apiGet, apiPost, apiDelete } from '../../lib/api.js';
import { field, card } from './ui.js';
import { badgeFor } from '../../lib/badge.js';
import TechnicianSection from './TechnicianSection.jsx';
import PlanSection from './PlanSection.jsx';

const SEMA = {
  verde: { dot: '🟢', ring: 'ring-green-500/50', text: 'text-green-400' },
  amarelo: { dot: '🟡', ring: 'ring-yellow-500/50', text: 'text-yellow-400' },
  vermelho: { dot: '🔴', ring: 'ring-red-500/50', text: 'text-red-400' },
};

function daysUntilNext(lastScanAt) {
  if (!lastScanAt) return null;
  const last = new Date(lastScanAt).getTime();
  const next = last + 30 * 86400_000;
  const d = Math.ceil((next - Date.now()) / 86400_000);
  return d;
}

function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString('pt-BR'); } catch { return '—'; }
}

// KL-44 P3 — sites dos clientes do técnico (dono mascarado, link para o laudo).
function TechnicianClients() {
  const [clients, setClients] = useState(null);
  useEffect(() => {
    apiGet('/account/technician/clients').then(({ ok, data }) => setClients(ok ? (data.clients || []) : []));
  }, []);
  if (clients === null) return null;
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <p className="text-lg font-bold text-white">Sites dos meus clientes</p>
      {clients.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">Nenhum cliente vinculado ainda. Quando um dono de site convidar você, aparecerá aqui.</p>
      ) : (
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
      )}
    </div>
  );
}

export default function Dashboard({ user = {} }) {
  const [sites, setSites] = useState(null);
  const [maxSites, setMaxSites] = useState(user.max_sites || 1);
  const [benchmark, setBenchmark] = useState(null);
  const [adding, setAdding] = useState(false);
  const [newUrl, setNewUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [upgrade, setUpgrade] = useState(false);
  const [history, setHistory] = useState(null);
  const [toast, setToast] = useState('');   // KL-68: ?added / ?claimed pós-signup/login
  // KL-44 P6: ?upgrade=pro (abre o modal) / ?upgraded=1 (pós-checkout PIX). Capturados 1x.
  // ⚠️ `window` NÃO existe no SSR (client:load também renderiza no servidor) → guarda.
  const [planUpgradeParam] = useState(() =>
    (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('upgrade') : '') || '');
  const [upgradedFlag] = useState(() =>
    typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('upgraded') === '1');

  // Confirmação de e-mail (KL-82 Slice 2): banner enquanto unconfirmed; some ao confirmar.
  const [confirmed, setConfirmed] = useState(() => user.email_confirmed !== false);
  const [resendState, setResendState] = useState(''); // '' | 'sending' | 'sent' | 'limit' | 'error'

  async function resendConfirmation() {
    setResendState('sending');
    const { ok, status } = await apiPost('/account/resend-confirmation', {});
    setResendState(ok ? 'sent' : status === 429 ? 'limit' : 'error');
  }

  // KL-68: toast de reivindicação pós-autenticação, depois limpa a URL.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const claimed = q.get('claimed');
    const added = q.get('added');
    const blocked = q.get('blocked');
    const conf = q.get('confirmed');   // KL-82 Slice 2: ?confirmed=1 | already
    if (claimed) setToast(`✅ ${claimed} adicionado · ✓ Propriedade verificada automaticamente`);
    else if (added) setToast(`✅ ${added} adicionado ao monitoramento`);
    else if (blocked) setToast('✅ Conta criada! Adicione o domínio do seu site no painel para começar a monitorar.');
    else if (conf === '1') setToast('✅ E-mail confirmado! Acesso completo desbloqueado.');
    else if (conf === 'already') setToast('✅ Seu e-mail já estava confirmado.');
    if (conf) setConfirmed(true);
    if (claimed || added || blocked || conf || q.get('upgrade') || q.get('upgraded')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  async function load() {
    const { ok, data } = await apiGet('/account/sites');
    if (ok) { setSites(data.sites || []); setMaxSites(data.max_sites || 1); }
    else setSites([]);
  }
  useEffect(() => { load(); }, []);

  // histórico de consultas (scans que o usuário fez, KL-25 → scanned_by_email)
  useEffect(() => {
    apiGet('/account/scan-history').then(({ ok, data }) => setHistory(ok ? (data.scans || []) : []));
  }, []);

  // Remover um item do histórico (desvincula o scan do e-mail; o scan em si é preservado).
  async function removeHistory(scanId, domain) {
    if (!window.confirm(`Remover ${domain} do histórico?`)) return;
    const { ok } = await apiDelete(`/account/scan-history/${scanId}`);
    if (ok) setHistory((prev) => (prev || []).filter((h) => h.id !== scanId));
  }

  // benchmark do setor do primeiro site
  useEffect(() => {
    const s = sites && sites[0];
    if (!s) return;
    const path = s.sector && s.sector !== 'outro' ? `/benchmark/${encodeURIComponent(s.sector)}` : '/benchmark';
    apiGet(path).then(({ ok, data }) => ok && setBenchmark({ ...data, myScore: s.last_scan_score }));
  }, [sites]);

  async function addSite(e) {
    e.preventDefault();
    setError(''); setUpgrade(false); setBusy(true);
    const { ok, status, error: err } = await apiPost('/account/sites', { url: newUrl });
    setBusy(false);
    if (ok) { setNewUrl(''); setAdding(false); await load(); return; }
    if (status === 403) { setUpgrade(true); setError(err); return; }
    setError(err || 'Não foi possível adicionar o site.');
  }

  if (sites === null) {
    // min-h reserva a altura enquanto carrega (evita o footer pular quando os sites chegam — CLS).
    return <div className="min-h-screen"><p className="text-slate-400">Carregando seus sites…</p></div>;
  }

  const used = sites.length;
  const atLimit = used >= maxSites;

  // histórico: não duplicar os sites já monitorados (o signup pode ter vinculado um)
  const norm = (u) => (u || '').toLowerCase().replace(/\/+$/, '');
  const monitored = new Set(sites.map((s) => norm(s.url)));
  const historyItems = (history || []).filter((h) => !monitored.has(norm(h.url)));

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">Olá{user.name ? `, ${user.name}` : ''}</h1>
        {(user.role === 'technician' || user.role === 'both') && (
          <p className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-brand-500/40 bg-brand-500/10 px-3 py-0.5 text-sm font-semibold text-brand-300">
            🔧 Profissional de TI
          </p>
        )}
        <p className="mt-1 text-slate-400">Verifique qualquer site à vontade; monitore os que importam.</p>
      </div>

      {toast && (
        <div className="flex items-center justify-between rounded-xl border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-300">
          <span>{toast}</span>
          <button onClick={() => setToast('')} className="text-green-400/70 hover:text-green-300">✕</button>
        </div>
      )}

      {/* KL-82 Slice 2 — banner de confirmação de e-mail (conta não confirmada). Gentil, não
          intrusivo; o dashboard básico funciona, mas PDF/checks detalhados pedem confirmação. */}
      {!confirmed && (
        <div className="rounded-xl border border-brand-500/30 bg-brand-500/10 px-4 py-3 text-sm">
          <p className="text-slate-200">
            📧 Confirme seu e-mail para desbloquear o relatório completo (PDF, checks detalhados).
            Enviamos um link para <strong className="text-white">{user.email}</strong>.
          </p>
          {resendState === 'sent' ? (
            <p className="mt-1 text-brand-300">Link reenviado ✓ — confira sua caixa de entrada.</p>
          ) : resendState === 'limit' ? (
            <p className="mt-1 text-slate-400">Aguarde alguns minutos para reenviar.</p>
          ) : resendState === 'error' ? (
            <p className="mt-1 text-red-300">Não foi possível reenviar agora. Tente mais tarde.</p>
          ) : (
            <button onClick={resendConfirmation} disabled={resendState === 'sending'}
              className="mt-1 inline-flex min-h-[44px] items-center text-brand-400 transition-colors hover:text-brand-300 disabled:opacity-60">
              {resendState === 'sending' ? 'Reenviando…' : 'Reenviar link de confirmação →'}
            </button>
          )}
        </div>
      )}

      {(user.role === 'technician' || user.role === 'both') && <TechnicianClients />}

      {/* Verificar um site — consulta livre e ILIMITADA (vai para /scan) */}
      <form action="/scan" method="GET" className={`${card} border-brand-500/30 bg-brand-500/5`}>
        <p className="text-lg font-bold text-white">🔍 Verificar um site</p>
        <p className="mt-0.5 text-sm text-slate-400">Consulte a segurança de qualquer site — sem limite.</p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input type="text" name="url" required placeholder="site.com.br" className={field} />
          <button type="submit"
            className="rounded-xl bg-brand-500 px-6 py-3.5 text-sm font-semibold text-slate-950 hover:bg-brand-400">
            Verificar →
          </button>
        </div>
      </form>

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Sites monitorados ({used}/{maxSites})</h2>
        {!adding && (
          <button onClick={() => (atLimit ? setUpgrade(true) : setAdding(true))}
            className="rounded-lg border border-slate-700 px-3.5 py-2 text-sm font-semibold text-slate-200 hover:bg-slate-800">
            + Monitorar outro site
          </button>
        )}
      </div>

      {adding && (
        <form onSubmit={addSite} className={`${card} flex flex-col gap-3 sm:flex-row`}>
          <input type="text" required value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
            placeholder="seusite.com.br" className={field} />
          <div className="flex gap-2">
            <button type="submit" disabled={busy}
              className="rounded-xl bg-brand-500 px-5 py-3.5 text-sm font-semibold text-slate-950 hover:bg-brand-400 disabled:opacity-60">
              {busy ? 'Adicionando…' : 'Monitorar'}
            </button>
            <button type="button" onClick={() => { setAdding(false); setError(''); }}
              className="rounded-xl border border-slate-700 px-5 py-3.5 text-sm text-slate-300 hover:bg-slate-800">
              Cancelar
            </button>
          </div>
        </form>
      )}

      {error && !upgrade && <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>}

      {upgrade && (
        <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
          <p className="font-semibold text-brand-300">Limite do plano atingido</p>
          <p className="mt-1 text-sm text-slate-300">{error || `Seu plano permite ${maxSites} site(s).`}</p>
          <p className="mt-2 text-sm text-slate-400">Upgrade para monitorar até 5 sites — em breve.</p>
        </div>
      )}

      {sites.length === 0 ? (
        <div className={card}>
          <p className="text-slate-300">Você ainda não monitora nenhum site.</p>
          <p className="mt-1 text-sm text-slate-400">Monitore um site para acompanhar a evolução do score todo mês.</p>
        </div>
      ) : (
        // KL-80: lista vertical no mobile, grade 2-col no desktop (densidade).
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:items-start">
          {sites.map((s) => <SiteCard key={s.target_id} site={s} onRemoved={load} />)}
        </div>
      )}

      {benchmark && benchmark.count > 0 && sites.length > 0 && (
        <BenchmarkBar b={benchmark} />
      )}

      {/* Histórico de consultas (somente leitura — não conta como monitorado) */}
      {historyItems.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-white">Histórico de consultas</h2>
          <div className={`${card} mt-3 !p-0`}>
            <ul className="divide-y divide-slate-800">
              {historyItems.map((h) => <HistoryRow key={h.id} scan={h} onRemove={removeHistory} />)}
            </ul>
          </div>
        </div>
      )}

      {/* KL-44 P6 — plano interativo (trial/upgrade/downgrade/pagamentos). `#plano` = âncora
          do nav "Planos" quando logado. */}
      <div id="plano">
        <PlanSection initialUpgrade={planUpgradeParam} showUpgradedToast={upgradedFlag} />
      </div>

      {(user.role === 'technician' || user.role === 'both') && (
        <div className={card}>
          <p className="text-sm text-brand-300">Perfil: 🔧 Profissional de TI</p>
          <a href="/dashboard/conta" className="mt-1 inline-flex text-sm text-brand-400 hover:text-brand-300">Gerenciar conta →</a>
        </div>
      )}
    </div>
  );
}

const HSEMA = { verde: '🟢', amarelo: '🟡', vermelho: '🔴' };

function HistoryRow({ scan, onRemove }) {
  const domain = (() => {
    try { return new URL(scan.url.includes('://') ? scan.url : `https://${scan.url}`).hostname.replace(/^www\./, ''); }
    catch { return scan.url; }
  })();
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-sm">
      <div className="min-w-0">
        <p className="truncate font-medium text-white">{domain}</p>
        <p className="text-xs text-slate-500">{fmtDate(scan.scanned_at)}</p>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-slate-300">{HSEMA[scan.semaphore] || '⚪'} {scan.score ?? '—'}</span>
        {/* "Ver" abre o perfil público existente (não re-escaneia). KL-80: alvos de toque ≥44px. */}
        <a href={`/site/${domain}`} target="_blank" rel="noopener noreferrer"
          className="inline-flex min-h-[44px] items-center px-2 text-brand-400 transition-colors hover:text-brand-300">Ver</a>
        <button onClick={() => onRemove && onRemove(scan.id, domain)} title="Remover do histórico"
          className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center text-slate-500 transition-colors hover:text-red-400" aria-label={`Remover ${domain} do histórico`}>✕</button>
      </div>
    </li>
  );
}

function SiteCard({ site, onRemoved }) {
  const sema = SEMA[site.last_semaphore] || SEMA.amarelo;
  const nextDays = daysUntilNext(site.last_scan_at);
  const badge = badgeFor(site.last_scan_score, true); // site monitorado pelo próprio usuário → tem conta
  // FIX técnico no dashboard: painel expansível com Técnico responsável + compartilhar
  // laudo direto no card (antes só existia no detalhe do site, via "Ver detalhes").
  const [showTech, setShowTech] = useState(false);
  // KL-71 Bug 8: remover site do próprio monitoramento (self-service, sem notificação).
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removing, setRemoving] = useState(false);
  async function removeSite() {
    setRemoving(true);
    const { ok } = await apiDelete(`/account/sites/${site.target_id}`);
    setRemoving(false);
    if (ok) onRemoved && onRemoved();
    else setConfirmRemove(false);
  }
  return (
    <div className={card}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lg font-semibold text-white">{site.domain || site.url}</p>
          <p className="mt-1 text-sm text-slate-400">
            {site.sector && site.sector !== 'outro' ? site.sector : 'Setor não classificado'}
            {site.is_owner && <span className="ml-2 rounded bg-brand-500/15 px-1.5 py-0.5 text-xs text-brand-300">dono</span>}
          </p>
          {badge && (
            <p className="mt-2 inline-flex items-center gap-1 rounded-full border border-brand-500/40 bg-brand-500/10 px-2.5 py-0.5 text-xs font-semibold text-brand-300">
              {badge.icon} {badge.label}
            </p>
          )}
        </div>
        <div className={`flex h-16 w-16 shrink-0 flex-col items-center justify-center rounded-full ring-2 ${sema.ring}`}>
          <span className={`text-xl font-bold ${sema.text}`}>{site.last_scan_score ?? '—'}</span>
          <span className="text-[10px] text-slate-500">/100</span>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-400">
        <span>{sema.dot} {site.last_semaphore || 'sem scan'}</span>
        <span>Último scan: {fmtDate(site.last_scan_at)}</span>
        {nextDays !== null && <span>Próximo: {nextDays > 0 ? `em ${nextDays} dia(s)` : 'em breve'}</span>}
      </div>
      <div className="mt-5 flex flex-wrap gap-3">
        <a href={`/dashboard/site/${site.target_id}`}
          className="rounded-lg bg-slate-800 inline-flex min-h-[44px] items-center px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">Ver detalhes</a>
        <button onClick={() => setShowTech((v) => !v)}
          className="rounded-lg border border-slate-700 inline-flex min-h-[44px] items-center px-4 py-2 text-sm text-slate-300 hover:bg-slate-800">
          🔧 Técnico e laudo {showTech ? '▾' : '▸'}
        </button>
        {site.domain && (
          <a href={`/site/${site.domain}`} target="_blank" rel="noopener noreferrer"
            className="rounded-lg border border-slate-700 inline-flex min-h-[44px] items-center px-4 py-2 text-sm text-slate-300 hover:bg-slate-800">
            🌐 Perfil público
          </a>
        )}
        <a href="/dashboard/widget"
          className="rounded-lg border border-slate-700 inline-flex min-h-[44px] items-center px-4 py-2 text-sm text-slate-300 hover:bg-slate-800">&lt;/&gt; Widget</a>
        <button onClick={() => setConfirmRemove(true)}
          className="rounded-lg border border-slate-700 inline-flex min-h-[44px] items-center px-4 py-2 text-sm text-slate-400 hover:bg-slate-800 hover:text-red-300">
          Remover
        </button>
      </div>
      {confirmRemove && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-sm">
          <p className="text-slate-200">Remover <b>{site.domain || site.url}</b> do seu monitoramento?</p>
          <p className="mt-1 text-xs text-slate-400">As vigílias deste site serão desativadas. Você pode voltar a monitorar quando quiser.</p>
          <div className="mt-3 flex gap-2">
            <button disabled={removing} onClick={removeSite}
              className="rounded-lg bg-red-500/80 px-4 py-2 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50">
              {removing ? 'Removendo…' : 'Remover'}
            </button>
            <button onClick={() => setConfirmRemove(false)}
              className="rounded-lg border border-slate-700 px-4 py-2 text-xs text-slate-300 hover:bg-slate-800">Cancelar</button>
          </div>
        </div>
      )}
      {showTech && (
        <div className="mt-4">
          <TechnicianSection targetId={site.target_id} />
        </div>
      )}
    </div>
  );
}

function BenchmarkBar({ b }) {
  const mine = b.myScore ?? 0;
  const avg = b.avg_score ?? 0;
  const pct = Math.min(100, Math.max(0, mine));
  return (
    <div className={card}>
      <p className="text-sm font-medium uppercase tracking-wide text-brand-400/80">Benchmark</p>
      <p className="mt-2 text-slate-300">
        Seu score: <span className="font-bold text-white">{mine}</span> · Média do setor:{' '}
        <span className="font-bold text-white">{avg}</span>
      </p>
      <div className="mt-3 h-3 w-full overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-sm text-slate-400">
        {mine >= avg ? 'Seu site está acima da média do setor. 👏' : 'Seu site está abaixo da média do setor — há espaço para melhorar.'}
      </p>
    </div>
  );
}
