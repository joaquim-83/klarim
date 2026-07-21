// Testes da sanitização do <title> do /scan (fix de segurança 2026-07-21). node --test, sem DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeScanDomain, scanTitle } from './scanTitle.js';

test('safeScanDomain: extrai hostname de URL completa', () => {
  assert.equal(safeScanDomain('https://igoove.com/path?x=1'), 'igoove.com');
});

test('safeScanDomain: aceita host sem protocolo', () => {
  assert.equal(safeScanDomain('igoove.com'), 'igoove.com');
});

test('safeScanDomain: remove www.', () => {
  assert.equal(safeScanDomain('https://www.klarim.net'), 'klarim.net');
});

test('safeScanDomain: input com tags → vazio (não reflete)', () => {
  assert.equal(safeScanDomain('<script>alert(1)</script>'), '');
});

test('safeScanDomain: string sem ponto → vazio (não é domínio)', () => {
  assert.equal(safeScanDomain('localhost'), '');
  assert.equal(safeScanDomain('naoedominio'), '');
});

test('safeScanDomain: vazio → vazio', () => {
  assert.equal(safeScanDomain(''), '');
  assert.equal(safeScanDomain(null), '');
  assert.equal(safeScanDomain(undefined), '');
});

test('safeScanDomain: strip de chars perigosos remanescentes', () => {
  // mesmo que o parse produza algo estranho, só sobram [a-z0-9.-]
  const out = safeScanDomain('https://ex"ample.com');
  assert.ok(/^[a-z0-9.-]*$/.test(out));
});

test('scanTitle: hostname válido → "Analisando {host}"', () => {
  assert.equal(scanTitle('https://igoove.com'), 'Analisando igoove.com');
  assert.equal(scanTitle('igoove.com'), 'Analisando igoove.com');
});

test('scanTitle: input não-parseável → "Analisando um site"', () => {
  assert.equal(scanTitle('<script>alert(1)</script>'), 'Analisando um site');
  assert.equal(scanTitle('naoedominio'), 'Analisando um site');
});

test('scanTitle: vazio → genérico', () => {
  assert.equal(scanTitle(''), 'Análise de segurança');
  assert.equal(scanTitle(null), 'Análise de segurança');
});

test('scanTitle: nunca contém < ou > (não reflete tags)', () => {
  for (const inp of ['<img src=x onerror=alert(1)>', '"><b>', 'javascript:alert(1)']) {
    const t = scanTitle(inp);
    assert.ok(!t.includes('<') && !t.includes('>'), `vazou em: ${inp} → ${t}`);
  }
});
