# KL-110 — Verificação de e-mail pré-envio: filtragem local + API Reoon + limpeza do backlog

**Data:** 2026-07-26 · **Prioridade:** Highest · **Status:** ✅ código pronto + testado (deploy seguro; verificação Power ativa só quando `REOON_API_KEY` entrar no `.env` da VM)

## Problema

O bounce rate dos senders cold (6-8% hard) vinha de e-mails ruins entrando na fila sem validação
de deliverability. O circuit breaker (KL-108) é **reativo** — pausa o sender DEPOIS que o bounce já
prejudicou a reputação. Este card ataca a **causa raiz**: verificar se a caixa existe ANTES de enviar.

## Solução — módulo `notifier/email_verifier.py` (fail-open, testável)

### Camada 0 — local (custo zero)
`verify_local(email, redis)`:
1. **Sintaxe/normalização** — `email-validator` (RFC 5322 + IDN), com **fallback regex** se a lib faltar.
2. **Descartáveis** — reusa a lista curada `api.disposable_emails.DISPOSABLE_EMAIL_DOMAINS` (KL-85),
   sem nova dependência nem fetch de rede no boot.
3. **MX** — dnspython, **cache Redis 24h por domínio** (`email_verify_mx:{domain}`). NULL-MX (RFC 7505)
   conta como sem MX. DNS incerto (timeout) → fail-open (não rejeita).
4. **Role-based** — flag (não rejeita) reusando `ROLE_BASED_PREFIXES` (KL-85).

### Camada 1 — API Reoon (`REOON_API_KEY`)
`verify_reoon` (modo `quick` ~0.5s / `power` 2-60s) → `parse_reoon_response` (pura). **Semáforo global
de 5 chamadas simultâneas** (restrição da API). `verify_api` faz **fallback fail-open**: API fora/timeout
→ status `unknown` (nunca bloqueia o pipeline). `check_balance` lê o saldo de créditos.

### Pipeline + decisão
`verify_email` = cache → Camada 0 → (cache de domínio catch-all) → Camada 1. Cache Redis por **SHA-256**
do e-mail (o e-mail em claro nunca vira chave), TTL **60d** definitivo / **7d** transitório; domínio
catch-all cacheado 7d. `is_safe_to_send(result, lead_score)`:

| Status | Ação |
|---|---|
| invalid · disabled · disposable · spamtrap | ❌ nunca (blocklist) |
| safe · valid · role | ✅ envia (role penalizado no score) |
| catch_all · unknown · inbox_full | ⚠️ só se lead_score > 50 |

## Integrações

1. **Extração** (`discovery/contact.py::_is_junk`) — domínio descartável nunca vira `contact_email`
   (o MX já era filtrado no `extract_email`). Camada 0 preventiva, custo zero, cobre worker + scripts.
2. **Alert worker** (`_verify_and_filter`, após o lead scoring / antes do envio) — verifica os melhores
   leads (≤`EMAIL_VERIFY_MAX_PER_CYCLE`=60/ciclo, semáforo 5), blocklista+descarta os definitivamente
   ruins e aplica `is_safe_to_send`. **Sem `REOON_API_KEY` é no-op** (o MX da Camada 0 já rodou na
   extração; reverificar local aqui só somaria latência de DNS). Fail-open: erro de infra MANTÉM o
   alvo. Alvo já verificado < TTL não regasta crédito.
3. **Lead scoring** (`discovery/alert_scoring.py`) — penaliza `catch_all` -10, `unknown` -5, `role` -15
   (sem DOBRAR se o prefixo já penalizou).

## Banco

4 colunas em `targets` (idempotentes no `ensure_schema`): `email_verified` · `email_verify_status` ·
`email_verified_at` · `email_is_role_based` (+ índice parcial). Métodos:
`update_target_email_verification`, `email_verification_stats`, `targets_needing_email_verification`.

## Observabilidade

`GET /system/email-verification-stats` (JWT admin) + MCP `get_email_verification_stats`: contagem por
status, quantos faltam verificar, total role-based e **saldo Reoon** (best-effort, fail-open). Feed do
KL-57 (qualidade de e-mail por setor/plataforma) via `email_verify_status`.

## Limpeza retroativa — `scripts/cleanup_email_backlog.py`

