// KL-153 P2 — testes da lógica pura da UX do Gate (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeUrl, maskCPF, isValidCPF, categorySummary, showChecksDetail, groupChecksByCategory,
  kycBannerVisible, showGateBridge, wizardNext, shouldShowWizard, formatCountdown, rateLimitMessage,
  usageText, upgradeTarget, ctaState, ctaLabel, signupBody, planName, planDetails, canUpgrade,
  canSubmitKyc, errDetail, showGateDashboardSection, isPureDeveloper, gateOnboardingSteps,
  reportButton, REPORT_KYC_MESSAGE,
} from './ux.js'

const VALID_CPF = '529.982.247-25'

// --- URL --- //
test('normalizeUrl: aceita domínio nu, http, https, localhost', () => {
  assert.equal(normalizeUrl('example.com'), 'https://example.com')
  assert.equal(normalizeUrl('https://x.com'), 'https://x.com')
  assert.equal(normalizeUrl('http://localhost:3000'), 'http://localhost:3000')
  assert.equal(normalizeUrl('  '), '')
})

// --- CPF (máscara + validação) --- //
test('maskCPF: formata progressivamente', () => {
  assert.equal(maskCPF('529'), '529')
  assert.equal(maskCPF('529982'), '529.982')
  assert.equal(maskCPF('52998224725'), '529.982.247-25')
  assert.equal(maskCPF('529.982.247-25'), '529.982.247-25')
  assert.equal(maskCPF('5299822472599'), '529.982.247-25')   // corta em 11 dígitos
})

test('isValidCPF: valida os 2 dígitos verificadores', () => {
  assert.equal(isValidCPF(VALID_CPF), true)
  assert.equal(isValidCPF('52998224725'), true)
  assert.equal(isValidCPF('529.982.247-20'), false)   // DV errado
  assert.equal(isValidCPF('111.111.111-11'), false)   // repetido
  assert.equal(isValidCPF('529.982.247'), false)      // curto
})

// --- Resultado por KYC --- //
test('categorySummary: devolve as categorias com contagens', () => {
  const r = { categories: [{ name: 'headers', checks_total: 7, checks_passed: 3, status: 'fail' }] }
  assert.equal(categorySummary(r).length, 1)
  assert.equal(categorySummary({}).length, 0)
})

test('showChecksDetail: só no nível complete', () => {
  assert.equal(showChecksDetail('complete'), true)
  assert.equal(showChecksDetail('basic'), false)
})

test('groupChecksByCategory: agrupa preservando ordem', () => {
  const g = groupChecksByCategory([
    { category: 'ssl', check: 'a' }, { category: 'headers', check: 'b' }, { category: 'ssl', check: 'c' }])
  assert.deepEqual(g.map((x) => x.name), ['ssl', 'headers'])
  assert.equal(g[0].checks.length, 2)
})

test('kycBannerVisible: aparece enquanto não completo', () => {
  assert.equal(kycBannerVisible('basic'), true)
  assert.equal(kycBannerVisible('complete'), false)
})

// --- Bridge scan→Gate --- //
test('showGateBridge: só com resultado (não loading/erro)', () => {
  assert.equal(showGateBridge('result', { score: 80 }), true)
  assert.equal(showGateBridge('progress', null), false)
  assert.equal(showGateBridge('error', null), false)
  assert.equal(showGateBridge('result', null), false)
})

// --- Wizard --- //
test('wizardNext: fluxo 6 steps com KYC condicional', () => {
  assert.equal(wizardNext(1), 2)
  assert.equal(wizardNext(2), 3)
  assert.equal(wizardNext(3, { kycCompleted: false }), 4)   // sem KYC → step KYC
  assert.equal(wizardNext(3, { kycCompleted: true }), 5)    // com KYC → pula p/ completo
  assert.equal(wizardNext(4, { skip: true }), 6)            // "pular por agora" → CI/CD
  assert.equal(wizardNext(4, {}), 5)                        // confirmou KYC → completo
  assert.equal(wizardNext(5), 6)
})

test('shouldShowWizard: só quando não há scans', () => {
  assert.equal(shouldShowWizard({ is_developer: true }, 0), true)
  assert.equal(shouldShowWizard({ is_developer: true }, 3), false)
  assert.equal(shouldShowWizard(null, 0), false)
})

