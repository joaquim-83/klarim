# KL-104 Parte 2 — Filtros avançados na página Alvos do admin

**Card:** KL-104 (High) · **Parte 2 de 3** · **Status:** ✅

## Objetivo
Acabar com a limitação da lista de alvos (`/painel/alvos`), que só filtrava por
status/plataforma/setor/origem/busca. Adicionar **10 filtros novos** que combinam com **AND**,
uma **barra de totais** ("N encontrados de X"), **URL com query params** (deep-link/bookmark) e o
tracking de **quais combinações são usadas** (KL-57). Tudo parametrizado (zero SQL injection).

## Entregue

### Backend — `discovery/store.py`
- **`TargetStore._target_filters(f)`** (staticmethod PURA, testável) — o coração da mudança:
  recebe o dict de filtros → devolve `(where_clauses, params)`. **Todo valor de input vira
  parâmetro `%s`** (nunca interpolado); valores desconhecidos são **ignorados** (não quebram a
  query). Compartilhado por `list_targets` **e** `count_targets_filtered` → a contagem bate
  exatamente com a listagem.
  | Filtro | Cláusula |
  |---|---|
  | `score` | `t.last_scan_score BETWEEN 0/50/90 AND 49/89/100` · `sem` → `IS NULL` |
  | `semaphore` | derivado do score: verde `>=90` · amarelo `>=50 AND <90` · vermelho `<50` · sem `IS NULL` |
  | `lead_score` | `t.alert_quality_score` alto `>=60` · medio `30-59` · baixo `<30` · sem `IS NULL` |
  | `has_email` | `(contact_email IS NOT NULL AND <> '')` / negação (3-estados) |
  | `monitored` | `EXISTS (SELECT 1 FROM user_sites us WHERE us.target_id=t.id)` / `NOT EXISTS` |
  | `owner_verified` | `t.owner_verified = TRUE` / `COALESCE(...,FALSE)=FALSE` |
  | `has_ai_profile` | `EXISTS (SELECT 1 FROM site_profile sp WHERE ... description <> '')` / negação |
  | `site_type` | `t.site_type = ANY(%s)` (array parametrizado, multi) |
  | `last_scan` | `nunca` (`IS NULL`) · `hoje` (`>= date_trunc('day',NOW())`) · `7d`/`30d` (`INTERVAL`) |
  | `tech` | `EXISTS (... site_tech_stack st WHERE st.name = ANY(%s))` (array, multi, EXISTS lazy) |
  Os 5 filtros antigos (status/platform/sector/source/low_confidence/search) foram **movidos para
  dentro** do mesmo helper — comportamento idêntico (a busca segue `%LIKE%` em url/domain/email,
  lowercased, parametrizada).
- **`list_targets(..., **filters)`** refatorado para usar o helper. Assinatura retrocompatível
  (os args nomeados dos 5 filtros antigos continuam; a tool MCP `list_targets` usa keyword args).
- **`count_targets_filtered(**filters)`** — `COUNT(*)` com os MESMOS filtros.
- **`top_technologies(limit=20)`** — top tecnologias por frequência (nome) p/ o dropdown.
- **3 índices parciais** (após `idx_targets_site_type`): `idx_targets_last_scan_score`,
  `idx_targets_last_scan_at`, `idx_targets_owner_verified` (todos `WHERE ... IS NOT NULL/TRUE`).

### Backend — `api/main.py`
- **`GET /targets`** ganhou 10 params opcionais (`score, semaphore, lead_score, has_email,
  monitored, owner_verified, site_type, last_scan, tech, has_ai_profile`). Monta o dict `filt`,
  chama `list_targets(**filt)` + `count_targets_filtered(**filt)`. Response agora inclui
  **`total`** (filtrado) + **`total_all`** (geral, cache Redis 1h) + `page`/`per_page`.
- **`_targets_total_all()`** — `count_targets()` cacheado (`targets:total_all`, TTL 3600); é o
  denominador da barra de totais (não muda a cada request).
- **`GET /targets/tech-list`** — top-20 tecnologias (cache `targets:tech_list`, TTL 1h). Registrado
  **antes** de `/targets/{target_id}` → sem colisão de rota.
