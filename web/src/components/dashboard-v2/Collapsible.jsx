// KL-90 — seção com header clicável que abre/recolhe (item 6 + affordance).
// Affordance: chevron grande que ROTACIONA, hover no header, cursor-pointer.
import { useState } from 'react';
import { card } from './shared.js';

export default function Collapsible({ title, count, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={card}>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="-m-2 flex w-full cursor-pointer items-center justify-between gap-3 rounded-xl p-2 text-left transition-colors hover:bg-slate-800/40">
        <span className="flex items-center gap-2 text-lg font-bold text-white">
          {title}
          {count != null && (
            <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs font-semibold text-slate-300">{count}</span>
          )}
        </span>
        <span className="flex items-center gap-2 text-sm text-slate-400">
          <span className="hidden sm:inline">{open ? 'recolher' : 'expandir'}</span>
          <span className={`text-xl leading-none text-brand-400 transition-transform duration-200 ${open ? 'rotate-90' : ''}`} aria-hidden="true">›</span>
        </span>
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  );
}
