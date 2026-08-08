// KL-153 P2 — lógica PURA da UX do Security Gate (testável, node --test): normalização de URL,
// CPF (máscara + validação client-side), agregação do resultado por KYC, wizard, rate limit,
// upgrade e estado do CTA. Os componentes React consomem estas funções; sem DOM/React aqui.

// ---- URL ---- //
// Normaliza o input do dev (aceita `example.com`, `http://localhost:3000`, `https://x.com`).
export function normalizeUrl(input) {
  const s = (input || '').trim();
  if (!s) return '';
  if (/^https?:\/\//i.test(s)) return s;
  return `https://${s}`;
}

// ---- CPF ---- //
// Máscara progressiva 000.000.000-00 enquanto o usuário digita.
export function maskCPF(value) {
  const d = (value || '').replace(/\D/g, '').slice(0, 11);
  let out = d.slice(0, 3);
  if (d.length > 3) out += '.' + d.slice(3, 6);
  if (d.length > 6) out += '.' + d.slice(6, 9);
  if (d.length > 9) out += '-' + d.slice(9, 11);
  return out;
}

function cpfDigit(digits) {
  const weight = digits.length + 1;
  let total = 0;
  for (let i = 0; i < digits.length; i++) total += Number(digits[i]) * (weight - i);
  const r = (total * 10) % 11;
  return r >= 10 ? 0 : r;
}

// Validação client-side (espelha o backend `validate_cpf`): 11 dígitos, não repetido, 2 DVs.
// A validação FINAL é sempre no backend; aqui é só feedback imediato.
export function isValidCPF(value) {
  const d = (value || '').replace(/\D/g, '');
  if (d.length !== 11) return false;
  if (d === d[0].repeat(11)) return false;
  if (cpfDigit(d.slice(0, 9)) !== Number(d[9])) return false;
  if (cpfDigit(d.slice(0, 10)) !== Number(d[10])) return false;
  return true;
}

// ---- Resultado do scan ---- //
// `basic` (sem KYC) e `complete` (com KYC) trazem `categories` (name/status/checks_*). Devolve as
// categorias já prontas para exibir (contagens), independente do nível.
export function categorySummary(result) {
  return Array.isArray(result?.categories) ? result.categories : [];
}

// Só o nível `complete` (KYC feito) vê os checks individuais + evidências/recomendações.
export function showChecksDetail(accessLevel) {
  return accessLevel === 'complete';
}

// Agrupa os checks detalhados (result.results) por categoria — usado no resultado COMPLETO.
export function groupChecksByCategory(results) {
  const groups = {};
  const order = [];
  for (const r of results || []) {
    const c = r.category || 'outros';
    if (!groups[c]) { groups[c] = []; order.push(c); }
    groups[c].push(r);
  }
  return order.map((c) => ({ name: c, checks: groups[c] }));
}

// O banner "complete seu cadastro" aparece enquanto a conta não tem KYC.
export function kycBannerVisible(accessLevel) {
  return accessLevel !== 'complete';
}

// A ponte scan-público → Gate só aparece quando há RESULTADO (não em loading/erro).
export function showGateBridge(step, result) {
  return step === 'result' && !!result;
}

// ---- Wizard (6 steps) ---- //
// 1 URL · 2 scanning · 3 resumo · 4 KYC · 5 completo · 6 CI/CD.
export function wizardNext(step, opts = {}) {
  const { kycCompleted = false, skip = false } = opts;
  switch (step) {
    case 1: return 2;
    case 2: return 3;
    case 3: return kycCompleted ? 5 : 4;   // já tem KYC → pula direto ao resultado completo
    case 4: return skip ? 6 : 5;           // "pular por agora" → CI/CD com resumo; confirmou → completo
    case 5: return 6;
    default: return 6;
  }
}

// GatePortal decide se mostra o wizard: só quando ainda não há scan nenhum.
export function shouldShowWizard(status, runsCount) {
  if (!status) return false;
  return (runsCount || 0) === 0;
}

// ---- Rate limit (429) ---- //
export function formatCountdown(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

export function rateLimitMessage(limitType, retryAfterSeconds) {
  const mins = Math.max(1, Math.round((Number(retryAfterSeconds) || 0) / 60));
  switch (limitType) {
    case 'user':
      return { title: 'Limite do plano atingido',
               message: 'Você atingiu o limite do plano Free. Faça upgrade para continuar.',
               showUpgrade: true, retryAfterSeconds };
    case 'ip':
      return { title: 'Muitas consultas', showUpgrade: false, retryAfterSeconds,
               message: `Muitas consultas do seu IP. Aguarde ${mins} minuto(s).` };
    case 'domain':
      return { title: 'Domínio escaneado recentemente', showUpgrade: false, retryAfterSeconds,
               message: `Este domínio foi escaneado recentemente. Aguarde ${mins} minuto(s).` };
    case 'interval':
    case 'domain_interval':
      return { title: 'Aguarde entre domínios', showUpgrade: false, retryAfterSeconds,
               message: `Aguarde ${mins} minuto(s) entre domínios diferentes no plano Free.` };
    default:
      return { title: 'Limite de consultas', showUpgrade: false, retryAfterSeconds,
               message: `Limite de consultas excedido. Aguarde ${mins} minuto(s).` };
  }
}

// ---- Uso + upgrade ---- //
export function usageText(used, limit) {
  if (Number(limit) === -1) return `${used || 0} scans usados esta hora · ilimitado`;
  return `${used || 0} de ${limit || 0} scans usados esta hora`;
}

const _NEXT = {
  free: { slug: 'pro', label: 'Pro', price_display: 'R$ 49/mês' },
  pro: { slug: 'team', label: 'Team', price_display: 'R$ 149/mês' },
};

// Próximo plano de upgrade (null em team/enterprise). O preço é só um HINT — o backend é a fonte.
export function upgradeTarget(planSlug) {
  return _NEXT[planSlug] || null;
}

// KL-156 — metadados do plano p/ o bloco "Seu plano" (espelham os limites do backend; só HINT
// visual — o enforcement é server-side). Cooldown por domínio = KL-155 (free 30min · pro 5min ·
// team/ent sem cooldown). Scans/hora = camada 2 do rate limiter.
const _PLAN_META = {
  free: { name: 'Free', scansHour: 5, cooldownLabel: '30 minutos' },
  pro: { name: 'Pro', scansHour: 50, cooldownLabel: '5 minutos' },
  team: { name: 'Team', scansHour: 200, cooldownLabel: 'sem cooldown' },
  enterprise: { name: 'Enterprise', scansHour: 'ilimitado', cooldownLabel: 'sem cooldown' },
};

export function planName(slug) {
  return (_PLAN_META[slug] || _PLAN_META.free).name;
}

export function planDetails(slug) {
  const m = _PLAN_META[slug] || _PLAN_META.free;
  return { slug: slug || 'free', name: m.name, scansHour: m.scansHour,
           cooldownLabel: m.cooldownLabel, next: upgradeTarget(slug) };
}

// Só há upgrade se existe um próximo plano (Team/Enterprise já são o topo).
export function canUpgrade(slug) {
  return upgradeTarget(slug) !== null;
}

// KL-158 — o botão "Confirmar" do KYC habilita com CPF válido + endereço (≥10) + telefone (a
// validação final é no backend, que também exige e-mail confirmado — KL-156).
export function canSubmitKyc(cpf, address, phone) {
  return isValidCPF(cpf) && (address || '').trim().length >= 10 && !!(phone || '').trim();
}

// ---- Estado do CTA da landing / menu ---- //
// status = GET /api/account/gate/status (ou {ok:false} se não logado).
export function ctaState({ ok, gate_active } = {}) {
  if (!ok) return 'logged_out';
  return gate_active ? 'active' : 'inactive';
}

export function ctaLabel(state) {
  switch (state) {
    case 'active': return { label: 'Abrir dashboard →', href: '/dashboard/gate' };
    case 'inactive': return { label: 'Ativar Security Gate →', action: 'activate' };
    default: return { label: 'Começar grátis →', href: '/cadastrar?type=developer' };
  }
}

// Corpo do POST /account/signup do fluxo dev (inclui `source=security-gate`).
export function signupBody(fields = {}) {
  const body = { email: fields.email };
  if (fields.url) body.url = fields.url;
  if (fields.role) body.role = fields.role;
  if (fields.invite) body.invite = fields.invite;
  if (fields.plan) body.plan = fields.plan;
  if (fields.source) body.source = fields.source;
  return body;
}
