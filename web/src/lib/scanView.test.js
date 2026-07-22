// KL-89 — testes da lógica pura da visão do resultado do scan (runner nativo `node --test`,
// sem deps, no padrão do KL-83). Cobre a tabela de visibilidade por nível de acesso, a
// linguagem contextual por origem (alerta vs orgânico) e o mascaramento de e-mail.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  accessLevelOf, isAlertVisitor, hasAccount, isFullAccess, maskEmail, maskedEmailOf,
  scoreHeadline, shareLabel, inlineSignupCopy, monitorConsentCopy, MONITOR_BENEFITS,
  viewFlags, reportUrls, SCAN_CATEGORIES, getCategoryStatus,
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

// --- CTA passwordless (KL-99 Fluxos C/D) ----------------------------------------------------- #
test('MONITOR_BENEFITS: 3 benefícios humanos (sair do ar / certificados / evolução)', () => {
  assert.equal(MONITOR_BENEFITS.length, 3)
  assert.match(MONITOR_BENEFITS.join(' '), /sair do ar/i)
  assert.match(MONITOR_BENEFITS.join(' '), /certificados/i)
  assert.match(MONITOR_BENEFITS.join(' '), /evolução/i)
})

test('inlineSignupCopy: headline usa a contagem de riscos (singular/plural)', () => {
  assert.match(inlineSignupCopy(3).headline, /3 riscos encontrados/i)
  assert.match(inlineSignupCopy(1).headline, /1 risco encontrado/i)
  assert.match(inlineSignupCopy(0).headline, /monitore este site/i)  // sem riscos → CTA neutro
})

test('inlineSignupCopy: botão "Monitorar", 3 benefícios, nota sem spam', () => {
  const c = inlineSignupCopy(2)
  assert.equal(c.button, 'Monitorar')
  assert.equal(c.benefits.length, 3)
  assert.match(c.subtitle, /avisado/i)
  assert.match(c.note, /spam/i)
})

test('monitorConsentCopy: "Sim, monitorar", inclui o domínio no título (sem campo de e-mail)', () => {
  const c = monitorConsentCopy('hotel.com.br')
  assert.equal(c.button, 'Sim, monitorar')
  assert.equal(c.title, 'Quer monitorar hotel.com.br?')
  assert.equal(c.benefits.length, 3)
})

test('monitorConsentCopy: sem domínio → "este site"', () => {
  assert.equal(monitorConsentCopy('').title, 'Quer monitorar este site?')
})

// --- tabela de visibilidade (KL-89 correção urgente) ----------------------------------------- #
// Regra: só LGPD tem cadeado (e só p/ quem não é conta confirmada). Todo o resto — score, share,
// benchmark, TODOS os riscos, barras+checks — é aberto. Evidência técnica só no acesso completo.
test('viewFlags: anonymous — CTA, benchmark, TODOS os riscos, checks sem evidência; LGPD travado', () => {
  const f = viewFlags({ access_level: 'anonymous' })
  assert.equal(f.showCTA, true)
  assert.equal(f.showShare, true)
  assert.equal(f.showPdf, true)
  assert.equal(f.showBenchmark, true)
  assert.equal(f.showAllRisks, true)    // correção: TODOS os riscos (sem gate)
  assert.equal(f.showEvidence, false)   // evidência só no acesso completo
  assert.equal(f.showPrivacy, false)    // LGPD travado
})

test('viewFlags: unconfirmed — igual ao anônimo p/ conteúdo, sem CTA de conta', () => {
  const f = viewFlags({ access_level: 'unconfirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.showBenchmark, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.showEvidence, false)
  assert.equal(f.showPrivacy, false)    // LGPD travado até confirmar o e-mail
})

test('viewFlags: confirmed vê tudo (evidência + LGPD) e sem CTA de conta', () => {
  const f = viewFlags({ access_level: 'confirmed' })
  assert.equal(f.showCTA, false)
  assert.equal(f.full, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.showEvidence, true)
  assert.equal(f.showPrivacy, true)
})

test('viewFlags: alert_session vê evidência + CTA, mas LGPD travado (não é conta)', () => {
  const f = viewFlags({ access_level: 'alert_session' })
  assert.equal(f.alertVisitor, true)
  assert.equal(f.full, true)
  assert.equal(f.showCTA, true)
  assert.equal(f.showAllRisks, true)
  assert.equal(f.showEvidence, true)
  assert.equal(f.showPrivacy, false)    // correção Problema 2: link do email = 🔒 LGPD
})

test('viewFlags: benchmark + TODOS os riscos PÚBLICOS em todo nível; LGPD só p/ conta confirmada', () => {
  for (const lvl of ['anonymous', 'unconfirmed', 'confirmed', 'alert_session']) {
    const f = viewFlags({ access_level: lvl })
    assert.equal(f.showBenchmark, true, `benchmark visível em ${lvl}`)
    assert.equal(f.showAllRisks, true, `todos os riscos em ${lvl}`)
  }
  // LGPD aberto SÓ no confirmed
  assert.equal(viewFlags({ access_level: 'anonymous' }).showPrivacy, false)
  assert.equal(viewFlags({ access_level: 'unconfirmed' }).showPrivacy, false)
  assert.equal(viewFlags({ access_level: 'alert_session' }).showPrivacy, false)
  assert.equal(viewFlags({ access_level: 'confirmed' }).showPrivacy, true)
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
