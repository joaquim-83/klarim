// KL-90 P2 — "Como corrigir" inline, com abas por plataforma (WordPress/Nginx/Apache).
// Se `siteType` é conhecido (ex.: "wordpress") a aba dele abre primeiro; senão WordPress.
// Compartilhado por RisksList (accordion de riscos) e CategoryBar (checks detalhados).
import { useState } from 'react';

const PLATFORMS = [
  { key: 'wordpress', label: 'WordPress' },
  { key: 'nginx', label: 'Nginx' },
  { key: 'apache', label: 'Apache' },
];

export default function FixInline({ fix, siteType, onForward }) {
  const avail = PLATFORMS.filter((p) => fix && fix[p.key]);
  const preferred = avail.find((p) => (siteType || '').toLowerCase().includes(p.key));
  const [tab, setTab] = useState((preferred || avail[0] || {}).key);
  if (!avail.length) return null;
  const text = fix[tab] || '';
  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      <div className="flex flex-wrap gap-1.5">
        {avail.map((p) => (
          <button key={p.key} type="button" onClick={() => setTab(p.key)}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
              tab === p.key ? 'bg-brand-500 text-[var(--accent-text)]'
                : 'border border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
            {p.label}
          </button>
        ))}
      </div>
      <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words rounded bg-slate-900 p-3 text-xs text-slate-200">{text}</pre>
      <button type="button" onClick={onForward}
        className="mt-2 inline-flex min-h-[44px] items-center text-xs text-brand-400 hover:text-brand-300">
        Não sei fazer isso → Encaminhar para técnico
      </button>
    </div>
  );
}
