// KL-89 — testes de LAYOUT (runner nativo `node --test`). Verifica que TODAS as páginas
// públicas puxam a largura de container do módulo compartilhado `layout.js` (fim das larguras
// ad-hoc por página) e que o container de conteúdo expande em telas grandes. Ler o fonte das
// páginas é suficiente: garante a fiação sem precisar renderizar Astro.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  CONTAINER, PAGE_CONTAINER, FORM_CONTAINER, PROSE_CONTAINER, CONTAINER_FORM, CONTAINER_PROSE,
} from './layout.js'

function src(rel) {
  return readFileSync(new URL(`../${rel}`, import.meta.url), 'utf8')
}

// Páginas de CONTEÚDO → container expandido (PAGE_CONTAINER).
const CONTENT_PAGES = [
  'pages/scan.astro',
  'pages/site/[domain].astro',
  'pages/setores.astro',
  'pages/setor/[slug].astro',
  'pages/melhores.astro',
  'pages/estatisticas.astro',
  'pages/planos.astro',
]
// Formulários → container estreito compartilhado (FORM_CONTAINER).
const FORM_PAGES = [
  'pages/cadastrar.astro',
  'pages/entrar.astro',
  'pages/recuperar-senha.astro',
  'pages/contato.astro',
]

// --- container expandido de conteúdo --------------------------------------------------------- #
test('CONTAINER expande progressivamente (2xl → 5xl md → 7xl lg) e centraliza', () => {
  assert.match(CONTAINER, /max-w-2xl/)
  assert.match(CONTAINER, /md:max-w-5xl/)
  assert.match(CONTAINER, /lg:max-w-7xl/)
  assert.match(CONTAINER, /mx-auto/)
})

test('PAGE_CONTAINER = container + padding de página', () => {
  assert.ok(PAGE_CONTAINER.startsWith(CONTAINER))
  assert.match(PAGE_CONTAINER, /pt-28/)
})

for (const page of CONTENT_PAGES) {
  test(`${page}: importa layout.js e usa {PAGE_CONTAINER} no <main>`, () => {
    const s = src(page)
    assert.match(s, /from '\.\.?\/(\.\.\/)?lib\/layout\.js'/, `${page} deve importar de lib/layout.js`)
    assert.match(s, /<main class=\{PAGE_CONTAINER\}>/, `${page} deve usar {PAGE_CONTAINER}`)
  })

  test(`${page}: sem largura ad-hoc no <main> (nada de max-w-6xl fixo)`, () => {
    const s = src(page)
    assert.doesNotMatch(s, /<main class="[^"]*max-w-/, `${page} não deve ter max-w fixo no <main>`)
  })
}

// --- formulários: estreitos, mas via constante compartilhada --------------------------------- #
test('FORM_CONTAINER é estreito de propósito (max-w-md) e compartilhado', () => {
  assert.ok(FORM_CONTAINER.startsWith(CONTAINER_FORM))
  assert.match(FORM_CONTAINER, /max-w-md/)
})

for (const page of FORM_PAGES) {
  test(`${page}: usa {FORM_CONTAINER} (formulário estreito centralizado)`, () => {
    const s = src(page)
    assert.match(s, /<main class=\{FORM_CONTAINER\}>/, `${page} deve usar {FORM_CONTAINER}`)
    assert.doesNotMatch(s, /<main class="[^"]*max-w-/, `${page} não deve ter max-w fixo no <main>`)
  })
}

// --- texto corrido (termos/privacidade/sobre) via Page.astro --------------------------------- #
test('Page.astro (wrapper de termos/privacidade/sobre) usa {PROSE_CONTAINER}', () => {
  const s = src('layouts/Page.astro')
  assert.match(s, /<main class=\{PROSE_CONTAINER\}>/)
  assert.ok(PROSE_CONTAINER.startsWith(CONTAINER_PROSE))
  assert.match(PROSE_CONTAINER, /max-w-3xl/)
})

test('as prose pages herdam o container do Page.astro (não redefinem <main>)', () => {
  for (const p of ['pages/termos.astro', 'pages/privacidade.astro', 'pages/sobre.astro']) {
    const s = src(p)
    assert.match(s, /import Page from/, `${p} deve usar o layout Page`)
    assert.doesNotMatch(s, /<main/, `${p} não deve definir seu próprio <main>`)
  }
})
