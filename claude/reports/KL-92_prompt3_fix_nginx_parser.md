# KL-92 — Fix bloqueador + Parser Nginx (Prompt 3)

**Card:** KL-92 · **Tipo:** fix bloqueador (P0) + gap de cobertura (P1)
**Status:** Implementado (aguardando deploy verde) · **Data:** 2026-07-20

---

## 1. Contexto

A validação em produção do Prompt 2 revelou 2 problemas:

1. **P0 (bloqueador):** `al_hourly_heatmap` usava `hour` como **alias SQL sem aspas** — `hour`
   é palavra-chave do PostgreSQL → **syntax error** → `server-metrics` retornava **500** → 5 de 6
   cards da Visão Geral + Tendência + Comportamento quebrados.
2. **P1 (gap de cobertura):** o middleware FastAPI só vê o tráfego da API (~12%). As páginas Astro
   (landing, `/scan`, `/site/*`, `/setor/*`) passam pelo Nginx **direto** ao container Astro sem
   tocar no FastAPI → `server-metrics` mostrava ~12 visitantes vs. ~100 reais (Cloudflare).

---

## 2. P0 — Fix do alias SQL reservado

`discovery/store.py::al_hourly_heatmap`:

```diff
- SELECT EXTRACT(DOW FROM created_at)::int dow, EXTRACT(HOUR FROM created_at)::int hour, COUNT(*) n
- ... GROUP BY dow, hour
+ SELECT EXTRACT(DOW FROM created_at)::int AS dow, EXTRACT(HOUR FROM created_at)::int AS hr, COUNT(*) AS n
+ ... GROUP BY 1, 2
```

**Validado contra PostgreSQL 16 real** (container throwaway): a query antiga dá
`ERROR: syntax error at or near "hour"`; a nova roda. O **GROUP BY posicional** (`1, 2`) é à prova
de reservada — não depende do nome do alias.

**Sweep** de outros aliases reservados em `store.py`: nenhum outro problemático (o
`al_server_metrics` usa `::int h` — `h` não é keyword; `al_daily_series` usa `::date d`). Todos os
6 métodos `al_*` de comportamento foram re-validados contra o Postgres 16 real (funil, série,
top-domínios, jornada, retenção, heatmap, anonimização LGPD) — todos OK.

---

## 3. P1 — Parser de access_log do Nginx (cobertura completa)

### Decisão de arquitetura: **hybrid** (não desliguei o middleware)

O card sugeria desligar o middleware e usar só o parser. **Optei por manter o middleware** (a opção
"OU manter" do card), porque:

- O middleware tem o **`user_id`** (decodifica o JWT) e dispara a **retroatividade**
  (`mark_ip_human_today`) nas ações humanas — que são todas `/api` (`/scan/result`,
  `/account/signup`, `/report/pdf`). Sem ele, o funil perderia essas contagens/relações.
- Para **não duplicar**, o parser processa **apenas** páginas **não-`/api`/`/mcp`**. Conjuntos
  disjuntos → zero duplicata, sem query de dedup.

Resultado: middleware cobre `/api`+`/mcp` (com user_id), parser cobre as páginas Astro (o que
faltava). Cobertura completa.

### Peças

- **`frontend/nginx/log_format.conf`** — `log_format klarim` (com `$http_cf_connecting_ip` +
  `$http_cf_ipcountry`) + `access_log /var/log/klarim/access.log klarim;`. Fica no **contexto
  http** (via `conf.d/00-klarim-log.conf`). Os **server blocks (http.conf/https.conf.template)
  ficam intactos** → o job `nginx -t` do CI continua verde sem mudança. O `access_log` do stdout
  (formato `main`, do nginx.conf base) segue ativo → `docker logs` intacto.
- **`frontend/Dockerfile`** — `COPY log_format.conf → conf.d/00-klarim-log.conf` + `mkdir -p
  /var/log/klarim`.
- **`docker-compose.yml`** — volume `klarim-nginx-logs` compartilhado: `web` (rw, escreve) →
  `api` (rw, o parser lê + trunca).
- **`discovery/store.py`** — coluna `access_log.source VARCHAR(20) DEFAULT 'middleware'`;
  `log_access_batch` inclui `source` (parser → `'nginx'`).
