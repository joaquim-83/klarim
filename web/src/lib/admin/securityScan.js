// KL-160 — lógica PURA da seção "Segurança da plataforma" do painel admin (testável, node --test).
// Sem DOM/React. Consumida por components/admin/PlatformSecurityCard.jsx.

// Semáforo pelo score do Gate (mesma escala do produto: ≥90 verde, ≥50 amarelo, senão vermelho).
export function scanSemaphore(score) {
  if (score == null) return { dot: '⚪', color: '#8B949E', label: '—' };
  if (score >= 90) return { dot: '🟢', color: '#00D26A', label: 'Seguro' };
  if (score >= 50) return { dot: '🟡', color: '#F0C000', label: 'Atenção' };
  return { dot: '🔴', color: '#F85149', label: 'Crítico' };
}

// Resumo de findings "N Critical | N High | N Medium" (o painel destaca).
export function findingsSummary(scan) {
  const c = Number(scan?.critical || 0);
  const h = Number(scan?.high || 0);
  const m = Number(scan?.medium || 0);
  return `${c} Critical | ${h} High | ${m} Medium`;
}

// Destacar em vermelho: score < 80 OU algum finding crítico (regra do card).
export function isUnhealthy(scan) {
  if (!scan) return false;
  if (Number(scan.critical || 0) > 0) return true;
  return scan.score != null && Number(scan.score) < 80;
}

// Texto do botão conforme o estado (rodando / cooldown / pronto).
export function scanButtonLabel({ running, busy } = {}) {
  if (running || busy) return 'Varredura em andamento…';
  return 'Executar varredura completa →';
}

// Mensagem ao clicar quando a resposta do POST não é "started".
export function triggerMessage(resp) {
  if (!resp) return '';
  if (resp.status === 'running') return 'Uma varredura já está em andamento.';
  if (resp.status === 'cooldown') {
    const mins = Math.max(1, Math.round((Number(resp.retry_after) || 0) / 60));
    return `Aguarde ${mins} min antes de rodar outra varredura.`;
  }
  if (resp.status === 'started') return 'Varredura iniciada…';
  return '';
}

// Cor por severidade de um check (para o detalhe expandível).
const SEV_COLOR = { critical: '#F85149', high: '#FF6B35', medium: '#F0C000', low: '#58A6FF', info: '#8B949E' };
export function severityColor(sev) {
  return SEV_COLOR[(sev || '').toLowerCase()] || '#8B949E';
}

// Ícone por status de um check.
export function checkIcon(status) {
  return { pass: '✅', fail: '❌', error: '⚠️', skip: '⏭️' }[(status || '').toLowerCase()] || '•';
}

// Ordena os checks: FAIL primeiro (por severidade desc), depois error, depois pass.
const _SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const _STATUS_RANK = { fail: 0, error: 1, skip: 2, pass: 3 };
export function sortChecks(results) {
  return [...(results || [])].sort((a, b) => {
    const sa = _STATUS_RANK[(a.status || '').toLowerCase()] ?? 9;
    const sb = _STATUS_RANK[(b.status || '').toLowerCase()] ?? 9;
    if (sa !== sb) return sa - sb;
    const va = _SEV_RANK[(a.severity || '').toLowerCase()] ?? 9;
    const vb = _SEV_RANK[(b.severity || '').toLowerCase()] ?? 9;
    return va - vb;
  });
}
