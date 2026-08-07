// KL-152 P1 — testes da lógica pura dos snippets do Security Gate (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  TOTAL_CHECKS, DEFAULT_URL, buildSnippet, secretRef, secretSteps, planProgress,
  DASHBOARD_PLATFORMS, ONBOARDING_PLATFORMS,
} from './snippets.js'

test('secretRef: GitHub usa ${{ secrets }}, demais usam $KLARIM_KEY', () => {
  assert.equal(secretRef('github'), '${{ secrets.KLARIM_KEY }}')
  assert.equal(secretRef('gitlab'), '$KLARIM_KEY')
  assert.equal(secretRef('bitbucket'), '$KLARIM_KEY')
  assert.equal(secretRef('curl'), '$KLARIM_KEY')
  assert.equal(secretRef('desconhecido'), '$KLARIM_KEY') // fallback
})

test('buildSnippet: pré-preenche a URL do projeto e referencia o secret (nunca a key crua)', () => {
  const s = buildSnippet('github', 'https://acme.com.br')
  assert.match(s, /https:\/\/acme\.com\.br/)          // URL do projeto
  assert.match(s, /\$\{\{ secrets\.KLARIM_KEY \}\}/)  // secret referenciado
  assert.match(s, /security-gate:/)                    // YAML do GitHub
  assert.doesNotMatch(s, /KLM_/)                       // nunca embute key crua
})

test('buildSnippet: URL vazia cai no placeholder padrão', () => {
  assert.match(buildSnippet('github', ''), new RegExp(DEFAULT_URL.replace(/\./g, '\\.')))
  assert.match(buildSnippet('gitlab', '   '), new RegExp(DEFAULT_URL.replace(/\./g, '\\.')))
})

test('buildSnippet: cada plataforma tem o formato certo', () => {
  assert.match(buildSnippet('gitlab', 'https://x.com'), /^security_gate:\n {2}stage: test/)
  assert.match(buildSnippet('bitbucket', 'https://x.com'), /^pipelines:\n {2}default:/)
  assert.match(buildSnippet('jenkins', 'https://x.com'), /^stage\('Klarim Security Gate'\) \{/)
  assert.match(buildSnippet('curl', 'https://x.com'), /^curl -s https:\/\/klarim\.net\/api\/gate\/scan/)
  // manual = curl/bash
  assert.match(buildSnippet('manual', 'https://x.com'), /curl -s/)
})

test('buildSnippet: sempre aponta para /api/gate/scan e fail_on critical', () => {
  for (const { id } of ONBOARDING_PLATFORMS.concat(DASHBOARD_PLATFORMS)) {
    const s = buildSnippet(id, 'https://x.com')
    assert.match(s, /klarim\.net\/api\/gate\/scan/)
    assert.match(s, /critical/)
  }
})

test('secretSteps: instruções específicas por plataforma', () => {
  assert.deepEqual(secretSteps('gitlab').flags, ['Protected', 'Masked'])
  assert.deepEqual(secretSteps('bitbucket').flags, ['Secured'])
  assert.equal(secretSteps('github').name, 'KLARIM_KEY')
  assert.equal(secretSteps('manual').exportLine, 'export KLARIM_KEY=')
})

test('planProgress: contagem, pct e próximo plano', () => {
  const free = planProgress('free', 4)
  assert.deepEqual([free.count, free.total, free.pct], [4, TOTAL_CHECKS, 22])
  assert.equal(free.next.label, 'Pro')
  assert.equal(free.next.checks, 9)

  const pro = planProgress('pro', 9)
  assert.equal(pro.pct, 50)
  assert.equal(pro.next.label, 'Team')
  assert.equal(pro.next.checks, 18)

  // Team/Enterprise: sem próximo plano (100%).
  assert.equal(planProgress('team', 18).next, null)
  assert.equal(planProgress('team', 18).pct, 100)
  assert.equal(planProgress('enterprise', 18).next, null)
})

test('planProgress: satura contagem inválida sem estourar', () => {
  assert.equal(planProgress('free', 999).count, TOTAL_CHECKS)
  assert.equal(planProgress('free', -5).count, 0)
  assert.equal(planProgress('free', undefined).count, 0)
})
