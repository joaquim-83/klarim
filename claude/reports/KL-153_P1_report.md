# KL-153 (Prompt 1/2) — Backend: KYC + rate limiting + scan avulso + upgrade + status

## Contexto e adaptações ao codebase real

O card foi escrito de forma genérica (tabela `accounts`, "Migration Alembic"). O Klarim usa outra
realidade, e o `CLAUDE.md` é autoritativo:

| Card (genérico) | Klarim (real, adotado) |
|---|---|
| tabela `accounts` | **`users`** (`account_type` owner/developer/both, `email_confirmed`, `gate_plan_id`, …) |
| "Migration Alembic" | **`_SCHEMA` + `ensure_schema`** idempotente (`ALTER … ADD COLUMN IF NOT EXISTS`) — **sem Alembic** |
| `phone VARCHAR(20)` novo | `phone` **já existia** (perfil dev, KL-151) — reusado |
| `POST /api/auth/register` | `POST /account/signup` (nginx tira o `/api`) + `POST /gate/register` já existentes |
| scan por `project_id` | o scan casa o projeto **por domínio**; `project_id` adicionado como opcional |

**NÃO** alterei o scanner público nem o frontend (Prompt 2). Backward compatibility mantida: scans
com projeto verificado seguem idênticos.

## Entregáveis

### 1–4. Schema (idempotente, `discovery/store.py`)
- **`users`**: `cpf VARCHAR(14)` (formatado `000.000.000-00`) + `idx_users_cpf` **único parcial**
  (`WHERE cpf IS NOT NULL`), `address`, `phone_verified`, `kyc_completed`, `kyc_completed_at`,
  `suspended`.
- **`gate_audit_log`**: +`cpf`/`url_scanned`/`domain`/`score`/`passed` (ALTER — CREATE TABLE IF NOT
  EXISTS não altera tabela existente).
- **`gate_runs.project_id` NULLABLE** (`ALTER COLUMN … DROP NOT NULL`, idempotente) — scan avulso.
- Store: `is_cpf_taken`, `update_user_kyc` (COALESCE preserva o 1º `kyc_completed_at`),
  `set_user_suspended`; `get_account_gate_fields` estendido (KYC+suspensão+email_confirmed);
  `insert_gate_audit` estendido.

### 2. `api/validators.py::validate_cpf` (puro)
Formato + 2 dígitos verificadores (módulo 11, pesos 10→2 e 11→2), rejeita sequência repetida,
**não** consulta a Receita, devolve **sempre formatado** ou `ValueError`.

### 3. `POST /account/kyc`
JWT + **e-mail confirmado** (403 senão). `422` CPF inválido · `409` CPF de outra conta.
`kyc_completed=TRUE` só com **CPF válido + endereço (≥10 chars) + telefone**; `phone_verified=TRUE`
quando há telefone (placeholder SMS). Retorna `{kyc_completed, kyc_completed_at, access_level}`.

### 5. Rate limiting 3 camadas — `api/gate_rate_limiter.py` (Redis; fail-open sem Redis)
Ordem fail-fast: **(0)** `suspended` → 403 · **(1)** IP 10/h (`gate:rl:ip`) · **(2)** conta/h por plano
(free 5 · pro 50 · team 200 · ent ∞, `gate:rl:user`) · **(3)** 1/domínio/30min (`gate:rl:domain`,
SET NX EX) · **(4)** intervalo entre domínios diferentes por conta (free 5min · pro 1min · team/ent 0,
`gate:rl:last`). 429 → `{detail, retry_after_seconds, limit_type, current_plan, upgrade_url}` + header
`Retry-After`. **Abuso:** SADD `gate:rl:distinct:{acct}` (TTL 24h); >20 distintos → `suspended=TRUE` +
audit `abuse_detected` + `logger.warning`. Conta suspensa → 403 `{suspended:true}` no `/gate/scan`.

### 6. Registro `source=security-gate`
`SignupBody.source`; `source=="security-gate"` → `provision_gate_developer` (account_type=developer +
Free + trial Pro 14d + API key) e a resposta inclui `api_key` (1×) + cookie de sessão.

