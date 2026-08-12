# Relatório — Compactação do CLAUDE.md

**Data:** 2026-08-12 · **Tarefa:** pré-requisito de compactação (limite 150k, target <120k)

## Tamanho antes/depois

| | chars | linhas |
|---|---|---|
| **Antes** | 227.519 | 2.382 |
| **Depois** | 50.552 | 362 |
| **Redução** | **−77%** | −85% |

Muito abaixo do target de 120k → margem ampla para crescer. `git diff`: 270 inserções, 2.290 deleções.

> ⚠️ **Nota de tracking:** o arquivo é rastreado pelo git como **`claude.md`** (minúsculo). No
> filesystem case-insensitive do macOS é o mesmo arquivo que `CLAUDE.md`; `git status` mostra
> `M claude.md`. Nenhum teste referencia o arquivo (verificado).

## O que MANTIVE (informação viva)

Verificado por keyword-check (20/20 presentes): `klarim:scan_queue`, `POSTGRES_*`, flush `scan:*`,
`content_guard`, `is_safe_to_send` (3 filtros), `CF-Connecting-IP`, `ssl_reject_handshake`,
`_target_filters`, `parseUTC`, "sem Alembic", `FakeStore`, `validate_cpf`, `address_data`,
`gate_rate_limiter`, `COALESCE` (NULL-safe), `account_level`, `_filter_scan_result`,
`PROFILE_VIEW_DAILY_LIMIT`, `email_health_by_domain`, `score_100_count`.

- Estado atual: stack, containers, portas, env vars operacionais, schema/tabelas vigentes.
- Endpoints ativos (paths, sem request/response completo — esse detalhe fica em `docs/API.md`).
- Convenções de código (ensure_schema sem Alembic, testes offline/FakeStore, padrão puro-testável, auth por `typ`).
- Regras do scanner passivo (timeout, rate limit, user-agent honesto).
- Gotchas operacionais (flush Redis, DATABASE_URL com `/`, nginx allowlist/headers, docker exec proibido, NULL-safe COALESCE).
- **Fontes autoritativas de métrica** (KL-95/136/150) — bloco preservado integralmente.
- **Regra de e-mail vigente** (KL-145: 3 filtros locais) + mapa de remetentes + circuit breaker hard/soft.
- **Auth nas páginas Astro** (cookie HttpOnly, serverAuth, SSR vs CDN).
- **Security Gate** — reescrito como seção de estado ATUAL (schema, planos, KYC, rate limit por camada, endpoints).

## Seções REMOVIDAS/MOVIDAS

- **Histórico de deploys** (runs, datas, verificações pós-deploy) → **`claude/DEPLOY_HISTORY.md`** (novo).
- **Narrativa de implementação por card** (§9 antiga, ~1.760 linhas) → colapsada. O "estado" foi
  promovido para as seções vivas; o rastro virou um **índice de 1 linha por card** (§11) apontando
  para `claude/reports/` e `docs/HISTORY.md`.
- **Evolução do pipeline de e-mail** (KL-108..KL-137, ~10 cards de idas e vindas) → substituída pela
  **regra viva única** (§4). Os cards superados ficam listados no índice marcados como "superados pelo KL-145".

## Seções COMPACTADAS

- **§3 Frontend** — prosa → bullets; unificada com regras de CSP/tema/responsivo/container/auth-Astro.
- **§4 E-mail** — de ~150 linhas de narrativa histórica → tabela de remetentes + regra de envio atual + cold/profile_view/lead-scoring/webhook em bullets.
- **§5 Arquitetura** — containers/nginx/scanner/workers/GCS/tech-detect/access-log/planos/MCP em bullets densos.
- **§9 Subsistemas** — NOVA seção de estado vigente (métricas, scan por nível, conta/níveis, owner mgmt, admin, conteúdo/SEO, LGPD, Security Gate) substituindo a narrativa de cards.
- **§10 Gotchas** — mantidos os operacionais, deduplicados os que repetiam seções de cima.

## Estrutura final (11 seções)

1 Links · 2 Stack · 3 Regras invioláveis · 4 E-mail · 5 Arquitetura · 6 Diretórios ·
7 Convenções · 8 Estado atual · 9 Subsistemas atuais · 10 Gotchas · 11 Índice de cards.

## Validação

- `wc -c CLAUDE.md` = 50.552 < 120.000 ✅
- Nenhum teste referencia CLAUDE.md (`grep -rl` em `tests/` = vazio) → CLAUDE.md é documentação, não código; pass/fail dos testes é idêntico ao baseline (zero mudança de código).
- Estrutura markdown íntegra (11 headers `##`, sem seção órfã).
- Marcador de pipeline `# KL-124 pipeline test:` preservado ao final.
