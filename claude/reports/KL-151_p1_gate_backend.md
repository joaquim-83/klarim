# KL-151 (Prompt 1/4) — Security Gate como produto: backend core

## Contexto

A engine do Security Gate (86 checks, 18 categorias) roda em produção. Este card a transforma em
**produto para devs externos**: o dev instala a CLI, autentica por API key e roda no pipeline dele —
sem clonar o repo. Este Prompt 1/4 é o **backend core**: contas dev, API keys, planos+enforcement,
projetos, verificação de domínio e convite dono→dev.

**NÃO neste prompt** (Prompts 2-4): API REST de scan, CLI, frontend, admin de planos.

## O que foi entregue

### Schema — 5 tabelas + 7 colunas em `users` (`discovery/store.py`)

A conta é **ÚNICA**: dono e dev são o mesmo `users`, distinguidos por `account_type`
(`owner`|`developer`|`both`). Colunas novas em `users`: `account_type`, `full_name`,
`company_name_dev`, `phone`, `gate_plan_id`, `gate_trial_started_at`, `gate_trial_ends_at`.

Tabelas `gate_*`: **`gate_plans`** (Free/Pro/Team/Enterprise, seed idempotente `seed_gate_plans`;
`-1`=ilimitado), **`gate_api_keys`** (só o SHA-256 + prefixo `KLM_xxxx` — nunca em claro),
**`gate_projects`** (domínio a escanear; só escaneia se `verified`; challenge de verificação no
`config` JSONB), **`gate_runs`** (histórico + contagem/dia p/ enforcement), **`gate_invites`**
(dono→dev, token, TTL 7d). + índices.

### API (`api/gate.py`, router incluído no `app`)

Duas famílias de auth: **API key** (`X-API-Key`, `authenticate_api_key`) em `/gate/*`; **JWT de
usuário** em `/account/gate/*`.

- `POST /gate/register` — cria conta `developer` + API key (**exibida 1×**) + 1º projeto + trial Pro
  14 dias (plano base Free). Anti-abuso: descartáveis + rate limit 5/h/IP.
- `POST /account/gate/regenerate-key` — revoga TODAS as keys ativas e emite nova (1×).
- `POST /gate/projects/{id}/verify/{start,check}` — reusa o desafio de domínio do KL-99
  (meta_tag/dns_txt/html_file) via API key; o challenge vive no `config` do projeto (TTL 7d, expiry
  resolvido no SQL); `check` rate-limited 10/h/IP.
- `POST /account/gate/invite` — o dono **nível 3** que possui o domínio VERIFICADO convida um dev por
  e-mail (token `token_urlsafe(32)`, TTL 7d); e-mail transacional `send_gate_invite` (fire-and-forget).
- `GET /gate/invite/{token}` (público) · `POST /gate/invite/{token}/accept` (o dev logado convidado →
  projeto `verified` method=`invite`, `invited_by`=dono) · `DELETE /account/gate/invite/{id}` (revoga +
  REMOVE o projeto do dev).
- `GET/POST /gate/projects`, `GET /account/gate/{keys,invites}` — leitura/gestão.

### Planos + enforcement (no SERVIDOR)

- **Plano efetivo** (`get_effective_gate_plan`): trial Pro ativo > plano associado > Free.
- **`get_allowed_checks(plan)`**: Free = 4 checks · Pro = 9 · Team/Enterprise = `["all"]` = os 18.
- **`enforce_scan_limit`** (429 no teto de scans/dia; `-1`=ilimitado) e **`enforce_domain_limit`**
  (403 no teto de domínios). Nunca se confia no client.

### Segurança

- API key **só como hash SHA-256** + prefixo; exibida 1×; auth compara o hash; revogada → 401.
- Domínio **só escaneia se verificado** (desafio de domínio OU convite do dono).
- Convite: só dono nível 3 **dono verificado do domínio** (`user_owns_verified_domain`); aceite exige
  o dev logado == e-mail convidado (anti-hijack); revogar remove o acesso.
- Rate limits: register 5/h, invite 10/h, verify/check 10/h por IP.

## Fix incidental (fresh-DB) — necessário p/ testar o dev stack

O índice `idx_targets_owner_verified` (KL-104 P2) era criado no topo do `_SCHEMA`, mas a coluna
`targets.owner_verified` só é adicionada mais abaixo (`ALTER`, KL-99). Num banco **FRESCO** o
`ensure_schema` falhava com `UndefinedColumn` — ou seja, o `docker-compose.dev.yml` (DB do zero) não
subia. Movido o índice para DEPOIS do `ADD COLUMN` (idempotente; DBs existentes intactos). Descoberto
justamente ao validar o schema do KL-151 num Postgres 16 fresco.

## Validação (Docker disponível nesta sessão)

- **Schema aplicado num Postgres 16 FRESCO** (container throwaway): `ensure_schema` roda limpo, as **5
  tabelas gate** existem, os **4 planos** são semeados, as **7 colunas** de `users` existem.
- **Todas as store methods exercitadas contra o Postgres real:** API key (create/lookup/touch/revoke),
  plano efetivo (trial ativo→Pro 9 checks; expirado→Free 4 checks), projeto (create/duplicate→None,
  challenge JSONB set/get/clear, mark verified), convite (create/get/accept/revoke),
  `user_owns_verified_domain`.
- **31 testes offline** (`test_kl151_gate_product.py`, TestClient + FakeStore): helpers puros, registro,
  API key auth (válida/ausente/prefixo/revogada/last_used), regenerate, plano efetivo trial/expirado,
  enforcement scans/domínios/checks, projetos + verificação, ciclo completo de convite.
- **Suíte completa: 2132 passed, 1 skipped.**

## Docs

- `CLAUDE.md` §9 (entrada do card), `docs/API.md` (tabela de endpoints do Gate), `docs/SECURITY.md`
  §11 (API key hash + convite).

## Próximos prompts

2/4: API REST de scan (usa `get_allowed_checks`/`enforce_scan_limit`/`create_gate_run`). 3/4: CLI.
4/4: frontend do portal do dev + admin de planos.
