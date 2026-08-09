// KL-153 P2 — testes da configuração de navegação (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EMPRESA_LINKS, DEV_LINKS, DEV_DROPDOWN_LABEL, PRODUCT_CARDS, authState, dashboardMenu,
  otherDropdowns, gatePlanCtaHref, gatePlanCtaLabel, gateSignupRedirect } from './nav.js'
import { loggedInRedirect } from './serverAuth.js'

// --- Header: dropdowns "Para empresas" / "Para devs" --- //
test('EMPRESA_LINKS: 4 links, começa por "Verificar meu site"', () => {
  assert.equal(EMPRESA_LINKS.length, 4)
  assert.equal(EMPRESA_LINKS[0].label, 'Verificar meu site')
  assert.equal(EMPRESA_LINKS[0].href, '/#scan')
  assert.deepEqual(EMPRESA_LINKS.map((l) => l.href),
    ['/#scan', '/#para-empresas', '/setores', '/planos'])
})

// KL-150 (ajuste): dropdown "Desenvolvedor ▼" com 1 sub-item (Security Gate).
test('DEV_DROPDOWN_LABEL = "Desenvolvedor"', () => {
  assert.equal(DEV_DROPDOWN_LABEL, 'Desenvolvedor')
})

test('DEV_LINKS: 1 sub-item (Security Gate)', () => {
  assert.equal(DEV_LINKS.length, 1)
  assert.equal(DEV_LINKS[0].label, 'Security Gate')
  assert.equal(DEV_LINKS[0].href, '/security-gate')
})

// --- Header: estado logado/deslogado --- //
test('authState: com user → "in"; sem → "out"', () => {
  assert.equal(authState({ user: { name: 'X' } }), 'in')
  assert.equal(authState(null), 'out')
  assert.equal(authState({}), 'out')
})

// --- Home: dual-card --- //
test('PRODUCT_CARDS: 2 cards com linguagem separada', () => {
  assert.equal(PRODUCT_CARDS.length, 2)
  const empresa = PRODUCT_CARDS.find((c) => c.audience === 'empresa')
  const dev = PRODUCT_CARDS.find((c) => c.audience === 'dev')
  assert.equal(empresa.cta, 'Verificar meu site')
  assert.match(empresa.subtitle, /Sem cadastro/)
  assert.equal(dev.cta, 'Começar grátis')
  assert.equal(dev.href, '/security-gate')
  assert.match(dev.subtitle, /CI\/CD/)
})

// --- Menu do dashboard: Security Gate p/ todos --- //
test('dashboardMenu: "Security Gate" visível para owner E developer', () => {
  for (const type of ['owner', 'developer', 'both']) {
    const labels = dashboardMenu(type).map((m) => m.label)
    assert.ok(labels.includes('Security Gate'), `type=${type}`)
    assert.ok(labels.includes('Meu dashboard'))
  }
})

// --- KL-156: fechar o outro dropdown ao abrir um --- //
test('otherDropdowns: devolve todos menos o que abriu', () => {
  const a = { id: 'a' }, b = { id: 'b' }, c = { id: 'c' }
  assert.deepEqual(otherDropdowns([a, b, c], b), [a, c])
  assert.deepEqual(otherDropdowns([a], a), [])
  assert.deepEqual(otherDropdowns(null, a), [])
})

// --- KL-157: redirect do usuário logado que cai no /cadastrar --- //
test('loggedInRedirect: dev → /dashboard/gate; senão → fallback/dashboard', () => {
  assert.equal(loggedInRedirect(true), '/dashboard/gate')
  assert.equal(loggedInRedirect(true, '/x'), '/dashboard/gate')   // dev ignora o fallback
  assert.equal(loggedInRedirect(false, '/dashboard/conta'), '/dashboard/conta')
  assert.equal(loggedInRedirect(false), '/dashboard')
})

// --- KL-150 (Fix 2): CTA de plano ciente de auth + redirect pós-signup com upgrade --- //
test('gatePlanCtaHref: deslogado passa pelo cadastro developer', () => {
  assert.equal(gatePlanCtaHref('free', false), '/cadastrar?type=developer')
  assert.equal(gatePlanCtaHref('pro', false), '/cadastrar?type=developer&plan=pro')
  assert.equal(gatePlanCtaHref('team', false), '/cadastrar?type=developer&plan=team')
  assert.equal(gatePlanCtaHref('enterprise', false), '/contato')
})

test('gatePlanCtaHref: logado vai direto ao portal (Pro/Team com upgrade)', () => {
  assert.equal(gatePlanCtaHref('free', true), '/dashboard/gate')
  assert.equal(gatePlanCtaHref('pro', true), '/dashboard/gate?upgrade=pro')
  assert.equal(gatePlanCtaHref('team', true), '/dashboard/gate?upgrade=team')
  assert.equal(gatePlanCtaHref('enterprise', true), '/contato')   // vendas independe de auth
})

test('gatePlanCtaLabel: por slug', () => {
  assert.equal(gatePlanCtaLabel('free'), 'Começar grátis →')
  assert.equal(gatePlanCtaLabel('pro'), 'Assinar →')
  assert.equal(gatePlanCtaLabel('team'), 'Assinar →')
  assert.equal(gatePlanCtaLabel('enterprise'), 'Falar com vendas')
})

test('gateSignupRedirect: plan=pro → redirect inclui upgrade=pro', () => {
  assert.equal(gateSignupRedirect('pro'), '/dashboard/gate?upgrade=pro')
  assert.equal(gateSignupRedirect('team'), '/dashboard/gate?upgrade=team')
  assert.equal(gateSignupRedirect(''), '/dashboard/gate')     // sem plano → só o portal
  assert.equal(gateSignupRedirect(null), '/dashboard/gate')
})

test('loggedInRedirect: dev com plano Gate → portal com upgrade', () => {
  assert.equal(loggedInRedirect(true, '/dashboard', 'pro'), '/dashboard/gate?upgrade=pro')
  assert.equal(loggedInRedirect(true, '/dashboard', ''), '/dashboard/gate')
})
