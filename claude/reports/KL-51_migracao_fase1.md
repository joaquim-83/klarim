# KL-51 — Migração do painel admin Vite → Astro (Fase 1: fundação + 7 páginas)

**Card:** KL-51
**Data:** 2026-07-15
**Tipo:** migração de frontend (aditiva) + roteamento Nginx
**Referência:** `claude/KL-51_mapeamento_painel_admin.md` (mapa completo do painel).

## Objetivo

Mover a fundação (libs, UI kit, layout, auth) + as **6 páginas mais simples** do painel admin
de `frontend/` (React SPA + Vite) para `web/` (Astro 7 SSR), mantendo **paridade funcional
total** e **sem regressão** nas 12 páginas ainda não migradas. As 12 restantes ficam para a Fase 2.

## O que foi migrado (7 páginas + fundação)

| Página | Rota | Componente Astro | Ilha React |
|---|---|---|---|
| Login | `/painel/login` | `pages/painel/login.astro` | `LoginIsland.jsx` |
| Overview | `/painel` | `pages/painel/index.astro` | `OverviewPage.jsx` (Recharts) |
| Config | `/painel/config` | `pages/painel/config.astro` | `ConfigPage.jsx` |
| Clientes | `/painel/clientes` | `pages/painel/clientes.astro` | `ClientesPage.jsx` |
| Rescans | `/painel/rescans` | `pages/painel/rescans.astro` | `RescansPage.jsx` |
| Pagamentos | `/painel/pagamentos` | `pages/painel/pagamentos.astro` | `PagamentosPage.jsx` |
| Inbox | `/painel/inbox` | `pages/painel/inbox.astro` | `InboxPage.jsx` (iframe sandbox) |
| Monitorados | `/painel/monitorados` | `pages/painel/monitorados.astro` | — (redirect 301 → /painel/clientes) |

**Fundação criada em `web/`:**
- `src/lib/admin/`: `adminApi.js` (objeto `admin` completo, Bearer/localStorage, base `/api`,
  401 → login), `auth.js` (get/set/clearToken + isAuthed), `useAsync.js` (useAsync + useDebounce).
- `src/components/admin/`: `ui.jsx` (kit + mapas de cor + `parseUTC`/relativeTime/formatDate),
  `SectorEditor.jsx`, `TargetEditors.jsx`, `ProfileEditor.jsx`, `LeadShared.jsx` (CLASS_META +
  ClassBadge extraídos — §0.3), `AdminShell.jsx` (sidebar/drawer/badge inbox/guard), + os 6
  componentes de página + `LoginIsland.jsx`.
- `src/layouts/AdminLayout.astro` (documento base noindex, sem Header/Footer/track.js).
- `src/styles/global.css`: **tokens `--color-klarim-*`** (9) + spinner `.klarim-spinner` (aditivo).
- `package.json` + `package-lock.json`: **recharts `^2.15.4`** adicionado (suporta React 19).

## Decisões de arquitetura (e desvios justificados do prompt)

### 1. Shell: componente React em vez de ilha-que-envolve-`<slot/>`
O prompt (§0.4) desenhava `<AdminShell client:load><slot/></AdminShell>` — uma **ilha
envolvendo outra ilha** (a página) via slot, padrão frágil no Astro (hidratação de island
aninhada). **Desvio:** `AdminShell.jsx` é um **componente React comum**, e **cada página o usa
como wrapper dentro da própria ilha** (`<AdminShell active="config">…</AdminShell>`). Resultado:
**um único island por página**, sem aninhamento, mesma UX. O `AdminLayout.astro` vira só o
documento (`<html>`+`<slot/>`), e a página renderiza uma única ilha (`<ConfigPage client:load/>`).
O item ativo do menu vem da prop `active` (SSR-safe, sem hydration mismatch).

### 2. Nginx: **Opção B** (coexistência sem regressão), não Opção A
O prompt recomendava a Opção A (rotear **todo** `/painel/*` ao Astro), mas isso faria as **12
páginas ainda não migradas darem 404** — uma **regressão** que viola "paridade funcional total".
**Desvio:** Opção B — a regex casa **só as rotas migradas**
(`^/painel(/(login|config|clientes|rescans|pagamentos|inbox|monitorados))?/?$`); todas as demais
`/painel/*` (alvos, scans, alertas, leads, analytics, sistema e detalhes) **caem no `location /`
(SPA Vite)** e continuam funcionando. Zero regressão; migração incremental e reversível. O
próprio prompt admitia "podem ir para o Vite" — é o que a Opção B faz.

### 3. Recharts instalado (não reimplementado em SVG)
`npm install recharts@^2.15.4` atualizou `package.json` **e** `package-lock.json` (em sync — o CI
usa `npm ci`). Só a Overview o usa (não entra no bundle público).

