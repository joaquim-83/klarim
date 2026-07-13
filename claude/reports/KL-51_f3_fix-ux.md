# KL-51 Fase 3 — Fix UX (ajustes pós-teste do dono)

> 5 correções de UX identificadas nos testes manuais dos fluxos de scan, cadastro e
> dashboard. Sem mudança de segurança/score; ajustes de fluxo e conveniência.

## 1. Contato → `scan@klarim.net`

Todas as referências públicas a `seguranca@klarim.net` viraram `scan@klarim.net`:
páginas `/contato`, `/privacidade`, `/sobre`, o HTML de descadastro (`api/main.py`) e os
mailtos de "não receber mais alertas" nos templates de e-mail. O endpoint `POST /contact`
**já** enviava para `scan@` (default de `send_contact`) — só o texto exibido mudou.
**Nenhuma mudança de sender no Resend:** `scan@` é só destinatário/mailto; os envios
continuam saindo do `RESEND_FROM` verificado.

## 2. Usuário logado escaneia sem limite (escanear ≠ monitorar)

**Antes:** logado com 1 site não conseguia escanear outro ("limite atingido").
**Agora:** escanear (consulta) é **ilimitado** para conta logada; o limite do plano vale
**só para monitorar** (adicionar ao dashboard).

- **Backend (`scan_summary`):** nova via de autorização — se o request tem sessão válida
  (`auth_users.optional_user`, cookie **ou** Bearer), o scan é autorizado **sem código de
  e-mail**; o e-mail da conta vira `scanned_by` (liga o scan à conta).
- **Frontend:** `scan.astro` (SSR) valida o cookie e passa `user` ao `ScanFlow`; logado ⇒
  o island **pula e-mail/código** e escaneia direto na montagem. `fetchSummary`/`apiPost`
  passaram a mandar `credentials:'include'` (cookie de sessão).
- **Monitorar** continua limitado: `POST /account/sites` devolve **403** no limite (nunca
  bloqueia o scan).

## 3. Histórico de consultas no cadastro

**Backend (`account_signup`):** após criar a conta, `store.get_targets_scanned_by_email`
busca os alvos que o e-mail já escaneou (`scans.scanned_by_email` do KL-25, ou
`targets.contact_email`) e vincula ao dashboard, **respeitando `max_sites`** (o site do
signup ocupa a vaga primeiro; no free = 1, o histórico não excede).

## 4. CTA de cadastro no topo do resultado + próximo dos PDFs

O CTA de conta aparece em **2 posições** (topo, logo após o benchmark; e reforço no fim):
- **Deslogado:** "Criar conta" (form GET `/cadastrar` com e-mail+url pré-preenchidos).
- **Logado:** "Adicionar ao monitoramento" (`POST /account/sites`) → sucesso "✓ no seu
  dashboard" ou **403** "limite de monitoramento atingido — upgrade". O scan nunca é
  bloqueado, só o monitoramento.

## 5. PDF com dropdown + envio por e-mail

- **Dropdown** substitui os 2 botões: **Relatório Executivo** (linguagem acessível) /
  **Relatório Técnico** (OWASP/CWE/LGPD), cada um baixa o PDF via `/api/report/*`.
- **"Enviar por e-mail"** (`POST /scan/send-report {url, email?}`): gera os 2 PDFs e envia
  via Resend em **background** (rate limit **3/e-mail/h**), resposta imediata com o e-mail
  **mascarado** (`_mask_email`). Logado → usa o e-mail da conta (sem perguntar); deslogado
  → usa o e-mail já verificado no scan (ou pede, se ausente).

## Testes

`tests/test_accounts.py` cresceu para **24 testes** (TestClient + FakeStore). Novos:
- `test_mask_email` (puro), `test_signup_links_previous_scans`,
  `test_signup_history_respects_plan_limit`, `test_send_report_masked`,
  `test_send_report_bad_email`, `test_send_report_uses_session_email`,
  `test_send_report_rate_limit`.
- `conftest.py` reseta o novo bucket `_send_report_attempts`.
- SQL de `get_targets_scanned_by_email` validado por sqlglot; `ScanFlow.jsx` por esbuild.

**Cobertura offline:** o `scan_summary` autorizando por sessão é o mesmo mecanismo
(`optional_user`) já coberto por `require_user`; o end-to-end (scan real disparado por
conta logada) é validado **em produção** após o deploy.

## Regras preservadas

- Escanear nunca é bloqueado; **só monitorar** respeita o plano (403 servidor-autoritativo).
- O e-mail já verificado (KL-25) é reaproveitado — nunca se re-pede verificação a logado.
- `scan@` é só destinatário/mailto — os envios saem do `RESEND_FROM` verificado.
