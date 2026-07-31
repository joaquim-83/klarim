// KL-133 — testes da lógica do blog: markdown → HTML sanitizado (XSS) + helpers puros.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdown, categoryLabel, formatBlogDate, sidebarLinks } from './blog.js'

test('renderMarkdown: converte headings, listas, bold', () => {
  const h = renderMarkdown('## Título\n\nTexto **forte**.\n\n- a\n- b')
  assert.ok(h.includes('<h2>') && h.includes('Título'))
  assert.ok(h.includes('<strong>forte</strong>'))
  assert.ok(h.includes('<ul>') && h.includes('<li>a</li>'))
})

test('renderMarkdown: tabelas (GFM) e blocos de código', () => {
  const table = renderMarkdown('| A | B |\n|---|---|\n| 1 | 2 |')
  assert.ok(table.includes('<table>') && table.includes('<td>1</td>'))
  const code = renderMarkdown('```python\nprint("x")\n```')
  assert.ok(code.includes('<pre>') && code.includes('<code'))
})

test('renderMarkdown: REMOVE <script> (XSS)', () => {
  const h = renderMarkdown('texto\n\n<script>alert(1)</script>\n\nmais')
  assert.ok(!h.includes('<script'), 'script deve ser removido')
  assert.ok(!h.toLowerCase().includes('alert(1)') || !h.includes('<script'))
})

test('renderMarkdown: REMOVE <iframe> e handlers on*', () => {
  const h = renderMarkdown('<iframe src="https://evil.com"></iframe>\n\n<img src="x" onerror="alert(1)">')
  assert.ok(!h.includes('<iframe'), 'iframe removido')
  assert.ok(!h.toLowerCase().includes('onerror'), 'handler removido')
})

test('renderMarkdown: bloqueia href javascript:', () => {
  const h = renderMarkdown('[clique](javascript:alert(1))')
  assert.ok(!h.toLowerCase().includes('javascript:'), 'esquema javascript: removido')
})

test('renderMarkdown: links externos ganham rel de segurança', () => {
  const h = renderMarkdown('[site](https://exemplo.com)')
  assert.ok(h.includes('href="https://exemplo.com"'))
  assert.ok(h.includes('rel="noopener noreferrer nofollow"'))
})

test('renderMarkdown: vazio → string vazia', () => {
  assert.equal(renderMarkdown(''), '')
  assert.equal(renderMarkdown(null), '')
})

test('categoryLabel: mapeia categorias conhecidas', () => {
  assert.equal(categoryLabel('lgpd'), 'LGPD')
  assert.equal(categoryLabel('setor'), 'Setores')
  assert.equal(categoryLabel('desconhecida'), 'Segurança')
})

test('formatBlogDate: data pt-BR ou vazio', () => {
  assert.ok(formatBlogDate('2026-07-31T00:00:00Z').includes('2026'))
  assert.equal(formatBlogDate(''), '')
})

test('sidebarLinks: até 5 links, inclui o CTA de scan', () => {
  const l = sidebarLinks('setor')
  assert.ok(l.length <= 5 && l.length >= 2)
  assert.ok(l.some((x) => x.href === '/scan'))
})
