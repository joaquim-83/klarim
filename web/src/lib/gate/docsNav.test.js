// KL-152 P2 — testes da navegação das docs (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { DOCS_NAV, DOCS_BASE, DOCS_DEFAULT, docHref, isActiveDoc, allDocSlugs } from './docsNav.js'

test('allDocSlugs: exatamente as 7 páginas', () => {
  const slugs = allDocSlugs()
  assert.equal(slugs.length, 7)
  assert.deepEqual(slugs, [
    'github-actions', 'gitlab-ci', 'bitbucket', 'jenkins', 'manual', 'api', 'troubleshooting',
  ])
})

test('docHref: monta o caminho sob /docs/gate', () => {
  assert.equal(docHref('github-actions'), '/docs/gate/github-actions')
  assert.equal(DOCS_BASE, '/docs/gate')
  assert.equal(docHref(DOCS_DEFAULT), '/docs/gate/github-actions')
})

test('isActiveDoc: só marca o slug atual', () => {
  assert.equal(isActiveDoc('api', 'api'), true)
  assert.equal(isActiveDoc('api', 'jenkins'), false)
})

test('DOCS_NAV: 3 grupos, todos os itens com slug+label', () => {
  assert.equal(DOCS_NAV.length, 3)
  for (const g of DOCS_NAV) {
    assert.ok(g.label && Array.isArray(g.items) && g.items.length > 0)
    for (const it of g.items) assert.ok(it.slug && it.label)
  }
})
