# KL-63 — MCP OAuth 2.1 com PKCE (autenticação por login)

**Card:** KL-63 · **Prioridade:** ALTA · **Data:** 2026-07-15

O MCP do Klarim passa a suportar **OAuth 2.1 + PKCE** (spec MCP nov/2025): o operador
adiciona a URL limpa `https://klarim.net/mcp/sse` no Claude, faz login com a senha do
admin numa página do próprio Klarim, e o Claude recebe um access token (JWT, 1h) +
refresh token (30d, com rotação). O Klarim é o **próprio authorization server**
(single-tenant). O **token estático `MCP_API_KEY` continua válido** como fallback (CLI +
conexões existentes) — nada foi removido.

## Arquitetura de roteamento

- **`/.well-known/oauth-protected-resource`** e **`/.well-known/oauth-authorization-server`**
  → rotas **públicas** na API (raiz, fora do mount `/mcp` e dos prefixos protegidos). O
  Nginx ganhou `location ^~ /.well-known/oauth-` (prioridade sobre o `~ /\.` que senão
  devolveria 404) proxyando **identidade** para `api:8000`.
- **`/mcp/authorize`, `/mcp/token`, `/mcp/register`** → rotas Starlette no `mcp_app`
  (já sob o `location /mcp/` do Nginx), **isentas** de auth na `MCPAuthMiddleware` (são o
  próprio fluxo de login — não podem exigir token).
- **`/mcp/sse` + `/mcp/messages/`** → protegidos pela `MCPAuthMiddleware` (agora aceita
  JWT OAuth **ou** token estático).

## Parte 1-2 — Metadata (RFC 9728 / RFC 8414)

`mcp_server/oauth.py`: `protected_resource_metadata()` (resource, authorization_servers,
scopes) e `authorization_server_metadata()` (issuer, authorization/token/registration
endpoints, `code_challenge_methods_supported: ["S256"]`, `token_endpoint_auth_methods:
["none"]`). Servidas com `Access-Control-Allow-Origin: *` (dado público). O 401 do
`/mcp/sse` traz `WWW-Authenticate: Bearer resource_metadata="…/.well-known/
oauth-protected-resource"` — dispara a descoberta no cliente.

## Parte 3 — Dynamic Client Registration (RFC 7591)

`POST /mcp/register` (público): valida `redirect_uris` (**só HTTPS ou HTTP loopback** —
anti open redirect; rejeita `http` remoto e `javascript:`/`data:`), gera `client_id`
(CSPRNG hex 16 bytes), grava em Redis `mcp:client:{id}` (TTL 30d). **Rate limit
5/h/IP**. `token_endpoint_auth_method: none` (cliente público, PKCE).

## Parte 4 — Authorization endpoint

`GET /mcp/authorize` — valida `response_type=code`, `client_id` (registrado),
`redirect_uri` (casada com a registrada), `code_challenge` + `code_challenge_method=S256`,
`state` — e renderiza a **página de login** (dark mode, logo Klarim, nome do client,
**só campo de senha** — single-tenant, sem e-mail). `POST /mcp/authorize` valida a senha
do admin (`ADMIN_PASSWORD`, **constant-time**), gera o authorization code (CSPRNG hex 32
bytes, Redis `mcp:auth_code:{code}` TTL **60s**) e redireciona
`{redirect_uri}?code=&state=`. **Rate limit 5/min/IP** (anti brute-force). Todos os
valores são **escapados** no HTML (anti-XSS via `client_name`/`state`/`redirect_uri`).

## Parte 5 — Token endpoint

`POST /mcp/token` (`application/x-www-form-urlencoded`, parse manual — sem depender de
python-multipart):
- **authorization_code**: code existe/não-expirou, `client_id`+`redirect_uri` conferem,
  **PKCE S256** (`sha256(code_verifier)==code_challenge`), code **consumido** (one-time).
- **refresh_token**: token existe, `client_id` confere, **rotação** (invalida o antigo,
  emite novo).
- Resposta `{access_token, token_type:Bearer, expires_in:3600, refresh_token, scope}` com
  `Cache-Control: no-store`.

**Access token (JWT HS256):** `sub=admin, iss, aud=…/mcp/sse, scope=mcp:admin,
typ=mcp_access, iat, exp(+1h)`. Assinado com **`MCP_JWT_SECRET`** (preferível) ou
`JWT_SECRET` (fallback). Refresh token: CSPRNG hex 64 bytes, Redis `mcp:refresh:{token}`
TTL 30d.

## Parte 6 — Middleware (`mcp_server/auth.py`)

Ordem: **(1)** Bearer JWT → `validate_access_token` (assinatura/iss/aud/exp/typ/scope);
**(2)** token estático `MCP_API_KEY` (Bearer, constant-time); **(3)** `?token=` (JWT
propagado ao `/messages/` **ou** `MCP_API_KEY` — Claude.ai passa a auth na URL do SSE).
Rotas OAuth isentas. **Fail-closed** sem `MCP_API_KEY` e sem JWT válido.

## Parte 8 — Redis keys

`mcp:client:{id}` (30d) · `mcp:auth_code:{code}` (60s, one-time) · `mcp:refresh:{token}`
(30d, rotacionado) · `rate:mcp_register:{ip}` (1h) · `rate:mcp_authorize:{ip}` (1min).
Reusa o Redis já conectado da API + o `_redis_allow` (KL-44). Sem Redis, o OAuth degrada
para 503 — mas o token estático continua funcionando.

## Parte 7 — Testes (`tests/test_mcp_oauth.py`)

Metadata (2 endpoints), DCR (registro, redirect inválido, rate limit), authorize (login
page, params inválidos, senha errada→401, senha certa→302+code), token (troca+PKCE, PKCE
errado→invalid_grant, code one-time, refresh com rotação, grant não suportado),
middleware (isenção das rotas OAuth, aceita estático+JWT via Bearer e `?token=`, token
inválido→401, fail-closed sem config), 401 com `WWW-Authenticate`, e PKCE S256 unit.

## Segurança (regra do card)

PKCE S256 **obrigatório**; `redirect_uri` sempre casada com a registrada (open-redirect
prevention); code one-time 60s; refresh rotacionado; rate limit no register (5/h) e no
login (5/min); senha admin em **constant-time**; HTML escapado (anti-XSS); metadata sem
segredo; JWT com aud/iss/exp validados. O token estático **não** foi removido.

## Deploy / verificação pós-deploy

1. `curl https://klarim.net/.well-known/oauth-protected-resource` → JSON.
2. `curl -sI https://klarim.net/mcp/sse` (sem token) → **401 + WWW-Authenticate**.
3. Fallback: conexão existente com `?token=<MCP_API_KEY>` continua funcionando.
4. OAuth: adicionar `https://klarim.net/mcp/sse` (URL limpa) no Claude → página de login →
   autorizar → conecta.

**Config (`.env` da VM):** opcional `MCP_JWT_SECRET` (senão usa `JWT_SECRET`) e
`MCP_ISSUER` (default `https://klarim.net`). Reusa `ADMIN_PASSWORD` e `MCP_API_KEY` já
existentes.

**Regra inviolável:** o token estático (`MCP_API_KEY`) permanece como fallback; o fluxo
OAuth é PKCE-only (público, sem client secret); nenhuma rota do fluxo (authorize/token/
register) pode exigir o token que ela mesma emite.
