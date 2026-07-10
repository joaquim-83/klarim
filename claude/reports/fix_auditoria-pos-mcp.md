# Fix — 3 achados da auditoria pós-MCP (HSTS, XSS callback, catch-all paths)

- **Tipo:** Correção de segurança (sem card Jira — urgente)
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Commit:** `fix: restore HSTS header, sanitize MCP auth callback_url, block suspicious directory paths`

---

## Diagnóstico (na VM, antes de mexer)

- **HSTS:** presente em `/`, `/sobre`, `/api/health`, `/mcp/auth` (todos com
  `strict-transport-security`). **Ausente só nos `/assets/*.js`** — o `location
  /assets/` tem `add_header Cache-Control`, e um `add_header` próprio **quebra a
  herança** dos headers do `server` block no Nginx. Ou seja, o achado era **mais
  estreito** que "sumiu de todas as respostas": só os assets (JS/CSS).
- **XSS callback:** o `callback_url` já era inserido com `html.escape` (que por
  padrão escapa `"`/`<`/`>`), então **não havia breakout** de `value="…"`. Ainda
  assim, um valor malicioso ficava refletido (escapado) — reforçado abaixo.
- **Paths suspeitos:** `/backup/`, `/uploads/`, `/admin/`, `/staging/` retornavam
  **200** com a SPA. Confirmado.

## Fix 1 — HSTS nos `/assets/` (herança do Nginx)

Os 5 security headers (HSTS, CSP, `X-Frame-Options`, `X-Content-Type-Options`,
`Referrer-Policy`) foram **repetidos dentro do `location /assets/`** dos dois server
blocks 443 (`https.conf.template`) — já que o `add_header Cache-Control` local quebra
a herança do `server`. Comentário no config alerta sobre o gotcha para o futuro.

## Fix 2 — `callback_url` da página `/mcp/auth`

`_auth_page_html` agora, **além do `escape()`**, só reflete o `callback_url` se ele
passar no `_safe_callback` (localhost/Anthropic); qualquer outro vira **string
vazia** (`value=""`). Defense-in-depth: para um payload de auditoria
(`?callback_url="><script>…`), o HTML sai **sem `<script>` e com `value=""`**.

## Fix 3 — 404 para diretórios "suspeitos"

Regex adicionado nos 3 server blocks (`http.conf` + 2 blocos 443):
```nginx
location ~* ^/(backup|uploads|admin|internal|debug|test|staging|tmp|temp|logs|private|secret|dump)(/|$) { return 404; }
```
A âncora `^/` **não** casa `/api/admin/*` (começa com `/api/`) nem `/painel/`.
(`data` foi deixado de fora por ser genérico demais.)

## Validação (Nginx descartável na VM, antes do deploy)

- `http.conf`: `/backup/ /uploads/ /admin/ /staging/ /test/ /debug/ /secret/` → **404**;
  `/ /sobre /painel/login /recuperar /api/health` → **200**;
  **`/api/admin/reclassify-status` → 401 (JWT, NÃO 404)**; `/mcp/sse` → 401; `nginx -t` OK.
- `https.conf.template` (render via envsubst + certs reais montados): `nginx -t` OK;
  **HSTS presente em `/assets/x.js`** (até no 404, via `always`) e em `/`;
  `/backup/` → 404; `/api/admin/reclassify-status` → 401.

## Testes

- `tests/test_mcp_server.py` (+3): payload de XSS não aparece no HTML e o
  `callback_url` inseguro vira `value=""`; callback confiável (localhost) é refletido;
  o mesmo via endpoint `GET /mcp/auth?callback_url=…`. **Suite: 216 passed, 1 skipped.**

## Checklist pós-deploy (a rodar depois do CI)

1. `curl -sI https://klarim.net/<asset>.js | grep strict` → HSTS presente.
2. `/backup/` → 404; `/sobre` → 200; `/api/health` → 200; `/painel/login` → 200.
3. `?callback_url="><script>` → escapado + `value=""`.
4. **Self-scan `python -m scanner.main https://klarim.net` → 100/100.**
5. MCP (`/mcp/`, `/mcp/sse`) e login do painel intactos; ACME (`^~`) não afetado.

## Notas

- O gotcha de **herança de `add_header`** no Nginx é a lição: qualquer `location` com
  `add_header` próprio precisa **repetir** os headers de segurança (documentado no
  `claude.md` seção 10).
- A CSP e o `X-Frame-Options: DENY` do site foram mantidos (não troquei para
  `SAMEORIGIN`) — `DENY` é mais restritivo e o self-scan aprova.
