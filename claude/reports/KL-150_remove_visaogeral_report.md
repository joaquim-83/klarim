# KL-150 — Fix Analytics: remover a aba "Visão geral"

**Data:** 10/08/2026 · **Escopo:** `/painel/analytics` (frontend) · **Regra:** implementar, testar e
**deploy direto**.

---

## 1. Problema

A aba **"Visão geral"** mostrava KPIs de visitante vindos do `access_log` (server-side) que **não batem
com o GA4** (GA4 = 4 visitantes, painel = 357). Além disso, as queries de `al_server_metrics`
(endpoint `server-metrics`) são **pesadas** e deixavam **todo o painel lento**. Remover a aba resolve os
dois problemas — mas a aba **"Comportamento"** (que vira a 1ª aba) **também** consumia `server-metrics`
(para "Domínios mais consultados" e "Mapa de calor"), então só remover a Visão geral **não** eliminaria
a query pesada nem satisfaria o teste 3 ("sem chamadas a server-metrics no load").

## 2. O que foi feito (tudo COMENTADO, reversível — nada deletado)

**Arquivo:** `web/src/components/admin/AdminAnalytics.jsx`

1. **Aba "Visão geral" removida do navegador de abas** — entrada `{ key: 'overview', label: 'Visão geral' }`
   comentada em `TABS`. Como `parseTabHash` cai no **1º item de `TAB_KEYS`** quando o hash é vazio ou
   inválido, a 1ª aba passa a ser **"Comportamento"** automaticamente (e um bookmark antigo `#overview`
   também cai em "Comportamento", sem quebrar).
2. **Render da Visão geral comentado** — o bloco `{nav.tab === 'overview' && (<OverviewTab … />)}` foi
   envolto em comentário. `OverviewTab`, `KpiGrid`, "Tendência" e `PeriodBanner` **continuam definidos**
   no arquivo (preservados para reativação).
3. **`server-metrics` desativado na aba "Comportamento"** (necessário para o teste 3 e para a meta de
   performance): a chamada `admin.aaServerMetrics(period)` e os dois blocos que dependiam dela
   (`TopDomainsBlock` = "Domínios mais consultados"; `HeatmapBlock` = "Mapa de calor") foram comentados.
   A aba segue com a inteligência de **`ip-behavior`** (Visitantes multi-site / Jornada pré-signup /
   Retenção) — que **não** usa os KPIs de visitante contestados. Os componentes `TopDomainsBlock` e
   `HeatmapBlock` **continuam definidos** (reversíveis).

**Backend intacto:** `get_server_metrics` e `get_analytics_metrics` (endpoints + MCP) **não** foram
removidos — seguem disponíveis para consultas pontuais via MCP, exatamente como pede o card.

## 3. Testes (todos os 4 do card, validados no navegador)

| # | Critério | Resultado |
|---|---|---|
| 1 | `/painel/analytics` abre sem a aba "Visão geral" | ✅ abas = `[Comportamento, Eventos, Páginas, Jornadas]` |
| 2 | 1ª aba visível é "Comportamento" | ✅ aba ativa = "Comportamento" (hash vazio) |
| 3 | Sem chamadas a `server-metrics`/`analytics-metrics` no load | ✅ no load só `ip-behavior` + `inbox/unread-count` (Network tab) |
| 4 | Carregamento mais rápido | ✅ `al_server_metrics` (query pesada) não é mais chamada no load |

- **Blocos renderizados na aba Comportamento:** Visitantes multi-site · Jornada pré-signup · Retenção
  pós-signup. **Sem** "Domínios mais consultados" e **sem** "Mapa de calor" (parkeados).
- **Console:** zero erros.
- **`npm run test:unit`** → **221 passed** · **`npm run build`** → Complete (zero falhas).
- **Backend:** nenhum arquivo `.py` alterado → suíte `pytest` inalterada (2321 passed no card anterior).

## 4. Reversão (quando houver GA4 API)

Descomentar, em `AdminAnalytics.jsx`:
1. a entrada `overview` em `TABS`;
2. o bloco `{nav.tab === 'overview' && (<OverviewTab … />)}`;
3. em `BehaviorTab`: a linha `const server = useAsync(() => admin.aaServerMetrics(period), …)` e os
   renders `<TopDomainsBlock server={server} />` / `<HeatmapBlock server={server} />`.

Nenhum componente/endpoint foi deletado.

## 5. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `web/src/components/admin/AdminAnalytics.jsx` | aba "Visão geral" + render comentados; `server-metrics` desativado na aba "Comportamento" (TopDomains/Heatmap parkeados) |

---

**Deploy:** commit + push + CI/CD (verificar pós-deploy que `/painel/analytics` abre em "Comportamento",
sem a aba "Visão geral" e sem chamada a `server-metrics` no load).
