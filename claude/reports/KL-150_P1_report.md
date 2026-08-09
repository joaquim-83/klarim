# KL-150 (Prompt 1/2) — Fixes funcionais: dropdown, redirect, dashboard dev, menu simplificado

> **Status: PRONTO PARA REVISÃO VISUAL** — implementado e validado no `docker-compose.dev.yml`.
> **NENHUM deploy/push foi feito.** Aguarda a validação visual do Cidinei antes de subir.

## Resumo

4 fixes funcionais na navegação/UX do site público e do dashboard, todos **frontend** (zero
mudança em backend, engine do scanner ou rate limiting). Reaproveita os endpoints existentes
(`/account/gate/status`, `/gate/runs`, `/gate/projects`, `/account/gate/activate`). Lógica nova é
**pura/testável** em `web/src/lib/*` (padrão KL-89), consumida pelos componentes.

---

## Fix 1 — Sobreposição avatar × dropdowns (`web/public/header.js`)

**Problema:** o menu do avatar (`<div id="user-menu">`) e os dropdowns de navegação
(`<details class="nav-dropdown">`) tinham handlers de "fechar ao clicar fora" **separados** e se
ignoravam → abrir um não fechava o outro (sobreposição).

**Fix:** um único conjunto — abrir o avatar fecha todos os dropdowns; abrir um dropdown fecha os
outros **e** o avatar; clicar fora fecha tudo (helpers `closeDropdowns`/`closeAvatar`). Bump do
cache-busting do script para `?v=5` no `Header.astro`.

**Validado no browser** (logado, avatar "D"/"J"):
| Ação | Resultado |
|---|---|
| "Para empresas" aberto → clicar avatar | dropdown fecha, avatar abre ✅ |
| avatar aberto → clicar "Para empresas" | avatar fecha, dropdown abre ✅ |
| qualquer aberto → clicar fora | tudo fecha ✅ |

## Fix 2 — Redirect pós-login/cadastro vindo de `/security-gate`

**Problema:** os CTAs de plano da landing eram `<a>` estáticos; o novo dev caía no `/dashboard`
de owner ("Adicione seu primeiro site") em vez do portal do Gate.

**Fix:**
- Nova ilha **`GatePlanCTA.jsx`** (`client:load`) por plano, ciente de auth (consulta
  `/account/gate/status`): **deslogado** → `/cadastrar?type=developer&plan={slug}`; **logado** →
  `/dashboard/gate?upgrade={slug}` (Free abre o portal; Enterprise → `/contato`). SSR-safe: o 1º
  paint é o link de cadastro (funcional sem JS); se um logado clicar antes do fetch resolver, o
  `/cadastrar` detecta a sessão no servidor (KL-157) e redireciona ao portal.
- `cadastrar.astro`: separa **plano Gate** (dev, pro/team → vira `?upgrade=` no portal) de **plano
  owner** (KL-44 P6, pro/agency → trial). Nunca cruza os dois.
- `serverAuth.js::loggedInRedirect(isDev, fallback, gatePlan)` carrega o `?upgrade=` no redirect do
  logado.
- `SignupForm.jsx`: o "Entrar →" do fluxo dev usa o `redirect` recebido (com `?upgrade=`).
- Href/label/redirect ficam em **`lib/nav.js`** (`gatePlanCtaHref`/`gatePlanCtaLabel`/
  `gateSignupRedirect`) — testável.

**Validado no browser:**
- Deslogado: free→`/cadastrar?type=developer`, pro→`…&plan=pro`, team→`…&plan=team`,
  enterprise→`/contato` ✅
- Logado: pro→`/dashboard/gate?upgrade=pro`, team→`…?upgrade=team`, free→`/dashboard/gate` ✅

## Fix 3 — Dashboard diferenciada para devs (`DashboardV2.jsx` + `GateDashboardSection.jsx`)

**Problema:** dev via "Adicione seu primeiro site" — irrelevante.

**Fix:** o `DashboardV2` busca o gate status (`/account/gate/status`) e decide o layout:
- **developer PURO** (`account_type='developer'`) → SÓ a seção Gate (sem sites/onboarding de owner).
- **both** (dev + owner) → seção Gate **no topo** + a seção de sites abaixo.
- **owner sem Gate** → dashboard inalterado (regressão preservada).

Novo `GateDashboardSection.jsx`: card "🔒 Security Gate" com plano · scans/hora · nível +
"Abrir dashboard Gate →" + checklist "Primeiros passos" (auto-marcado). O checklist é pura
(`ux.js::gateOnboardingSteps`): conta criada · API key · 1º scan (via `/gate/runs`) · **domínio
verificado** p/ CI-CD (via `/gate/projects`, contando os `verified` — o cadastro cria 1 projeto
NÃO verificado, então contar "qualquer projeto" marcaria cedo demais) · upgrade (plano ≠ free).