### 4. Ilhas admin em `client:only="react"` (não `client:load`)
As ilhas do painel usam **`client:only="react"`** (renderizam só no client). Motivos: (a) o painel
é uma ferramenta **client-only autenticada** (dados via API + token do localStorage) — o SPA Vite
original também era 100% client, então isto é **mais fiel à paridade** que `client:load`; (b)
**elimina todos os riscos de SSR** — Recharts sem DOM, hydration mismatch, e qualquer acesso a
`window`/`localStorage` durante o render — importante porque o build local não pôde ser validado
(I/O do iCloud). O corpo do `AdminLayout.astro` fica no fundo dark até a ilha hidratar (sem flash
branco; comportamento idêntico ao SPA Vite).

## Análise de CSP (o maior risco — resolvido)

O `web/` roda sob CSP **estrita** no domínio principal (`script-src 'self'` + 3 hashes SHA-256,
**sem** `'unsafe-inline'`), e **loose** no subdomínio `painel.` (`script-src 'self' 'unsafe-inline'`).

- **Subdomínio `painel.klarim.net` (acesso primário do operador):** CSP permite `'unsafe-inline'`
  → as ilhas React rodam sem depender de hash. **Seguro por construção.**
- **Domínio principal `klarim.net/painel`:** CSP estrita. ⚠️ **A verificação em produção provou
  que a CSP estrita BLOQUEIA o bootstrap de island do Astro** (console: `ReferenceError: Astro is
  not defined` → tela branca) — a suposição inicial de que os 2 hashes existentes cobririam as
  ilhas admin estava **errada** (o bootstrap do `client:only` tem hash diferente do que está na
  CSP). Como `klarim.net/painel` e `painel.klarim.net/painel` só diferem na CSP e o subdomínio
  (unsafe-inline) funciona 100%, a causa é conclusivamente o `script-src`.
  **Correção aplicada:** o `location /painel` do server principal recebeu a **mesma CSP relaxada
  do subdomínio** (`script-src 'self' 'unsafe-inline'`), em vez do `include` estrito. Justificativa:
  `/painel` é operador-only, **noindex** e não-linkado → **não é página pública pontuada pelo
  self-scan**; a home e todo o resto do `klarim.net` mantêm a CSP estrita (100/100 intacto). É a
  mesma postura que o subdomínio painel. já tinha. Robusto: independe de `client:load`/`client:only`
  e dos hashes frágeis do Astro.
- **`/_astro/` no subdomínio:** o server block `painel.` **não tinha** `location /_astro/` — sem
  ele os chunks das ilhas cairiam no `index.html` do Vite e o painel não hidrataria. **Adicionado.**
- **Inbox iframe (`sandbox="" srcDoc`):** governado por `default-src 'self'` (não há `frame-src`).
  `about:srcdoc` é gerado localmente e permitido; `sandbox=""` bloqueia scripts do e-mail externo
  (anti stored-XSS preservado). **A verificar no browser pós-deploy** (item do checklist abaixo).

## Nginx — resumo das mudanças

- **`http.conf`** (fallback pré-cert): + `location ~ ^/painel(migradas)` → Astro.
- **`https.conf.template` (server principal):** + `location ~ ^/painel(migradas)` → Astro, com
  `include klarim_security_headers.conf` + `Cache-Control no-store` (a regra do add_header próprio
  que quebra a herança — reincluídos os headers de segurança).
- **`https.conf.template` (subdomínio `painel.`):** + `location ^~ /_astro/` (faltava) + `location
  ~ ^/painel(migradas)`, ambos repetindo os 5 headers de segurança inline do subdomínio.
- Preservados: security headers, subdomínio, `/api/`, `/mcp/`, bloqueios de paths sensíveis, o
  fluxo Astro público e o SPA Vite. Validação de sintaxe: job **`nginx-check`** do CI.

## Validação estática (todos ✓)

```
react-router-dom em código migrado ............ 0   (NavLink/useNavigate/useParams/Outlet: 0 uso real)
tokens --color-klarim-* em global.css ......... 9
parseUTC em ui.jsx ............................ 3   (preserva timestamps naive do Postgres)
iframe sandbox em InboxPage ................... presente
dangerouslySetInnerHTML= (atributo) .......... 0
import.meta.env.VITE_ em admin ............... 0
<script> inline em .astro do painel .......... 0   (CSP-safe)
páginas .astro em /painel ..................... 8
componentes de página importando AdminShell ... 6
```

## Coexistência Vite ↔ Astro (durante a migração)

- Rotas migradas → Astro; não migradas → SPA Vite (fallback `location /`). Ambas com o **mesmo
  visual de sidebar** (o Vite mantém seu `AdminLayout`; o Astro tem o `AdminShell` idêntico).
- Navegar entre uma página Astro e uma Vite é um **full reload** (apps distintos) — dentro de cada
  app a navegação segue SPA. Funcionalmente transparente.
- Auth compartilhada: **mesmo `localStorage('klarim_admin_token')`** e mesmo backend `/auth/login`
  — logar no Astro vale para as páginas Vite e vice-versa. O `frontend/` Vite **não foi deletado**.

