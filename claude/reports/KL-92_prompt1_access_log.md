# KL-92 — Tracking Server-Side por IP (Prompt 1 de 2)

**Card:** KL-92 · **Prioridade:** Highest · **Status:** Implementado (aguardando deploy verde)
**Data:** 2026-07-20

---

## 1. Problema

O `tracker.js` (client-side) infla os visitantes ~5x porque **pre-fetches de e-mail executam
JavaScript** no browser do bot. O KL-64 tentou corrigir com detecção de interação humana, mas
qualquer defesa client-side depende de código que roda no browser do bot. A **fonte de verdade**
das métricas de visitante tem que ser o **servidor**, que vê o IP real (`CF-Connecting-IP`) e
classifica bot/humano sem depender do client.

Este Prompt 1 cria a **infraestrutura server-side**: tabela `access_log`, middleware de captura,
classificação de bot e 3 endpoints de analytics. O tracker.js **continua existindo** para eventos
de interação frontend (não foi removido). O Prompt 2 adiciona queries de comportamento e migra o
dashboard para o `access_log` como fonte primária.

---

## 2. O que foi entregue

### 2.1 Tabela `access_log` (`discovery/store.py::_SCHEMA`, idempotente)

`id BIGSERIAL`, `ip_address INET NOT NULL`, `country_code`, `endpoint`, `http_method`,
`http_status`, `domain_queried`, `user_id`, `user_agent`, `referrer`, `response_time_ms`,
`is_bot`, `bot_reason`, `created_at`. **6 índices** (`ip`, `date`, `domain`, `user`,
`(is_bot, created_at)`, `(country_code, created_at)`). Criada no `ensure_schema` — sem Alembic.

### 2.2 Middleware `api/access_log_middleware.py`

- Registrado **depois** do auth em `api/main.py` → fica **OUTERMOST** → enxerga o status final
  (inclusive 401 de tentativa de bot).
- **Skip de assets** (`should_log`): `SKIP_PREFIXES` (`/_astro/`, `/assets/`, `/track.js`,
  `/favicon`, `/.well-known/`, …) + `SKIP_EXTENSIONS` (`.js`, `.css`, `.png`, `.woff2`, …).
- **Extração:** IP real (`CF-Connecting-IP` → `X-Real-IP` → peer), país (`CF-IPCountry`), user_id
  (JWT de usuário no cookie/Bearer), `domain_queried` (`/site/{domain}`, `/scan?url=`, ou
  `request.state.domain_queried` que o handler pode setar para POSTs — o body **não** é lido no
  middleware). Latência medida com `time.monotonic()`.
- **Fire-and-forget:** a captura é síncrona e barata; o processamento pesado (classificação +
  contador Redis + enfileiramento) roda em background (`_spawn`) → o response volta na hora.
- **Buffer + flush em batch:** cada registro entra num buffer em memória; um loop (`_flush_loop`,
  iniciado no `lifespan`) drena em **batch INSERT** (`log_access_batch`) a cada **5s**. Cap de
  segurança de 10.000 no buffer (descarta os mais antigos). Erro de banco = lote descartado (sem
  loop infinito), **nunca bloqueia o request**.
- **Resiliência:** toda a lógica de log está sob `try/except` fora do caminho síncrono. Se o Redis
  estiver fora, a classificação de rate/pré-fetch **pula** (fail-open). Se o Postgres estiver fora,
  o log é perdido — o response nunca é afetado.

### 2.3 Classificação de bot `api/bot_classifier.py` (função PURA)

`classify_bot(ip, user_agent, country, endpoint, request_count_last_hour, user_id,
has_other_requests) → (is_bot, bot_reason)`. Ordem (1ª regra que casa vence):

1. **IP próprio** (`34.135.194.208` + `KLARIM_OWN_IPS`) → humano (self-scan/healthcheck/cron).
2. **Usuário autenticado** (`user_id` presente) → humano (logou com senha).
3. **Datacenter** (`is_datacenter_ip`, ~30 CIDRs estáticos AWS/GCP/Azure/DigitalOcean/Hetzner,
   pré-compilados, **sem lookup externo**) → `datacenter_ip`.
4. **Crawler declarado** no User-Agent (`googlebot`, `bytespider`, `python-requests`, `curl/`, …)
   → `crawler_ua`.
5. **Rate anormal** (>50 req/h) sem conta → `high_rate` (contador Redis `access_rate:{ip}`, TTL 1h).
6. **Padrão de pré-fetch** (país US + `/site/*` + sem navegação prévia) → `prefetch_pattern`.

**Retroatividade** (`HUMAN_ACTIONS` + `store.mark_ip_human_today`): quando um IP executa uma ação
humana (scan/signup/login/PDF/evento), **todos** os registros daquele IP no dia viram não-bot
(`bot_reason='retroactive_human'`) — corrige o dev/cliente real atrás de um IP de datacenter.

> **Gotcha resolvido:** o Nginx faz `rewrite ^/api/(.*)$ /$1` → o middleware vê os paths **sem** o
> prefixo `/api` (`/scan/result`, `/events`, `/account/signup`). `HUMAN_ACTIONS` e a extração de
> domínio usam os paths já sem prefixo.

