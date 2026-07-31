// KL-135 — testes do banner de consentimento (public/cookie-consent.js). Carrega o IIFE num
// contexto `vm` com DOM/cookie mockados e verifica a REGRA CRÍTICA (LGPD): o GA4 NUNCA é
// injetado sem consentimento de analytics; e as escolhas (aceitar/recusar/configurar) gravam
// o cookie certo e (des)ligam o GA4.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const SRC = readFileSync(new URL('../../public/cookie-consent.js', import.meta.url), 'utf8')

function mkEl(id, hidden = false) {
  return {
    id, checked: false, _attrs: hidden ? { hidden: '' } : {},
    hasAttribute(a) { return a in this._attrs },
    setAttribute(a, v) { this._attrs[a] = v == null ? '' : String(v) },
    removeAttribute(a) { delete this._attrs[a] },
  }
}

function makeEnv({ cookie = '', protocol = 'https:' } = {}) {
  const jar = {}
  const rawSets = []
  if (cookie) { const i = cookie.indexOf('='); jar[cookie.slice(0, i)] = cookie.slice(i + 1) }
  const created = []          // scripts injetados no <head>
  const listeners = {}
  const els = {
    'cookie-banner': mkEl('cookie-banner', true),   // começa hidden (como no HTML)
    'cc-config': mkEl('cc-config', true),
    'cc-analytics': mkEl('cc-analytics'),
  }
  const document = {
    get cookie() { return Object.entries(jar).map(([k, v]) => k + '=' + v).join('; ') },
    set cookie(str) {
      rawSets.push(str)
      const first = str.split(';')[0]; const eq = first.indexOf('=')
      jar[first.slice(0, eq).trim()] = first.slice(eq + 1)
    },
    getElementById: (id) => els[id] || null,
    querySelector: (sel) => (sel.includes('googletagmanager')
      ? created.find((s) => (s.src || '').includes('googletagmanager')) || null : null),
    createElement: () => ({}),
    head: { appendChild: (s) => created.push(s) },
    addEventListener: (e, cb) => { (listeners[e] = listeners[e] || []).push(cb) },
  }
  const ctx = {
    document, created, rawSets, els, listeners,
    window: { location: { protocol } },
    Date, console,
  }
  ctx.window.window = ctx.window
  vm.createContext(ctx)
  vm.runInContext(SRC, ctx)
  return ctx
}

function click(ctx, action) {
  const node = { getAttribute: (a) => (a === 'data-cc' ? action : null) }
  const evt = { target: { closest: () => node }, preventDefault() { this._prevented = true } }
  ;(ctx.listeners.click || []).forEach((cb) => cb(evt))
  return evt
}

const gaLoaded = (ctx) => ctx.created.some((s) => (s.src || '').includes('googletagmanager')) || !!ctx.window.__klarimGA4
const bannerOpen = (ctx) => !ctx.els['cookie-banner'].hasAttribute('hidden')
const consent = (ctx) => decodeURIComponent((ctx.document.cookie.split('klarim_consent=')[1] || '').split(';')[0])

test('1ª visita (sem cookie): banner aberto e GA4 NÃO carrega', () => {
  const ctx = makeEnv()
  assert.equal(gaLoaded(ctx), false, 'GA4 nunca deve carregar sem consentimento')
  assert.equal(bannerOpen(ctx), true, 'banner deve aparecer na 1ª visita')
})

test('Aceitar todos: cookie=all, GA4 carrega, banner fecha', () => {
  const ctx = makeEnv()
  click(ctx, 'accept')
  assert.equal(consent(ctx), 'all')
  assert.equal(gaLoaded(ctx), true)
  assert.equal(bannerOpen(ctx), false)
})

test('Recusar: cookie=essential, GA4 NÃO carrega, banner fecha', () => {
  const ctx = makeEnv()
  click(ctx, 'reject')
  assert.equal(consent(ctx), 'essential')
  assert.equal(gaLoaded(ctx), false)
  assert.equal(bannerOpen(ctx), false)
})

test('Configurar + salvar com analytics marcado → cookie=analytics + GA4', () => {
  const ctx = makeEnv()
  click(ctx, 'configure')
  assert.equal(ctx.els['cc-config'].hasAttribute('hidden'), false, 'painel abre')
  ctx.els['cc-analytics'].checked = true
  click(ctx, 'save')
  assert.equal(consent(ctx), 'analytics')
  assert.equal(gaLoaded(ctx), true)
})

test('Configurar + salvar com analytics desmarcado → cookie=essential, sem GA4', () => {
  const ctx = makeEnv()
  click(ctx, 'configure')
  ctx.els['cc-analytics'].checked = false
  click(ctx, 'save')
  assert.equal(consent(ctx), 'essential')
  assert.equal(gaLoaded(ctx), false)
})

test('Retorno com cookie=all: GA4 carrega no init, banner NÃO aparece', () => {
  const ctx = makeEnv({ cookie: 'klarim_consent=all' })
  assert.equal(gaLoaded(ctx), true)
  assert.equal(bannerOpen(ctx), false)
})

test('Retorno com cookie=essential: GA4 NÃO carrega e banner NÃO aparece', () => {
  const ctx = makeEnv({ cookie: 'klarim_consent=essential' })
  assert.equal(gaLoaded(ctx), false)
  assert.equal(bannerOpen(ctx), false)
})

test('Retorno com cookie=analytics: GA4 carrega no init', () => {
  const ctx = makeEnv({ cookie: 'klarim_consent=analytics' })
  assert.equal(gaLoaded(ctx), true)
})

test('reopen reabre o banner (previne default do link)', () => {
  const ctx = makeEnv({ cookie: 'klarim_consent=essential' })
  assert.equal(bannerOpen(ctx), false)
  const evt = click(ctx, 'reopen')
  assert.equal(bannerOpen(ctx), true)
  assert.equal(evt._prevented, true)
})

test('cookie tem Path=/, SameSite=Lax, Max-Age=1 ano e Secure em https', () => {
  const ctx = makeEnv({ protocol: 'https:' })
  click(ctx, 'accept')
  const raw = ctx.rawSets[ctx.rawSets.length - 1]
  assert.match(raw, /Path=\//)
  assert.match(raw, /SameSite=Lax/)
  assert.match(raw, /Max-Age=31536000/)
  assert.match(raw, /Secure/)
})

test('sem Secure em http (dev/local)', () => {
  const ctx = makeEnv({ protocol: 'http:' })
  click(ctx, 'reject')
  const raw = ctx.rawSets[ctx.rawSets.length - 1]
  assert.doesNotMatch(raw, /Secure/)
})

test('GA4 é idempotente (aceitar duas vezes não injeta 2 scripts)', () => {
  const ctx = makeEnv()
  click(ctx, 'accept')
  click(ctx, 'accept')
  const n = ctx.created.filter((s) => (s.src || '').includes('googletagmanager')).length
  assert.equal(n, 1)
})
