// KL-153 P2 — configuração PURA de navegação (testável, node --test). Separa os DOIS públicos:
// "Para empresas" (linguagem site/seguro/clientes/verificar) e "Para devs" (deploy/exposto/
// pipeline/CI-CD). O Header.astro e a home consomem estas listas no BUILD; os testes cobrem os
// links/labels. Nada de DOM/React aqui.

export const EMPRESA_LINKS = [
  { href: '/#scan', label: 'Verificar meu site' },
  { href: '/#para-empresas', label: 'Monitoramento' },
  { href: '/ferramentas', label: 'Ferramentas' },   // KL-134 — micro-ferramentas SEO
  { href: '/setores', label: 'Setores' },
  { href: '/planos', label: 'Planos empresa' },
];

// KL-150 (ajuste) — o menu de devs volta a ser um DROPDOWN "Desenvolvedor ▼" (preparado para
// crescer com novos produtos). Hoje tem 1 sub-item (Security Gate); o dropdown com 1 item é
// intencional. Os antigos "Documentação/Planos dev/API" seguem como acessos rápidos na landing.
export const DEV_DROPDOWN_LABEL = 'Desenvolvedor';
export const DEV_LINKS = [
  { href: '/security-gate', label: 'Security Gate' },
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

// KL-150 (Fix 2) — CTA dos planos da landing /security-gate, ciente do estado de auth.
// LOGADO vai direto ao portal do Gate (Free → abrir; Pro/Team → upgrade); DESLOGADO passa pelo
// cadastro developer (que, após o signup, redireciona ao portal com ?upgrade= — ver
// `gateSignupRedirect`). Enterprise sempre fala com vendas. Pura/testável.
export function gatePlanCtaHref(slug, loggedIn) {
  if (slug === 'enterprise') return '/contato';
  if (slug === 'free') return loggedIn ? '/dashboard/gate' : '/cadastrar?type=developer';
  // pro / team
  return loggedIn ? `/dashboard/gate?upgrade=${slug}` : `/cadastrar?type=developer&plan=${slug}`;
}

export function gatePlanCtaLabel(slug) {
  if (slug === 'free') return 'Começar grátis →';
  if (slug === 'enterprise') return 'Falar com vendas';
  return 'Assinar →';   // pro / team
}

// KL-150 (Fix 2) — destino pós-signup do fluxo dev: com um plano Gate (pro/team) redireciona ao
// portal já disparando o upgrade (`?upgrade=`); sem plano, só ao portal. Usado no `cadastrar.astro`
// e reusado pelo `loggedInRedirect` (serverAuth) p/ o usuário logado que cai no /cadastrar.
export function gateSignupRedirect(gatePlan) {
  return gatePlan ? `/dashboard/gate?upgrade=${gatePlan}` : '/dashboard/gate';
}

// KL-150 P2 (item 2) — nav do dashboard do Gate (o dev ficava preso em /dashboard/gate sem links
// para voltar/navegar). Devolve os links do menu horizontal, com `current` marcando a página atual.
// Conta `both` (dev + owner) ganha "Meus sites" (o /dashboard mostra os sites monitorados). Pura.
export function gateDashboardNav(accountType) {
  const links = [{ label: 'Dashboard', href: '/dashboard' }];
  if (accountType === 'both') links.push({ label: 'Meus sites', href: '/dashboard' });
  links.push({ label: 'Security Gate', href: '/dashboard/gate', current: true });
  links.push({ label: 'Minha conta', href: '/dashboard/conta' });
  return links;
}
