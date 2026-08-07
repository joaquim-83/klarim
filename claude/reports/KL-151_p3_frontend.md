# KL-151 (Prompt 3/4) — Frontend: landing /security-gate + portal do dev + admin de planos

## Contexto

Backend (P1: contas/keys/planos/projetos/convites) + API/CLI/MCP (P2: scan síncrono, rate limit,
runs) deployados e verdes. Este prompt entrega **todas as interfaces**: a landing pública do produto,
o portal do dev no dashboard e o admin de planos.

## Backend novo (`api/gate.py` + `discovery/store.py`)

- **`GET /gate/plans`** (público, sem auth) — a landing renderiza a tabela de planos a partir daqui,
  então uma edição no admin reflete **sem deploy** (rule 5).
- **`GET /account/gate/key-info`** (JWT) — prefixo/criação/último uso da key (NUNCA o valor).
- **Dual-auth `_resolve_gate_account`** — os endpoints do portal (`/gate/projects`, `/gate/runs`,
  `/gate/scan`, verify…) passam a aceitar **API key OU o cookie JWT** do dashboard (rule 3). O
  browser manda o cookie HttpOnly automaticamente (same-origin).
- **Admin de planos** (JWT admin via prefixo `/admin`): `GET /admin/gate/plans` (+ lista de checks),
  `POST /admin/gate/plans`, `PUT /admin/gate/plans/{id}`, `GET /admin/gate/accounts`,
  `POST /admin/gate/accounts/{id}/plan`. Store: `create_gate_plan`/`update_gate_plan` (edição sem
  deploy, reflete no próximo scan via plano efetivo) + `list_gate_dev_accounts` (uso por conta:
  plano, scans hoje, projetos, prefixo da key, fim do trial).

## Frontend

### Landing `web/src/pages/security-gate.astro` (SSR)

Busca `/gate/plans` server-side (fallback estático). Base + Header + Footer. Seções: **hero**
("Seu deploy está seguro?"), **18 categorias** verificadas, **como funciona** (3 passos), **snippets
de CI/CD** (GitHub Actions / GitLab / curl em `<details>` — colapsável **sem JS**, CSP-safe),
**tabela de planos ao vivo** (Free/Pro/Team/Enterprise) e **badge real** (klarim.net 90/100).
**SEO:** título + descrição + **Schema.org `SoftwareApplication`** (via `jsonLd` do Base) + OG.
Link **"Security Gate"** no `Header.astro`. `/security-gate` no **sitemap** (`_SITEMAP_STATIC`) e nas
**allowlists do nginx** (http.conf + https.conf.template).

### Portal do dev `web/src/pages/dashboard/gate.astro` + `dashboard-v2/GatePortal.jsx`

Island `client:load` protegido pelo middleware do dashboard (JWT no cookie). Seções: **API key**
(prefixo mascarado + regenerar → nova key exibida 1×), **projetos** (lista + verificação + novo
projeto), **histórico de runs** (score colorido, status, CI) com **detalhe expandível** (findings
formatados como o CLI + `checks_blocked` com **CTA de upgrade** para `/security-gate#planos`),
**integração** (snippet copiável). Auth por cookie (dual-auth no backend).

### Admin `web/src/pages/painel/gate-plans.astro` + `admin/GatePlansPage.jsx`

Lista os planos (preço/scans/domínios/checks/histórico/status), **edita** (modal com todos os
campos + seletor de checks, incluindo `["all"]`), **cria** novos, lista **contas dev** com uso e
permite **atribuir plano** manualmente (efeito imediato). Item "Gate Plans" no `AdminShell`; métodos
`gate*` adicionados ao `adminApi.js` (Bearer admin).

## Fix incidental

`useAsync` devolve `error` como **string** (não objeto) — ajustei os usos no island para
`message={error}` (evita quebrar o build).

## Testes / validação

- **+16 testes backend** (`test_kl151_p3_portal_admin.py`): planos públicos (checks_count Free=4,
  Team=18), key-info (mascarado/sem-key/sem-sessão), dual-auth (portal via cookie JWT), admin
  (requer admin; listar/editar/criar/duplicado-409/campos-422; contas dev; atribuir plano/404).
- **Astro build ✓** (landing + portal + admin + islands compilam), **`npm run test:unit` 154 ✓**.
- **Store P3 validada contra Postgres 16 real** (`update_gate_plan` dinâmico + jsonb,
  `create_gate_plan` + slug duplicado, `list_gate_dev_accounts` com subqueries de uso).
- **Suíte backend: 2167 passed, 1 skipped.**

## Notas

- A landing/portal usam React islands (`client:load`) já suportados pela CSP pública (os 3 hashes do
  Astro cobrem a hidratação); os snippets da landing são `<details>` estáticos (zero JS inline novo).
- O CTA da landing aponta para `/cadastrar?type=developer` — o fluxo de registro dev completo (UI de
  onboarding + verificação guiada) é do Prompt 4.

## Docs

- `CLAUDE.md` §9 (continuação do card, Prompt 3/4). `docs/API.md` já lista os endpoints do Gate (P2);
  os novos (`/gate/plans`, `/account/gate/key-info`, `/admin/gate/*`) seguem o mesmo prefixo.

## Próximo prompt

4/4 = segurança avançada (audit log, rotação de keys), Enterprise (CNPJ/contrato), publicação do CLI
no PyPI, onboarding guiado do dev.
