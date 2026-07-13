import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '../../lib/api.js';
import { field, card } from './ui.js';

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
    return <p className="text-slate-400">Carregando seus sites…</p>;
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
        <p className="mt-1 text-slate-400">Verifique qualquer site à vontade; monitore os que importam.</p>
      </div>

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
        <div className="space-y-4">
          {sites.map((s) => <SiteCard key={s.target_id} site={s} />)}
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
              {historyItems.map((h) => <HistoryRow key={h.id} scan={h} />)}
            </ul>
          </div>
        </div>
      )}

      <div className={card}>
        <p className="text-sm text-slate-400">Plano</p>
        <p className="mt-1 font-semibold text-white">
          {user.plan === 'free' ? 'Gratuito' : user.plan} ({maxSites} site{maxSites > 1 ? 's' : ''})
        </p>
        <p className="mt-2 text-sm text-slate-500">Upgrade para até 5 sites — em breve.</p>
      </div>
    </div>
  );
}

const HSEMA = { verde: '🟢', amarelo: '🟡', vermelho: '🔴' };

function HistoryRow({ scan }) {
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
      <div className="flex items-center gap-4">
        <span className="text-slate-300">{HSEMA[scan.semaphore] || '⚪'} {scan.score ?? '—'}</span>
        <a href={`/scan?url=${encodeURIComponent(scan.url)}`}
          className="text-brand-400 hover:text-brand-300">Ver resultado →</a>
      </div>
    </li>
  );
}

function SiteCard({ site }) {
  const sema = SEMA[site.last_semaphore] || SEMA.amarelo;
  const nextDays = daysUntilNext(site.last_scan_at);
  const encoded = encodeURIComponent(site.url);
  return (
    <div className={card}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lg font-semibold text-white">{site.domain || site.url}</p>
          <p className="mt-1 text-sm text-slate-400">
            {site.sector && site.sector !== 'outro' ? site.sector : 'Setor não classificado'}
            {site.is_owner && <span className="ml-2 rounded bg-brand-500/15 px-1.5 py-0.5 text-xs text-brand-300">dono</span>}
          </p>
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
          className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">Ver detalhes</a>
        <a href={`/api/report/executive?url=${encoded}`}
          className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800">📄 Baixar PDF</a>
      </div>
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
