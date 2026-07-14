# KL-57 — Perfil no resultado do scan + gestão de conta + dashboard admin

**Status:** implementado, testes verdes (offline), builds verdes (Astro + admin).
**Data:** 2026-07-14

Três frentes de maturidade da plataforma, sem tocar na lógica de scan/score:

1. o resultado do scan passa a **conectar com o perfil público** `/site/{dominio}`;
2. o usuário **gerencia a própria conta** (nome, senha, exclusão);
3. o painel admin ganha **totalizadores** + **saúde do sistema** na home.

---

## 1. Perfil integrado ao resultado do scan

**Backend (`api/main.py`).**
- `_profile_info(url)` (novo, best-effort): resolve o alvo pela URL e devolve
  `{has_profile, profile_domain}`. `has_profile` é True só quando existe
  `site_profile`, `public_visible` **não** foi desligado (KL-56) e o alvo **não** foi
  descartado — exatamente o critério de visibilidade de `/public/profile/{domain}`
  (KL-51 f4). Como o perfil é gerado em **background** após o scan (KL-51 f5), na 1ª
  análise de um site ainda pode não existir → o front mostra "sendo gerado".
- `GET /scan/summary` anexa `has_profile`/`profile_domain` em **ambos** os caminhos
  (resultado existente e scan novo). Vale para o tier gratuito e o completo — é só o
  sinal do link, não vaza nenhum dado do perfil.
- `profile_link_clicked` entrou em `_KNOWN_EVENTS` (KL-21) → mede conversão scan → perfil.

**Frontend (`web/src/components/scan/ScanFlow.jsx`).**
- `ResultView` lê `data.has_profile`/`data.profile_domain`.
- **"🔗 Ver perfil público"** (link para `/site/{dominio}`, nova aba) ao lado dos
  botões de PDF/e-mail (`PdfControls`), só quando há perfil.
- Nova seção **"Compartilhar"** (`ShareSection`) no fim do relatório: mostra
  `klarim.net/site/{dominio}`, botão **Copiar link** (clipboard + feedback "Link
  copiado ✓") e **Abrir perfil →**. Sem perfil ainda → aviso discreto "disponível em
  instantes". Sem pop-up/modal/redirect — link natural no fluxo.
- Tracking `profile_link_clicked` nos dois pontos de clique.

## 2. Gestão de conta do usuário

**Backend (`api/main.py` + `discovery/store.py`).**
- `PUT /account/me {name}` → `store.update_user_name` (sanitiza HTML; e-mail é a
  identidade da conta, não muda por aqui).
- `POST /account/change-password {current_password, new_password}` → confere a atual
  via bcrypt (`get_user_by_email(with_hash=True)` + `verify_password`), exige nova ≥ 8
  chars, grava com `set_user_password`. **Não** invalida a sessão. Rate limit
  5/e-mail/10min (reusa `_reset_attempts`, chave `change:`).
- `DELETE /account/me {password}` → confirma por senha → `store.delete_user` (**CASCADE**
  apaga `user_sites`; `targets`/`scans`/`site_profile` são dados do sistema e
  **permanecem** — o perfil público segue no ar). Limpa o cookie de sessão. Envia
  `account_deleted.html` em background (best-effort).
- `store.update_user_name` / `store.delete_user` (novos). `_user_public` passou a
  expor `created_at` (para a tela mostrar a data de criação).
- Novo template `notifier/templates/account_deleted.html` +
  `KlarimMailer.send_account_deleted`.

**Frontend (`web/`).**
- Página SSR `/dashboard/conta` (`pages/dashboard/conta.astro`, protegida pelo
  middleware) + ilha `components/account/AccountSettings.jsx`: dados pessoais (nome +
  salvar), segurança (alterar senha inline), plano (read-only + data de criação), zona
  de perigo (excluir conta com confirmação por senha inline). Dark mode, tokens de `ui.js`.
- Link **"Minha conta"** no `Header.astro` (estado logado) + **"Gerenciar conta →"** no
  card de plano do `Dashboard.jsx`.
- `lib/api.js`: `apiPut` (novo) e `apiDelete` agora aceita body.
- Eventos `password_changed`/`account_deleted` (KL-21) para churn.

## 3. Dashboard admin — totalizadores + saúde

**Backend.**
- `store.dashboard_summary()` (novo): em poucas queries numa conexão (sem N+1, sem
  full scan caro) agrega **alvos** (total, por status, `score_100`), **scans** (total,
  média, semáforo, **manual** vs **automatizado**, hoje, 7 dias), **perfis/landings**
  (total, públicas, ocultas, com IA, com CNAE), **contas** (total, ativas, sites
  monitorados) e **alertas** (total, hoje).
- `GET /admin/dashboard-stats` (JWT admin, prefixo `/admin`): devolve o summary +
  `inbox.unread`.
- **Manual vs automatizado:** `scanned_by_email IS NOT NULL` = scan do site público
  (alguém digitou a URL); `NULL` = scan worker/discovery. A coluna já existia (KL-25) —
  não foi preciso `scan_source` novo.

**Frontend (`frontend/`, painel React).**
- `adminApi.dashboardStats()`.
- `pages/admin/Overview.jsx`: grade de **totalizadores** (Scans, Landings, Score 100,
  Contas, Scans manuais, Scans automáticos), grade de **enriquecimento** (perfis, com
  IA, com CNAE, scans 7d) e card **Saúde do sistema** (workers ▶️/⏸️/🔴 +
  postgres/redis/ct_logs via `/system/status`, best-effort com fallback). Mantém os
  KPIs de negócio, os gráficos e a atividade recente que já existiam.

---

## Testes

Novo `tests/test_kl57_account_dashboard.py` (16 testes, offline, TestClient + FakeStore):
atualizar nome (+ auth + sanitização), alterar senha (sucesso / senha atual errada 401 /
nova curta 400), excluir conta (sucesso + cookie limpo / senha errada 401 / CASCADE de
`user_sites` / preserva targets), dashboard-stats (requer admin 401 / todos os campos +
inbox + manual vs auto) e `_profile_info` (com perfil visível / sem perfil / oculto /
descartado). **16 passed.**

Regressão: `test_accounts.py`, `test_events.py`, `test_kl51_f4_profiles.py`,
`test_system.py` → **50 passed**. Builds: Astro (`web/`) e admin (`frontend/`) OK.

## Arquivos

- `api/main.py` — `_profile_info`, `has_profile` no summary, `PUT/DELETE /account/me`,
  `POST /account/change-password`, `GET /admin/dashboard-stats`, `_user_public.created_at`,
  eventos KL-57.
- `discovery/store.py` — `update_user_name`, `delete_user`, `dashboard_summary`.
- `notifier/email_client.py` + `templates/account_deleted.html` — `send_account_deleted`.
- `web/` — `ScanFlow.jsx` (perfil + compartilhar), `AccountSettings.jsx` (novo),
  `pages/dashboard/conta.astro` (novo), `Header.astro`, `Dashboard.jsx`, `lib/api.js`.
- `frontend/` — `pages/admin/Overview.jsx`, `lib/adminApi.js`.
- `tests/test_kl57_account_dashboard.py` (novo).
- `CLAUDE.md` (seção 42).

## Regras invioláveis

- A exclusão de conta **nunca** remove `targets`/`scans`/`site_profile` (o perfil público
  permanece).
- O resultado gratuito **continua** sem detalhe dos checks pagos (KL-27); `has_profile` é
  só o sinal do link.
- Os totalizadores são queries agregadas (sem N+1, sem full scan caro).
