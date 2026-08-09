// KL-161 — lógica PURA do canal de direitos do titular (LGPD/DSAR). Testável (node --test); os
// tipos e a validação espelham o backend (POST /lgpd/request). Sem DOM/React aqui.

// slug (casa com o backend `_LGPD_TYPES` e com o `?tipo=` do link "Remover meus dados") → rótulo.
export const LGPD_TYPES = [
  { value: 'acesso', label: 'Acesso aos dados' },
  { value: 'correcao', label: 'Correção' },
  { value: 'exclusao', label: 'Exclusão' },
  { value: 'portabilidade', label: 'Portabilidade' },
  { value: 'revogacao', label: 'Revogar consentimento' },
  { value: 'outra', label: 'Outra' },
];

const _VALUES = new Set(LGPD_TYPES.map((t) => t.value));

// Pré-seleção do tipo a partir do `?tipo=` da URL (ex.: "Remover meus dados" → exclusao). Aceita
// sinônimos comuns; valor desconhecido → '' (o form mostra o placeholder "Selecione…").
const _ALIAS = {
  exclusao: 'exclusao', excluir: 'exclusao', remover: 'exclusao', delete: 'exclusao',
  acesso: 'acesso', acessar: 'acesso',
  correcao: 'correcao', corrigir: 'correcao',
  portabilidade: 'portabilidade',
  revogacao: 'revogacao', revogar: 'revogacao', consentimento: 'revogacao',
  outra: 'outra', outro: 'outra',
};
export function tipoFromParam(param) {
  const p = (param || '').trim().toLowerCase();
  const resolved = _ALIAS[p] || p;
  return _VALUES.has(resolved) ? resolved : '';
}

export function lgpdTypeLabel(value) {
  const t = LGPD_TYPES.find((x) => x.value === value);
  return t ? t.label : '';
}

// Validação de e-mail simples (o backend revalida com `_EMAIL_RE`).
export function isValidEmail(email) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((email || '').trim());
}

// Validação client-side dos campos obrigatórios do form (feedback imediato; o backend é a fonte).
// CPF é OPCIONAL — não entra aqui (o form só avisa se preenchido e inválido, sem bloquear o envio).
export function validateLgpdForm({ type, name, email, description } = {}) {
  const errors = {};
  if (!_VALUES.has((type || '').trim())) errors.type = 'Selecione o tipo de solicitação.';
  if (!(name || '').trim()) errors.name = 'Informe seu nome completo.';
  if (!isValidEmail(email)) errors.email = 'E-mail inválido.';
  if ((description || '').trim().length < 10) {
    errors.description = 'Descreva sua solicitação (mínimo 10 caracteres).';
  }
  return { ok: Object.keys(errors).length === 0, errors };
}
