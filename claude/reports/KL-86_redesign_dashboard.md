# KL-86 — Redesign do dashboard: 6 blocos de valor, zero espaço vazio

**Card:** KL-86 · **Prioridade:** High · **Data:** 2026-07-19
**Dependências:** KL-82 ✅ (mais contas → mais dashboards), KL-20 ✅ (riscos), KL-74 ✅ (ranking).

---

## Objetivo

O dashboard antigo era 70% espaço vazio em 1440px. O novo responde em 5s: "meu site está
bem?" (saúde + riscos), "o que mudou?" (tendência + evolução), "o que faço agora?" (checklist).
**Regra de ouro do card:** nenhuma feature nova — só expõe o que o banco já tem.

## Backend — 1 request agrega tudo

**`GET /account/dashboard-summary`** foca no **site primário** (1º monitorado) e reusa os
helpers existentes (nada de query nova pesada):
- **Saúde:** score + `_score_trend` (compara com o scan anterior, ±2 = estável) + `get_sector_position` (rank).
- **Riscos:** `build_risk_summary` (KL-20, top 3, linguagem de negócio setorizada).
- **Checklist** (`_build_checklist`, priorizado 1=urgente): e-mail não confirmado · score caiu ·
  vigília com erro · SSL ≤30d (`_ssl_expiry_days` lê a evidência do `check_42_cert_chain`) ·
  perfil incompleto · corrigir o risco #1 · compartilhar. Sem urgência (prioridade ≤3) →
  insere "Tudo em dia 👏".
- **Evolução:** `score_history` = scores dos scans (via `list_scans`, ASC).
- **Categorias:** `_dashboard_categories` reusa o `_build_categories` (KL-82) mapeando check_id →
  6 categorias, com status ok/warning/critical.
- **Plano:** `plans.get_subscription`. **Perfil:** `get_site_profile` (só company_name/phone/setor).
- **Sem site:** `has_site:false` + `_new_user_checklist` (adicionar site + confirmar e-mail).

Store: **nenhum método novo** — trend/history vêm de `list_scans(limit=30)` (item 0 = atual,
1 = anterior); vigília summary agrega `get_user_vigilias` em Python.

**`PUT /account/profile-confirm`** (onboarding): o dono edita `company_name`/`phone` (reusa
`update_site_profile_fields` → `edited_by_admin=TRUE`; 403 se não for dono).

**Segurança:** `contact_email`/cnpj/whatsapp nunca no payload (teste blinda contra regressão).

## Frontend — 6 blocos, mobile-first

`Dashboard.jsx` reescrito: **1 chamada** ao agregador → renderiza os 6 blocos.
- **Grid responsivo com placement explícito** (`lg:col-start`/`lg:row-start`): desktop 2/3+1/3
  (esquerda: Saúde, Riscos, Evolução, Categorias · direita: Checklist, Plano). A ordem-fonte é a
  **ordem mobile** (Saúde → Checklist → Riscos → Categorias → Evolução → Plano) — o checklist
  sobe no celular, como o card pede; no desktop os `row-start`/`col-start` reposicionam.
- **Bloco 1 (Saúde):** hero com score, tendência (↑/↓/→), rank no setor, anel colorido, último/
  próximo scan. **Bloco 2 (Riscos):** top 3 + "Como corrigir →". **Bloco 3 (Checklist):**
  ações clicáveis (cta/link/modal), "Ver mais". **Bloco 4 (Evolução):** `ScoreChart` SVG puro
  (sem Recharts — leve, sem dependência), 1 ponto → mensagem. **Bloco 5 (Categorias):** grid
  2/3-col, link para o detalhe `#cat`. **Bloco 6 (Plano):** reusa o `PlanSection` (interativo:
  trial/upgrade PIX/downgrade) — decisão consciente para não regredir o checkout.
- **Onboarding** (`ProfileOnboarding`): o item "Complete o perfil" abre editor inline (company_
  name/phone) → `PUT /account/profile-confirm`.
- **Sem site (§8):** buscador "Pesquise qualquer site" + checklist reduzido.
- **Preservado:** banner de confirmação de e-mail (KL-82 S2), toasts (`?claimed/added/confirmed`),
  view de técnico (role), adicionar site, outros sites monitorados.
- **Linguagem:** "Pesquisar" (não "Verificar"), "Olá, {empresa}" (do profiler).

## Decisões

- **Bloco 6 = PlanSection existente** (não reimplementei o checkout PIX no bloco). O agregador
  ainda devolve `plan` (útil/testado), mas o front usa o componente interativo. A regra "1
  chamada" vale para os dados dos blocos 1–5; PlanSection é ilha auto-contida pré-existente.
- **SSL expiry é best-effort** (parse de texto da evidência) — se não achar, o item some (nunca
  mostra dado errado). É "reuso de dado existente" (o scan já traz), não feature nova.
- **ScoreChart em SVG puro** (não Recharts) — Recharts só entra na Overview do painel admin;
  no dashboard público-logado, SVG evita peso de bundle.

## Testes

- **`tests/test_kl86_dashboard.py` (11):** helpers puros (`_dashboard_categories` 6 cats;
  `_ssl_expiry_days`; `_score_trend` up/down/stable; `_build_checklist` e-mail/queda/SSL/all-good;
  `_vigilia_summary`; `_new_user_checklist`); endpoint (auth; sem site; com site — trend/rank/
  categorias/history ASC/perfil; **`contact_email` ausente**; item SSL).
- **Suite:** `1069 passed, 1 skipped` (1058 → 1069). Build Astro **verde**.

## Honestidade

Não fiz uma inspeção visual real em DevTools 375px/1440px — as classes de grid são responsivas
padrão, o placement explícito foi raciocinado e o build compila, mas uma passada visual (ou um
check no browser após o deploy, com uma conta que tenha site monitorado) fecharia 100% o "zero
espaço vazio" e a ordem mobile.
