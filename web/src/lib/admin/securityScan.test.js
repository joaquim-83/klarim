// KL-160 — testes da lógica pura da seção "Segurança da plataforma" (node --test).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  scanSemaphore, findingsSummary, isUnhealthy, scanButtonLabel, triggerMessage,
  severityColor, checkIcon, sortChecks, clampText,
} from './securityScan.js'

test('scanSemaphore: escala verde/amarelo/vermelho + nulo', () => {
  assert.equal(scanSemaphore(95).dot, '🟢')
  assert.equal(scanSemaphore(90).dot, '🟢')
  assert.equal(scanSemaphore(70).dot, '🟡')
  assert.equal(scanSemaphore(30).dot, '🔴')
  assert.equal(scanSemaphore(null).label, '—')
})

test('findingsSummary: N Critical | N High | N Medium', () => {
  assert.equal(findingsSummary({ critical: 0, high: 1, medium: 0 }), '0 Critical | 1 High | 0 Medium')
  assert.equal(findingsSummary({}), '0 Critical | 0 High | 0 Medium')
  assert.equal(findingsSummary(null), '0 Critical | 0 High | 0 Medium')
})

test('isUnhealthy: score<80 OU crítico', () => {
  assert.equal(isUnhealthy({ score: 90, critical: 0 }), false)
  assert.equal(isUnhealthy({ score: 79, critical: 0 }), true)
  assert.equal(isUnhealthy({ score: 100, critical: 1 }), true)   // crítico mesmo com score alto
  assert.equal(isUnhealthy({ score: 80, critical: 0 }), false)   // 80 é o limite (não destaca)
  assert.equal(isUnhealthy(null), false)
})

test('scanButtonLabel: rodando vs pronto', () => {
  assert.match(scanButtonLabel({ running: true }), /andamento/)
  assert.match(scanButtonLabel({ busy: true }), /andamento/)
  assert.equal(scanButtonLabel({}), 'Executar varredura completa →')
})

test('triggerMessage: por status', () => {
  assert.match(triggerMessage({ status: 'running' }), /já está em andamento/)
  assert.match(triggerMessage({ status: 'cooldown', retry_after: 300 }), /Aguarde 5 min/)
  assert.match(triggerMessage({ status: 'started' }), /iniciada/)
  assert.equal(triggerMessage(null), '')
})

test('severityColor + checkIcon', () => {
  assert.equal(severityColor('critical'), '#F85149')
  assert.equal(severityColor('desconhecido'), '#8B949E')
  assert.equal(checkIcon('fail'), '❌')
  assert.equal(checkIcon('pass'), '✅')
})

test('sortChecks: FAIL primeiro por severidade, PASS por último', () => {
  const out = sortChecks([
    { check: 'ok', status: 'pass', severity: 'info' },
    { check: 'med', status: 'fail', severity: 'medium' },
    { check: 'crit', status: 'fail', severity: 'critical' },
    { check: 'err', status: 'error', severity: 'high' },
  ])
  assert.deepEqual(out.map((c) => c.check), ['crit', 'med', 'err', 'ok'])
})

// KL-150 fix (P1) — clampText: corta textos longos (defesa contra body HTML no detail/mensagem)
test('clampText: corta acima do max + "…"; curto passa inteiro', () => {
  assert.equal(clampText('abc', 400), 'abc')
  const long = '<html>'.repeat(200)
  const out = clampText(long, 50)
  assert.equal(out.length, 51)              // 50 chars + "…"
  assert.ok(out.endsWith('…'))
  assert.equal(clampText(null), '')
  assert.equal(clampText(undefined), '')
})
