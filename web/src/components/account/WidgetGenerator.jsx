import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/api.js';
import { card, field } from './ui.js';

// Gerador do widget embeddable "Verificado por Klarim" (KL-42): o usuário escolhe um
// site monitorado + estilo, vê o preview e copia o snippet <script>.

const SITE = 'https://klarim.net';
const STYLES = [
  { id: 'inline', label: 'Inline (badge horizontal)' },
  { id: 'card', label: 'Card (vertical)' },
  { id: 'minimal', label: 'Minimal (ícone + score)' },
];

function semaColor(sema, score) {
  const s = sema || (score >= 90 ? 'verde' : score >= 50 ? 'amarelo' : 'vermelho');
  return s === 'verde' ? '#00D26A' : s === 'vermelho' ? '#F85149' : '#F0C000';
}

// Réplica visual do que o widget.js injeta (para o preview) — mesmos 3 estilos.
function WidgetPreview({ domain, score, sema, style }) {
  const c = semaColor(sema, score);
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: '8px', textDecoration: 'none',
    fontFamily: 'Arial, Helvetica, sans-serif', background: '#0D1117',
    border: '1px solid #30363D', borderRadius: '10px', color: '#E6EDF3', lineHeight: 1.2,
  };
  if (style === 'minimal') {
    return (
      <span style={{ ...base, padding: '6px 10px', fontSize: '12px' }}>
        <span>🛡️</span><span style={{ fontWeight: 'bold', color: c }}>{score}</span>
        <span style={{ color: '#8B949E' }}>/100</span>
      </span>
    );
  }
  if (style === 'card') {
    return (
      <span style={{ ...base, flexDirection: 'column', alignItems: 'flex-start', padding: '12px 14px', width: '180px' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#8B949E' }}>
          <span>🛡️</span><span>Verificado por Klarim</span>
        </span>
        <span style={{ display: 'flex', alignItems: 'baseline', gap: '5px', marginTop: '6px' }}>
          <span style={{ fontSize: '30px', fontWeight: 'bold', color: c }}>{score}</span>
          <span style={{ color: '#8B949E', fontSize: '13px' }}>/100</span>
          <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: c }} />
        </span>
        <span style={{ fontSize: '11px', color: '#8B949E', marginTop: '4px' }}>klarim.net</span>
      </span>
    );
  }
  return (
    <span style={{ ...base, padding: '8px 12px', fontSize: '13px' }}>
      <span>🛡️</span><span>Verificado por Klarim</span>
      <span style={{ fontWeight: 'bold', color: c }}>· {score}/100</span>
      <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: c }} />
    </span>
  );
}

export default function WidgetGenerator() {
  const [sites, setSites] = useState(null);
  const [idx, setIdx] = useState(0);
  const [style, setStyle] = useState('inline');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    apiGet('/account/sites').then(({ ok, data }) => setSites(ok ? (data.sites || []) : []));
  }, []);

  if (sites === null) return <p className="text-slate-400">Carregando…</p>;
  if (sites.length === 0) {
    return (
      <div className={card}>
        <h1 className="text-2xl font-bold text-white">Widget para seu site</h1>
        <p className="mt-3 text-slate-300">Você ainda não monitora nenhum site.</p>
        <a href="/dashboard" className="mt-4 inline-block text-brand-400 hover:text-brand-300">← Voltar ao dashboard</a>
      </div>
    );
  }

  const site = sites[Math.min(idx, sites.length - 1)];
  const domain = site.domain || site.url;
  const score = site.last_scan_score ?? 0;
  const src = style === 'inline'
    ? `${SITE}/api/widget/${domain}.js`
    : `${SITE}/api/widget/${domain}.js?style=${style}`;
  const embed = `<script async src="${src}"></script>`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(embed);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      try { window.klarimTrack?.('widget_copied', { style }, `https://${domain}`); } catch { /* noop */ }
    } catch { /* clipboard indisponível */ }
  }

  return (
    <div className="space-y-6">
      <div>
        <a href="/dashboard" className="text-sm text-brand-400 hover:text-brand-300">← Voltar ao dashboard</a>
        <h1 className="mt-1 text-2xl font-bold text-white">Widget para seu site</h1>
        <p className="mt-1 text-slate-400">
          Mostre o selo "Verificado por Klarim" no seu site. Cada visitante vira um possível novo usuário.
        </p>
      </div>

      <div className={card}>
        {sites.length > 1 && (
          <div className="mb-4">
            <label className="mb-1.5 block text-sm text-slate-300">Site</label>
            <select value={idx} onChange={(e) => setIdx(Number(e.target.value))} className={field}>
              {sites.map((s, i) => <option key={s.target_id} value={i}>{s.domain || s.url}</option>)}
            </select>
          </div>
        )}

        <label className="mb-1.5 block text-sm text-slate-300">Estilo</label>
        <div className="flex flex-wrap gap-2">
          {STYLES.map((s) => (
            <button key={s.id} type="button" onClick={() => setStyle(s.id)}
              className={`rounded-xl border px-4 py-2 text-sm transition-colors ${style === s.id
                ? 'border-brand-500 bg-brand-500/10 text-brand-300'
                : 'border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
              {s.label}
            </button>
          ))}
        </div>

        <p className="mt-6 mb-2 text-sm text-slate-400">Preview:</p>
        <div className="flex min-h-24 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/40 p-6">
          <WidgetPreview domain={domain} score={score} sema={site.last_semaphore} style={style} />
        </div>

        <p className="mt-6 mb-2 text-sm text-slate-400">Copie e cole no HTML do seu site:</p>
        <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-xs text-slate-200"><code>{embed}</code></pre>
        <button type="button" onClick={copy}
          className="mt-3 inline-flex items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3 text-sm font-semibold text-slate-950 transition-colors hover:bg-brand-400">
          {copied ? 'Código copiado ✓' : 'Copiar código'}
        </button>

        <p className="mt-4 text-sm text-slate-500">
          💡 O widget atualiza automaticamente quando seu score mudar. Ele carrega de forma assíncrona
          e não impacta a performance do seu site.
        </p>
      </div>
    </div>
  );
}
