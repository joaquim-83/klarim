// KL-132 — testes da lógica pura de SEO dos perfis (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  formatDomainName, displayName, profileTitle, semaphoreText, profileDescription,
} from './seo.js'

test('formatDomainName: 1ª parte capitalizada, sem www/TLD', () => {
  assert.equal(formatDomainName('lotusforme.com.br'), 'Lotusforme')
  assert.equal(formatDomainName('www.acme.com.br'), 'Acme')
  assert.equal(formatDomainName('X.io'), 'X')
  assert.equal(formatDomainName(''), '')
})

test('displayName: usa company_name quando presente, senão o domínio', () => {
  assert.equal(displayName('Padaria Silva', 'padariasilva.com.br'), 'Padaria Silva')
  assert.equal(displayName('', 'padariasilva.com.br'), 'Padariasilva')
  assert.equal(displayName(null, 'padariasilva.com.br'), 'Padariasilva')
})

test('profileTitle: captura "é seguro?" + score e cabe em 60 chars', () => {
  const t = profileTitle({ companyName: 'Padaria Silva', domain: 'x.com.br', score: 84 })
  assert.ok(t.includes('é seguro?'))
  assert.ok(t.includes('Score 84/100'))
  assert.ok(t.includes('Padaria Silva'))
  assert.ok(t.length <= 60, `título tem ${t.length} chars`)
})

test('profileTitle: fallback p/ domínio quando company_name é null', () => {
  const t = profileTitle({ companyName: null, domain: 'lotusforme.com.br', score: 42 })
  assert.ok(t.startsWith('Lotusforme é seguro?'))
  assert.ok(t.length <= 60)
})

test('profileTitle: trunca company_name longo mantendo ≤60', () => {
  const t = profileTitle({
    companyName: 'Companhia Brasileira de Distribuição e Logística Integrada Ltda',
    domain: 'x.com.br', score: 100,
  })
  assert.ok(t.length <= 60, `título tem ${t.length} chars`)
  assert.ok(t.includes('é seguro? Score 100/100'))
  assert.ok(t.includes('…'))
})

test('semaphoreText: mapeia os 3 semáforos', () => {
  assert.equal(semaphoreText('verde'), 'Excelente')
  assert.equal(semaphoreText('amarelo'), 'Atenção')
  assert.equal(semaphoreText('vermelho'), 'Crítico')
  assert.equal(semaphoreText('x'), 'Atenção')
})

test('profileDescription: inclui score, semáforo e "48 pontos", ≤155', () => {
  const d = profileDescription({ domain: 'x.com.br', score: 84, semaphore: 'amarelo' })
  assert.ok(d.includes('84/100') && d.includes('Atenção') && d.includes('48 pontos'))
  assert.ok(d.length <= 155, `desc tem ${d.length} chars`)
})

test('profileDescription: domínio longo → truncado em 155', () => {
  const d = profileDescription({
    domain: 'um-dominio-absurdamente-longo-para-testar-o-limite.com.br',
    score: 55, semaphore: 'vermelho',
  })
  assert.ok(d.length <= 155)
})
