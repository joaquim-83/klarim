# KL-145 — Desacoplar o Reoon do envio: 3 filtros (sintaxe + MX + blocklist)

## Contexto e problema

O Reoon (verificação de deliverability, KL-110) consumiu **10 cards** (KL-108, 110, 122, 125,
127, 128, 129, 130, 136, 137) e **~5.000 créditos**. Resultado: **2-8 envios/dia** com bounce
**4,4%**. Antes do Reoon: centenas de envios/dia, bounce ~3-4%.

**Causa raiz:** o Reoon classifica ~**97% dos servidores brasileiros como `unknown`** (Locaweb,
Hostinger, UOL, Titan… não respondem ao SMTP-check). Como filtro, é inútil — e a regra binária
por-status do KL-137 (`is_safe_to_send` só deixava passar `safe`/`valid`/`role`) barrava quase
tudo. O pipeline bloqueava a maioria dos e-mails elegíveis sem melhorar a taxa de bounce.

**Objetivo:** volume de 2-8/ciclo → **100-180/ciclo**, com o bounce convergindo via blocklist
aprendente (cada bounce, pelo webhook Resend, entra na `email_blocklist`).

## A mudança

A decisão de envio voltou a ser **LOCAL e barata**: 3 filtros, sem API externa, sem gates de
score, sem status de verificação.

### 1. `notifier/email_verifier.py` — nova `is_safe_to_send`

Assinatura mudou de `is_safe_to_send(result: VerifyResult, lead_score: int)` para
`is_safe_to_send(email: str, redis=None, store=None) -> bool` (async). Os 3 filtros:

1. **`_is_valid_syntax(email)`** — reusa a normalização RFC da Camada 0 (email-validator, fallback
   regex).
2. **`_email_has_mx(email, redis)`** — reusa o lookup MX da Camada 0 (dnspython + cache Redis 24h
   por domínio). **Fail-open:** DNS incerto (`unknown`) conta como MX presente; só `no_mx`
   definitivo rejeita (não barra e-mail legítimo por falha transitória de DNS).
3. **`_is_blocklisted(email, store)`** — `store.is_email_blocked(email)` sobre a `email_blocklist`
   (a blocklist é alimentada pelo webhook de bounce; já funcionava). Sem store → fail-open.

Tudo que passa nos 3 → **ENVIA**. A constante `SENDABLE_STATUSES` (regra binária por status do
KL-137) foi **removida**. O Reoon (`verify_reoon`/`verify_local`/`verify_email`/cache) **fica no
módulo** só como enriquecimento em background.

### 2. `discovery/alert_worker.py` — `_verify_and_filter` reescrito

De ~140 linhas de partição/verificação Reoon → ~45 linhas de 3 filtros. **Removidos:** partição
`sendable`/`unverified`, cap de verificação, chamada à API no envio, `_reoon_balance`, os atributos
`email_verify_max`/`email_verify_enabled`/`email_verify_ttl_days` (e a leitura de
`EMAIL_VERIFY_MAX_PER_CYCLE` no `_reload_settings`), gates de score, trust-downgrade, e o
`email_verified`/`email_verify_status` como condição de envio.

Novo stats por-filtro do ciclo:
`eligible / valid_syntax / has_mx / not_blocklisted / blocked_syntax / blocked_mx /
blocked_blocklist / errors`, com log
`[alert] KL-145: N eligible → N syntax ok → N MX ok → N not blocklisted → N sendable`.

O filtro de MX respeita `ALERT_VALIDATE_MX` (cache Redis em produção; desligado em dev/testes, onde
o `_validate_batch` — que **fica**, com self-heal de e-mail sujo, filtro demo e discard permanente
de blocklist/no-MX — já cobre MX/blocklist).

### 3. `discovery/store.py` — `_ALERT_ELIGIBLE_WHERE` limpo

Removidos os filtros de `email_verify_status`/`email_verify_source` (KL-128/130 — exclusão de
`unknown`+`power` e block-statuses). A blocklist (tabela dedicada) faz o trabalho que esses filtros
faziam. Mantidos os filtros legítimos: `status='scanned'`, `contact_email IS NOT NULL`,
`fail_count>0` (ou score 100 verde), janela de 30 dias, gate de acessibilidade (`gate_fail_count`/
`last_scan_score`). O método `retire_unknown_power_targets` (limpeza retroativa via script) fica.

