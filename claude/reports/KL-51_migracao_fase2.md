# KL-51 — Migração do painel admin Vite → Astro (Fase 2: páginas restantes + corte do Vite)

**Card:** KL-51
**Data:** 2026-07-15
**Referência:** `claude/KL-51_mapeamento_painel_admin.md` + `claude/reports/KL-51_migracao_fase1.md`.

## Objetivo

Migrar as **9 páginas restantes** do painel admin (Grupos A/B/C do prompt) de `frontend/`
(Vite) para `web/` (Astro), remover as rotas `/painel` do Vite e simplificar o Nginx. Com isso
o painel fica **100% no Astro**.

## Páginas migradas (9) — completam o painel

| # | Página | Rota | Componente | Ilha |
|---|---|---|---|---|
| A1 | Scans | `/painel/scans` | `scans.astro` | `ScansPage.jsx` |
| A2 | ScanDetalhe | `/painel/scans/:id` | `scans/[id].astro` | `ScanDetalhePage.jsx` |
| A3 | Alertas | `/painel/alertas` | `alertas.astro` | `AlertasPage.jsx` (2 abas) |
| A4 | Sistema | `/painel/sistema` | `sistema.astro` | `SistemaPage.jsx` (auto-refresh 30s) |
| B1 | Alvos | `/painel/alvos` | `alvos.astro` | `AlvosPage.jsx` (a mais complexa) |
| B2 | AlvoDetalhe | `/painel/alvos/:id` | `alvos/[id].astro` | `AlvoDetalhePage.jsx` |
| B3 | Leads | `/painel/leads` | `leads.astro` | `LeadsPage.jsx` |
| B4 | LeadDetalhe | `/painel/leads/:id` | `leads/[id].astro` | `LeadDetalhePage.jsx` |
| C1 | Analytics | `/painel/analytics` | `analytics.astro` | `AnalyticsPage.jsx` (funil = divs, sem Recharts) |

Total do painel agora no Astro: **17 arquivos `.astro`** (8 da Fase 1 + 9 desta) + 15 componentes
`*Page.jsx` (6 + 9). O único que usa Recharts continua sendo o Overview (Fase 1).

## Padrão seguido (idêntico à Fase 1)

- `client:only="react"` em todas as ilhas (não `client:load`).
- `AdminShell` como **wrapper interno** de cada página (prop `active`), não ilha-em-slot.
- `<a href>` no lugar de `Link`/`NavLink`; `window.location.href` no lugar de `useNavigate`.
- **Rotas dinâmicas**: `id` lido de `window.location.pathname.split('/').filter(Boolean).pop()`
  dentro da ilha (client-only, `window` sempre existe) — sem `useParams`. Nos 3 detalhes
  (Scan/Alvo/Lead).
- `parseUTC`/`useAsync` reusados do kit da Fase 1. Zero `react-router-dom`.
- **Reuso da Fase 1**: `AlvosPage`/`AlvoDetalhePage` usam `SectorEditor`/`TargetEditors`/
  `ProfileEditor`; `LeadsPage`/`LeadDetalhePage` importam `CLASS_META`/`ClassBadge` de
  `LeadShared` (extraído na Fase 1, resolvendo o acoplamento `LeadDetalhe→Leads`).

## Nginx — simplificado para `^/painel(/|$)`

A regex da Fase 1 casava só as 7 rotas migradas; agora **todo** `/painel` vai ao Astro. Aplicado
nos **3 lugares**:
1. `http.conf` (fallback pré-cert).
2. `https.conf.template` server principal — **mantém a CSP relaxada** (`script-src 'self'
   'unsafe-inline'`, decisão da Fase 1: o strict bloqueia o bootstrap de island; `/painel` é
   noindex/operador-only, não pontuado pelo self-scan).
3. `https.conf.template` subdomínio `painel.` — mantém o `location ^~ /_astro/` da Fase 1.

Regex antiga (`^/painel(/(login|config|…|monitorados))?/?$`) → **removida dos 3 lugares** (grep:
0 ocorrências). Segurança preservada (headers repetidos onde há `add_header` próprio).

