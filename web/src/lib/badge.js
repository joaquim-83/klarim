// Selo FACTUAL "Monitorado por Klarim" (KL-42; regra KL-78 item 3) — espelha
// `_score_badge` do backend. Só aparece com **score perfeito (100)** E **conta atribuída**
// (`hasAccount`). Regra inviolável: NUNCA "Approved"/"Certificado"/"Verified" (endosso).
// Selo único (removida a distinção ⭐≥90/✅≥80) — o selo é conquista real, não participação.
export function badgeFor(score, hasAccount = false) {
  if (score == null || score < 100 || !hasAccount) return null;
  return { level: 'high', label: 'Monitorado por Klarim', icon: '⭐', short: 'Monitorado' };
}

export const SEMA_TEXT = {
  verde: 'text-green-400', amarelo: 'text-yellow-400', vermelho: 'text-red-400',
};
