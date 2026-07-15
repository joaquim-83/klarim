# Configurações editáveis ao vivo + gestão de senha + rotação do token MCP

**Card:** KL-44 (transversal — admin) · **Data:** 2026-07-15

A página `/painel/config` deixou de ser read-only: o admin edita os 10 parâmetros
operacionais ao vivo (o banco tem prioridade sobre o `.env`, **sem redeploy**), troca a
própria senha (hash bcrypt no banco) e rotaciona o token MCP — tudo pelo painel.

## Parte 1 — `admin_settings` + resolução banco→env

- Tabela **`admin_settings`** (`key` PK, `value`, `updated_by`, `updated_at`) no `_SCHEMA`
  (idempotente). Guarda os overrides dos parâmetros + `ADMIN_PASSWORD_HASH` + `MCP_API_KEY`.
- **`store.get_setting(key, default)`** — resolução **banco → `os.environ` (.env) → default**,
  **fail-open** (erro no banco cai para o env; nunca derruba um worker).
- **Workers leem por ciclo:** `alert`/`rescan`/`discovery` ganharam um reload no início do
  `run_cycle` (e o `scan` relê `WORKER_MAX_SCANS_PER_HOUR` por iteração) via `get_setting`.
  Assim uma mudança no painel vale no **próximo ciclo**, sem restart. Antes cada param era
  lido só no `__init__` (exigia redeploy).

## Parte 2 — Gestão de senha

- **`verify_admin_password(password)`** (`api/main.py`): hash **bcrypt** do banco
  (`ADMIN_PASSWORD_HASH`, prioridade) → `ADMIN_PASSWORD` do `.env` (texto puro, legado /
  primeiro acesso). Usado por `POST /auth/login` **e** por `/mcp/authorize` (OAuth KL-63).
  Primeiro acesso usa o `.env`; após trocar no painel, usa o hash do banco — sem downtime.
- **`PATCH /admin/password`** `{current_password, new_password, confirm_password}`: valida a
  senha atual, exige **≥12 caracteres com maiúscula + minúscula + número**, confirma, grava
  o hash bcrypt (`auth_users.hash_password`), **invalida todos os refresh tokens OAuth**
  (força re-login) e **nunca retorna a senha**. Rate limit **3/min/IP**.

## Parte 3 — Rotação do token MCP

- **`POST /admin/rotate-mcp-token`** `{current_password}`: gera novo `MCP_API_KEY` (CSPRNG,
  `secrets.token_hex(32)` = 64 hex), grava em `admin_settings`, aplica em runtime
  (`os.environ['MCP_API_KEY']` — o middleware do mesmo processo pega na hora), invalida os
  refresh tokens OAuth e retorna o token **uma única vez**. Rate limit **1/hora**. Conexões
  CLI com o token antigo param; **OAuth (JWT) não é afetado**.
- No boot da API, `_load_runtime_overrides()` recarrega o `MCP_API_KEY` do banco para o
  `os.environ` — a rotação **sobrevive a restart** (o resto da config é resolvido por
  `get_setting`, banco-primeiro).

## Parte 4 — Endpoints de configuração

- **`GET /admin/config`** → lista os 10 params com `value`, `source` (`db`/`env`/`default`),
  `type`/`min`/`max`/`unit` + `env_value` + o token MCP **mascarado** (`••••{últimos 8}`) +
  `password_source`. **Nunca** expõe o hash ou o token inteiro.
- **`PUT /admin/config/{key}`** `{value}` → valida **whitelist** (`_CONFIG_PARAMS`) + tipo
  int + faixa; grava override. Rate limit **10/min**.
- **`POST /admin/config/reset/{key}`** → remove o override (volta ao `.env`).
- **`GET /admin/system-info`** → versão, uptime da API, Redis conectado, último start.
- Todos sob `/admin` (JWT admin). Registrados no `adminApi.js`.

## Parte 5 — Frontend (`ConfigPage.jsx`)

- **Parâmetros operacionais:** tabela editável — ✏️ abre input inline com ✓/✗; badge
  `db`/`env` ao lado do valor; faixa e o valor do `.env` em texto menor; botão **Resetar**
  quando há override. Toast de feedback.
- **Segurança (nova):** troca de senha (3 campos + requisitos visíveis) e Token MCP
  (mascarado + **Rotacionar** via modal com senha → mostra o token novo **uma vez** com
  botão copiar + aviso).
- **Informações (nova):** versão, uptime, Redis, último start.

## Parte 6 — Testes (`tests/test_admin_config.py`)

Config CRUD (banco > env, reset), validação (faixa/whitelist/tipo), senha (sucesso grava
hash bcrypt, senha errada 401, fraca 400, mismatch 400, rate limit 429),
`verify_admin_password` (banco→env), rotação (token novo, **o antigo para de funcionar no
middleware**, senha errada 401), e `system-info`. Auth obrigatória em todos.

## Segurança (regra do card)

Todo endpoint tem **auth** (JWT admin), **validação** (whitelist + faixa + força de senha)
e **rate limit** (config 10/min, senha 3/min, rotação 1/h). A senha **nunca** trafega de
volta; o hash e o token nunca aparecem inteiros no `GET`. Trocar a senha / rotacionar o
token **invalida os refresh tokens OAuth** (força re-login). O `get_setting` é **fail-open**
(um erro de banco nunca pausa um worker).

## Deploy / verificação

`git push` → CI (Test + Build web + Nginx check + Deploy). Após deploy: abrir
`/painel/config`, editar um parâmetro (badge `db` aparece), resetar (volta a `env`); a
seção Segurança permite trocar a senha e rotacionar o token. O schema (`admin_settings`)
roda no `ensure_schema`.

**Regra inviolável:** o banco tem prioridade sobre o `.env`; a senha em texto puro do `.env`
é só fallback/primeiro acesso; `get_setting` é fail-open; nenhuma resposta expõe senha,
hash ou token inteiro.
