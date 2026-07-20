// KL-89 — testes da lógica pura da visão do resultado do scan (runner nativo `node --test`,
// sem deps, no padrão do KL-83). Cobre a tabela de visibilidade por nível de acesso, a
// linguagem contextual por origem (alerta vs orgânico) e o mascaramento de e-mail.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  accessLevelOf, isAlertVisitor, hasAccount, isFullAccess, maskEmail, maskedEmailOf,
  scoreHeadline, shareLabel, ctaCopy, viewFlags, reportUrls,
} from './scanView.js'

// --- nível de acesso ------------------------------------------------------------------------- #
test('accessLevelOf: default anonymous quando ausente/ inválido', () => {
  assert.equal(accessLevelOf(undefined), 'anonymous')
  assert.equal(accessLevelOf({}), 'anonymous')
  assert.equal(accessLevelOf({ access_level: 'bogus' }), 'anonymous')
})

test('accessLevelOf: reconhece os 4 níveis do KL-82', () => {
  for (const lvl of ['anonymous', 'unconfirmed', 'confirmed', 'alert_session']) {
    assert.equal(accessLevelOf({ access_level: lvl }), lvl)
  }
})

test('isAlertVisitor: só true para alert_session', () => {
  assert.equal(isAlertVisitor({ access_level: 'alert_session' }), true)
  assert.equal(isAlertVisitor({ access_level: 'anonymous' }), false)
  assert.equal(isAlertVisitor({ access_level: 'confirmed' }), false)
})

test('hasAccount: confirmed/unconfirmed têm conta; anonymous/alert_session não', () => {
  assert.equal(hasAccount('confirmed'), true)
  assert.equal(hasAccount('unconfirmed'), true)
  assert.equal(hasAccount('anonymous'), false)
  assert.equal(hasAccount('alert_session'), false)
})

test('isFullAccess: confirmed e alert_session veem tudo; anonymous/unconfirmed não', () => {
  assert.equal(isFullAccess('confirmed'), true)
  assert.equal(isFullAccess('alert_session'), true)
  assert.equal(isFullAccess('anonymous'), false)
  assert.equal(isFullAccess('unconfirmed'), false)
})

// --- máscara de e-mail ----------------------------------------------------------------------- #
test('maskEmail: 1ª + *** + última antes do @', () => {
  assert.equal(maskEmail('joao@empresa.com.br'), 'j***o@empresa.com.br')
})

test('maskEmail: usuário curto (≤2) não expõe a última letra', () => {
  assert.equal(maskEmail('ab@x.com'), 'a***@x.com')
  assert.equal(maskEmail('a@x.com'), 'a***@x.com')
})

test('maskEmail: entradas inválidas → string vazia (nunca vaza e-mail cru)', () => {
  assert.equal(maskEmail(''), '')
  assert.equal(maskEmail('semarroba'), '')
  assert.equal(maskEmail('@x.com'), '')
  assert.equal(maskEmail('a@'), '')
  assert.equal(maskEmail(null), '')
})

test('maskedEmailOf: prefere o hint do backend; senão mascara local', () => {
  assert.equal(maskedEmailOf({ alert_email_hint: 'j***o@x.com' }), 'j***o@x.com')
  assert.equal(maskedEmailOf({ alert_email: 'joao@x.com' }), 'j***o@x.com')
  assert.equal(maskedEmailOf({}), '')
})

// --- linguagem contextual por origem (KL-89 item 4) ------------------------------------------ #
test('scoreHeadline: alerta diz "Seu site" e some com "E o seu?"', () => {
  const h = scoreHeadline(83, true)
  assert.match(h.lead, /Seu site tem score 83/)
  assert.equal(h.question, null)
  assert.equal(h.tail, 'Veja o que melhorar.')
})

test('scoreHeadline: orgânico diz "Este site" e mantém "E o seu?"', () => {
  const h = scoreHeadline(83, false)
  assert.match(h.lead, /Este site tem score 83/)
  assert.equal(h.question, 'E o seu?')
})

test('shareLabel: possessivo adapta pela origem', () => {
  assert.equal(shareLabel(true), 'Compartilhe seu resultado')
  assert.equal(shareLabel(false), 'Compartilhe este resultado')
})

