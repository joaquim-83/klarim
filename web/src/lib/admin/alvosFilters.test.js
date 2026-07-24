// KL-104 P2 — testes puros do modelo de filtros da página Alvos (URL <-> estado <-> params).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  readFiltersFromURL, filtersToQueryString, filtersToApiParams,
  activeFilterCount, nextToggle, toggleMultiValue, multiValues,
} from './alvosFilters.js'

test('readFiltersFromURL lê selects, texto e bools 3-estados', () => {
  const f = readFiltersFromURL('?status=scanned&score=90-100&has_email=true&monitored=false&search=hotel')
  assert.equal(f.status, 'scanned')
  assert.equal(f.score, '90-100')
  assert.equal(f.has_email, true)
  assert.equal(f.monitored, false)
  assert.equal(f.search, 'hotel')
})

test('readFiltersFromURL ignora vazios e bool inválido', () => {
  const f = readFiltersFromURL('?status=&has_email=maybe&sector=hotel')
  assert.equal('status' in f, false)      // vazio → ausente
  assert.equal('has_email' in f, false)   // não é true/false → ausente
  assert.equal(f.sector, 'hotel')
})

test('filtersToQueryString ida-e-volta preserva os filtros ativos', () => {
  const f = { status: 'alerted', score: '0-49', has_email: true, owner_verified: false, site_type: 'ecommerce,saas' }
  const round = readFiltersFromURL('?' + filtersToQueryString(f))
  assert.deepEqual(round, f)
})

test('filtersToQueryString omite bools undefined', () => {
  const qs = filtersToQueryString({ has_email: undefined, monitored: true })
  assert.equal(qs.includes('has_email'), false)
  assert.equal(qs.includes('monitored=true'), true)
})

test('filtersToApiParams passa bool booleano e string como está', () => {
  const p = filtersToApiParams({ status: 'scanned', has_email: false, tech: 'wordpress', search: '' })
  assert.equal(p.status, 'scanned')
  assert.equal(p.has_email, false)         // false vai (backend distingue sim/não)
  assert.equal(p.tech, 'wordpress')
  assert.equal('search' in p, false)       // string vazia não vira param
})

test('activeFilterCount conta strings e bools (true e false)', () => {
  assert.equal(activeFilterCount({}), 0)
  assert.equal(activeFilterCount({ status: 'scanned', has_email: false, monitored: true }), 3)
  assert.equal(activeFilterCount({ status: '' }), 0)   // vazio não conta
})

test('nextToggle cicla undefined → true → false → undefined', () => {
  assert.equal(nextToggle(undefined), true)
  assert.equal(nextToggle(true), false)
  assert.equal(nextToggle(false), undefined)
  assert.equal(nextToggle(null), true)
})

test('toggleMultiValue adiciona, remove e limpa (CSV)', () => {
  assert.equal(toggleMultiValue(undefined, 'saas'), 'saas')
  assert.equal(toggleMultiValue('saas', 'ecommerce'), 'saas,ecommerce')
  assert.equal(toggleMultiValue('saas,ecommerce', 'saas'), 'ecommerce')
  assert.equal(toggleMultiValue('saas', 'saas'), undefined)   // último removido → limpa
})

test('multiValues faz parse do CSV ignorando vazios', () => {
  assert.deepEqual(multiValues('a, b ,,c'), ['a', 'b', 'c'])
  assert.deepEqual(multiValues(''), [])
  assert.deepEqual(multiValues(undefined), [])
})
