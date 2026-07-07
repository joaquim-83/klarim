# KL-14 — Dashboard admin (klarim.net/painel) — gestão completa

- **Card Jira:** KL-14
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-5 (frontend React), KL-11/12/13 (APIs de targets/scans/alerts/rescans), KL-7 (payments)
- **Commit:** `feat(KL-14): add admin dashboard with auth, management, and monitoring`

---

## Objetivo

Painel do operador em `klarim.net/painel` para operar e monitorar o Klarim: KPIs,
gráficos, gestão de alvos, disparos manuais (scan/alerta/re-scan), pagamentos e
configurações. Faz parte do **mesmo app React**; as rotas `/painel/*` são
protegidas por JWT. Sem novo domínio, container ou certificado.

## Parte 1 — Autenticação (`api/main.py`)

- `POST /auth/login {username,password}` → `{token, expires_in: 86400}`.
  Credenciais em `ADMIN_USER`/`ADMIN_PASSWORD`; JWT (**PyJWT**, HS256, 24h)
  assinado com `JWT_SECRET`. Comparação constant-time (`hmac.compare_digest`). Sem
  tabela de usuários.
- **Middleware HTTP** `_admin_auth_mw`: protege os prefixos `/targets`, `/scans`,
  `/alerts`, `/rescans`, `/email`, `/payments`, `/config` — exige `Bearer` válido
  (401 se ausente/inválido/expirado). Públicas: `/health`, `/scan/summary`,
  `/payment/*`, `/report/*`, `/webhooks/*`, `/recovery/*`, `/unsubscribe`,
  `/auth/login`. `_is_protected` casa por prefixo exato + `/` (então `/scan` e
  `/payment` singulares não são pegos por `/scans`/`/payments`).
- Segredos gerados **na VM** (`openssl rand -hex 32`), nunca no repo.

## Parte 2 — Endpoints de gestão novos (protegidos)

`GET /targets/{id}`, `POST /targets/{id}/discard`, `GET /scans/stats`,
`/scans/daily`, `/alerts/daily`, `GET /scans/{id}/report/{executive|technical}`
(PDF sem gating de pagamento), `GET /payments/list`, `/payments/stats`,
`GET /config` (params operacionais, sem segredos). No store: `payment_stats`/
`list_charges` (payments), `scan_stats`/`scans_daily`/`alerts_daily` (discovery);
`list_targets` agora traz `last_semaphore` (JOIN scans) e
`list_alerts`/`list_rescans` trazem a `url` do alvo. Rotas específicas
(`/scans/stats`, `/scans/daily`) registradas **antes** de `/scans/{id}` para não
serem capturadas como id.

## Parte 3 — Frontend (`frontend/src/`)

- **Libs:** `lib/auth.js` (token no localStorage + checa `exp`), `lib/adminApi.js`
  (Bearer + redirect em 401 + `adminDownload` p/ PDFs), `lib/useAsync.js`.
- **Componentes:** `components/admin/AdminLayout` (sidebar responsiva com menu
  hamburger no mobile + logout), `ProtectedRoute`, `ui.jsx`
  (Card/StatCard/Badge/PlatformBadge/StatusBadge/SemaphoreDot/Pagination/Button +
  `relativeTime`/`formatDate`).
- **Telas** (`pages/admin/`): `Login`; `Overview` (6 KPIs + **Recharts**: donut
  por status, bar por plataforma, 2 line charts diários, atividade recente);
  `Alvos` (lista + filtros status/plataforma/setor/busca + ações scan/alerta +
  modal "adicionar"); `AlvoDetalhe` (ficha + históricos de scans/alertas/re-scans +
  ações incl. descartar); `Scans` + `ScanDetalhe` (checks + PDF executivo/técnico);
  `Alertas` (stats + histórico); `Pagamentos` (receita + conversão + lista);
  `Rescans` (evolução + %); `Config` (read-only, valores reais via `/config`).
- **Identidade:** paleta dark do Klarim (tokens `bg-klarim-*`). **Responsivo**
  (sidebar colapsa < md). **Code-split:** o painel é `lazy()` — o site público não
  baixa o bundle do dashboard (Recharts isolado num chunk; bundle público caiu de
  657 kB → 201 kB).

## Parte 10 — Nginx

Nenhuma mudança: `try_files $uri $uri/ /index.html` já entrega o SPA em
`/painel/*`; a API admin fica em `/api/*` (mesmo proxy), protegida por JWT.

## Validação

- **Testes** (`tests/test_auth.py`, 10 casos): login ok/erro/não-configurado,
  rotas protegidas sem token/inválido/expirado → 401, token válido atravessa,
  públicas livres, round-trip do token. `tests/test_payments.py`: `list_charges` +
  `payment_stats` (MemoryStore). **Suíte total: 82 passed, 1 skipped.**
- **Build do frontend:** `npm run build` OK; code-split confirmado (chunks
  separados por tela; Recharts só no chunk do Overview).
- **Produção (VM):** validado pós-deploy — ver seção abaixo.

## Validação em produção (pós-deploy) — confirmada

CI/CD verde (test + deploy; frontend rebuild com Recharts). `ADMIN_USER`/
`ADMIN_PASSWORD`/`JWT_SECRET` gerados na VM (senha e secret nunca saíram da VM).

- [x] **Público:** `GET /api/health` → 200; `/painel/login` serve o SPA (200 +
      `<div id="root">`).
- [x] **Proteção:** `GET /api/targets`, `/config`, `/payments/stats` **sem token →
      401**.
- [x] **Login:** senha correta → **token**; senha errada → **401**.
- [x] **Com token:** `GET /config` → 200 (`{batch:100, alerts 10/50, rescan
      24h/30d, scans 50/h}`); `/targets/stats`, `/payments/stats`, `/scans/stats`
      → 200 com dados reais (ex.: pagamentos `total:11, PAID:5, R$ 145,00`; scans
      `avg 86, {amarelo:1, verde:1}`).
- [x] **Middleware constant-time** e prefixos: `/scan`/`/payment` singulares
      permanecem públicos; `/scans`/`/payments` protegidos.
- [~] **UI no navegador:** build OK + SPA servido; o login visual não foi
      exercido por mim porque **digitar senha é uma ação vedada ao agente** — o
      operador acessa `klarim.net/painel/login` (usuário `admin`; senha no
      `.env` da VM, recuperável com `sudo grep ADMIN_PASSWORD /opt/klarim/.env`).

## Critérios de aceite

- [x] Login JWT (ADMIN_USER/PASSWORD do .env).
- [x] APIs admin protegidas por JWT; públicas livres.
- [x] Visão geral com KPIs e gráficos (Recharts).
- [x] Alvos (lista, filtros, ações, detalhe).
- [x] Scans (lista, detalhe dos checks, PDF).
- [x] Alertas (histórico + stats).
- [x] Pagamentos (lista + receita + conversão).
- [x] Re-scans (evolução).
- [x] Configurações (read-only).
- [x] Responsivo (mobile) + sidebar + logout.
- [x] `ADMIN_USER`/`ADMIN_PASSWORD`/`JWT_SECRET` documentadas.
- [x] Documentação (`claude.md` §18, `README.md`).
- [x] Relatório em PT-BR.
- [x] Deploy + validação em produção + commit/push.

## Follow-ups

- Edição ao vivo das configurações (hoje read-only; requer escrever no `.env` +
  restart dos containers de forma controlada).
- Seleção em massa + "escanear selecionados" na tela de Alvos (checkbox) — o MVP
  já tem ações por linha; a ação em massa fica para depois.
- Refresh token / expiração deslizante (hoje o token dura 24h fixas).
