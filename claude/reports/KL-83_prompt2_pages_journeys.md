# KL-83 — Redesign do Analytics Admin (Prompt 2 de 2) — Páginas + Jornadas

**Card:** KL-83 · **Prioridade:** High · **Data:** 2026-07-19
**Escopo:** frontend das Abas 3 (Páginas) e 4 (Jornadas), removendo os placeholders "Em breve".
**Zero mudança de backend** — os endpoints `/pages`, `/journeys`, `/funnel-by-sector`,
`/sessions` já estavam no ar (Prompt 1). Admin-only (painel operator, noindex) → risco ~0.

---

## Aba 3 — Páginas

Tabela completa (`SortableTable`) com 7 colunas (path, views, sessions, bounce, próxima, conv.,
Δ), consumindo `GET /admin/analytics/pages`:
- **Sorting client-side** (`sortRows`) em todas as colunas (menos "Próxima") — clicar no header
  alterna asc/desc com indicador ▲/▼ e `aria-sort`. Default views DESC.
- **Busca** debounce 300ms (param `search` no endpoint).
- **Agrupar por tipo:** grupos colapsáveis (`data.groups`, ordenados por total_views) com contagem
  "N views · M páginas"; dentro, as páginas do grupo ordenadas pelo sort ativo.
- **Cores:** bounce ≥70 vermelho/≥50 amarelo/<50 verde; conv. >0 badge verde; Δ `+n` verde /
  `-n` vermelho / `—` cinza (`deltaMeta`).
- **Paginação** client-side (`PaginationBar`, 25/50/100).
- **Navegação cruzada:** clicar numa página → `#events?path=<path>` (Aba 2 filtrada).

## Aba 4 — Jornadas (3 seções)

1. **Caminhos mais comuns** (`/journeys`, top 10): breadcrumb com chips coloridos por tipo de
   passo (`journeyStepKind`: `alerta`=azul/entrada, `[saiu]`=vermelho, `/cadastrar|/scan`=verde/
   conversão, demais=cinza), barra proporcional ao caminho #1, `(X% conv.)`.
2. **Funil por setor** (`/funnel-by-sector`): `SortableTable` ordenável (default click_rate DESC),
   `click_rate` colorida (≥15 verde/≥8 amarelo/<8 vermelho), setores sem alerta filtrados +
   máx. 20 (`filterSectors`, com "e mais N setores…").
3. **Sessões (drill-down)** (`/sessions`): reusa o `SessionCard` (converted-first), paginação
   "Próximas/Anteriores"; "**ver todas →**" navega para `#events?group=session`.

## Componentização (card D)

Extraídos para `web/src/components/admin/analytics/`:
- **`SessionCard.jsx`** — card colapsável de sessão + timeline (Aba 2 toggle ON + Aba 4).
  `aria-expanded`. Exporta `CAMPAIGN_COLOR`/`EV_COLOR`.
- **`SortableTable.jsx`** — tabela com headers clicáveis (`<th scope="col">` + `aria-sort`).
- **`PaginationBar.jsx`** — paginação reutilizável (Abas 2/3/4), seletor opcional 25/50/100.

Lógica **pura** em `web/src/lib/admin/analyticsUtils.js` (sem React/DOM): `sortRows`, `paginate`,
`journeyStepKind`/`STEP_COLOR`, `bounceColor`/`clickRateColor`/`deltaMeta`, `filterSectors`,
`escapeHtml`, `parseTabHash`/`buildTabHash` (navegação por hash entre abas).

## Navegação cruzada

Hash com params: `#events?path=/site/x`, `#events?group=session`. `parseTabHash`/`buildTabHash`
serializam. A `EventsTab` recebe `initialParams` e é remontada por `key` quando os params mudam
(reset limpo do filtro/toggle).

## Testes — `node --test` (sem dependência nova)

Não havia infra de teste JS no `web/`. Em vez de adicionar Vitest (mexeria no lock), usei o
**runner nativo do Node** (`node --test`, Node 22 no CI): **15 testes** em
`analyticsUtils.test.js` — sorting (numérico/string/nulls-last/imutável), paginação (clamp/vazio),
`journeyStepKind`, thresholds de cor, `deltaMeta`, `filterSectors`, `escapeHtml`, round-trip do
hash. Script `npm run test:unit` adicionado e **gateado no CI** (roda antes do `npm run build`).
Backend intocado (pytest 1103 do Prompt 1 permanece).

## Acessibilidade

`<thead>`/`<th scope="col">`, `aria-sort` nos headers, `aria-expanded` nos colapsáveis, inputs de
busca com placeholder. Admin sempre dark (tokens `klarim-*`), responsivo.

## Segurança

Endpoints já admin-only (Prompt 1). Busca não interpolada em HTML (React escapa `{}`; `escapeHtml`
como defesa extra). `contact_email` nunca aparece. Paginação obrigatória (nunca 500+ rows de uma
vez). Lazy: cada aba só busca quando ativada.

## Validação

Build Astro **verde**; `test:unit` **15/15**; nenhum placeholder "Em breve" restante; as 4 abas
funcionais. **KL-83 concluído** (Prompts 1+2).
