// KL-134 P2 — testes da lógica pura das micro-ferramentas (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  TOOLS, toolBySlug, buildToolUrl, parseToolError, formatScore, gradeColor, lgpdGradeColor,
  statusMeta, groupTechByCategory, fullScanHref, FAQS, faqFor,
  SCORE_GREEN, SCORE_YELLOW, SCORE_RED,
} from './tools.js'

// --- metadados --- //
test('TOOLS: 5 ferramentas com slug/endpoint/paramName', () => {
  assert.equal(TOOLS.length, 5)
  assert.deepEqual(TOOLS.map((t) => t.slug), ['ssl', 'headers', 'lgpd', 'tech', 'email'])
  assert.equal(toolBySlug('email').paramName, 'domain')   // e-mail opera sobre domínio
  assert.equal(toolBySlug('ssl').paramName, 'url')
  assert.equal(toolBySlug('inexistente'), null)
})

// --- buildToolUrl --- //
test('buildToolUrl monta a URL com o parâmetro certo e encoda o valor', () => {
  assert.equal(buildToolUrl('/api/tools/ssl', 'url', 'example.com'),
    '/api/tools/ssl?url=example.com')
  assert.equal(buildToolUrl('/api/tools/email', 'domain', 'a b.com'),
    '/api/tools/email?domain=a%20b.com')
})

// --- parseToolError --- //
test('parseToolError: mensagens amigáveis por status', () => {
  assert.match(parseToolError(400), /URL inválida/)
  assert.match(parseToolError(429), /Muitas consultas/)
  assert.match(parseToolError(504), /não respondeu em 15 segundos/)
  assert.match(parseToolError(502), /Não foi possível acessar/)
  assert.match(parseToolError(500, { detail: 'boom' }), /boom/)   // fallback pro detail
  assert.match(parseToolError(0), /Não foi possível concluir/)
})

// --- formatScore --- //
test('formatScore: 7/7 verde, 6/7 amarelo, 2/7 vermelho', () => {
  assert.equal(formatScore('7/7').color, SCORE_GREEN)
  assert.equal(formatScore('7/7').name, 'verde')
  assert.equal(formatScore('6/7').color, SCORE_YELLOW)
  assert.equal(formatScore('6/7').name, 'amarelo')
  assert.equal(formatScore('2/7').color, SCORE_RED)
  assert.equal(formatScore('3/8').color, SCORE_RED)   // 0.375 < 0.5
  assert.equal(formatScore('lixo').ratio, 0)
})

// --- grade colors --- //
test('gradeColor / lgpdGradeColor', () => {
  assert.equal(gradeColor('A'), SCORE_GREEN)
  assert.equal(gradeColor('F'), SCORE_RED)
  assert.equal(lgpdGradeColor('Adequado'), SCORE_GREEN)
  assert.equal(lgpdGradeColor('Parcialmente adequado'), SCORE_YELLOW)
  assert.equal(lgpdGradeColor('Inadequado'), SCORE_RED)
})

// --- statusMeta --- //
test('statusMeta: pass/fail/warn', () => {
  assert.equal(statusMeta('pass').color, SCORE_GREEN)
  assert.equal(statusMeta('fail').color, SCORE_RED)
  assert.equal(statusMeta('warn').color, SCORE_YELLOW)
})

// --- groupTechByCategory --- //
test('groupTechByCategory: agrupa preservando ordem de aparição', () => {
  const g = groupTechByCategory([
    { name: 'WordPress', category: 'CMS' },
    { name: 'Cloudflare', category: 'CDN' },
    { name: 'Astro', category: 'CMS' },
  ])
  assert.deepEqual(g.map((x) => x.category), ['CMS', 'CDN'])
  assert.equal(g[0].items.length, 2)
})

// --- fullScanHref --- //
test('fullScanHref aponta ao scanner completo com o domínio', () => {
  assert.equal(fullScanHref('example.com'), '/scan?url=example.com')
})

// --- FAQ: cada ferramenta tem 3–5 perguntas (spec test #8) --- //
test('FAQS: cada ferramenta tem 3 a 5 perguntas bem formadas', () => {
  for (const t of TOOLS) {
    const faq = faqFor(t.slug)
    assert.ok(faq.length >= 3 && faq.length <= 5, `${t.slug}: ${faq.length} perguntas`)
    for (const item of faq) {
      assert.ok(item.q && item.q.length > 5, `${t.slug}: pergunta vazia`)
      assert.ok(item.a && item.a.length > 15, `${t.slug}: resposta muito curta`)
    }
  }
  assert.equal(Object.keys(FAQS).length, 5)
})
