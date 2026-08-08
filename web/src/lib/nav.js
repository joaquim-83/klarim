// KL-153 P2 — configuração PURA de navegação (testável, node --test). Separa os DOIS públicos:
// "Para empresas" (linguagem site/seguro/clientes/verificar) e "Para devs" (deploy/exposto/
// pipeline/CI-CD). O Header.astro e a home consomem estas listas no BUILD; os testes cobrem os
// links/labels. Nada de DOM/React aqui.

export const EMPRESA_LINKS = [
  { href: '/#scan', label: 'Verificar meu site' },
  { href: '/#para-empresas', label: 'Monitoramento' },
  { href: '/setores', label: 'Setores' },
  { href: '/planos', label: 'Planos empresa' },
];

export const DEV_LINKS = [
  { href: '/security-gate', label: 'Security Gate' },
  { href: '/docs/gate/github-actions', label: 'Documentação' },
  { href: '/security-gate#planos', label: 'Planos dev' },
  { href: '/docs/gate/api', label: 'API' },
];

// Cards da home separando os dois produtos. Linguagem DISTINTA por público (regra do card).
export const PRODUCT_CARDS = [
  {
    audience: 'empresa', id: 'para-empresas', icon: '🔍',
    title: 'Seu site é seguro para seus clientes?',
    subtitle: 'Verificação gratuita em 30 segundos. Sem cadastro.',
    cta: 'Verificar meu site', href: '#scan',
  },
  {
    audience: 'dev', id: 'para-devs', icon: '⌘',
    title: 'Seu deploy expõe dados?',
    subtitle: '86 verificações de segurança no CI/CD. Resultado em 30 segundos.',
    cta: 'Começar grátis', href: '/security-gate',
  },
];

// Estado de auth do header a partir da resposta de /api/account/me (`{user}` = logado).
export function authState(me) {
  return me && me.user ? 'in' : 'out';
}

// Menu do dashboard (dropdown do usuário logado). "Security Gate" aparece para TODOS os tipos de
// conta (owner e developer) — o /dashboard/gate ativa o Gate sozinho quando a conta ainda é owner.
export function dashboardMenu(_accountType) {
  return [
    { href: '/dashboard', label: 'Meu dashboard' },
    { href: '/dashboard/gate', label: 'Security Gate' },
    { href: '/dashboard/conta', label: 'Minha conta' },
  ];
}

// KL-156 — quais dropdowns fechar quando `current` abre (todos os outros). Lógica pura usada pelo
// header.js (fechar o outro dropdown ao abrir um; e todos ao clicar fora).
export function otherDropdowns(all, current) {
  return Array.from(all || []).filter((d) => d !== current);
}
