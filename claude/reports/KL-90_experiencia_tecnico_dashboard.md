# KL-90 — Experiência do técnico no dashboard

> **Objetivo:** o "Ver →" da lista "Sites dos meus clientes" deve abrir o **dashboard
> técnico completo** do site do cliente (não o perfil público). O técnico precisa dos
> 48 checks com evidência, "Como corrigir" por plataforma, PDF técnico, linguagem
> técnica, monitoramento (vigílias read-only) + toggle "Receber alertas deste site",
> banner de contexto e "← Voltar". **Nunca** pode ver dados da conta do dono
> (e-mail cru, plano, pagamentos), remover site ou editar perfil. **Segurança:** o
> técnico só acessa sites **vinculados** a ele.

## O que foi feito

### Backend — modo técnico no `dashboard-summary`
- **`api/dashboard.py`**
  - `_mask_email(email)` — mascara o e-mail do dono (`d***o@exemplo.com.br`); regra
    inviolável, o técnico **nunca** vê o e-mail cru.
  - `build_dashboard_summary` ganhou o **ramo técnico** (o caminho do dono é inalterado):
    se `site_id` **não** é um site próprio, busca `get_technician_clients(uid)` e exige um
    **vínculo ATIVO** deste técnico para o alvo → senão **404** (nunca 500, nunca vaza).
    Com vínculo, delega a `_build_technician_view`.
  - `_build_technician_view(store, user, tid, link)` — monta a resposta **técnica completa**:
    site + score + benchmark + **categorias com `evidence` + `fix_inline`** + riscos +
    histórico + **vigílias do DONO em read-only** (`get_user_vigilias(owner_uid)`),
    `technician_mode: True`, `owner_email` mascarado, `can_receive_alerts`,
    `selected_site_id`. **Sem** `plan`, **sem** `checklist`, **sem** dados de conta do dono.
- **`api/main.py`** — `PUT /account/technician/notifications` (`{target_id, enabled}`):
  liga/desliga a cópia de alertas de um site vinculado; `set_technician_alerts` só toca o
  vínculo **ativo** deste técnico → 404 se não existe (segurança).
- **`discovery/store.py`**
  - Coluna idempotente `technician_links.receive_alerts BOOLEAN DEFAULT true` no `ensure_schema`.
  - `get_technician_clients` passa a devolver `receive_alerts`.
  - `set_technician_alerts(tech_uid, target_id, enabled)` — UPDATE só do vínculo ativo do técnico.
  - `get_alert_technicians_for_domain(domain)` — técnicos ativos com `receive_alerts=true`
    (só o e-mail do técnico, nunca dados do dono) para o CC da vigília.
- **`discovery/vigilia_worker.py`** — após enviar o alerta ao dono, `_emit_alert` faz **CC
  best-effort** aos técnicos vinculados que optaram por receber (mesmo `send_vigilia_alert`,
  independente do envio ao dono, nunca expõe o dono; respeita a blocklist).

### Frontend — `TechnicianView` (modo técnico)
- **`web/src/components/dashboard-v2/TechnicianView.jsx`** (novo) — renderizado quando
  `data.technician_mode`: **banner** "🔧 Visualizando como técnico · {domain} · Dono:
  {mascarado}" + "← Voltar para meus clientes"; `<ScoreCard technician>` (PDF **técnico**,
  sem Compartilhar/Vincular); toggle **"🔔 Receber alertas deste site"**
  (`PUT /account/technician/notifications`); `<MonitoringSection>` (vigílias do dono
  read-only); `<CategoryBar technical>` (evidência primária); card **"Prioridades para o
  cliente"** (`<RisksList>`); `<ScoreHistory>`.
- **`CategoryBar.jsx`** — prop `technical`: no modo técnico a **evidência** é primária
  (mono, fundo escuro) e o `risk_message` vira secundário ("Impacto p/ o cliente:");
  no modo dono é o inverso.
- **`ScoreCard.jsx`** — prop `technician`: PDF `technical` ("📄 Relatório técnico");
  esconde "↗ Compartilhar" e "🔧 Vincular Técnico".
- **`TechnicianClients.jsx`** — "Ver →" agora aponta para `/dashboard?site_id={target_id}`
  ("Ver dashboard técnico →"), não mais o perfil público `/site/{domain}`.
- **`DashboardV2.jsx`** — 2 mudanças: (1) branch `if (data.technician_mode) return
  <TechnicianView …>`; (2) **fix do deep-link** — o mount lia sempre `load(null)` e caía no
  dashboard do próprio técnico; agora lê `site_id` da URL (`initialSiteId`) e faz
  `load(initialSiteId || null)`, então `/dashboard?site_id=56` abre o site do cliente já
  selecionado. Owner sem param → `load(null)` (primário) — sem regressão.

## Segurança
- Técnico só acessa sites **vinculados** (vínculo ativo); qualquer outro `site_id` → **404**
  (validado no browser: `?site_id=57` mostra "Site não encontrado", zero dado exposto).
- E-mail do dono **sempre mascarado**; **nenhum** dado de conta do dono (plano/pagamento/
  remover site/editar perfil) no payload nem na UI.
- `set_technician_alerts` e `get_alert_technicians_for_domain` filtram por vínculo **ativo**.

## Testes
- **Backend:** +2 testes em `tests/test_kl90_dashboard_summary.py` (`test_technician_mode`
  — `technician_mode`/owner mascarado/sem plano/sem checklist/6 categorias/evidência;
  `test_technician_mode_unlinked_404` — site não vinculado → 404). Suíte KL-90: **22 passed**.
  Suíte completa: **1525 passed** (as 3 falhas — 2× webhook AbacatePay e 1× CTA do alerta —
  são artefatos de env do container dev: os testes assumem env limpo; passam com as
  variáveis `ABACATEPAY_WEBHOOK_SECRET`/`JWT_SECRET`/`UNSUBSCRIBE_SECRET` **não** setadas,
  como no CI).
- **Frontend:** **96 node --test** passam; **astro build** ok.

## Validação em dev (`tecnico@agencia.com.br` / `dev123456`)
1. ✅ "Ver dashboard técnico →" abre o dashboard técnico do cliente (site 56).
2. ✅ 48 checks com evidência (CSP: evidência primária + fix WordPress/Nginx/Apache).
3. ✅ PDF técnico ("Relatório técnico").
4. ✅ Linguagem técnica (evidência primária, "Impacto p/ o cliente" secundário).
5. ✅ Monitoramento read-only (2 vigílias do dono) + toggle de alertas (persiste ON↔OFF).
6. ✅ Banner "🔧 Visualizando como técnico · … · Dono: d***o@exemplo.com.br" + "← Voltar".
7. ✅ Sem dados de conta do dono (sem plano/remover-site/editar-perfil).
8. ✅ Segurança: site não vinculado → "Site não encontrado"; dashboard do dono intacto;
   sem erros no console.
