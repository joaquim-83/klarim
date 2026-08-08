// KL-153 P2 — testes da configuração de navegação (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EMPRESA_LINKS, DEV_LINKS, PRODUCT_CARDS, authState, dashboardMenu, otherDropdowns } from './nav.js'
import { loggedInRedirect } from './serverAuth.js'

// --- Header: dropdowns "Para empresas" / "Para devs" --- //
test('EMPRESA_LINKS: 4 links, começa por "Verificar meu site"', () => {
  assert.equal(EMPRESA_LINKS.length, 4)
  assert.equal(EMPRESA_LINKS[0].label, 'Verificar meu site')
  assert.equal(EMPRESA_LINKS[0].href, '/#scan')
  assert.deepEqual(EMPRESA_LINKS.map((l) => l.href),
    ['/#scan', '/#para-empresas', '/setores', '/planos'])
})

test('DEV_LINKS: 4 links (Security Gate, Documentação, Planos dev, API)', () => {
  assert.equal(DEV_LINKS.length, 4)
  assert.deepEqual(DEV_LINKS.map((l) => l.label),
    ['Security Gate', 'Documentação', 'Planos dev', 'API'])
  assert.equal(DEV_LINKS[0].href, '/security-gate')
  assert.equal(DEV_LINKS[3].href, '/docs/gate/api')
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
