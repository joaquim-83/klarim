// KL-89 — testes da lógica pura da visão do resultado do scan (runner nativo `node --test`,
// sem deps, no padrão do KL-83). Cobre a tabela de visibilidade por nível de acesso, a
// linguagem contextual por origem (alerta vs orgânico) e o mascaramento de e-mail.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  accessLevelOf, isAlertVisitor, hasAccount, isFullAccess, maskEmail, maskedEmailOf,
  scoreHeadline, shareLabel, ctaCopy, viewFlags, reportUrls,
  SCAN_CATEGORIES, getCategoryStatus,
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

// --- tabela de visibilidade (KL-89 item 2 + fixes 2/5) --------------------------------------- #
test('viewFlags: anonymous mostra CTA/barras/benchmark; trava LGPD e evidência', () => {
  const f = viewFlags({ access_level: 'anonymous' })
  assert.equal(f.showCTA, true)
  assert.equal(f.showShare, true)
  assert.equal(f.showPdf, true)
  assert.equal(f.showBenchmark, true)   // fix 5: benchmark é público (sem cadeado)
  assert.equal(f.showAllRisks, false)
  assert.equal(f.categoriesMode, 'bars')
  assert.equal(f.showEvidence, false)
  assert.equal(f.showPrivacy, false)    // fix 2: LGPD travado p/ anônimo (desktop E mobile)
})

test('viewFlags: unconfirmed some com o CTA de criar conta, tem benchmark, mas não LGPD', () => {
  const f = viewFlags({ access_level: 'unconfirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.showBenchmark, true)
  assert.equal(f.categoriesMode, 'summary')
  assert.equal(f.showAllRisks, false)
  assert.equal(f.showPrivacy, false)    // fix 2: LGPD travado p/ não confirmado também
})

test('viewFlags: confirmed vê tudo e sem CTA de conta', () => {
  const f = viewFlags({ access_level: 'confirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.full, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.categoriesMode, 'full')
  assert.equal(f.showEvidence, true)
  assert.equal(f.showPrivacy, true)
})

test('viewFlags: alert_session vê tudo (full) E ainda mostra o CTA (não tem conta)', () => {
  const f = viewFlags({ access_level: 'alert_session' })
  assert.equal(f.alertVisitor, true)
  assert.equal(f.full, true)
  assert.equal(f.showCTA, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.categoriesMode, 'full')
  assert.equal(f.showPrivacy, true)
})

test('viewFlags: benchmark PÚBLICO em TODOS os níveis (fix 5) e LGPD só no acesso completo (fix 2)', () => {
  for (const lvl of ['anonymous', 'unconfirmed', 'confirmed', 'alert_session']) {
    assert.equal(viewFlags({ access_level: lvl }).showBenchmark, true, `benchmark visível em ${lvl}`)
  }
  assert.equal(viewFlags({ access_level: 'anonymous' }).showPrivacy, false)
  assert.equal(viewFlags({ access_level: 'unconfirmed' }).showPrivacy, false)
  assert.equal(viewFlags({ access_level: 'confirmed' }).showPrivacy, true)
  assert.equal(viewFlags({ access_level: 'alert_session' }).showPrivacy, true)
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

// --- progresso do scanner por categoria (KL-89 fix 6) ---------------------------------------- #
test('getCategoryStatus: done/active/pending pelas faixas de %', () => {
  assert.equal(getCategoryStatus({ start: 0, end: 16 }, 20), 'done')
  assert.equal(getCategoryStatus({ start: 17, end: 33 }, 20), 'active')
  assert.equal(getCategoryStatus({ start: 34, end: 50 }, 20), 'pending')
})

test('getCategoryStatus: 0% → só a primeira categoria ativa, o resto pendente', () => {
  const st = SCAN_CATEGORIES.map((c) => getCategoryStatus(c, 0))
  assert.equal(st[0], 'active')
  assert.deepEqual(st.slice(1), Array(SCAN_CATEGORIES.length - 1).fill('pending'))
})

test('getCategoryStatus: 100% → todas concluídas', () => {
  for (const c of SCAN_CATEGORIES) assert.equal(getCategoryStatus(c, 100), 'done')
})

test('getCategoryStatus: ~52% → 3 concluídas, 1 analisando, 2 pendentes (não 6 ✅ juntas)', () => {
  const st = SCAN_CATEGORIES.map((c) => getCategoryStatus(c, 52))
  assert.equal(st.filter((s) => s === 'done').length, 3)
  assert.equal(st.filter((s) => s === 'active').length, 1)
  assert.equal(st.filter((s) => s === 'pending').length, 2)
})

test('SCAN_CATEGORIES: 6 camadas, faixas contíguas cobrindo 0–100', () => {
  assert.equal(SCAN_CATEGORIES.length, 6)
  assert.equal(SCAN_CATEGORIES[0].start, 0)
  assert.equal(SCAN_CATEGORIES.at(-1).end, 100)
})