## Deploy (Parte final)

- **Commit 1** `c95f166` — migração (fundação + 7 páginas + Nginx). CI **success** (test +
  build-web + nginx-check + deploy). O `build-web` verde validou a compilação dos ~25 arquivos
  (imports, JSX, recharts, `client:only`) — o que o build local não pôde (stall de I/O do iCloud).
- **Verificação no browser (produção):**
  - ✅ **`painel.klarim.net/painel`** (subdomínio, CSP relaxada): **funcionando 100%** — já logado,
    `/painel/login` redirecionou p/ `/painel`, o Overview renderizou completo (sidebar AdminShell,
    3 grades de KPIs vindas da API, badge do Inbox "1", card Saúde do sistema, eixos do Recharts).
    Prova: ilhas hidratam, `client:only` ok, adminApi+Bearer+`/api` ok, Recharts ok, sem bloqueio CSP.
  - ❌→✅ **`klarim.net/painel`** (domínio principal, CSP estrita): **tela branca** + `ReferenceError:
    Astro is not defined` (CSP estrita bloqueou o bootstrap de island). **Corrigido** no commit 2
    (CSP relaxada no `location /painel` do server principal — ver §CSP).
- **Commit 2** `6cf123a` — fix da CSP do `klarim.net/painel`. CI **success** (4 jobs). Re-teste:
  `klarim.net/painel` renderiza o **Overview completo** (KPIs, Saúde do sistema, Recharts), título
  "Klarim — Painel", **console sem erros** (o `Astro is not defined` sumiu). Os dois caminhos
  funcionam: `painel.klarim.net/painel` **e** `klarim.net/painel`.

### Verificação no browser (produção) — resultados

| Item | Resultado |
|---|---|
| `painel.klarim.net/painel/login` → redirect autenticado → `/painel` | ✅ |
| Overview — 3 grades de KPIs (da API), Recharts, Saúde do sistema, sidebar | ✅ (subdomínio **e** klarim.net) |
| Inbox — tabs, lista, dots não-lidas, badges de origem, modal abre/fecha | ✅ (fallback `(sem conteúdo)` p/ e-mail sem HTML) |
| Inbox iframe `sandbox` renderizando HTML | ⚠️ não exercido em runtime (nenhuma msg armazenada tinha `body_html`); **caminho verificado em código** (`sandbox=""`+`srcDoc`, 0 `dangerouslySetInnerHTML`) |
| AdminShell — sidebar, item ativo, badge do Inbox "1", hidratação `client:only` | ✅ |
| Coexistência (Opção B) — "Alvos" (não migrada) → **Vite** (título vira o do Vite) | ✅ sem regressão |
| Monitorados → 301 `/painel/clientes` | ✅ (regra Nginx + `Astro.redirect`) |
| Console sem violação de CSP (após o fix) | ✅ nos dois domínios |

## Checklist de verificação PÓS-deploy (browser, contra produção)

- [ ] `painel.klarim.net/painel/login` → login funciona, JWT no localStorage, redirect a `/painel`.
- [ ] `/painel` (Overview) → 3 grades de KPIs + 4 gráficos Recharts + saúde do sistema + atividade.
- [ ] `/painel/config` → tabela de 10 params.
- [ ] `/painel/clientes` → contas + sites.
- [ ] `/painel/rescans` → KPIs por evolução + filtro + paginação.
- [ ] `/painel/pagamentos` → KPIs + filtro + lista.
- [ ] `/painel/inbox` → tabs, abrir mensagem (**iframe sandbox renderiza**), estrelar, arquivar.
- [ ] `/painel/monitorados` → 301 para `/painel/clientes`.
- [ ] Páginas **não migradas** (`/painel/alvos`, `/painel/scans`, …) → ainda servidas pelo Vite
      (sem 404).
- [ ] Console sem violação de CSP nas páginas admin (principal **e** subdomínio).
- [ ] Badge do inbox atualiza (poll 60s).

## Riscos residuais

1. **iframe srcdoc sob `default-src 'self'`** — muito provavelmente OK; verificar no Inbox.
2. **CSP estrita no `klarim.net/painel`** — mitigada pela prova do `/dashboard`; o subdomínio é o
   acesso primário e é imune (CSP loose). Se algo bloquear, adicionar o hash do runtime ao
   `security_headers.conf` (não deve ser necessário).
3. **Churn do `npm install recharts`** (141 add / 126 rm por dedupe) — o CI `build-web` compila o
   site inteiro do lock; qualquer regressão do site público é pega antes do deploy.

## Fase 2 (não neste prompt)

Alvos + AlvoDetalhe, Scans + ScanDetalhe, Alertas (2 abas), Leads + LeadDetalhe, Analytics,
Sistema (auto-refresh) e, ao final, o corte do Vite (remover `/painel` do build quando 100%
migrado + simplificar o Nginx). O padrão (island por página + `AdminShell` + Opção B no Nginx)
já está estabelecido aqui.
