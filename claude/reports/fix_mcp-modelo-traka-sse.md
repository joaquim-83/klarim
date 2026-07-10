# Fix — MCP server no modelo Traka (SSE + auth middleware + propagação de token)

- **Tipo:** Rewrite do scaffolding MCP (parte do KL-18)
- **Data:** 2026-07-10
- **Executor:** Claude CLI (Opus 4.8)
- **Commit:** `fix: rewrite MCP server to Traka SSE model (auth middleware + token propagation, same SDK)`

---

## Contexto e decisão

O MCP não conectava no Claude.ai web. O pedido era reescrever no modelo Traka
(**FastMCP 3.x + SSE + auth middleware ASGI**). Na investigação (venv isolado)
descobri:

1. **`fastmcp` 3.4.4 não tem `sse_app()`** (só `http_app`) — o código de referência do
   card não bate com a API real da 3.4.
2. **`fastmcp` força `starlette 1.3.1`**, incompatível com FastAPI 0.115 → exigiria
   **bump FastAPI 0.115 → 0.139** (mexe na API inteira: pagamentos, relatórios, todos
   os endpoints). Alto risco logo após a auditoria de segurança.
3. **A causa real** do Claude.ai não conectar é a **propagação do token** no endpoint
   SSE anunciado (o próprio card chama de "o bug do Traka, hotfix 5.2") — independente
   do SDK.

**Perguntei ao operador** e a escolha foi: **modelo Traka SEM trocar de SDK**. Ou
seja, adotar o scaffolding Traka (middleware ASGI, SSE puro, tools em módulos, token
propagation) sobre o SDK atual (`mcp` v1.x, que já tem SSE) — mesmo resultado no
Claude.ai, sem o bump de FastAPI.

## O que mudou

### Estrutura em módulos (modelo Traka)

```
mcp_server/
├── _base.py     # instância FastMCP + helpers (_guard/_api/_store)
├── server.py    # mcp_app (SSE) + propagação de token
├── auth.py      # MCPAuthMiddleware (ASGI)
└── tools/       # system.py, targets.py, scans.py, alerts.py, payments.py, analytics.py
```

As **25 tools ficaram idênticas** — só migraram do `server.py` monolítico para
`tools/` por domínio. Sem import circular: as tools importam de `_base`; o `server.py`
importa `_base` + `tools`.

### `auth.py` — `MCPAuthMiddleware`

ASGI, envolve o `mcp_app` inteiro. **Fail-closed** (sem `MCP_API_KEY` ⇒ tudo 401),
**constant-time** (`hmac.compare_digest`), aceita `Authorization: Bearer <chave>` **ou**
`?token=<chave>`, e emite `WWW-Authenticate: Bearer realm="klarim-mcp"` em toda 401.

### `server.py` — SSE + propagação de token (o fix crítico)

O `SseServerTransport` anuncia `data: /mcp/messages/?session_id=<hex>` **sem** a auth.
Como a middleware protege `/messages/`, os POSTs do Claude levariam 401 na 2ª fase.
`_token_propagating_send` **reescreve o evento `endpoint`** para incluir
`&token=<token>` (o mesmo com que o cliente abriu o SSE) → os POSTs chegam
autenticados. Este é o motivo de o Claude.ai conectar agora.

### Mount em 3 linhas (`api/main.py`)

```python
from mcp_server.server import mcp_app
from mcp_server.auth import MCPAuthMiddleware
app.mount("/mcp", MCPAuthMiddleware(mcp_app))
```

### Código obsoleto removido

Streamable HTTP (dual transport), `session_manager` + lifespan MCP
(`_mcp_streamable_cm`), `_authorized()` inline, session tokens (`_mcp_sessions`), a
página de auth web (`/mcp/auth` + `/mcp/auth/verify`), `_safe_callback`, rate limit da
verify — tudo removido. A auth agora é **direta por `MCP_API_KEY`** no `?token=`.

### Mantido

- **SDK `mcp>=1.27,<2`** + pins `starlette>=0.40,<0.42` / `sse-starlette>=1.6.1,<2.2`
  (nada mudou no `requirements.txt` — sem bump de FastAPI).
- **Nginx** `location /mcp/` intacto (buffering/cache off, `access_log off`, resolver).
- `MCP_API_KEY` (mesma variável).

## Validação

- **Smoke test (uvicorn):** SSE sem token → **401 + `WWW-Authenticate`**; SSE `?token=`
  → `data: /mcp/messages/?session_id=…&token=<KEY>` (**token propagado**); SSE Bearer →
  idem; token errado → 401; POST `/messages/` sem token → 401.
- **Testes** (`tests/test_mcp_server.py`, reescrito): 25 tools registradas, middleware
  (Bearer/query/errado/fail-closed), `WWW-Authenticate` na 401, propagação de token
  (1º evento só), `_guard`, execução das tools com store falso. **Suite: 204 passed, 1
  skipped.**

## Conectar (URL única, chave no `?token=`)

- **Claude.ai web:** Configurações → Conectores → Add →
  `https://klarim.net/mcp/sse?token=<MCP_API_KEY>`
- **Claude Desktop:** `{"mcpServers":{"klarim":{"url":"https://klarim.net/mcp/sse",
  "headers":{"Authorization":"Bearer <MCP_API_KEY>"}}}}`
- **Claude Code:** `claude mcp add klarim --transport sse https://klarim.net/mcp/sse
  --header "Authorization: Bearer <MCP_API_KEY>"`

## Notas

- **Trade-off de segurança aceito** (alinhado ao Traka): com a chave no `?token=`, a
  `MCP_API_KEY` trafega na URL. O `access_log off` no Nginx evita o vazamento nos logs
  de acesso; o token propagado no `/mcp/messages/` também não é logado. (O fluxo de
  session token, que evitava a chave na URL, foi removido a pedido — simplicidade.)
- **Bare `/mcp` / `/mcp/`** agora dão 404 (não há mais Streamable HTTP no root); a URL
  de conexão é `/mcp/sse`. O redirect `location = /mcp` do Nginx ficou inócuo — deixei
  como está (mexer no Nginx seria risco desnecessário).