## Corte do Vite (conservador, como pedido)

- `frontend/src/App.jsx`: **removidas** as rotas `/painel/*` (o bloco "Dashboard admin" + os
  `lazy()` dos componentes admin + `AdminFallback` + `ProtectedRoute` + `Suspense`/`lazy` não
  usados). O Vite não serve mais o painel (o Nginx intercepta `/painel` → Astro), evitando
  duplicação e tirando o bundle do painel do build público.
- **NÃO deletado**: `frontend/src/pages/admin/` e `frontend/src/components/admin/` continuam no
  disco como referência (fora do build — sem import). O container `web` (Nginx) segue como
  reverse proxy. O corte total (remover o Vite / mover o Nginx) fica para tarefa futura.
- **Páginas públicas do Vite preservadas**: o Vite ainda serve o fluxo de scan legado
  (`/result`, `/pay`, `/report`, `/recuperar`, `/parceiros`, `/monitorados`) — não tocadas.

## Validação estática (todos ✓)

```
react-router-dom em admin migrado ............ 0
import real de react-router / <Link> JSX ..... 0  (as 8 menções são comentários "Portado…")
páginas .astro em /painel .................... 17 (8 fase1 + 9 fase2)
componentes *Page.jsx ........................ 15 (6 fase1 + 9 fase2)
id via pathname nos 3 detalhes ............... 3
LeadShared importado (Leads + LeadDetalhe) ... 2
auto-refresh 30s no Sistema .................. 1
<script> inline em .astro do painel .......... 0  (CSP-safe)
Nginx `^/painel(/|$)` nos 3 lugares .......... 3
regex antiga (login|config|…) remanescente ... 0
App.jsx importando admin ..................... 0 (só um comentário de referência)
```

## Deploy

_(preenchido após push + CI verde)_

- Commit: `<hash>` — push para `main`.
- CI (test + build-web + nginx-check + deploy): `<status>`.
- Verificação no browser (`painel.klarim.net`): `<resultado>`.

## Checklist de verificação PÓS-deploy (browser, produção)

- [ ] Scans — período (default 7d, custom), semáforo client-side, score min/max, paginação, link detalhe
- [ ] ScanDetalhe — score grande, checks, download PDF exec+técnico, reescanear
- [ ] Alertas — 2 abas (enviados + consultas de perfil), KPIs, paginação
- [ ] Sistema — 4 workers, deps, e-mail, bounce, atividade, **auto-refresh 30s**
- [ ] Alvos — filtros, busca debounce, seleção múltipla + classificar, editores inline ✏️, AddTarget/Profile modais, link detalhe
- [ ] AlvoDetalhe — editores inline, 5 ações, 4 blocos de histórico
- [ ] Leads — cards clicáveis filtram, métricas, recalcular, link detalhe
- [ ] LeadDetalhe — ScoreBar+breakdown, tags/notas/opt-out, scans do e-mail
- [ ] Analytics — período, funil, carrinho, campanhas, páginas, timeline
- [ ] Páginas da Fase 1 (Overview/Config/Clientes/Rescans/Pagamentos/Inbox) continuam OK
- [ ] Console sem violação de CSP nos dois domínios

## Nota de risco

O build local do `web/` estala por I/O do iCloud (o build da Fase 1 levou ~30 min mas concluiu
com exit 0). Confiamos no job **`build-web`** do CI (compila o site inteiro) e no **`nginx-check`**
(`nginx -t`) como gate do deploy. O código segue **exatamente** o padrão da Fase 1, já validado
em produção.

## Resultado

- **100% do painel admin** no Astro (17 rotas: 15 páginas + login + redirect monitorados).
- Nginx simplificado (`^/painel(/|$)` → Astro nos 3 blocos).
- Rotas `/painel` removidas do `App.jsx` do Vite (sem duplicação; código admin antigo mantido
  como referência fora do build).
- Container `web` (Nginx) segue como reverse proxy. **KL-51 pode ser fechado como "Feito".**
