// KL-90 P2 — RisksList (Camada 2): accordion dos riscos (linguagem de negócio, KL-20).
// Já vem ordenado por severidade do backend. Só 1 risco expandido por vez; expandir mostra
// "Como corrigir" (fix por plataforma) + encaminhar para técnico.
import { useState } from 'react';
import { SEV_META } from './shared.js';
import FixInline from './FixInline.jsx';

// Conteúdo "puro" (sem card/header) — renderizado dentro de um Collapsible (item 6).
export default function RisksList({ risks, siteType, onForward }) {
  // Affordance: o 1º risco (mais crítico) já vem expandido → o usuário vê que os outros abrem.
  const [openId, setOpenId] = useState(() => (risks && risks[0] ? (risks[0].check_id || 0) : null));
  return (
    <div>
      {(!risks || risks.length === 0) ? (
        <p className="text-green-400">Nenhum risco identificado. Seu site está excelente! 🎉</p>
      ) : (
        <ul className="divide-y divide-slate-800">
          {risks.map((r, i) => {
            const id = r.check_id || i;
            const open = openId === id;
            const sev = SEV_META[r.severity] || SEV_META.baixa;
            const canFix = !!r.fix_inline;
            return (
              <li key={id} className="py-3 first:pt-0">
                <button type="button" onClick={() => setOpenId(open ? null : id)}
                  className="flex w-full items-start gap-3 text-left">
                  <span aria-hidden="true" title={sev.label}>{sev.icon}</span>
                  <span className="flex-1">
                    <span className="block text-sm font-semibold text-slate-100">{r.title}</span>
                    <span className="mt-0.5 block text-sm text-slate-400">{r.description}</span>
                  </span>
                  {canFix && <span className="text-xs text-brand-400">{open ? '▲' : 'Como corrigir ▼'}</span>}
                </button>
                {open && canFix && (
                  <div className="pl-7">
                    <FixInline fix={r.fix_inline} siteType={siteType} onForward={onForward} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
