// KL-90 UX (item 3 + regressão 4) — painel FIXO de sites (substitui o dropdown). Lista os
// sites monitorados (domínio/score/semáforo, selecionado destacado, **remover** por site) +
// o histórico dos últimos sites pesquisados. Scroll interno se a lista for longa.
import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/api.js';
import { card, SEMA_DOT, SEMA_COLOR, relDate } from './shared.js';

export default function MonitoredSitesPanel({ sites, selectedId, onSelect, onAddSite, onRemove }) {
  const [history, setHistory] = useState([]);
  useEffect(() => {
    let alive = true;
    apiGet('/account/scan-history').then(({ ok, data }) => {
      if (alive && ok) setHistory((data.scans || []).slice(0, 8));
    });
    return () => { alive = false; };
  }, []);

  const monitoredDomains = new Set((sites || []).map((s) => s.domain));
  const recent = history.filter((h) => {
    try { return !monitoredDomains.has(new URL(h.url.startsWith('http') ? h.url : `https://${h.url}`).hostname.replace(/^www\./, '')); }
    catch { return true; }
  });

  return (
    <div className={`${card} lg:sticky lg:top-24`}>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-400">Meus sites ({sites.length})</h2>
        <button type="button" onClick={onAddSite}
          className="inline-flex min-h-[44px] items-center rounded-lg px-2 text-sm font-semibold text-brand-400 hover:text-brand-300">
          + Adicionar
        </button>
      </div>

      <div className="mt-2 max-h-[22rem] space-y-1 overflow-y-auto">
        {sites.map((s) => {
          const active = s.id === selectedId;
          return (
            <div key={s.id}
              className={`group flex items-center gap-1 rounded-lg pr-1 transition-colors ${
                active ? 'bg-brand-500/10 ring-1 ring-brand-500/40' : 'hover:bg-slate-800/50'}`}>
              <button type="button" onClick={() => !active && onSelect(s.id)}
                className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left">
                <span aria-hidden="true">{SEMA_DOT[s.semaphore] || '⚪'}</span>
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100">{s.domain}</span>
                <span className="text-sm font-bold" style={{ color: SEMA_COLOR[s.semaphore] || '#94a3b8' }}>{s.score ?? '—'}</span>
              </button>
              <button type="button" onClick={() => onRemove(s)} aria-label={`Remover ${s.domain}`}
                title="Remover do monitoramento"
                className="shrink-0 rounded p-1.5 text-slate-500 transition-colors hover:text-red-400 focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100">
                ✕
              </button>
            </div>
          );
        })}
      </div>

      {recent.length > 0 && (
        <div className="mt-4 border-t border-slate-800 pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Pesquisados recentemente</h3>
          <div className="mt-2 space-y-1">
            {recent.map((h) => {
              let domain = h.url;
              try { domain = new URL(h.url.startsWith('http') ? h.url : `https://${h.url}`).hostname.replace(/^www\./, ''); } catch {}
              return (
                <a key={h.id} href={`/site/${domain}`}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-slate-800/50">
                  <span aria-hidden="true">🔍</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-slate-200">{domain}</span>
                    <span className="block text-xs text-slate-500">{relDate(h.scanned_at)}</span>
                  </span>
                  <span className="text-sm text-slate-400">{h.score ?? '—'}</span>
                </a>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
