# Fix — Ativação do Security Gate para contas existentes (user logado)

## Problema

O CTA "Começar grátis" da landing `/security-gate` sempre abria o formulário de registro (pedia
e-mail) — **mesmo com o usuário já logado**. Um owner/técnico com conta existente não tinha caminho
para ativar o Gate: se tentasse registrar de novo, o e-mail já existia e retornava erro genérico.

## Solução (3 partes)

### 1. Endpoint de ativação para conta existente

**`POST /api/account/gate/activate`** (JWT de sessão, `@_require_level(1)`) em `api/gate.py`:

- **Idempotente:** se a conta já é `developer`/`both` → `{status: "already_active", ...}` (não regera
  key nem reinicia trial).
- Caso contrário: promove `account_type` (**owner → both**, senão → developer), **gera a API key**
  (exibida UMA VEZ, só se ainda não houver uma ativa) e concede **trial Pro de 14 dias** (plano base
  Free) se a conta ainda não tem plano/trial.
- **Audit** `gate_activated` (`previous_type`/`new_type`).
- Resposta: `{status, api_key (null se já tinha), plan, key_prefix, has_key, trial_ends_at,
  dashboard_url}`.

**`GET /api/account/gate/status`** (JWT de sessão; 401 se não logado) — a landing usa para escolher o
CTA: `{logged_in, gate_active, account_type, plan, has_key, key_prefix, dashboard_url}`.

### 2. Landing com CTA dinâmico

Novo island **`web/src/components/security-gate/GateLandingCTA.jsx`** (`client:load`), no hero da
`security-gate.astro`. No mount consulta `GET /api/account/gate/status` (cookie HttpOnly, same-origin):

| Estado | Botão |
|---|---|
| Não logado (401) | **Começar grátis →** (link p/ `/cadastrar?type=developer`) |
| Logado + Gate não ativo | **Ativar Security Gate →** (POST activate) |
| Logado + Gate já ativo | **Abrir dashboard →** (link p/ `/dashboard/gate`) |

**SSR/cache-safe:** o 1º paint é o estado "não logado" (link, funciona sem JS e evita salto de
layout / cache do Cloudflare); o island re-decide no cliente. Se a ativação devolve uma **key nova**,
um **modal** exibe a key UMA VEZ (Copiar + aviso "exibida apenas uma vez") antes de ir ao dashboard;
se a conta já tinha key, redireciona direto.

### 3. Fallback no registro — conta existente

`POST /gate/register` deixou de retornar erro genérico para e-mail existente. Agora
`_account_exists_response` devolve **409 estruturado**:

- Gate **já ativo** → `{error: "account_exists", message: "…já tem o Security Gate ativo. Faça
  login…", login_url: "/entrar"}`.
- Conta existe, **Gate não ativo** → idem + `activate_after_login: true` ("…Faça login e ative o
  Security Gate no dashboard.").

Cobre também a corrida (create_user devolve None → mesma resposta).

## Segurança

- **API key exibida UMA VEZ** na ativação; depois só o prefixo (`list_gate_api_keys` nunca devolve o
  valor). No banco vive só o **SHA-256** — teste garante `key_hash == sha256(full)` e que o valor cru
  nunca aparece em `key_hash`/`key_prefix`.
- Ativação exige sessão (`require_user` → 401) e nível ≥ 1.
- Reusa métodos de store já validados (`set_account_type`, `set_account_gate_plan`,
  `create_gate_api_key`, `get_account_gate_fields`) — **sem SQL novo**.
- Audit `gate_activated`/`key_created` em toda ativação (compliance, KL-151 P4).

## Testes

**`tests/test_kl151_activate.py`** (+13): owner ativa (→ both, key 1x, trial Pro); key só como hash;
trial concede plano efetivo Pro; developer/both → already_active; sem sessão → 401; não regera key
existente; audit `gate_activated`; `status` logado-out/inativo/ativo; registro com e-mail existente →
409 com/sem `activate_after_login`.

- Suíte KL-151 completa (5 arquivos): **93 passed**.
- Frontend: `npm run test:unit` **154 passed**, `npm run build` OK (island compila).

## Arquivos

- `api/gate.py` — `POST /account/gate/activate`, `GET /account/gate/status`, `_account_exists_response`
  + `_gate_active` helper; 409 estruturado no `gate_register`.
- `web/src/components/security-gate/GateLandingCTA.jsx` (novo) + `web/src/pages/security-gate.astro`
  (hero usa o island).
- `tests/test_kl151_activate.py` (novo).
- `CLAUDE.md` §9 (nota do fix).

Nenhuma mudança de nginx (endpoints sob `/api/` já proxiados; island servido de `/_astro/` por
`script-src 'self'`; a rota `/security-gate` já está na allowlist).