### 7. Scan avulso
`POST /gate/scan` com `project_id` opcional: explícito → valida posse+verificação; ausente → casa por
domínio (backward compat) **OU scan avulso** (sem projeto, exige e-mail confirmado). `gate_runs.project_id`
= NULL no avulso.

### 8. Resultado filtrado por KYC
Sem KYC (`basic`): `score`/`passed`/`threshold`/`categories` (name/status/checks_total/passed/failed) +
`kyc_required_for_details`+`kyc_message`; **sem** `results`/paths/recomendações/histórico. Com KYC
(`complete`): tudo + `history` (runs anteriores do mesmo domínio) + `ci_snippet`. A engine roda TODOS os
checks; o filtro é só na resposta. Audit do scan grava `cpf`/`score`/`passed`.

### 9. `GET /account/gate/status` estendido
`{is_developer, kyc_completed, has_api_key, api_key_prefix, has_projects, projects_count,
scans_used_hour, scans_limit_hour, access_level, suspended, plan_slug, …}`. `plan` continua o **NOME**
(backward compat com a landing); `plan_slug` traz o slug.

### 10. `POST /account/gate/upgrade`
Nível ≥2. PIX AbacatePay avulso mensal (recorrência = escopo futuro, documentado); plano `gate:{slug}`
na `subscription_payments` → o webhook `_confirm_subscription_payment` detecta o prefixo `gate:` e ativa
o `gate_plan_id` (`plan_upgraded`). `409` se já no plano. Retorna `{checkout_url, plan, price_display,
charge_id, br_code, br_code_base64}`.

### 11. `POST /account/gate/activate`
Já era idempotente (`already_active` para developer/both) + audit `gate_activated` — confirmado, sem
mudança necessária.

## Testes — `tests/test_kl153_backend.py` (+39)
CPF (válido/normalizado/DV errado/repetido/tamanho) · KYC (completo/parcial/422/409/403/401) · rate
limiting (cada camada com fake Redis: user free/pro, IP, domínio, intervalo free/pro/team, mesmo
domínio, abuso 21, payload+Retry-After, fail-open) · scan avulso (ok/401/403 sem e-mail) · resultado
por KYC (basic sem `results` + `kyc_required_for_details` / complete com `results`+`history`+`ci_snippet`)
· audit (cpf presente com KYC / null sem) · 429 com `Retry-After` · suspensão (guard + abuso) · status ·
activate (owner→both / idempotente) · upgrade (checkout / 401 / 409) · `provision_gate_developer`.

6 FakeStores dos testes KL-151 atualizados (audit aceita os novos kwargs; `get_account_gate_fields`
devolve KYC; `list_gate_projects` para o status; `test_scan_unregistered_domain_403` → agora scan
avulso). **Suíte completa: 2277 passed, 1 skipped, 0 failed.**

## Segurança
CPF só formato+DV (sem PII externa), armazenado formatado e único; CPF entra no audit (compliance),
nunca em log de app. Rate limiting no SERVIDOR, fail-open sem Redis (não bloqueia por falha de infra).
Scan avulso exige e-mail confirmado. Upgrade nunca guarda dado de cartão/PIX (só o id da cobrança).

## Arquivos
**Novos:** `api/validators.py`, `api/gate_rate_limiter.py`, `tests/test_kl153_backend.py`.
**Editados:** `discovery/store.py` (schema + store methods), `api/gate.py` (KYC/status/upgrade/scan/
provision/log_gate_audit), `api/main.py` (SignupBody.source + signup branch + webhook gate),
`docs/API.md`, `docs/SECURITY.md` (§12), `CLAUDE.md`, e 3 testes KL-151 (fixtures).

## Pendente (Prompt 2)
Frontend: wizard de KYC, dashboard redesenhado, prompts de upgrade/suspensão. Recorrência de assinatura
(a AbacatePay hoje só faz PIX avulso).
