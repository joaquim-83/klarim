// Selo FACTUAL derivado do score (KL-42) — espelha `_score_badge` do backend.
// Regra inviolável: NUNCA "Approved"/"Certificado"/"Verified" (endosso) — sempre
// "Monitorado por Klarim". O ícone diferencia a faixa: ≥90 ⭐ · ≥80 ✅ · <80 sem selo.
export function badgeFor(score) {
  if (score == null) return null;
  if (score >= 90) return { level: 'high', label: 'Monitorado por Klarim', icon: '⭐', short: 'Monitorado' };
  if (score >= 80) return { level: 'mid', label: 'Monitorado por Klarim', icon: '✅', short: 'Monitorado' };
  return null;
}

export const SEMA_TEXT = {
  verde: 'text-green-400', amarelo: 'text-yellow-400', vermelho: 'text-red-400',
};