Decisão de layout em funções puras testáveis: `showGateDashboardSection`/`isPureDeveloper`.

**Validado no browser** (conta `devpuro@exemplo.com.br` = pure dev; `dono@exemplo.com.br` = owner →
ativado o Gate → `both`):
- pure dev: só a seção Gate, sem "primeiro site"; checklist ✅ Conta ✅ API key ☐ 1º scan ☐ CI/CD
  ☐ upgrade (correto para dev recém-cadastrado com 1 projeto não verificado) ✅
- both: seção Gate **no topo** (índice 0) + "Meus sites (5)" abaixo ✅
- owner: sem seção Gate, dashboard normal ("Meus sites"/Monitoramento/Selo) ✅

## Fix 4 — Menu "Para devs" simplificado (`nav.js` + `Header.astro` + `security-gate.astro`)

**Fix:** `DEV_LINKS` reduzido a **1 destino** (Security Gate). No header desktop, "Para devs" virou
**link DIRETO** "Security Gate" (sem dropdown) — logado e deslogado. Os antigos "Documentação/
Planos dev/API" viraram **acessos rápidos na landing** (linha no hero: "Planos e preços ·
Documentação da API"). O drawer mobile segue listando `DEV_LINKS` (1 item).

**Validado no browser:** header mostra "Para empresas ▼" (dropdown) + "Security Gate" (link direto);
hero da landing exibe os 2 acessos rápidos. ✅

---

## Testes

- **Frontend `node --test` (`npm run test:unit`): 203 passed** (+9): `nav.test.js` (DEV_LINKS=1,
  `gatePlanCtaHref`/`Label`, `gateSignupRedirect`, `loggedInRedirect` c/ plano) + `gate/ux.test.js`
  (`showGateDashboardSection`, `isPureDeveloper`, `gateOnboardingSteps`).
- **Backend `pytest`: 2288 passed, 1 skipped** (sem mudança de backend — só sanidade).
- **`npm run build`: OK.**
- **Console do browser: zero erro/CSP** em todas as páginas testadas.

## Nota de decisão (KL-153 preservado)

O card sugeria, no teste #3, "dashboardMenu para owner sem Gate → não inclui seção Gate". O
`nav.dashboardMenu` (menu do header) foi **mantido** exibindo "Security Gate" para TODOS os tipos
de conta — é a decisão deliberada do KL-153 (o `/dashboard/gate` auto-ativa o Gate quando a conta
ainda é owner), com teste já verde e documentada no `claude.md`. A diferenciação do Fix 3 acontece
na **página** do dashboard (via `showGateDashboardSection`), que é o objetivo real do card: owner
sem Gate **não** vê a seção Gate no `/dashboard`; dev vê. Isso satisfaz o espírito dos testes #2/#3
sem regredir o KL-153.

## Arquivos

**Modificados:** `web/public/header.js`, `web/src/components/Header.astro`,
`web/src/components/account/SignupForm.jsx`, `web/src/components/dashboard-v2/DashboardV2.jsx`,
`web/src/lib/gate/ux.js`, `web/src/lib/gate/ux.test.js`, `web/src/lib/nav.js`,
`web/src/lib/nav.test.js`, `web/src/lib/serverAuth.js`, `web/src/pages/cadastrar.astro`,
`web/src/pages/security-gate.astro`.
**Novos:** `web/src/components/dashboard-v2/GateDashboardSection.jsx`,
`web/src/components/security-gate/GatePlanCTA.jsx`.

## Como revisar localmente

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
```
Estado do DB de dev já preparado nesta sessão de validação:
- **owner** (Fix 3 regressão): `dono3@teste.com` / `dev123456` → `/dashboard` (sem seção Gate).
- **pure dev** (Fix 3): `devpuro@exemplo.com.br` / `dev123456` (criado via `POST /gate/register`,
  `project_url=https://devpuro.com.br`) → `/dashboard` (só a seção Gate).
- **both** (Fix 3): `dono@exemplo.com.br` / `dev123456` — o Gate já foi ativado nesta sessão →
  `/dashboard` (Gate no topo + sites). (Um re-seed reverte `dono` a owner; reative por
  `/dashboard/gate`, que auto-ativa.)
- **Fix 4/2**: `/security-gate` deslogado (CTAs → `/cadastrar?...&plan=`) × logado (→
  `/dashboard/gate?upgrade=`).
- **Fix 1**: no header logado, abrir "Para empresas" e o avatar alternadamente + clicar fora.

## Escopo NÃO tocado (Prompt 2)

Conteúdo/copy da landing, backend, engine, rate limiting e o SEO (títulos/URLs/Schema.org do
KL-132) permaneceram intactos.
