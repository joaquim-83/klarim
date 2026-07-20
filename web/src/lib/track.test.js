// KL-64 — testes do tracker (public/track.js). Carrega o IIFE num contexto `vm` com um DOM
// mínimo mockado e verifica a detecção de humano: page_view NÃO dispara no load, só após
// interação (scroll/click) ou 5s visível, e sempre com verified_human=true.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const SRC = readFileSync(new URL('../../public/track.js', import.meta.url), 'utf8')

function makeEnv(pathname = '/site/hotel.com.br') {
  const listeners = {}
  const fetches = []
  let timeoutCb = null
  const store = {}
  const ctx = {
    fetches, listeners,
    getTimeout: () => timeoutCb,
    window: { location: { pathname, search: '' } },
    document: {
      referrer: '',
      visibilityState: 'visible',
      addEventListener: (e, cb) => { (listeners[e] = listeners[e] || []).push(cb) },
      removeEventListener: (e, cb) => { listeners[e] = (listeners[e] || []).filter((x) => x !== cb) },
    },
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v) },
    },
    crypto: { randomUUID: () => 'uuid-test' },
    fetch: (url, opts) => { fetches.push({ url, body: JSON.parse(opts.body) }); return { catch: () => {} } },
    setTimeout: (cb) => { timeoutCb = cb; return 1 },
    URLSearchParams,
    console,
  }
  vm.createContext(ctx)
  vm.runInContext(SRC, ctx)
  return ctx
}

function fire(ctx, ev) {
  (ctx.listeners[ev] || []).slice().forEach((cb) => cb())
}

test('track.js: NÃO dispara page_view no load (espera sinal humano)', () => {
  const ctx = makeEnv()
  assert.equal(ctx.fetches.length, 0)
})

test('track.js: dispara page_view após scroll, com verified_human=true', () => {
  const ctx = makeEnv()
  fire(ctx, 'scroll')
  const pv = ctx.fetches.find((f) => f.body.event_type === 'page_view')
  assert.ok(pv, 'page_view deve disparar após interação')
  assert.equal(pv.body.verified_human, true)
  assert.equal(pv.body.metadata.detection, 'interaction')
})

test('track.js: click também conta como humano e libera o profile_view do perfil', () => {
  const ctx = makeEnv()
  fire(ctx, 'click')
  const p = ctx.fetches.find((f) => f.body.event_type === 'profile_view')
  assert.ok(p, 'profile_view deve disparar em /site/ após humano')
  assert.equal(p.body.verified_human, true)
  assert.equal(p.body.metadata.domain, 'hotel.com.br')
})

test('track.js: fallback de 5s (aba visível) dispara o page_view', () => {
  const ctx = makeEnv()
  assert.equal(ctx.fetches.length, 0)
  ctx.getTimeout()()   // executa o callback do setTimeout (5s)
  const pv = ctx.fetches.find((f) => f.body.event_type === 'page_view')
  assert.ok(pv)
  assert.equal(pv.body.verified_human, true)
  assert.equal(pv.body.metadata.detection, 'timeout')
})

test('track.js: evento de AÇÃO dispara na hora (não espera humano)', () => {
  const ctx = makeEnv('/scan')
  assert.equal(ctx.fetches.length, 0)          // page_view (passivo) não disparou
  ctx.window.klarimTrack('scan_started', { url: 'x' }, 'https://x.com')
  const s = ctx.fetches.find((f) => f.body.event_type === 'scan_started')
  assert.ok(s, 'ação deve disparar imediatamente')
  assert.equal(s.body.verified_human, false)   // ainda sem interação → false (backend filtra)
})

test('track.js: sem duplicar page_view em interações repetidas', () => {
  const ctx = makeEnv()
  fire(ctx, 'scroll')
  fire(ctx, 'click')
  const pvs = ctx.fetches.filter((f) => f.body.event_type === 'page_view')
  assert.equal(pvs.length, 1)
})

test('track.js: /painel não dispara nada (fora do tracking público)', () => {
  const ctx = makeEnv('/painel/analytics')
  fire(ctx, 'scroll')
  assert.equal(ctx.fetches.length, 0)
})