// --- Rate limit --- //
test('formatCountdown: mm:ss', () => {
  assert.equal(formatCountdown(305), '05:05')
  assert.equal(formatCountdown(0), '00:00')
  assert.equal(formatCountdown(-5), '00:00')
})

test('rateLimitMessage: mensagem contextual por limit_type', () => {
  const u = rateLimitMessage('user', 3600)
  assert.equal(u.showUpgrade, true)
  assert.match(u.message, /plano Free/)
  assert.match(rateLimitMessage('ip', 600).message, /IP/)
  assert.match(rateLimitMessage('domain', 600).message, /domínio foi escaneado/)
  assert.match(rateLimitMessage('interval', 300).message, /entre domínios diferentes/)
  assert.match(rateLimitMessage('domain_interval', 300).message, /entre domínios diferentes/)
})

// --- Uso + upgrade --- //
test('usageText: mostra uso e trata ilimitado', () => {
  assert.equal(usageText(3, 5), '3 de 5 scans usados esta hora')
  assert.match(usageText(3, -1), /ilimitado/)
})

test('upgradeTarget: próximo plano com preço', () => {
  assert.equal(upgradeTarget('free').slug, 'pro')
  assert.equal(upgradeTarget('free').price_display, 'R$ 49/mês')
  assert.equal(upgradeTarget('pro').slug, 'team')
  assert.equal(upgradeTarget('team'), null)
})

// --- KL-156: bloco do plano + upgrade --- //
test('planName: nome legível por slug', () => {
  assert.equal(planName('free'), 'Free')
  assert.equal(planName('pro'), 'Pro')
  assert.equal(planName('team'), 'Team')
  assert.equal(planName('enterprise'), 'Enterprise')
  assert.equal(planName(undefined), 'Free')   // fallback
})

test('planDetails: limites + próximo plano', () => {
  const free = planDetails('free')
  assert.equal(free.name, 'Free')
  assert.equal(free.scansHour, 5)
  assert.equal(free.cooldownLabel, '30 minutos')
  assert.equal(free.next.slug, 'pro')
  const team = planDetails('team')
  assert.equal(team.cooldownLabel, 'sem cooldown')
  assert.equal(team.next, null)
})

test('canUpgrade: false no plano máximo', () => {
  assert.equal(canUpgrade('free'), true)
  assert.equal(canUpgrade('pro'), true)
  assert.equal(canUpgrade('team'), false)
  assert.equal(canUpgrade('enterprise'), false)
})

// --- KL-158: submit do KYC (banner clicável) --- //
test('canSubmitKyc: CPF válido + endereço ≥10 + telefone', () => {
  assert.equal(canSubmitKyc('529.982.247-25', 'Rua Exemplo 123 Centro', '11999'), true)
  assert.equal(canSubmitKyc('111.111.111-11', 'Rua Exemplo 123 Centro', '11999'), false)  // CPF inválido
  assert.equal(canSubmitKyc('529.982.247-25', 'curto', '11999'), false)                    // endereço < 10
  assert.equal(canSubmitKyc('529.982.247-25', 'Rua Exemplo 123 Centro', ''), false)        // sem telefone
})

// KL-163 P2 — canSubmitKyc aceita endereço como OBJETO estruturado (isAddressComplete).
test('canSubmitKyc: endereço como objeto estruturado', () => {
  const addr = { cep: '80010-000', street: 'Rua XV', number: '123', complement: '',
    neighborhood: 'Centro', city: 'Curitiba', state: 'PR' }
  assert.equal(canSubmitKyc('529.982.247-25', addr, '11999'), true)
  assert.equal(canSubmitKyc('529.982.247-25', { ...addr, number: '' }, '11999'), false)  // incompleto
  assert.equal(canSubmitKyc('529.982.247-25', { ...addr, cep: '123' }, '11999'), false)  // CEP inválido
})

// --- KL-159: coerção do detail de erro para STRING (nunca objeto) --- //
test('errDetail: string, objeto {error} e ausente', () => {
  assert.equal(errDetail({ detail: 'Você já está no plano Pro.' }, 409), 'Você já está no plano Pro.')
  assert.equal(errDetail({ detail: { error: 'insufficient_level', required_level: 2 } }, 403), 'insufficient_level')
  assert.equal(errDetail({}, 500), 'Erro 500')
  assert.equal(errDetail({ detail: { foo: 1 } }, 422), 'Erro 422')   // objeto sem .error → fallback
})

