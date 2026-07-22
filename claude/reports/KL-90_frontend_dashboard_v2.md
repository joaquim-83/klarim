# KL-90 — Prompt 2: Frontend do Dashboard v2

**Data:** 2026-07-21
**Card:** KL-90 (Prompt 2 de 3)
**Ambiente:** desenvolvimento local. **Sem deploy, sem push/commit.**
**Pré-requisito:** Prompt 0 (dev) + Prompt 1 (endpoint `/account/dashboard-summary`).

---

## Decisão de rota (registrada)

O prompt pediu a página em `/painel/dashboard-v2` dizendo "não modificar o dashboard
antigo (`/painel/dashboard`)". Mas na base o **dashboard do usuário** (dono do site)
vive em **`/dashboard`** (`web/src/pages/dashboard/index.astro`), protegido pelo
`src/middleware.js` com o **cookie de usuário**; `/painel/*` é o **painel do operador**
(admin), com auth diferente. Como o endpoint `/account/dashboard-summary` é **user-auth**,
uma página em `/painel/` **não pegaria a sessão do usuário** (o middleware só cobre
`/dashboard/*`).

Por isso o Dashboard v2 foi criado em **`/dashboard/v2`** (`web/src/pages/dashboard/v2.astro`),
espelhando `dashboard/index.astro` (mesmo layout `Base`+`Header`+`Footer`, mesma auth).
**Coexiste** com `/dashboard` (o antigo, **não modificado**). O swap é o Prompt 3.

> URL de teste correta: **`http://localhost:3000/dashboard/v2`** (não `/painel/dashboard-v2`).

---

## Entregue

`web/src/pages/dashboard/v2.astro` + 10 componentes React em
`web/src/components/dashboard-v2/` (+ `shared.js` de tokens/helpers e `FixInline.jsx`
reutilizável):

| Componente | Papel |
|---|---|
| `DashboardV2.jsx` | Orquestrador: 1 fetch, `selectedSiteId`, loading skeleton, erro+retry, banners (offline/score 100), toast, scan, layout 3 camadas |
| `SiteSelector.jsx` | Dropdown dos sites (badge de semáforo + score) + "Adicionar site" |
| `ScoreCard.jsx` | Score grande + anel do semáforo + tendência + benchmark (F-pattern, canto sup. esq.) |
| `StatusPanel.jsx` | Riscos/SSL/online/último scan + 3 ações (PDF, Compartilhar, Escanear) |
| `CategoryBar.jsx` | 6 pills (status + passed/total) → expande os checks da categoria (Camada 2) |
| `RisksList.jsx` | Accordion de riscos (KL-20, ordenados por severidade) → "Como corrigir" |
| `FixInline.jsx` | Abas por plataforma (WordPress/Nginx/Apache; auto-seleciona pelo `site_type`) + "Encaminhar para técnico" |
| `Checklist.jsx` | Ações derivadas (máx 5); concluídas riscadas |
| `ScoreHistory.jsx` | Gráfico de linha (SVG) do histórico + tooltip por ponto |
| `PlanCard.jsx` | Plano + status + dias de trial + features + CTA |
| `EmptyDashboard.jsx` | Usuário sem site: CTA de adicionar + checklist reduzido |

### Princípios do card aplicados
- **"Tudo bem?" em 2s:** score+semáforo+tendência+benchmark sem scroll (Camada 1).
- **Progressive disclosure:** Camada 1 (resumo) → Camada 2 (checks/riscos expandem) →
  Camada 3 (evolução/plano). Accordion nos riscos e nas categorias.
- **F-pattern:** score no canto sup. esquerdo, ações na coluna direita.
- **Cor = status:** verde/amarelo/vermelho constantes nos 2 temas; laranja da marca p/ CTAs.
- **Layout:** desktop 2/3+1/3 no topo, pares 50/50 abaixo (`max-w-7xl`); mobile empilha
  na ordem seletor→score→ações→categorias→riscos→checklist→evolução→plano (ordem-fonte
  = ordem mobile); categorias em scroll horizontal no mobile.

