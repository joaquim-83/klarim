// Selo de certificação derivado do score (KL-42) — espelha `_score_badge` do backend.
// ≥90 Klarim Verified ⭐ · ≥80 Klarim Approved ✅ · <80 sem selo.
export function badgeFor(score) {
  if (score == null) return null;
  if (score >= 90) return { level: 'verified', label: 'Klarim Verified', icon: '⭐', short: 'Verificado' };
  if (score >= 80) return { level: 'approved', label: 'Klarim Approved', icon: '✅', short: 'Aprovado' };
  return null;
}

export const SEMA_TEXT = {
  verde: 'text-green-400', amarelo: 'text-yellow-400', vermelho: 'text-red-400',
};
