// KL-90 Prompt 2 — tokens e helpers compartilhados do Dashboard v2.
// Reusa o padrão visual das ilhas de conta (ui.js): utilitários slate/white são
// theme-aware (KL-87 sobrescreve --color-slate-*/--color-white por tema), então os
// mesmos componentes funcionam no light e no dark. Cores de status (verde/amarelo/
// vermelho) e o laranja da marca são constantes nos 2 temas.

export const SEMA_COLOR = { verde: '#22c55e', amarelo: '#eab308', vermelho: '#ef4444' };
export const SEMA_DOT = { verde: '🟢', amarelo: '🟡', vermelho: '🔴' };
export const SEMA_LABEL = { verde: 'Seguro', amarelo: 'Atenção', vermelho: 'Crítico' };

export const CAT_ICON = { ok: '✅', warning: '⚠️', critical: '❌' };
export const CAT_COLOR = { ok: '#22c55e', warning: '#eab308', critical: '#ef4444' };

// Severidade dos riscos (o endpoint devolve em minúsculo).
export const SEV_META = {
  critica: { icon: '🔴', label: 'Crítica', color: '#ef4444' },
  alta: { icon: '🟠', label: 'Alta', color: '#f97316' },
  media: { icon: '🟡', label: 'Média', color: '#eab308' },
  baixa: { icon: '⚪', label: 'Baixa', color: '#94a3b8' },
};

// Tokens de estilo (theme-aware). `--accent-text` garante contraste no botão laranja
// (constante nos 2 temas, KL-87) — não usar text-white em cima do brand-500.
export const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-5 sm:p-6';
export const brandBtn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-5 py-3 ' +
  'text-sm font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] ' +
  'disabled:opacity-50 disabled:cursor-not-allowed sm:w-auto';
export const outlineBtn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-slate-700 px-5 py-3 ' +
  'text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-800 active:scale-[0.98] ' +
  'disabled:opacity-50 disabled:cursor-not-allowed sm:w-auto';

// Timestamps naive do Postgres → adiciona Z antes de new Date (padrão KL-51).
export function parseUTC(s) {
  if (!s) return s;
  return /[Zz]/.test(s) || /[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`;
}

export function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(parseUTC(s)).toLocaleDateString('pt-BR'); } catch { return '—'; }
}

// Data relativa curta ("hoje", "ontem", "há 3 dias", senão a data).
export function relDate(s) {
  if (!s) return '—';
  try {
    const d = new Date(parseUTC(s));
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) return 'hoje';
    if (days === 1) return 'ontem';
    if (days < 30) return `há ${days} dias`;
    return d.toLocaleDateString('pt-BR');
  } catch { return '—'; }
}

// Perfil público de um domínio (compartilhamento).
export const profileUrl = (domain) => `https://klarim.net/site/${domain}`;