### Decisões técnicas
- **Gráfico em SVG puro** (não recharts): a página do dashboard herda a **CSP estrita**
  do público em produção, que bloqueia libs que injetam estilo. Mesma escolha do
  `ScoreChart` do KL-86. Sem custo de bundle, tooltip no hover.
- **Tema:** usa os utilitários `slate`/`white` (theme-aware via os overrides de CSS var
  do KL-87) + `text-[var(--accent-text)]` nos botões laranja. Zero cor hardcoded de tema.
- **`site_type=wordpress`** faz a aba **WordPress** abrir primeiro no "Como corrigir".
- PDF = `/api/report/executive?url={domain}` (desabilitado se não há score); Compartilhar
  copia `klarim.net/site/{domain}` + toast; Escanear = `/scan/result?url=&refresh=1` +
  re-fetch.

---

## Validação (no navegador, com o seed)

Login `dono@exemplo.com.br` → `http://localhost:3000/dashboard/v2`:

| Item | Resultado |
|---|---|
| Camada 1 sem scroll (score/semáforo/ações/categorias) | ✅ anel amarelo, "83/100 · Atenção · estável", "1º de 13 · acima da média (57)" |
| Seletor de site (5 sites) | ✅ troca p/ loja (42/🔴 Crítico, 12 riscos) e re-fetcha tudo |
| Riscos → "Como corrigir" | ✅ accordion; abas WordPress/Nginx/Apache; WordPress default (site_type) |
| Categorias → checks | ✅ 6 pills full-width; expandem os checks com evidência/fix |
| Checklist | ✅ 5 itens; "Complete o perfil" riscado (concluído) |
| Evolução (gráfico) | ✅ SVG 10 pontos + tooltip |
| Plano | ✅ Pro · Trial · 24 dias · features |
| Tema claro/escuro | ✅ legível nos dois (status colors constantes, botão laranja com contraste) |
| Auth | ✅ sem sessão → 302 `/entrar?redirect=/dashboard/v2` |
| Console | ✅ **zero erros** (hidratação ok) durante todas as interações |
| Dashboard antigo `/dashboard` | ✅ intacto (não modificado) |
| `npm run build` (produção) | ✅ compila sem erro |
| `npm run test:unit` | ✅ 96 pass, 0 fail |

Mobile 375px: usa responsivo Tailwind padrão (`grid-cols-1` base → empilha; pills
`overflow-x-auto`); o navegador de automação não simula viewport móvel de forma
confiável, então a checagem pixel-perfect fica p/ o DevTools do dono (item do checklist).

---

## Correções feitas durante a validação (dev)

1. **Grid do topo não preenchia a largura:** o dev server do Astro tinha um **scan
   incompleto do Tailwind** para classes NOVAS de arquivos recém-criados — `lg:col-span-3`
   e `border-[6px]` não geravam (enquanto `lg:col-span-2`/`grid-cols-3`, já usados no
   dashboard antigo, funcionavam). Reiniciar o dev server (scan limpo) resolveu — o
   `npm run build` de produção já gerava tudo. Por robustez, troquei para o padrão provado
   (`lg:grid-cols-3` + `lg:col-span-2`; `border-4`) e valores padrão nos `min-w`.
2. **Astro em crash-loop no restart:** o lock `web/.astro/dev.json` (bind mount) sobrevive
   ao restart do container → "dev server already running". Ajustei o `command` do serviço
   `astro` no `docker-compose.dev.yml` para **remover o lock no boot** (`rm -f .astro/dev.json`).

---

## Regras atendidas

- ✅ Sem deploy, sem push/commit.
- ✅ Dashboard antigo (`/dashboard` / `Dashboard.jsx`) **não modificado**.
- ✅ Rota separada (`/dashboard/v2`) que coexiste.
- ✅ Componentes e padrões do projeto (React islands `client:load`, Tailwind theme-aware).
- ✅ Hot reload funcionando (HMR do Astro/Vite).

## Próximo (Prompt 3)
Swap: tornar o v2 o `/dashboard` padrão (após aprovação do dono), migrando/aposentando o
`Dashboard.jsx` antigo e os helpers órfãos do KL-86.