- **Fase 0 (local):** verifica todo o backlog não verificado; blocklista invalid/disposable, grava status.
- **Fase 1 (bulk Reoon):** verifica os sobreviventes em lote (create-bulk-verification-task + poll);
  blocklista invalid/disabled/disposable/spamtrap. Só roda com `REOON_API_KEY`; limitada por `--api-limit`.
- Flags: `--dry-run`, `--local-only`, `--api-limit N`. Relatório com contagem por resultado.
- Execução: `docker compose exec api python -m scripts.cleanup_email_backlog`.

## Testes

**+41 testes** (`tests/test_kl110_email_verifier.py`), suíte total **1748 passed, 1 skipped**:
Camada 0 (valid/invalid/disposable/no-mx/role/mx-unknown-fail-open/cache), parse Reoon (safe/role/
catch_all/unknown), `verify_reoon` com client falso, fallback fail-open, pipeline (skip_api/no-key/
short-circuit/cache-hit/domínio catch-all), `is_safe_to_send` (12 casos), **semáforo ≤5 concorrentes**,
penalidades de lead scoring, filtro de descartável na extração, `email_verification_stats` (offline),
e a integração do worker `_verify_and_filter` (block+gate, no-op sem key, alvo fresco pula a API).

## Segurança (regra 2026-07-15)

- `REOON_API_KEY` só do ambiente (`.env` da VM), **nunca** em log/frontend/código. Logs mascaram o
  e-mail (`_mask`).
- Cache Redis usa **SHA-256** do e-mail (não expõe e-mail em chave).
- Rate limit self-imposed: semáforo de 5 chamadas Reoon.
- `/system/email-verification-stats` sob prefixo `/system` (JWT admin). MCP sem args.
- Fail-open em toda a cadeia (DNS/Redis/API fora → degrada, nunca derruba o pipeline).
- Sem input de usuário; host da API é fixo (sem SSRF).

## Desvios da spec (documentados, faithful ao intento)

- **Integração "no profiler":** o `commercial_email` do profiler vai só para `site_profile`, NÃO para
  `targets.contact_email` (que nasce em `discovery/worker.py::extract_email`). A verificação preventiva
  foi posta no **chokepoint real de extração** (`discovery/contact.py`), que cobre worker + scripts.
- **Descartáveis:** reusei a lista curada do KL-85 em vez de adicionar o pacote `disposable-email-domains`
  (evita dependência nova + fetch de rede no boot). `email-validator` foi adicionado ao `requirements.txt`.
- **Dev stack:** o Docker Desktop estava desligado → não subi `docker-compose.dev.yml`. A lógica é
  coberta pelos 41 testes offline (mocks para DNS/Redis/Reoon). O SQL das 4 colunas é DDL idempotente
  padrão do `ensure_schema`.

## Rollout / validação pós-deploy

O deploy é **seguro e inerte**: sem `REOON_API_KEY`, a verificação Power do worker é no-op; as penalidades
de scoring só disparam quando `email_verify_status` estiver preenchido (nunca, até a key entrar). Landam
o schema, o filtro preventivo de descartável e o endpoint/MCP.

Passos manuais do dono (na VM), na ordem:
1. Configurar `REOON_API_KEY` no `/opt/klarim/.env` + recriar containers (`docker compose up -d`).
2. Rodar `scripts.cleanup_email_backlog` (Fase 0 dá pra rodar já; Fase 1 usa a key).
3. Acompanhar `get_email_verification_stats` + o bounce hard por `get_email_health`.
4. Com bounce hard < 5% em 7 dias, remover o `ALERT_SENDER_MAX_BOUNCE_RATE=8` emergencial do `.env`.

## Arquivos

Novos: `notifier/email_verifier.py`, `scripts/cleanup_email_backlog.py`,
`tests/test_kl110_email_verifier.py`. Alterados: `discovery/store.py` (schema + 3 métodos),
`discovery/alert_worker.py` (verificação pré-envio), `discovery/alert_scoring.py` (penalidades),
`discovery/contact.py` (filtro descartável), `api/main.py` (endpoint), `mcp_server/tools/system.py`
(tool), `requirements.txt` (email-validator), docs (`claude.md`, `docs/DEPLOY.md`, `docs/API.md`),
`tests/test_mcp_server.py`.