### 2.4 Endpoints admin (`api/admin_analytics.py`, cache Redis 5 min, rate 30/min/IP)

| Path | Conteúdo |
|---|---|
| `GET /admin/analytics/server-metrics` | visitantes BR/total (IPs únicos, `is_bot=false`), `bots_filtered`, scans, contas, PDFs, `alert_clicks_br`, `profiles_viewed_br`, `unique_domains_queried`, `top_countries`, `top_endpoints`, `hourly_distribution` (24h densa) |
| `GET /admin/analytics/ip-behavior` | `multi_site_visitors`, `returning_visitors`, `avg_sites_per_visitor`, `top_multi_site_ips`/`top_returning_ips` com **`ip_masked`** (1º octeto) |
| `GET /admin/analytics/ip-detail?ip=` | dossiê de 1 IP (aceita IP completo, admin-only; 422 se inválido): first/last seen, dias ativos, domínios, ações, user_id, is_bot, timeline. `ip` no response **mascarado (2 octetos)** |

Arquitetura testável (padrão KL-83): agregações **brutas** (SQL) em `discovery/store.py`
(`al_server_metrics`/`al_ip_behavior`/`al_ip_detail`); **derivação pura** (expansão horária,
mascaramento LGPD) no módulo (`assemble_server_metrics`/`assemble_ip_behavior`), unit-testada.

### 2.5 MCP tools (3 novas, `mcp_server/tools/analytics.py`)

`get_server_metrics` (sem `hourly_distribution` — economia de tokens), `get_ip_behavior`,
`get_ip_detail(ip)`. Total de MCP tools: **61+**.

### 2.6 LGPD

- IP é dado pessoal → **retenção 90 dias**; depois `anonymize_old_access_logs` (loop diário no
  `lifespan`) trunca o último octeto (`set_masklen(ip, 24)`), idempotente (só IPv4 `/32`).
- Nos responses da API o IP volta **mascarado**; o completo fica só no banco.
- Nunca logamos body de POST (senhas/dados sensíveis) — só path + metadados.

---

## 3. Testes

**Arquivo:** `tests/test_kl92_access_log.py` — **77 testes offline** (mínimo do card: 30).

Cobrem: classificação (datacenter AWS/GCP/DO/Hetzner, IP próprio, crawler, rate, pré-fetch, usuário
autenticado, IP inválido, retroatividade, override por env), helpers do middleware (skip de assets,
extração de IP/país/domínio, mascaramento 1/2 octetos + IPv6), buffer/flush (grava em batch, drena,
engole erro de banco), processamento em background (classifica + retroatividade), integração via
TestClient (loga request não-estático, ignora asset, **fail-safe** quando `_capture` levanta), os 3
endpoints (auth 401, shape, mascaramento, 422 de IP inválido, not-found) e o contrato dos métodos
de store (LGPD/batch).

**Fix de isolamento:** o rate bucket in-memory do `admin_analytics` (`_rl_bucket`) vazava estado
entre arquivos de teste (KL-83 + KL-92 juntos passavam de 30 req/min do mesmo IP do TestClient →
429). Adicionado `_aa._rl_bucket.clear()` ao `tests/conftest.py` (autouse).

```
tests/test_kl92_access_log.py ......................................... 77 passed
Suite completa: 1390 passed, 1 skipped
```

---

## 4. Validação na VM (pós-deploy)

O SQL dos métodos `al_*`, `log_access_batch`, `mark_ip_human_today` e `anonymize_old_access_logs`
segue o padrão dos demais `aa_*` (validado na VM). Checklist pós-deploy:

1. Confirmar `access_log` criada com os 6 índices (`\d access_log`).
2. Confirmar que requests não-estáticos geram registros e que assets **não** geram.
3. Conferir `server-metrics` mostrando visitantes BR **reais** (~100-200), não os 4.000+ inflados.
4. Conferir `ip-behavior` (multi-site / returning) e `ip-detail` (timeline).
5. Flush do Redis `scan:*` **não** é necessário (não mexe em scoring/checks).

---

## 5. Arquivos

**Novos:** `api/access_log_middleware.py`, `api/bot_classifier.py`,
`tests/test_kl92_access_log.py`.

**Alterados:** `discovery/store.py` (tabela + 6 métodos), `api/main.py` (registro do middleware +
loop de flush/anonimização no lifespan), `api/admin_analytics.py` (3 endpoints + 2 assemble puros),
`mcp_server/tools/analytics.py` (3 tools), `tests/test_mcp_server.py` (+3 na lista),
`tests/conftest.py` (isolamento do rate bucket), `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.

---

## 6. Dados gerados (regra KL-57)

- Visitantes reais por país (fonte confiável vs. tracker inflado).
- Padrões de pré-fetch por provedor de e-mail (via `bot_reason='prefetch_pattern'`).
- Correlação IP datacenter × ação humana (falsos-positivos → `retroactive_human`).
- Distribuição horária de tráfego real (`hourly_distribution`).

---

## 7. Próximo (Prompt 2)

Queries de comportamento (sessões reconstruídas por IP, caminhos) e migração do dashboard admin
para usar o `access_log` como fonte primária das métricas de visitante.
