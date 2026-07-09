# Fix — 4 vulnerabilidades da auto-auditoria (o Klarim praticando o que prega)

**Tipo:** Correção de segurança (sem card Jira — urgente)
**Data:** 2026-07-08

Varredura manual pela perspectiva de uma empresa de cibersegurança que recebeu
nosso alerta e poderia nos testar de volta. Score 100/100 não basta — a superfície
de ataque real precisa ser mínima.

## Fix 1 — 🔴 CRÍTICO: docs/OpenAPI desligados em produção

O FastAPI expunha `/docs` (Swagger) e `/openapi.json` **sem auth** — 57 endpoints
mapeáveis num request. Agora o app só cria `docs_url/redoc_url/openapi_url` quando
`KLARIM_DEV_MODE=true`; em produção ficam `None` ⇒ **404**. (`api/main.py`, na
criação do `FastAPI`.)

## Fix 2 — 🟠 ALTO: rate limit no login

`POST /auth/login` aceitava brute force ilimitado. Novo `_login_rate_limit`
(dependency): **5 tentativas/min por IP** (janela deslizante, `X-Real-IP` do
Nginx). A 6ª → **429** com header `Retry-After`. Estado in-memory
(`_login_attempts`), com limpeza oportunista; nota no código para migrar a Redis
se escalar para múltiplos workers. Sequência validada: `401×5, 429, 429`.

## Fix 3 — 🟡 MÉDIO: sanitização anti stored-XSS no `/events`

`POST /events` (público) aceitava HTML/JS em `page_url`, `target_url`, `referrer`,
`utm_*` e `metadata` — o painel admin renderiza esses campos. Duas camadas:

- **Backend:** `_sanitize_str` (remove tags via regex, tira `javascript:`/`data:`/
  `vbscript:`, limita a 500 chars) e `_sanitize_metadata` (recursivo, profundidade
  ≤4, ≤50 chaves) aplicados a todos os campos-texto antes de gravar. `event_type`
  já era whitelist (`_KNOWN_EVENTS`).
- **Frontend:** confirmado que **não há `dangerouslySetInnerHTML`** — o React
  escapa `{}` por padrão.

## Fix 4 — 🟡 MÉDIO: Nginx 404 para paths sensíveis

`try_files … /index.html` fazia qualquer path retornar 200 com a SPA (`/.env`,
`/.git/config`, `/phpinfo.php`), confundindo scanners. Adicionados 3 `location`
regex (404) no `http.conf` e nos **2 server blocks 443** do `https.conf.template`:

- `location ~ /\.` → dotfiles (`.env`, `.git`, `.htaccess`…)
- `location ~* \.(php|sql|bak|log|ya?ml|toml|ini|conf|config)$` → extensões
- `location ~* ^/(phpinfo|server-status|server-info|wp-admin|wp-login|administrator|admin\.php)`

**Cuidado com o ACME:** regex tem prioridade sobre prefixo no Nginx, então o
`/.well-known/acme-challenge/` virou `location ^~ …` (prioridade sobre os regex) —
a renovação Let's Encrypt **não quebra**. `/api/` (sem ponto/extensão) e `/painel/`
(rotas da SPA) **não** são afetados. Ambas as configs passam `nginx -t` (validado
localmente com cert self-signed + upstream mockado).

## Testes

- `tests/test_security_hardening.py` (6): docs 404; login `401×5 → 429` + por-IP;
  `_sanitize_str`/`_sanitize_metadata`; endpoint `/events` sanitiza de ponta a ponta.
- `tests/conftest.py` (novo): autouse que zera `_login_attempts`/`_event_rl` entre
  testes (o rate limit é estado global; o TestClient reusa o mesmo IP).
- **Suíte: 145 passed, 1 skipped.** `nginx -t` OK nas duas configs.

## Validação em produção (pós-deploy)

| Item | Esperado |
|------|----------|
| `GET /api/docs` · `/api/openapi.json` · `/api/redoc` | 404 |
| 7× `POST /api/auth/login` (senha errada) | `401 401 401 401 401 429 429` |
| `GET /.env` · `/.git/config` · `/phpinfo.php` · `/debug.log` | 404 |
| XSS em `/events` (`page_url=<script>…`) | gravado sem tags |
| `/` · `/painel/login` · `/api/health` | 200 (normais) |
| self-scan `python -m scanner.main https://klarim.net` | mantém 100/100 |

## Arquivos

- `api/main.py` (Fix 1/2/3: app sem docs, `_login_rate_limit`+dependency,
  `_sanitize_str`/`_sanitize_metadata` no `/events`)
- `frontend/nginx/http.conf`, `frontend/nginx/https.conf.template` (Fix 4)
- `tests/test_security_hardening.py`, `tests/conftest.py` (novos)
- `CLAUDE.md` (seção 10 — hardening de segurança)
