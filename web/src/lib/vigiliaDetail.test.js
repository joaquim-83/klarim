// KL-123 — testes da lógica pura do card de vigília expansível (node --test, sem deps).
// Cobre status/badge/label, estado acessível de e-mail e o descarte otimista de typosquat
// (o "Não é ameaça" atualiza a lista + badge SEM recarregar).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  statusMeta, showBadge, vigiliaLabel, emailStateLabel, applyDismiss, pendingAlerts,
} from './vigiliaDetail.js'

test('statusMeta: mapeia os status conhecidos + fallback', () => {
  assert.equal(statusMeta('ok').label, 'OK')
  assert.equal(statusMeta('critical').label, 'Crítico')
  assert.equal(statusMeta('unknown').icon, '⚪')
  assert.equal(statusMeta('bogus').label, 'OK')   // fallback
})

test('showBadge: só quando pending_count > 0', () => {
  assert.equal(showBadge(2), true)
  assert.equal(showBadge(0), false)
  assert.equal(showBadge(undefined), false)
  assert.equal(showBadge(null), false)
})

test('vigiliaLabel: rótulo acessível por tipo + fallback', () => {
  assert.equal(vigiliaLabel('phishing'), 'Typosquat / phishing')
  assert.equal(vigiliaLabel('email'), 'Proteção de e-mail')
  assert.equal(vigiliaLabel('desconhecido'), 'desconhecido')
})

test('emailStateLabel: pass/absent/unknown em linguagem acessível', () => {
  assert.equal(emailStateLabel('pass').text, 'Ativo')
  assert.equal(emailStateLabel('absent').text, 'Ausente')
  assert.equal(emailStateLabel('unknown').text, 'Não verificado')
  assert.equal(emailStateLabel(undefined).text, 'Não verificado')
})

test('pendingAlerts: só os não descartados', () => {
  const details = { data: { alerts: [
    { id: 1, dismissed: false }, { id: 2, dismissed: true }, { id: 3, dismissed: false },
  ] } }
  assert.deepEqual(pendingAlerts(details).map((a) => a.id), [1, 3])
  assert.deepEqual(pendingAlerts({}), [])
  assert.deepEqual(pendingAlerts(null), [])
})

test('applyDismiss: marca o alerta + recalcula pending_count/status/summary (sem reload)', () => {
  const details = {
    status: 'critical', pending_count: 2,
    data: { alerts: [
      { id: 1, suspicious_domain: 'lrim.com.br', dismissed: false },
      { id: 2, suspicious_domain: 'larim.net', dismissed: false },
    ] },
  }
  const after = applyDismiss(details, 1)
  assert.equal(after.pending_count, 1)
  assert.equal(after.status, 'critical')
  assert.equal(after.data.alerts.find((a) => a.id === 1).dismissed, true)
  assert.equal(after.data.alerts.find((a) => a.id === 2).dismissed, false)
  // imutável: o objeto original não muda
  assert.equal(details.pending_count, 2)

  const empty = applyDismiss(after, 2)
  assert.equal(empty.pending_count, 0)
  assert.equal(empty.status, 'ok')
  assert.match(empty.summary, /Nenhum domínio suspeito/)
})

test('applyDismiss: no-op gracioso quando não há lista de alertas', () => {
  const d = { status: 'ok', data: {} }
  assert.equal(applyDismiss(d, 5), d)
  assert.equal(applyDismiss(null, 5), null)
})