- Evento **`admin_filter_used`** adicionado ao `_KNOWN_EVENTS` (gate do `POST /events`).

### Frontend
- **`web/src/lib/admin/alvosFilters.js`** (PURO, node-testável): `readFiltersFromURL` /
  `filtersToQueryString` / `filtersToApiParams` / `activeFilterCount` / `nextToggle` (3-estados) /
  `toggleMultiValue` (CSV) / `multiValues`. É o contrato URL ⇄ estado ⇄ params.
- **`web/src/components/admin/AlvosFilters.jsx`** — a barra de filtros: **linha 1** sempre visível
  (Status · Setor · Score · Semáforo · Tem email · Busca · botão "Filtros avançados"); **linha 2**
  colapsável (Lead score · Site type multi · Tecnologia multi+busca · Último scan · Monitorado ·
  Dono verificado · Perfil IA · Plataforma · Origem · Classificação incerta). Toggles 3-estados
  (cinza=todos / verde=sim / vermelho=não), multi-selects via `<details>` nativo (CSP-safe, sem
  lib). **Barra de totais**: "**N** alvos encontrados (de X)" + "Limpar filtros ✕" (some quando
  não há filtro ativo). A linha 2 já abre expandida se um deep-link trouxe filtro avançado.
- **`AlvosPage.jsx`** — os 15 filtros num único objeto `filters` sincronizado com a **URL**
  (`replaceState`, não polui histórico). `setFilter`/`clearFilters` resetam a página. Fetch com
  os params debounced (300ms → coalesce de teclas/cliques). Busca o `tech-list` (cacheado).
  Registra `admin_filter_used` (fire-and-forget, keepalive, sem PII — só os NOMES dos filtros +
  combo) a cada combinação nova. Paginação agora usa o `total` (exata, não "cheio = tem próxima").
- **`adminApi.js`** — `admin.techList()`.

## Segurança
- **Injeção:** 100% parametrizado. Todo input (search, arrays de site_type/tech, valores de score
  etc.) vira `%s`/`ANY(%s)`; valores fora do dicionário são **descartados** (nunca chegam ao SQL).
  Teste `test_injection_safe` confirma que `'; DROP TABLE targets; --` vira parâmetro, nunca SQL.
- Endpoint admin segue sob **JWT admin** (nada relaxado). `contact_email`/cnpj/whatsapp **nunca**
  no response (o SELECT devolve `t.*`, mas o filtro `has_email` só testa presença; o front não
  exibe e-mail que não seja o já mostrado hoje). O evento `admin_filter_used` não carrega PII.

## Performance
- Validado no **Postgres de produção**: filtro combinado (score 90-100 + has_email + tech
  WordPress) rodou em **11ms** (alvo do card: <2s p/ 50k). Os EXISTS de tech/monitored/perfil só
  entram na query quando o filtro está ativo (lazy); índices parciais cobrem score/last_scan/owner.

## Testes
- Backend `test_kl104p2_filters.py` (**+11**): cada filtro → cláusula+params, combinação AND,
  3-estados, arrays parametrizados, janelas de tempo, valores inválidos ignorados, injeção,
  filtros antigos intactos. `test_target_edit.py` atualizado (FakeStore: `**filters` +
  `count_targets_filtered`/`count_targets`).
- Frontend `alvosFilters.test.js` (**+9** `node --test`): ida-e-volta URL⇄estado, bools 3-estados,
  params da API, contagem de ativos, toggles, multi-CSV.
- **Suite:** 1644 backend passed · 107 `node --test` · build Astro OK.

## Validação pós-deploy (a fazer)
Painel → Alvos: aplicar filtros (score, semáforo, tem email, tecnologia…), conferir a barra
"N de X", o deep-link (recarregar com a URL preserva os filtros), "Limpar filtros", e a paginação.
MCP `get_analytics_metrics`/eventos → `admin_filter_used` aparece com os combos usados.

## Não coberto (Parte 3)
Visão 360° do alvo (monitoramento, funil, comportamento por-alvo, timeline) — próxima parte.
