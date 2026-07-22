// KL-90 (regressão 5) — dashboard do TÉCNICO: sites dos clientes (dono mascarado).
// GET /account/technician/clients. Espelha o TechnicianClients do Dashboard de produção.
import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/api.js';
import { card, SEMA_DOT } from './shared.js';

export default function TechnicianClients() {
  const [clients, setClients] = useState(null);
  useEffect(() => {
    apiGet('/account/technician/clients').then(({ ok, data }) => setClients(ok ? (data.clients || []) : []));
  }, []);
  if (clients === null) return null;
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-bold text-white">🔧 Sites dos meus clientes</h2>
        <span className="rounded-full border border-brand-500/40 bg-brand-500/10 px-2.5 py-0.5 text-xs font-semibold text-brand-300">Profissional de TI</span>
      </div>
      {clients.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          Nenhum cliente vinculou você ainda. Quando um dono te convidar como técnico, os sites dele aparecem aqui.
        </p>
      ) : (
        <div className="mt-3 space-y-2">
          {clients.map((c) => (
            <div key={c.link_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5 text-sm">
              <span className="flex items-center gap-2 font-mono text-slate-200">
                <span aria-hidden="true">{SEMA_DOT[c.last_semaphore] || '⚪'}</span>{c.domain}
              </span>
              <span className="text-slate-400">{c.last_scan_score ?? '—'}/100</span>
              <span className="text-xs text-slate-500">Dono: {c.owner_email}</span>
              <a href={`/site/${c.domain}`} className="text-xs text-brand-400 hover:text-brand-300">Ver →</a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