- **`api/bot_classifier.py::classify_bot_simple(ip, ua, country)`** — classificação SEM contexto
  de request (sem rate/endpoint): IP próprio → datacenter → crawler → **US=`prefetch_likely`**. A
  retroatividade do middleware corrige um eventual falso-positivo.
- **`api/nginx_log_parser.py`** — `parse_line` (pura: regex do log_format, pula assets +`/api`
  +`/mcp`, extrai domínio reusando `extract_domain`, valida IP, `source='nginx'`) +
  `NginxLogParser` (leitura **incremental** por offset; detecta **rotação** por inode e truncação
  externa; ao passar de **50 MB** trunca — seguro pois o Nginx abre logs em `O_APPEND`). Loop de
  **30s** no lifespan. Fail-safe (erro de I/O/DB é logado e engolido).

### Validação local (docker)

- `nginx -t` do config **completo** (log_format.conf + http.conf E + https.rendered.conf +
  security_headers) → **syntax is ok** nos dois.
- Runtime: subi um nginx com o log_format, fiz curls com `CF-Connecting-IP`/`CF-IPCountry`, e
  confirmei que o `parse_line` casa **exatamente** as linhas geradas (contrato log_format ↔ regex),
  incluindo a classificação (BR humano; IP de datacenter US → bot).

---

## 4. Antes / depois esperado (pós-deploy)

| | Antes (P2 quebrado) | Depois (P3) |
|---|---|---|
| `server-metrics` | **HTTP 500** (alias `hour`) | HTTP 200 com heatmap |
| Visitantes BR | ~12 (só API) | ~80–100 (páginas Astro capturadas) |
| Top endpoints | só `/mcp`, `/events`, `/public/*` | + `/`, `/scan`, `/site/*`, `/setor/*` |
| Dashboard Visão Geral | 5/6 cards "indisponível" | 6 cards com valor |
| Comportamento | heatmap quebrado | heatmap renderiza |

---

## 5. Testes

- **`tests/test_kl92_nginx_parser.py`** — 26 testes: `classify_bot_simple` (datacenter/crawler/US/
  BR/próprio/vazio), `parse_line` (site/scan/setor/skip-api/skip-mcp/skip-asset/ip-inválido/US-bot/
  malformada/rt-dash), `NginxLogParser` (insere, incremental, pula api+assets, detecta rotação,
  arquivo ausente=no-op, truncação reseta offset).
- **`tests/test_kl92_access_log.py`** — +2 guardas: o fix P0 (`AS hr`/`GROUP BY 1, 2`, sem
  `::int hour`) e a coluna `source` no INSERT.
- **Total novo: 28** (mínimo do card: 12). Suíte completa: **1428 passed**.

---

## 6. Arquivos

**Novos:** `frontend/nginx/log_format.conf`, `api/nginx_log_parser.py`,
`tests/test_kl92_nginx_parser.py`.

**Alterados:** `discovery/store.py` (fix P0 + coluna `source` + `log_access_batch`),
`api/bot_classifier.py` (`classify_bot_simple`), `api/main.py` (loop do parser no lifespan),
`frontend/Dockerfile` (conf.d + mkdir), `docker-compose.yml` (volume `klarim-nginx-logs`),
`tests/test_kl92_access_log.py` (+2 guardas), `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.

---

## 7. Validação na VM (pós-deploy)

1. `curl -H "Authorization: Bearer $TOKEN" localhost:8000/admin/analytics/server-metrics?period=7d`
   → **HTTP 200** com `hourly_heatmap` populado.
2. Dashboard Visão Geral: 6 cards com valor, zero "indisponível"/500; Comportamento com heatmap.
3. Visitantes BR sobe de ~12 → ~80–100 conforme o parser drena o log do Nginx.
4. `SELECT source, COUNT(*) FROM access_log GROUP BY source` → aparece `nginx` além de `middleware`.
5. Top endpoints inclui `/`, `/scan`, `/site/*`, `/setor/*`.

---

## 8. Risco & mitigação

A mudança toca infra sensível (Nginx + docker-compose). Mitigações: os **server blocks não
mudaram** (CI `nginx -t` cobre); o `log_format.conf` foi validado com `nginx -t` local (HTTP+HTTPS)
e em runtime; o volume novo não afeta os existentes; o parser é fail-safe (arquivo ausente = no-op).
Se o parser falhar, o middleware (Prompt 1) segue populando o access_log normalmente.
