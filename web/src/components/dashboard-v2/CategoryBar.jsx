// KL-90 P2 — CategoryBar (Camada 1 resumo → Camada 2 detalhe).
// 6 pills com status ✅/⚠️/❌ + passed/total. Clique expande os checks da categoria
// (accordion abaixo). Mobile: as pills rolam na horizontal.
import { useState } from 'react';
import { card, CAT_ICON, CAT_COLOR } from './shared.js';
import FixInline from './FixInline.jsx';

const ST_ICON = { pass: '✅', fail: '❌', inconclusive: '➖' };
const ST_COLOR = { pass: '#22c55e', fail: '#ef4444', inconclusive: '#94a3b8' };

// `technical` (modo técnico): a EVIDÊNCIA vira o texto principal (mono) e o risk_message
// vira "Impacto p/ o cliente" (secundário). Modo dono: o contrário.
function CheckRow({ c, siteType, onForward, technical }) {
  const [open, setOpen] = useState(false);
  const canOpen = c.status === 'fail' && (c.evidence || c.risk_message || c.fix_inline);
  return (
    <li className="border-b border-slate-800/60 last:border-0">
      <button type="button" disabled={!canOpen} onClick={() => setOpen((v) => !v)}
        className={`flex w-full items-start gap-2 py-2.5 text-left ${canOpen ? 'hover:opacity-80' : ''}`}>
        <span aria-hidden="true" style={{ color: ST_COLOR[c.status] }}>{ST_ICON[c.status] || '➖'}</span>
        <span className="flex-1 text-sm text-slate-200">{c.name}</span>
        {canOpen && <span className="text-xs text-slate-500">{open ? '▲' : '▼'}</span>}
      </button>
      {open && canOpen && (
        <div className="pb-3 pl-6">
          {technical ? (
            <>
              {c.evidence && <p className="rounded bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-200">{c.evidence}</p>}
              {c.risk_message && <p className="mt-1.5 text-xs text-slate-500">Impacto p/ o cliente: {c.risk_message}</p>}
            </>
          ) : (
            <>
              {c.risk_message && <p className="text-sm text-slate-400">{c.risk_message}</p>}
              {c.evidence && <p className="mt-1 font-mono text-xs text-slate-500">{c.evidence}</p>}
            </>
          )}
          {c.fix_inline && <FixInline fix={c.fix_inline} siteType={siteType} onForward={onForward} />}
        </div>
      )}
    </li>
  );
}

export default function CategoryBar({ categories, siteType, onForward, technical = false }) {
  const [openSlug, setOpenSlug] = useState(null);
  if (!categories || categories.length === 0) return null;
  const open = categories.find((c) => c.slug === openSlug);
  return (
    <div className={card}>
      <h3 className="mb-3 text-sm font-semibold text-slate-400">Categorias de segurança</h3>
      <div className="-mx-1 flex gap-2 overflow-x-auto pb-1 lg:grid lg:grid-cols-6 lg:overflow-visible">
        {categories.map((c) => {
          const active = c.slug === openSlug;
          return (
            <button key={c.slug} type="button"
              onClick={() => setOpenSlug(active ? null : c.slug)}
              className={`flex min-w-28 shrink-0 flex-col gap-1 rounded-xl border p-3 text-left transition-colors lg:min-w-0 ${
                active ? 'border-brand-500 bg-brand-500/5' : 'border-slate-800 bg-slate-900/60 hover:bg-slate-800/50'}`}>
              <span className="flex items-center justify-between">
                <span aria-hidden="true">{CAT_ICON[c.status] || '⚪'}</span>
                <span className="text-sm font-bold" style={{ color: CAT_COLOR[c.status] }}>{c.passed}/{c.total}</span>
              </span>
              <span className="truncate text-xs font-medium text-slate-200">{c.name}</span>
            </button>
          );
        })}
      </div>

      {open && (
        <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-4">
          <div className="flex items-center justify-between">
            <p className="font-semibold text-white">{open.name}</p>
            <button type="button" onClick={() => setOpenSlug(null)} className="text-slate-500 hover:text-slate-300">✕</button>
          </div>
          <ul className="mt-2">
            {open.checks.map((c) => (
              <CheckRow key={c.id} c={c} siteType={siteType} onForward={onForward} technical={technical} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
