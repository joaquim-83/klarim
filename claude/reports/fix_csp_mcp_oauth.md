# Fix CSP — página de login OAuth do MCP bloqueada

**Prioridade:** URGENTE (bloqueava a conexão OAuth do KL-63)
**Data:** 2026-07-15
**Arquivo:** `frontend/nginx/https.conf.template`

## Sintoma

Ao autorizar no `/mcp/authorize`, o browser bloqueava o envio do formulário de login:

```
Sending form data to '<URL>' violates the following Content Security Policy
directive: "form-action 'self'".
```

## Diagnóstico (causa-raiz)

A CSP compartilhada (`frontend/nginx/security_headers.conf`, incluída no `server` block)
tem **`form-action 'self'`**. O `location /mcp/` **não** tinha `add_header` próprio, então
**herdava** essa CSP.

O `<form>` da página de login POSTa em `/mcp/authorize` (**self ✓**), mas ao autorizar o
servidor responde **302 redirecionando para o callback do cliente MCP** — que é
`http://localhost:<porta>/callback` (Claude Desktop/CLI, loopback) ou `https://…` (Claude
web). O browser aplica o `form-action` **também ao alvo do redirect** durante o envio do
form → o callback **não** é `'self'` → **bloqueado**. (A validação real do `redirect_uri`
contra o registrado continua no servidor — `oauth.valid_redirect_uri` + match com o
client registrado; a CSP é só a barreira do browser.)

## Fix

Location **dedicada** (regex, vence o prefixo `/mcp/`) só para as 3 rotas do fluxo OAuth
(`/mcp/authorize`, `/mcp/token`, `/mcp/register`), com **CSP própria** cujo `form-action`
libera **self + https + loopback**:

```nginx
location ~ ^/mcp/(authorize|token|register)(/|$) {
    ... proxy p/ api:8000 (mesmo resolver dinâmico) ...
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline';
      style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';
      base-uri 'none'; frame-ancestors 'none';
      form-action 'self' https: http://localhost:* http://127.0.0.1:*" always;
    add_header Strict-Transport-Security ... ; add_header X-Frame-Options "DENY";
    add_header X-Content-Type-Options "nosniff"; add_header Referrer-Policy "no-referrer";
}
```

- **`form-action 'self' https: http://localhost:* http://127.0.0.1:*`** cobre o POST (self)
  + o redirect para o callback (loopback dos clientes desktop/CLI e https do Claude web).
- ⚠️ **Herança do Nginx:** um `add_header` próprio no location **quebra a herança** de
  TODOS os headers do server (gotcha do §10 do `claude.md`) → os headers de segurança
  essenciais são **re-declarados** aqui (HSTS, X-Frame-Options `DENY`, X-Content-Type-
  Options, Referrer-Policy `no-referrer` — não vazar `code`/`state` no referer).
- **`script-src 'self' 'unsafe-inline'`** e **`style-src 'self' 'unsafe-inline'`** liberam
  o HTML da página de login (só usa estilos inline; sem script inline hoje, mas fica
  preparado). A página é `noindex`, operador-único, e todos os valores são escapados
  (anti-XSS) no `oauth.py`.
- O **SSE** (`/mcp/sse`, `/mcp/messages/`) continua no `location /mcp/` (inalterado).

**Aplicado nos dois server blocks** de `https.conf.template` que têm `/mcp/`: o
**principal** (`klarim.net`) e o **subdomínio do painel** (`painel.klarim.net`). O
`http.conf` (fallback sem-cert) **não** tem CSP nenhuma, logo não tem o bug — não foi
alterado.

## Validação

- `nginx -t` na config renderizada (com cert dummy + o include dos security headers) →
  **sintaxe OK** (mesmo teste do job `nginx-check` do CI). A location OAuth aparece **2×**
  (principal + painel).
- Pós-deploy: abrir `https://klarim.net/mcp/authorize?...` no browser → console **sem**
  erro de CSP; o POST de login redireciona ao callback sem bloqueio.

## Regra preservada

O `location /mcp/` do SSE fica intacto (buffering off, `access_log off` por causa do
`?token=`). A location OAuth é **regex** e tem prioridade sobre o prefixo `/mcp/` só para
as 3 rotas do fluxo. A validação de `redirect_uri` no servidor (anti open-redirect) é a
barreira primária; a CSP `form-action` é defense-in-depth do lado do browser.
