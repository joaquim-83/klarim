// KL-161 — testes da lógica pura do canal LGPD (node --test, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LGPD_TYPES, tipoFromParam, lgpdTypeLabel, isValidEmail, validateLgpdForm } from './lgpd.js'

test('LGPD_TYPES: 6 tipos com os slugs esperados', () => {
  assert.equal(LGPD_TYPES.length, 6)
  assert.deepEqual(LGPD_TYPES.map((t) => t.value),
    ['acesso', 'correcao', 'exclusao', 'portabilidade', 'revogacao', 'outra'])
  assert.equal(lgpdTypeLabel('exclusao'), 'Exclusão')
  assert.equal(lgpdTypeLabel('inexistente'), '')
})

// --- pré-seleção pelo ?tipo= (link "Remover meus dados" → exclusao) --- #
test('tipoFromParam: exclusao e sinônimos → exclusao', () => {
  assert.equal(tipoFromParam('exclusao'), 'exclusao')
  assert.equal(tipoFromParam('excluir'), 'exclusao')
  assert.equal(tipoFromParam('remover'), 'exclusao')
  assert.equal(tipoFromParam('EXCLUSAO'), 'exclusao')   // case-insensitive
})

test('tipoFromParam: outros tipos + desconhecido/vazio → ""', () => {
  assert.equal(tipoFromParam('acesso'), 'acesso')
  assert.equal(tipoFromParam('portabilidade'), 'portabilidade')
  assert.equal(tipoFromParam('revogar'), 'revogacao')
  assert.equal(tipoFromParam('qualquer-coisa'), '')
  assert.equal(tipoFromParam(''), '')
  assert.equal(tipoFromParam(null), '')
})

// --- validação de e-mail --- #
test('isValidEmail', () => {
  assert.equal(isValidEmail('a@b.com'), true)
  assert.equal(isValidEmail('sem-arroba'), false)
  assert.equal(isValidEmail('a@b'), false)
  assert.equal(isValidEmail(''), false)
})

// --- validação dos campos obrigatórios --- #
test('validateLgpdForm: completo → ok', () => {
  const r = validateLgpdForm({ type: 'exclusao', name: 'João', email: 'j@x.com',
    description: 'Solicito a exclusão dos meus dados pessoais.' })
  assert.equal(r.ok, true)
  assert.deepEqual(r.errors, {})
})

test('validateLgpdForm: campos faltando → erros por campo', () => {
  const r = validateLgpdForm({ type: '', name: '', email: 'x', description: 'curto' })
  assert.equal(r.ok, false)
  assert.ok(r.errors.type)
  assert.ok(r.errors.name)
  assert.ok(r.errors.email)
  assert.ok(r.errors.description)   // < 10 chars
})

test('validateLgpdForm: tipo inválido é rejeitado', () => {
  const r = validateLgpdForm({ type: 'hackear', name: 'X', email: 'x@y.com',
    description: 'descricao com dez ou mais' })
  assert.equal(r.ok, false)
  assert.ok(r.errors.type)
})
