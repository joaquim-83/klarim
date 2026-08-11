// KL-163 P2 — testes da lógica pura de endereço estruturado (CEP + ViaCEP). node --test, sem deps.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  UF_LIST, ADDRESS_REQUIRED, maskCep, isValidCep, parseViaCepResponse, isAddressComplete, emptyAddress,
} from './address.js'

const FULL = {
  cep: '80010-000', street: 'Rua XV de Novembro', number: '123', complement: 'Sala 4',
  neighborhood: 'Centro', city: 'Curitiba', state: 'PR',
}

test('maskCep: formata 80010000 → 80010-000', () => {
  assert.equal(maskCep('80010000'), '80010-000')
  assert.equal(maskCep('80010-000'), '80010-000')
  assert.equal(maskCep('80010'), '80010')          // parcial, sem traço ainda
  assert.equal(maskCep('800100001234'), '80010-000') // trunca em 8 dígitos
  assert.equal(maskCep(''), '')
})

test('isValidCep: 8 dígitos (com ou sem traço)', () => {
  assert.equal(isValidCep('80010-000'), true)
  assert.equal(isValidCep('80010000'), true)
  assert.equal(isValidCep('00000'), false)
  assert.equal(isValidCep('80010-00'), false)
  assert.equal(isValidCep(''), false)
})

test('parseViaCepResponse: dados válidos → extrai campos', () => {
  const data = { cep: '80010-000', logradouro: 'Rua XV de Novembro', bairro: 'Centro',
    localidade: 'Curitiba', uf: 'PR' }
  const out = parseViaCepResponse(data)
  assert.deepEqual(out, { cep: '80010-000', street: 'Rua XV de Novembro',
    neighborhood: 'Centro', city: 'Curitiba', state: 'PR' })
})

test('parseViaCepResponse: erro → null', () => {
  assert.equal(parseViaCepResponse({ erro: true }), null)
  assert.equal(parseViaCepResponse(null), null)
  assert.equal(parseViaCepResponse(undefined), null)
})

test('parseViaCepResponse: UF inválida vira vazia (editável manual)', () => {
  const out = parseViaCepResponse({ cep: '80010-000', logradouro: 'X', bairro: 'Y', localidade: 'Z', uf: 'XX' })
  assert.equal(out.state, '')
})

test('isAddressComplete: todos os campos → true', () => {
  assert.equal(isAddressComplete(FULL), true)
})

test('isAddressComplete: sem número → false', () => {
  assert.equal(isAddressComplete({ ...FULL, number: '' }), false)
})

test('isAddressComplete: complemento opcional (ausente → ainda true)', () => {
  const { complement, ...rest } = FULL   // eslint-disable-line no-unused-vars
  assert.equal(isAddressComplete(rest), true)
})

test('isAddressComplete: CEP/UF inválidos → false', () => {
  assert.equal(isAddressComplete({ ...FULL, cep: '123' }), false)
  assert.equal(isAddressComplete({ ...FULL, state: 'XX' }), false)
  assert.equal(isAddressComplete(null), false)
  assert.equal(isAddressComplete('texto livre'), false)
})

test('UF_LIST: 27 UFs (26 estados + DF), sem duplicatas', () => {
  assert.equal(UF_LIST.length, 27)
  assert.equal(new Set(UF_LIST).size, 27)
  assert.ok(UF_LIST.includes('PR') && UF_LIST.includes('DF') && UF_LIST.includes('SP'))
})

test('ADDRESS_REQUIRED: campos obrigatórios (sem complement)', () => {
  assert.deepEqual(ADDRESS_REQUIRED, ['cep', 'street', 'number', 'neighborhood', 'city', 'state'])
})

test('emptyAddress: objeto zerado com as 7 chaves', () => {
  assert.deepEqual(emptyAddress(),
    { cep: '', street: '', number: '', complement: '', neighborhood: '', city: '', state: '' })
})
