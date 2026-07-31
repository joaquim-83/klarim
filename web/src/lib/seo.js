// KL-132 — helpers PUROS de SEO programático dos perfis (testáveis em node --test).
// Títulos que capturam buscas de reputação ("{empresa} é seguro?") + meta descriptions
// com score/semáforo/48 pontos, respeitando os limites do Google (título ~60, desc ~155).

const TITLE_MAX = 60;
const DESC_MAX = 155;

// `lotusforme.com.br` → `Lotusforme` (1ª parte antes do ponto, inicial maiúscula).
export function formatDomainName(domain) {
  const d = String(domain || '').trim().toLowerCase().replace(/^www\./, '');
  if (!d) return '';
  const first = d.split('.')[0] || d;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

function truncate(s, max) {
  const str = String(s || '');
  return str.length > max ? str.slice(0, Math.max(1, max - 1)).trimEnd() + '…' : str;
}

// Nome de exibição: company_name (se houver) senão o domínio formatado.
export function displayName(companyName, domain) {
  const c = (companyName || '').trim();
  return c || formatDomainName(domain);
}

// Título ≤60 chars: "{Nome} é seguro? Score {score}/100 | Klarim" (nome truncado se preciso).
export function profileTitle({ companyName, domain, score } = {}) {
  const suffix = ` é seguro? Score ${score}/100 | Klarim`;
  const budget = Math.max(1, TITLE_MAX - suffix.length);
  const name = truncate(displayName(companyName, domain), budget);
  return name + suffix;
}

// Semáforo → texto humano (para a meta description).
export function semaphoreText(semaphore) {
  return { verde: 'Excelente', amarelo: 'Atenção', vermelho: 'Crítico' }[semaphore] || 'Atenção';
}

// Meta description ≤155 chars com score + semáforo + "48 pontos".
export function profileDescription({ domain, score, semaphore } = {}) {
  const sem = semaphoreText(semaphore);
  const full = `Análise de segurança do site ${domain}: score ${score}/100 (${sem}). `
    + 'HTTPS, SSL, headers e mais — 48 pontos verificados. Relatório completo grátis.';
  return truncate(full, DESC_MAX);
}
