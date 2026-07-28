// KL-123 — lógica PURA do card de vigília expansível (testável em node --test; a UI vive
// em components/dashboard-v2/VigiliaDetail.jsx). Mantém a linguagem acessível ao dono
// (nada de OWASP/CWE) e o mapeamento de status/badge fora do componente.

// Rótulos acessíveis por tipo (espelham api/vigilia_details.py::VIGILIA_LABELS).
export const VIGILIA_LABELS = {
  ssl: 'Certificado SSL',
  domain: 'Registro do domínio',
  score: 'Nota de segurança',
  email: 'Proteção de e-mail',
  reputation: 'Reputação online',
  uptime: 'Disponibilidade do site',
  changes: 'Alterações no site',
  phishing: 'Typosquat / phishing',
};

export function vigiliaLabel(tipo) {
  return VIGILIA_LABELS[tipo] || tipo;
}

// Status da vigília → ícone/cor/rótulo (verde/amarelo/vermelho constantes nos 2 temas).
export const VIG_STATUS = {
  ok: { icon: '🟢', color: '#22c55e', label: 'OK' },
  warning: { icon: '🟡', color: '#eab308', label: 'Atenção' },
  alert: { icon: '🟡', color: '#eab308', label: 'Atenção' },
  critical: { icon: '🔴', color: '#ef4444', label: 'Crítico' },
  error: { icon: '🔴', color: '#ef4444', label: 'Erro' },
  unknown: { icon: '⚪', color: '#94a3b8', label: 'Aguardando' },
};

export function statusMeta(status) {
  return VIG_STATUS[status] || VIG_STATUS.ok;
}

// Badge numérico só quando há itens pendentes (pending_count > 0).
export function showBadge(pendingCount) {
  return typeof pendingCount === 'number' && pendingCount > 0;
}

// Estado acessível de SPF/DKIM/DMARC (nada de jargão).
export function emailStateLabel(state) {
  if (state === 'pass') return { icon: '✅', text: 'Ativo' };
  if (state === 'absent') return { icon: '❌', text: 'Ausente' };
  return { icon: '➖', text: 'Não verificado' };
}

// Aplica "Não é ameaça" LOCALMENTE (sem recarregar): marca o alerta como descartado e
// recalcula pending_count/status/summary — é o que o botão faz no otimista.
export function applyDismiss(details, alertId) {
  const alerts = details && details.data && Array.isArray(details.data.alerts)
    ? details.data.alerts
    : null;
  if (!alerts) return details;
  const next = alerts.map((a) => (a.id === alertId ? { ...a, dismissed: true } : a));
  const pending = next.filter((a) => !a.dismissed).length;
  return {
    ...details,
    data: { ...details.data, alerts: next },
    pending_count: pending,
    status: pending > 0 ? 'critical' : 'ok',
    summary: pending > 0
      ? `${pending} domínio(s) suspeito(s) para revisar.`
      : 'Nenhum domínio suspeito detectado.',
  };
}

// Domínios suspeitos ainda pendentes (a lista visível no card de phishing).
export function pendingAlerts(details) {
  const alerts = details && details.data && Array.isArray(details.data.alerts)
    ? details.data.alerts
    : [];
  return alerts.filter((a) => !a.dismissed);
}
