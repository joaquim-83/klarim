# KL-153 (Prompt 2/2) — Frontend: header + home + wizard KYC + dashboard Gate + bridge

## Contexto

Prompt 1 entregou o backend (KYC, rate limiting, scan avulso, upgrade, status). Este prompt separa
a comunicação dos DOIS públicos (dono PME × dev) e redesenha a experiência frontend do Gate.

**Realidade do repo:** os testes frontend são **`node --test`** (não Vitest), rodando lógica PURA de
`src/lib/*.js` (não JSX/DOM). Segui esse padrão: extraí a lógica testável para libs e os componentes
a consomem — os 29 comportamentos exigidos viram testes de função pura (mesma estratégia do KL-89).

## Entregáveis

### Lógica pura (testável) — 2 libs novas
- **`web/src/lib/nav.js`** — `EMPRESA_LINKS`/`DEV_LINKS` (dropdowns), `PRODUCT_CARDS` (dual-card),
  `authState`, `dashboardMenu` (Security Gate p/ todos).
- **`web/src/lib/gate/ux.js`** — `normalizeUrl`, `maskCPF`/`isValidCPF` (espelha o backend),
  `categorySummary`/`showChecksDetail`/`groupChecksByCategory`, `kycBannerVisible`, `showGateBridge`,
  `wizardNext`/`shouldShowWizard`, `formatCountdown`/`rateLimitMessage`, `usageText`/`upgradeTarget`,
  `ctaState`/`ctaLabel`, `signupBody`.

### 1. Header público reestruturado — `Header.astro` + `NavDropdown.astro`
Substitui o dropdown único "Desenvolvedor" por **"Para empresas ▼"** (Verificar meu site · Monitoramento
· Setores · Planos empresa) e **"Para devs ▼"** (Security Gate · Documentação · Planos dev · API). Dropdowns
CSP-safe (`<details>`/`<summary>`, tap/click). **Mobile:** hambúrguer → drawer com as 2 seções separadas.
Nos dois estados de auth (`data-auth` in/out). Item "Entrar"/"Cadastrar" (deslogado) ou avatar+menu (logado).

### 2. Dual-card na home — `home/ProductSplit.astro` + `index.astro`
Seção abaixo do hero (scroll-down; o hero segue sendo a 1ª tela `min-h-[100dvh]`). Card **empresa** ("Seu
site é seguro para seus clientes?" · "Verificar meu site" → `#scan`) × card **dev** ("Seu deploy expõe
dados?" · "Começar grátis" → `/security-gate`). Linguagem separada (site/clientes × deploy/CI-CD).

### 3. Bridge scan→Gate — `ScanResultDetail.jsx`
Card `<GateBridge />` ao fim do resultado do scan público ("Quer ir mais fundo? … 86 pontos adicionais"
→ `/security-gate`). Só renderiza no sucesso (o componente só monta com resultado; `showGateBridge` testado).

### 4. Wizard scan-first (6 steps) — `GateOnboarding.jsx` (reescrito)
1 URL (`normalizeUrl`) → **`POST /api/gate/scan`** avulso → 2 scanning → 3 resumo (score + categorias,
sem detalhes) + banner KYC → 4 **KYC inline** (CPF mascarado+validado, endereço, telefone → `POST
/api/account/kyc`; "Pular por agora" → step 6) → 5 resultado completo (do run persistido `GET
/api/gate/runs/{id}` — **não re-escaneia**, o rate limit por domínio barraria) → 6 integração CI/CD
(`GateIntegrationTabs`). Pula o step 4 se `kyc_completed`.

### 5. Dashboard Gate redesenhado — `GatePortal.jsx` (reescrito)
**Status bar** (score do último run + plano + uso/hora + **Upgrade inline** → `POST /api/account/gate/
upgrade` → abre `checkout_url`). **Novo scan** avulso (modal → `POST /gate/scan`, resultado KYC-aware +
banner). API key (copiar/regenerar), Projetos+planos, Fornecedores (Enterprise), Integração, **Histórico**.
Labels visíveis em todos os inputs; tokens KL-87; contraste AA.

### 6. Menu — Security Gate p/ todos — `Header.astro`
"🔒 Security Gate" no dropdown do usuário logado (owner E developer). O `/dashboard/gate` (GatePortal)
**ativa o Gate sozinho** quando a conta ainda é owner (`POST /account/gate/activate` no mount → re-fetch).

### 7. Landing `/security-gate` — CTA de cadastro
`GateLandingCTA` (3 estados já existiam) → deslogado linka `/cadastrar?type=developer`. **`cadastrar.astro`**
lê `type=developer` → passa `source='security-gate'` + `redirect=/dashboard/gate` ao **`SignupForm`**, que
inclui `source` no POST (`signupBody`) → backend cria conta developer + API key. Heading/subtítulo dev.

### 8. 429 no frontend — `GatePortal.jsx` + `ux.js`
`rateLimitMessage(limit_type, retry_after_seconds)` → mensagem contextual (user→upgrade · ip · domain ·
interval) + **countdown** (`formatCountdown`) que decrementa. Nunca erro genérico.

## Testes — `node --test` (+21 novos → 187 no total)
`web/src/lib/nav.test.js` (6) + `web/src/lib/gate/ux.test.js` (15), ligados no `npm run test:unit`.
Cobrem: dropdowns (links/labels), authState, dual-card, dashboardMenu; URL/CPF/categorias/checks-detail/
KYC-banner/bridge/wizard/rate-limit/uso/upgrade/CTA/signupBody. **187 pass, 0 fail.** `npm run build` OK.

## Validação no browser (docker-compose.dev.yml)
Stack dev no ar (web :3000 / astro :4321 / api / db / redis). Verificado: **home** com o header
"Para empresas ▼"/"Para devs ▼" (dropdown de empresas abre com os 4 links) + a seção **dual-card** ("Dois
produtos, um só lugar" — empresa "Verificar meu site" × dev "Começar grátis"); **/security-gate** com o
header novo + CTA "Começar grátis"; **/dashboard/gate** protegido pela middleware (redireciona a
`/entrar?redirect=/dashboard/gate`). **Zero erro no console** em todas. O GatePortal/wizard autenticados
não foram exercitados via login no browser (regra de não digitar senha em formulário) — cobertos pelo
build + 187 testes unitários + reuso dos componentes já testados do dashboard.

## Regras respeitadas
Backend **inalterado** (Prompt 1); scanner público **inalterado** (só apresentação). Linguagem separada
(empresa × dev). CSP-safe (sem JS inline; dropdowns `<details>`; sem novos `public/*.js` → nenhuma mudança
de nginx nem `?v`). Nenhuma rota Astro nova (Monitoramento → âncora `/#para-empresas`) → allowlist do nginx
intocada. `useState`/`window` com guarda (`try/catch` no localStorage/sessionStorage). Tokens KL-87.

## Arquivos
**Novos:** `web/src/lib/nav.js`(+test), `web/src/lib/gate/ux.js`(+test), `web/src/components/NavDropdown.astro`,
`web/src/components/home/ProductSplit.astro`.
**Editados:** `Header.astro`, `index.astro`, `ScanResultDetail.jsx`, `GateOnboarding.jsx` (reescrito),
`GatePortal.jsx` (reescrito), `cadastrar.astro`, `SignupForm.jsx`, `package.json` (test:unit), `claude.md`.