### 4. `api/main.py` — `/system/status.email_verification`

O bloco passou a expor o funil do último filtro de envio (`send_filter`:
eligible/valid_syntax/has_mx/not_blocklisted/blocked_*) + o saldo Reoon do enriquecimento em
background (`reoon_balance`/`reoon_balance_warning`/`unverified_count`).

## O que NÃO foi removido

- **Reoon no `email_verifier`** — `verify_reoon`/`verify_local`/`verify_email`/cache/`check_balance`
  seguem, usados por `scripts/cleanup_email_backlog.py` e pelo saldo do `/system/status`
  (enriquecimento em background), **nunca no fluxo de envio**.
- **Blocklist + webhook de bounce** — cada bounce entra na `email_blocklist` (mecanismo de
  aprendizado do KL-145).
- **Circuit breaker hard-bounce** (KL-108), **lead scoring** (só ORDENA, KL-137), **link no e-mail**
  (KL-137/138), **List-Unsubscribe** (KL-102), **rotação de senders** (KL-91), `_validate_batch`
  (self-heal + discard), `retire_unknown_power_targets`.

## Testes

- **Novo:** `tests/test_kl145_three_filters.py` (19 testes) — os 3 filtros isolados e combinados,
  `is_safe_to_send` (válido/sintaxe/no-MX/blocklist/fail-open), status Reoon ignorado, volume de 200
  → 200 sendable, bounce→blocklist→bloqueado, sem chamada Reoon no envio, sem código morto.
- **Atualizados p/ os 3 filtros:** `test_kl110_email_verifier.py` (seções `is_safe_to_send` +
  `_verify_and_filter`), `test_email_pipeline.py` (decisão de envio), `test_kl136_operational_health.py`
  (Fix 2/4 Reoon removidos, mantidos role-penalty + rescan diagnostics), `test_e2e_flows.py`,
  `test_alert_worker.py`/`test_alert_sender_migration.py` (worker manual ganha `validate_mx`),
  `test_kl130_exclude_terminals.py` (WHERE sem filtros Reoon + retire method).
- **Removidos** (testavam a priorização de subset Reoon no envio, feature removida):
  `test_kl127_pipeline_integration.py`, `test_kl129_subset_priority.py`.

**Resultado:** `2028 passed, 1 skipped` (backend pytest). Smoke test com DNS real confirmou os 3
filtros (gmail → envia; sintaxe ruim → bloqueia; blocklist → bloqueia; DNS incerto → fail-open).

> ⚠️ Docker não estava disponível no ambiente local para subir o `docker-compose.dev.yml`; a
> validação foi pela suíte offline completa + smoke test com DNS real. A CI roda pytest no push
> (gate verde antes do deploy) e o deploy valida em produção.

## Segurança

O e-mail nunca vaza em log (mascarado, LGPD). A blocklist é o mecanismo de defesa (bounce →
blocklist → suprime envios futuros); o circuit breaker hard-bounce (KL-108) segue como defesa
reativa por remetente. Nenhum endpoint/fluxo novo foi exposto.

## Documentação atualizada

- `CLAUDE.md` — nova "REGRA DE ENVIO ATUAL (KL-145)" + entrada do card em §9; a regra binária do
  KL-137 virou histórica.
- `docs/DEPLOY.md` — `REOON_API_KEY` deixa de ser pré-requisito de envio; `EMAIL_VERIFY_*` marcadas
  como removidas; nota KL-145 nos "Valores operacionais atuais".

## Pós-deploy

1. Confirmar no log do `klarim-discovery-1` o funil `[alert] KL-145: … → N sendable` com N > 100.
2. Fechar o **KL-145 no Jira** após o primeiro ciclo com `sent > 100`.
3. (Opcional) apagar `EMAIL_VERIFY_MAX_PER_CYCLE`/`EMAIL_VERIFY_ENABLED`/`EMAIL_VERIFY_TTL_DAYS` do
   `.env` da VM — são ignoradas, mas confundem.