test('ctaCopy: alerta → só senha, "Monitore seu site", 3 benefícios humanos', () => {
  const c = ctaCopy(true, 'hotel.com.br')
  assert.equal(c.passwordOnly, true)
  assert.equal(c.title, 'Monitore seu site gratuitamente')
  assert.equal(c.button, 'Criar conta →')
  assert.equal(c.benefits.length, 3)
  assert.match(c.benefits.join(' '), /sair do ar/i)
  assert.match(c.benefits.join(' '), /certificados/i)
})

test('ctaCopy: orgânico → e-mail+senha, inclui o domínio no título', () => {
  const c = ctaCopy(false, 'hotel.com.br')
  assert.equal(c.passwordOnly, false)
  assert.equal(c.title, 'Monitore hotel.com.br gratuitamente')
  assert.equal(c.button, 'Criar conta gratuita →')
})

test('ctaCopy: orgânico sem domínio → "este site"', () => {
  assert.equal(ctaCopy(false, '').title, 'Monitore este site gratuitamente')
})

// --- tabela de visibilidade (KL-89 item 2) --------------------------------------------------- #
test('viewFlags: anonymous mostra CTA, barras, 1 risco; trava benchmark/LGPD/evidência', () => {
  const f = viewFlags({ access_level: 'anonymous' })
  assert.equal(f.showCTA, true)
  assert.equal(f.showShare, true)
  assert.equal(f.showPdf, true)
  assert.equal(f.showBenchmark, false)
  assert.equal(f.benchmarkLocked, true)
  assert.equal(f.showAllRisks, false)
  assert.equal(f.categoriesMode, 'bars')
  assert.equal(f.showEvidence, false)
  assert.equal(f.showLGPD, false)
})

test('viewFlags: unconfirmed some com o CTA de criar conta e libera benchmark, mas não LGPD', () => {
  const f = viewFlags({ access_level: 'unconfirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.showBenchmark, true)
  assert.equal(f.benchmarkLocked, false)
  assert.equal(f.categoriesMode, 'summary')
  assert.equal(f.showAllRisks, false)
  assert.equal(f.showLGPD, false)
})

test('viewFlags: confirmed vê tudo e sem CTA de conta', () => {
  const f = viewFlags({ access_level: 'confirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.full, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.categoriesMode, 'full')
  assert.equal(f.showEvidence, true)
  assert.equal(f.showLGPD, true)
})

test('viewFlags: alert_session vê tudo (full) E ainda mostra o CTA (não tem conta)', () => {
  const f = viewFlags({ access_level: 'alert_session' })
  assert.equal(f.alertVisitor, true)
  assert.equal(f.full, true)
  assert.equal(f.showCTA, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.categoriesMode, 'full')
  assert.equal(f.showLGPD, true)
})

test('viewFlags: passwordOnly só no alerta (orgânico pede e-mail+senha)', () => {
  assert.equal(viewFlags({ access_level: 'alert_session' }).passwordOnly, true)
  assert.equal(viewFlags({ access_level: 'anonymous' }).passwordOnly, false)
  assert.equal(viewFlags({ access_level: 'confirmed' }).passwordOnly, false)
})

test('viewFlags: as flags derivam SÓ do nível (mesmo resultado p/ desktop e mobile)', () => {
  // A função não recebe dispositivo/viewport — logo desktop e mobile renderizam idêntico.
  assert.equal(viewFlags.length, 1) // um único parâmetro: o result
  assert.deepEqual(viewFlags({ access_level: 'confirmed' }), viewFlags({ access_level: 'confirmed' }))
})

// --- URLs de PDF ----------------------------------------------------------------------------- #
test('reportUrls: usa report_urls do backend quando presente', () => {
  const r = { report_urls: { executive: '/report/executive?url=x', technical: '/report/technical?url=x' } }
  assert.deepEqual(reportUrls(r, 'https://x.com'), r.report_urls)
})

test('reportUrls: constrói do endpoint público quando o backend não mandou (PDF sem conta)', () => {
  const u = reportUrls({ access_level: 'anonymous' }, 'https://hotel.com.br')
  assert.match(u.executive, /^\/report\/executive\?url=/)
  assert.match(u.technical, /^\/report\/technical\?url=/)
  assert.match(u.executive, /hotel\.com\.br/)
})
