# KL-147 — Security Gate: detecção de SPA fallback por fingerprint (ETag / Content-Length)

## Contexto e problema

O Security Gate gerava **falsos positivos massivos** em SPAs que retornam **200 + `index.html`
para qualquer path**. O `sistema.igoove.com.br` foi de **0/100 com 14 findings** — todos falsos
exceto o HSTS ausente. Causa: o SPA serve o mesmo `index.html` (mesmo ETag) para `/.env`, `/admin`,
`/swagger`, etc.

O guard existente (`_is_spa_fallback_nonhtml`, KL-141 P3) filtrava só paths com **extensão não-HTML
listada** quando o Content-Type era `text/html` — não cobria paths **sem extensão** (`/admin`,
`/swagger`, `/debug`) nem extensões não listadas (`/.env.local`, `/.git/config`).

## A solução — probe de controle (1 conceito, 4 arquivos)

Antes dos checks, o engine faz **1 HEAD** num path aleatório que certamente não existe
(`/_klarim_gate_probe_{uuid}`). Se responde 200, o alvo é um SPA com fallback: captura o
**fingerprint** (ETag + Content-Type + Content-Length). Cada check de exposição/API compara o 200 de
cada path com esse fingerprint — **mesmo fingerprint = fallback (PASS)**, diferente = exposição real
(FAIL).

### 1. `security_gate/utils.py` (novo) — comparador compartilhado

`matches_spa_fingerprint(response, fingerprint)`: **ETag** é o comparador primário (identidade do
corpo). Sem ETag, cai para **Content-Type + Content-Length** iguais. Usado por exposure **e** api
(o card pedia compartilhar).

### 2. `security_gate/engine.py` — probe + roteamento do fingerprint

- `_detect_spa_fallback(client, base_url)`: HEAD no probe; 200 → fingerprint, senão `None`.
  Best-effort (qualquer erro → `None`, os checks rodam normalmente).
- `run_all`: faz o probe **só se algum check spa-aware vai rodar** (`_SPA_AWARE = {exposure, api}`),
  evitando o request extra à toa; passa o fingerprint só a esses dois checks (headers/ssl/credentials
  não têm paths para comparar).

### 3. `security_gate/checks/exposure.py`

Novo parâmetro `spa_fingerprint`. Guard-chain por path com 200:
**allowlist** (KL-141 P3) → **fingerprint (KL-147)** → **Content-Type nonhtml** (KL-141 P3) →
directory-listing (GET). O fingerprint cobre a lacuna: paths sem extensão e extensões novas.

### 4. `security_gate/checks/api_security.py`

Novo parâmetro `spa_fingerprint`. Um endpoint protegido em 200 que casa o fallback vira **PASS**
("fallback de SPA (não é endpoint real)") em vez de FAIL "sem auth".

## Validação contra os 3 alvos (obrigatória)

| Alvo | Infra | Score | Findings reais |
|---|---|---|---|
| `klarim.net` | nginx (404 p/ exploit) | **100/100 🟢** | 0 — probe 404 → sem fingerprint, **inalterado** |
| `sistema.igoove.com.br` | SPA (fallback 200) | **90/100 🟢** | 1 (HSTS ausente) — **antes: 0/100 com 14 falsos** |
| Traka Cloud Run (`igoove-leads-api-…run.app`) | Cloud Run (404) | **63/100 🟡** | 2 High + 3 Medium (headers ausentes reais) — probe 404 → **inalterado** |

No Igoove, todos os 11 grupos de exposição voltaram a PASS e os 4 endpoints protegidos mostram
"fallback de SPA (não é endpoint real)". Nos outros dois (nginx/Cloud Run que devolvem 404 ao probe),
nada mudou — o fingerprint só atua quando o alvo é de fato um SPA.

## Testes

- **Novo `tests/test_kl147_spa_fingerprint.py`** (+20): `matches_spa_fingerprint` (etag/CT+CL/sem
  match), `_detect_spa_fallback` (404→None, 200→fingerprint, erro de rede→None), exposure com
  fingerprint (`/admin` e `/.env` mesmo ETag→PASS · `/.env` ETag diferente→FAIL · `/admin` JSON→FAIL ·
  CT+CL sem ETag→PASS · CL diferente→FAIL), api_security (mesmo ETag→PASS · 401→PASS · 200 diferente→
  FAIL), e **não-regressão** (sem fingerprint, exposição/endpoint real seguem FAIL).
- Engine tests do KL-141 atualizados para a assinatura `(client, url, config, spa_fingerprint)` dos
  checks spa-aware (probe mockado nos testes com client real).
- **`2063 pytest passed, 1 skipped`.** Os testes de exposure/api/credentials do KL-141 seguem verdes
  (não-regressão), e o `_is_spa_fallback_nonhtml` continua funcionando (guard 3 da chain).

## Regras atendidas

1. **1 request extra** por scan (HEAD do probe; só quando exposure/api rodam) ✅
2. `matches_spa_fingerprint` **compartilhado** entre exposure e api (`utils.py`) ✅
3. Relatório PT-BR ✅
4. `CLAUDE.md` atualizado (entrada do card KL-147) ✅
5. Validação contra os 3 alvos ✅

> ⚠️ Docker não estava disponível no ambiente local para o `docker-compose.dev.yml`; a validação foi
> a suíte offline completa + a execução real do CLI contra os 3 alvos autorizados (acima). A CI roda
> pytest no push e o job `security-gate` (KL-141 P4) roda o Gate contra `klarim.net` pós-deploy.

## Pós-deploy

Fechar o **KL-147 no Jira** após confirmar (já feito acima) os 3 scores esperados. O job
`security-gate` da CI passará a usar o probe automaticamente no próximo deploy.
