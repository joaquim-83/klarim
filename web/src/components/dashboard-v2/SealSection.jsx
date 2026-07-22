// KL-90 (regressão 1) — Selo "Monitorado por Klarim" (KL-44 P5). Snippet embeddable
// (tema/tamanho → copiar). Exclusivo de planos pagos: Free vê como benefício de upgrade.
// Espelha o SealSection do SiteDetail de produção (mesmo /seal/widget.js).
import { useState } from 'react';
import { card } from './shared.js';

export default function SealSection({ domain, planName }) {
  const [theme, setTheme] = useState('auto');
  const [size, setSize] = useState('compact');
  const [copied, setCopied] = useState(false);
  const isFree = (planName || 'Free').toLowerCase().includes('free') || (planName || '').toLowerCase().includes('gratuito');

  const snippet = `<!-- Selo Klarim - Monitorado -->
<div id="klarim-seal"></div>
<script src="https://klarim.net/seal/widget.js"
        data-domain="${domain}"${theme !== 'auto' ? `\n        data-theme="${theme}"` : ''}${size !== 'compact' ? `\n        data-size="${size}"` : ''}></script>`;

  function copy() {
    navigator.clipboard?.writeText(snippet);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  }

  if (isFree) {
    return (
      <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
        <h2 className="text-lg font-bold text-white">🛡️ Selo "Monitorado por Klarim"</h2>
        <p className="mt-1 text-sm text-slate-400">
          Exiba no seu site que ele é monitorado — passa confiança aos visitantes. Disponível nos planos <strong className="text-slate-200">Pro</strong> e <strong className="text-slate-200">Agency</strong>.
        </p>
        <a href="/planos" className="mt-3 inline-flex min-h-[44px] items-center rounded-xl bg-brand-500 px-5 text-sm font-semibold text-[var(--accent-text)] hover:bg-brand-400">
          Fazer upgrade para liberar o selo →
        </a>
      </div>
    );
  }

  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">🛡️ Selo "Monitorado por Klarim"</h2>
      <p className="mt-1 text-sm text-slate-400">Exiba "Monitorado por Klarim" no seu site. Sem rastreio de visitantes.</p>
      <div className="mt-4 flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1.5 text-slate-400">Tema
          <select value={theme} onChange={(e) => setTheme(e.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
            <option value="auto">Auto</option><option value="dark">Escuro</option><option value="light">Claro</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-slate-400">Tamanho
          <select value={size} onChange={(e) => setSize(e.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
            <option value="compact">Compacto</option><option value="full">Completo</option>
          </select>
        </label>
      </div>
      <pre className="mt-3 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300"><code>{snippet}</code></pre>
      <button onClick={copy} className="mt-2 inline-flex min-h-[44px] items-center rounded-lg bg-brand-500 px-4 text-sm font-semibold text-[var(--accent-text)] hover:bg-brand-400">
        {copied ? 'Copiado ✓' : 'Copiar código do selo'}
      </button>
      <p className="mt-2 text-xs text-slate-500">Sugestão: cole no rodapé do site. O selo abre o perfil público em nova aba.</p>
    </div>
  );
}
