import { useState } from 'react';
import { card } from './ui.js';
import { badgeFor } from '../../lib/badge.js';

// Compartilhamento de score (KL-42): selo + posição no ranking + card social
// (download square/landscape) + copiar link + WhatsApp/LinkedIn/Twitter. Reutilizável
// no dashboard e no resultado do scan.

const SITE = 'https://klarim.net';

const ghost =
  'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700 px-4 py-2.5 ' +
  'text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-800';

export default function ShareScore({ domain, score, badge, ranking }) {
  const [copied, setCopied] = useState(false);
  const b = badge || badgeFor(score, true); // card do próprio site monitorado → tem conta
  const profileUrl = `${SITE}/site/${domain}`;
  const text = `Nosso site tem score ${score}/100 de segurança no Klarim. E o seu?`;

  function track(ev, meta) {
    try { window.klarimTrack?.(ev, meta, `https://${domain}`); } catch { /* nunca quebra */ }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(profileUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      track('share_clicked', { network: 'copy_link' });
    } catch { /* clipboard indisponível */ }
  }

  const wa = `https://wa.me/?text=${encodeURIComponent(text + ' ' + profileUrl)}`;
  const li = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(profileUrl)}`;
  const tw = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(profileUrl)}`;

  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Compartilhar seu score</h2>

      {/* Selo + posição no ranking */}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {b && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-500/40 bg-brand-500/10 px-3 py-1 text-sm font-semibold text-brand-300">
            {b.icon} {b.label}
          </span>
        )}
        {ranking && ranking.total > 1 && (
          <a href={`/ranking/${ranking.sector}`}
            className="text-sm text-slate-300 hover:text-white">
            #{ranking.position} de {ranking.total} em {ranking.sector_label}
            <span className="text-slate-500"> · acima de {ranking.percentile}%</span>
          </a>
        )}
      </div>

      {/* Preview do card */}
      <div className="mt-4 overflow-hidden rounded-xl border border-slate-800">
        <img src={`/api/card/${encodeURIComponent(domain)}.png?format=landscape`}
          alt={`Card de score de ${domain}`} loading="lazy" className="w-full" />
      </div>

      {/* Downloads */}
      <div className="mt-4 flex flex-wrap gap-3">
        <a href={`/api/card/${encodeURIComponent(domain)}.png?format=square`} download={`klarim-${domain}-instagram.png`}
          onClick={() => track('card_downloaded', { format: 'square' })} className={ghost}>
          📥 Instagram (1080×1080)
        </a>
        <a href={`/api/card/${encodeURIComponent(domain)}.png?format=landscape`} download={`klarim-${domain}-linkedin.png`}
          onClick={() => track('card_downloaded', { format: 'landscape' })} className={ghost}>
          📥 LinkedIn (1200×630)
        </a>
        <button type="button" onClick={copyLink} className={ghost}>
          {copied ? 'Link copiado ✓' : '🔗 Copiar link do perfil'}
        </button>
      </div>

      {/* Compartilhamento direto */}
      <p className="mt-5 text-sm text-slate-400">Ou compartilhe diretamente:</p>
      <div className="mt-2 flex flex-wrap gap-3">
        <a href={wa} target="_blank" rel="noopener" onClick={() => track('share_clicked', { network: 'whatsapp' })} className={ghost}>WhatsApp</a>
        <a href={li} target="_blank" rel="noopener" onClick={() => track('share_clicked', { network: 'linkedin' })} className={ghost}>LinkedIn</a>
        <a href={tw} target="_blank" rel="noopener" onClick={() => track('share_clicked', { network: 'twitter' })} className={ghost}>Twitter</a>
      </div>
    </div>
  );
}
