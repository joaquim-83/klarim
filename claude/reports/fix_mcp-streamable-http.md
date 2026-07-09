# Fix — Transporte Streamable HTTP no MCP server (compatibilidade Claude Desktop)

- **Tipo:** Fix crítico (parte do KL-18)
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Commit:** `fix: add Streamable HTTP transport to MCP server for Claude Desktop compatibility`

---

## Problema

O Claude Desktop usa **Streamable HTTP** para conectores personalizados, não SSE. O
endpoint SSE (`/mcp/sse`) funcionava via curl, mas o Claude Desktop fala Streamable
HTTP e falhava com "Problema de conexão".

## O que mudou

### Dois transportes no mesmo `/mcp` (`mcp_server/server.py`)

- **Streamable HTTP** em **`/mcp/`** (o do Claude Desktop): `FastMCP(stateless_http=True)`;
  o `session_manager` do FastMCP é inicializado (`mcp.streamable_http_app()`) e
  embrulhado num ASGI callable com autenticação, montado no **root** do sub-app
  (`Route("/")`). Detalhe: o Starlette só trata **instância de classe** como app ASGI
  — uma função `async(scope,…)` viraria endpoint request-response (descoberto na
  investigação). Aceita GET/POST/DELETE.
- **SSE** em **`/mcp/sse`** mantido para clients legados/curl.
- **Lifespan:** o `session_manager.run()` precisa estar **ativo** durante o app —
  rodado no lifespan do FastAPI (`_mcp_streamable_cm()` → `lifespan_cm()`), no-op se o
  MCP não montou.

### Correção crítica — host validation (teria quebrado a produção)

O `StreamableHTTPSessionManager` vem com **DNS-rebinding protection ligada por
padrão**, com `allowed_hosts=['127.0.0.1:*','localhost:*','[::1]:*']`. Atrás do Nginx
o Host é `klarim.net`/`painel.klarim.net` → seria **rejeitado** ("Invalid Host
header"). O smoke test local só passou porque o Host era `127.0.0.1`. Fix:
```python
mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
```
Seguro: estamos atrás do Nginx e a auth é por `MCP_API_KEY`/session token.

### Autenticação (inalterada, agora nos dois transportes)

`_authorized()` aceita, em constant-time, a **API key** (Bearer) OU um **session
token** válido (Bearer ou `?token=`), tanto no Streamable HTTP quanto no SSE.
A página `/mcp/auth` agora entrega a URL primária **`…/mcp/?token=<session>`**
(Streamable HTTP), com a de SSE como alternativa.

### Nginx

- `location = /mcp { return 308 /mcp/$is_args$args; }` — o `/mcp` **pelado** cairia no
  `location /` (SPA); redireciona para `/mcp/` preservando a query (`?token=`).
- `location /mcp/` (já existente) serve os dois transportes com buffering/cache off,
  `Connection ''`, resolver dinâmico e `access_log off`.

## Validação

- **Smoke test local (uvicorn):** `POST /mcp/` sem auth → 401; com auth →
  `initialize` retorna `serverInfo:{name:"klarim"}`; `tools/list` retorna as 25 tools;
  SSE legado (`/mcp/sse`) segue com `event: endpoint`.
- **nginx (throwaway container na VM):** `nginx -t` OK; `/mcp` → **308** →
  `/mcp/` (query preservada em `/mcp?token=x` → `/mcp/?token=x`); `/api/health` → 200;
  `/mcp/sse` → 401.
- **Testes** (`tests/test_mcp_server.py`, +2): `initialize` + `tools/list` via
  Streamable HTTP (com `with TestClient(...)` para ativar o lifespan/session manager)
  e a verificação de que o session manager foi montado. **Suite: 213 passed, 1 skipped.**

## Como conectar no Claude Desktop

1. Abra `https://klarim.net/mcp/auth`, cole a `MCP_API_KEY`.
2. Copie **`https://klarim.net/mcp/?token=<session>`** e cole como URL do conector
   personalizado (Streamable HTTP).
3. Alternativas: SSE (`…/mcp/sse?token=<session>`), header `Authorization: Bearer
   <MCP_API_KEY>`, ou a ponte local `mcp-remote`:
   ```json
   { "mcpServers": { "klarim": { "command": "npx",
       "args": ["-y", "mcp-remote", "https://klarim.net/mcp/?token=<session>"] } } }
   ```

## Notas de design

- **stateless_http=True:** cada request é independente (sem sessão persistida) — o
  `tools/list` funciona sem manter estado no servidor, ideal atrás de proxy.
- **Bare `/mcp` → 308:** preferimos redirecionar (preservando método e query) a
  duplicar rotas; a URL canônica é `/mcp/` com barra.