// --- CTA da landing --- //
test('ctaState/ctaLabel: 3 estados', () => {
  assert.equal(ctaState({ ok: false }), 'logged_out')
  assert.equal(ctaState({ ok: true, gate_active: false }), 'inactive')
  assert.equal(ctaState({ ok: true, gate_active: true }), 'active')
  assert.equal(ctaLabel('logged_out').href, '/cadastrar?type=developer')
  assert.equal(ctaLabel('active').href, '/dashboard/gate')
  assert.equal(ctaLabel('inactive').action, 'activate')
})

// --- signup source=security-gate --- //
test('signupBody: inclui source quando dev', () => {
  assert.equal(signupBody({ email: 'a@b.com', source: 'security-gate' }).source, 'security-gate')
  assert.equal('source' in signupBody({ email: 'a@b.com' }), false)
})

// --- KL-150 (Fix 3): seção Security Gate no dashboard principal --- //
test('showGateDashboardSection: só p/ conta dev (is_developer)', () => {
  assert.equal(showGateDashboardSection({ is_developer: true, account_type: 'developer' }), true)
  assert.equal(showGateDashboardSection({ is_developer: true, account_type: 'both' }), true)
  assert.equal(showGateDashboardSection({ is_developer: false, account_type: 'owner' }), false)
  assert.equal(showGateDashboardSection(null), false)
  assert.equal(showGateDashboardSection({}), false)
})

test('isPureDeveloper: só account_type=developer (both é dev + owner)', () => {
  assert.equal(isPureDeveloper({ account_type: 'developer' }), true)
  assert.equal(isPureDeveloper({ account_type: 'both' }), false)
  assert.equal(isPureDeveloper({ account_type: 'owner' }), false)
  assert.equal(isPureDeveloper(null), false)
})

test('gateOnboardingSteps: marca automaticamente pelos dados do gate status', () => {
  // dev recém-cadastrado: API key + 1 projeto NÃO verificado, 0 scans, plano free.
  const novo = gateOnboardingSteps({ has_api_key: true, plan_slug: 'free' }, 0, 0)
  assert.equal(novo.length, 5)
  const done = Object.fromEntries(novo.map((s) => [s.key, s.done]))
  assert.equal(done.account, true)      // conta criada — sempre
  assert.equal(done.apikey, true)       // has_api_key
  assert.equal(done.scan, false)        // 0 runs
  assert.equal(done.cicd, false)        // 0 projetos VERIFICADOS (o cadastro cria 1 não verificado)
  assert.equal(done.upgrade, false)     // plano free

  const avancado = gateOnboardingSteps({ has_api_key: true, plan_slug: 'pro' }, 3, 2)
  const d2 = Object.fromEntries(avancado.map((s) => [s.key, s.done]))
  assert.equal(d2.scan, true)           // 3 runs
  assert.equal(d2.cicd, true)           // 2 projetos verificados
  assert.equal(d2.upgrade, true)        // plano pago
})

test('gateOnboardingSteps: robusto a status ausente', () => {
  const steps = gateOnboardingSteps(null, null, null)
  assert.equal(steps.length, 5)
  assert.equal(steps.find((s) => s.key === 'account').done, true)
  assert.equal(steps.find((s) => s.key === 'scan').done, false)
  assert.equal(steps.find((s) => s.key === 'cicd').done, false)
})

// KL-163 — botão "Exportar relatório" (behaviors #10/#11 do card).
test('reportButton: com KYC → mostra o botão', () => {
  const b = reportButton(true)
  assert.equal(b.show, true)
  assert.equal(b.label, '📄 Exportar relatório')
  assert.equal(b.message, null)
})

test('reportButton: sem KYC → mensagem de bloqueio (não botão)', () => {
  for (const v of [false, undefined, null]) {
    const b = reportButton(v)
    assert.equal(b.show, false)
    assert.equal(b.label, null)
    assert.equal(b.message, REPORT_KYC_MESSAGE)
    assert.match(b.message, /cadastro/i)
  }
})
