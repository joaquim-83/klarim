# Fix — Auth web do MCP connector + regressão do login do painel

- **Tipo:** Bugfix crítico (parte do KL-18)
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Commit:** `fix: implement MCP auth web flow for Claude Desktop + investigate admin panel login`

---

## Fix 1 — Login do painel voltou a funcionar (502 → 200)

### Diagnóstico (na VM, via `gcloud compute ssh`)

- Containers todos **Up**; `curl localhost:8000/health` → **200**; `curl localhost:8000/auth/login`
  com credenciais reais → **200**. **A API estava saudável.**
- Mas `curl https://painel.klarim.net/api/health` → **502** e `…/api/auth/login` → **502**.
- **Causa raiz:** o container `api` foi **recriado** num deploy recente (novo IP no
  bridge do Docker), mas o container `web` (Nginx) **não** — e o Nginx tinha
  **cacheado o IP antigo** do `api` (resolve o hostname do `proxy_pass` uma vez, no
  boot). Resultado: 502 (Bad Gateway) para tudo que passava pelo Nginx, incluindo o
  login do painel. Não era regressão de código (o login funciona; testei local com
  as deps novas → 200/401 corretos).

### Correção imediata (aplicada na VM)

`sudo docker compose restart web` → o Nginx re-resolveu o IP do `api`:
`painel/klarim /api/health` voltou a **200** e o login com credenciais reais → **200**.

### Correção permanente (no repo — Nginx resolver dinâmico)

`http.conf` e os **dois** server blocks 443 do `https.conf.template`, nos `location
/api/` e `/mcp/`:
```nginx
resolver 127.0.0.11 valid=10s ipv6=off;   # DNS embutido do Docker
set $klarim_api api:8000;
rewrite ^/api/(.*)$ /$1 break;             # só no /api/ (o proxy_pass com variável
proxy_pass http://$klarim_api;             #  não faz o strip do prefixo)
```
Com o upstream numa **variável**, o Nginx re-resolve o IP a cada request (TTL 10s) —
imune à recriação do `api`. Validado **funcionalmente na VM** com um Nginx
descartável apontando para o `api` real: `GET /api/health` → 200, `POST
/api/auth/login` (senha errada) → 401 (prova que o `rewrite` faz o strip de `/api/`),
`GET /api/targets/stats` sem JWT → 401, `GET /mcp/sse` sem key → 401, `nginx -t` OK.

## Fix 2 — Fluxo de auth web do MCP (Claude Desktop)

**Problema:** ao adicionar um conector personalizado, o Claude Desktop **não oferece
campo para API key** — um 401 seco resulta em `step=start_error`.

**Solução:** um fluxo web que converte a API key (master) num **session token**
temporário, usado na URL do conector — **a API key nunca vai em URL**.

- **`GET /mcp/auth`** — página HTML (sem React, sem JS), campo `type="password"`,
  visual Klarim, CSP restritivo (`default-src 'none'; style-src 'unsafe-inline';
  form-action 'self'`).
- **`POST /mcp/auth/verify`** — valida a key em **constant-time** (`hmac.compare_digest`),
  **rate limit 5/min por IP**; ok → cria session token (`secrets.token_hex(32)`,
  **256 bits, TTL 24h**) e mostra a URL pronta `https://klarim.net/mcp/sse?token=<session>`
  (ou redireciona ao `callback_url`, se for um destino **confiável** —
  localhost/Anthropic; `_safe_callback` barra **open-redirect**).
- **`_authorized()`** — a SSE e os POSTs `/messages/` aceitam, em constant-time: a
  **API key** (Bearer) OU um **session token** válido (Bearer **ou** `?token=`).
  Sessões expiram em 24h (limpas na criação e na validação). **Sem `MCP_API_KEY` ⇒
  MCP desligado** (tudo 401).

### Checklist de segurança

- [x] API key nunca em log (não logamos key/token) e nunca em URL (só o session token).
- [x] `hmac.compare_digest` (constant-time) na key e no session token.
- [x] Session token `secrets.token_hex(32)` (256 bits) · expira em 24h.
- [x] HTTPS (Nginx/Let's Encrypt) · campo `type="password"`.
- [x] Rate limit 5/min por IP no verify (429 na 6ª).
- [x] Sem fallback inseguro (sem `MCP_API_KEY` → desligado).
- [x] CSP restritivo nas páginas de auth · anti open-redirect (`_safe_callback`).
- [x] `access_log off` no `location /mcp/` do Nginx → `?token=` não vaza nos logs.

## Validação

- **Smoke test local (uvicorn):** `GET /mcp/auth` → campo password; verify key errada
  → 401; verify key certa → session token (64 hex); `GET /mcp/sse?token=<session>` →
  `event: endpoint`; `Bearer <session>` → `event: endpoint`; `Bearer <API key>` →
  `event: endpoint`; token inválido → 401.
- **Testes** (`tests/test_mcp_server.py`, +10): `_authorized` (key/session, header/query),
  ciclo da sessão + expiração, `_safe_callback`, página de auth + CSP, verify
  (errada/certa/callback seguro/callback inseguro/rate limit).
- **Suite completa: 211 passed, 1 skipped.** O login (`test_auth.py`) segue passando.

## Ação manual na VM (após o deploy do CI)

O deploy recria o `web` com o Nginx novo (resolver dinâmico). Verificar:
```bash
curl -s -o /dev/null -w "%{http_code}" https://painel.klarim.net/api/health   # 200
curl -s https://klarim.net/mcp/auth | grep -o 'type="password"'               # existe
```
Conectar o Claude: abrir `https://klarim.net/mcp/auth`, colar a `MCP_API_KEY`, copiar
a URL `…/mcp/sse?token=<session>` e usá-la no conector personalizado.

## Nota honesta sobre o Claude Desktop

O padrão 100% nativo do Claude para conectores remotos é **OAuth 2.1** (discovery +
DCR + PKCE). Este fix entrega o caminho **pragmático e seguro** que funciona hoje:
página para emitir um session token + URL com `?token=`. Se no futuro for preciso o
OAuth nativo, o FastMCP já traz suporte (`auth_server_provider`) — fica como evolução.
