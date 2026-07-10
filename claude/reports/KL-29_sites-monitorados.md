# KL-29 — Sites Monitorados: selo de segurança + seção pública para score 100

**Card:** KL-29
**Objetivo:** credibilidade real (não prova social fabricada) + retenção + viralidade.
Sites com **score 100/100** (scan completo, 29 checks) ganham monitoramento gratuito
e aparecem numa seção pública. Se o score cair, o Klarim alerta e remove o selo até
corrigir; se voltar a 100, restaura automaticamente.

---

## Status flow

`pending` (ofertado, aguardando aprovação) → `active` (aprovado, score 100) →
`suspended` (score caiu) → `active` (corrigiu) **ou** `removed` (dono pediu saída).

## 1. Tabela `monitored_sites`

`discovery/store.py` (`_SCHEMA`): `monitored_sites` com `target_id`, `domain` (UNIQUE),
`url`, `display_name`, `logo_url`, `contact_email`, `approval_token` (uso único),
`approved`/`approved_at`, `last_check_score`/`last_check_at`, `status`,
`suspended_reason`. Índices por status e domínio único (upsert por domínio).

**Métodos do store:** `upsert_monitoring_offer` (idempotente por domínio; não
rebaixa `active`/`suspended`), `get_monitored_by_token`/`_by_domain`,
`approve_monitored_site` (uso único do token), `remove_monitored_site_by_domain`,
`get_active_monitored_sites` (público), `get_monitored_for_rescan`,
`update_monitor_check`, `suspend`/`restore_monitored_site`, `list_monitored_sites`,
`get_monitored`, `set_monitored_status`, `monitored_stats`, `count_active_monitored`.

## 2. Oferta de monitoramento (score 100)

- **Na tela** (`Result.jsx`, `is_full` e `score == 100`): bloco "🎉 Score 100/100 +
  Monitoramento gratuito" com input de e-mail (pré-preenchido pelo `contact_email`
  do payload completo) → `POST /monitoring/offer` → redireciona para a página de
  aprovação.
- **Por e-mail** (`monitor_offer.html`): quando o re-scan de re-engajamento (KL-13)
  atinge 100, `_maybe_offer_monitoring` **confere no scan completo (29)** e, se 100 e
  ainda não ofertado, cria a oferta e envia o convite (link com `approval_token`).

## 3. Fluxo de aprovação (tokens)

- **`POST /monitoring/offer {url, email}`** (público, rate-limit 10/h/IP): o servidor
  **confere** que o scan **completo recente** da URL é score 100 (`get_recent_only
  (full=True)`, sem reescanear) — não confia no cliente; **409** caso contrário. Cria/
  reusa o registro `pending` e devolve o `approval_token`.
- **`GET /monitoring/status?token=`** — estado da oferta (para a página de aprovação).
- **`POST /monitoring/approve {token, display_name?}`** — marca `active`, invalida o
  token (uso único), captura o favicon (`https://{domain}/favicon.ico`) como
  `logo_url` e salva o nome da empresa.
- **`GET /monitoring/remove?domain=&token=`** — link de remoção do rodapé dos e-mails;
  token **HMAC** por domínio (`_monitor_removal_token`, casa entre API e worker);
  responde HTML.
- **Frontend:** `/monitorados/aprovar?token=` (`MonitorarAprovar.jsx`) — form com nome
  da empresa + confirmação.

## 4. Re-scan semanal + suspensão/restauração automática

`RescanWorker._monitor_cycle` (loop `_monitor_loop`, `MONITOR_INTERVAL_DAYS=7`):
para cada site `active`/`suspended`, roda o scan **completo (29)**, grava
`update_monitor_check`; **score < 100 e active** → `suspend` + `send_monitor_alert`;
**score == 100 e suspended** → `restore` + `send_monitor_restored`. Roda no mesmo
container (`asyncio.create_task(self._monitor_loop())` no `start`), com o mesmo rate
limit de scan (`WORKER_MAX_SCANS_PER_HOUR`).

## 5. Seção pública `/monitorados`

- **`GET /monitoring/sites`** (público, **sem** `contact_email`/`target_id`/
  `approval_token`): só sites `active`, via `_public_monitored`.
- **`Monitorados.jsx`:** grid de cards (favicon com fallback 🔒, nome, domínio
  clicável, "Score 100/100 🟢", "Verificado em DD/MM/YYYY") + CTA "Escanear meu site".

## 6. E-mails

`monitor_offer.html` (convite), `monitor_alert.html` (score caiu + link de remoção),
`monitor_restored.html` (voltou a 100). `KlarimMailer.send_monitor_offer/alert/
restored`. Assuntos neutros no padrão KL-27 (`{domain} — …`).

## 7. Footer e navegação

Link **"Monitorados"** no footer (ao lado de Sobre/Parceiros). Na landing, prévia
"🔒 N sites com score 100 monitorados → Ver todos" (quando houver).

## 8. Dashboard admin `/painel/monitorados`

Lista por status (pending/active/suspended/removed) com filtros + ações
(aprovar/suspender/reativar/remover) + KPIs. Item **"Monitorados"** na sidebar (entre
Analytics e Sistema). Endpoints `GET /monitoring/admin/list|stats`, `POST
/monitoring/admin/{id}/status` (prefixo `/monitoring/admin` protegido por JWT no
middleware; o público `/monitoring/*` fica livre).

## 9. MCP tools

`mcp_server/tools/monitoring.py`: **`list_monitored_sites(status?)`** (lista +
stats) e **`offer_monitoring(target_id)`** (roda o scan completo, confere 100, cria a
oferta + envia o convite — reusa `_maybe_offer_monitoring`).

## 10. Segurança

- Payload público **nunca** expõe `contact_email`, `target_id` ou `approval_token`.
- Oferta só para sites com **score 100 comprovado no servidor** (anti-abuso) + rate
  limit por IP.
- `approval_token` de **uso único** (nulificado na aprovação); remoção por **HMAC**
  por domínio (constant-time). `/monitoring/admin/*` exige JWT.

## 11. Testes

`tests/test_monitoring.py` (9): payload público sem dados sensíveis, `/monitoring/
sites` seguro, oferta rejeita <100 (409) e cria pending em 100, oferta já ativa,
aprovação ok/inválida, remoção HMAC válida/inválida, admin exige JWT.

## 12. Deploy

Migração automática (`ensure_schema` no boot cria `monitored_sites`). Vars novas
(opcionais, `.env`): `MONITOR_INTERVAL_DAYS` (7), `SITE_BASE` (https://klarim.net).

## Arquivos

**Backend:** `discovery/store.py` (tabela + métodos), `api/main.py` (endpoints +
tokens + `contact_email` no full payload), `discovery/rescan_worker.py` (monitor
cycle + auto-oferta), `notifier/email_client.py` + 3 templates,
`mcp_server/tools/monitoring.py` + `__init__.py`.
**Frontend:** `pages/Monitorados.jsx`, `pages/MonitorarAprovar.jsx`,
`pages/admin/Monitorados.jsx`, `pages/Result.jsx` (oferta), `pages/Landing.jsx`
(prévia), `components/Footer.jsx`, `components/admin/AdminLayout.jsx`, `App.jsx`,
`lib/api.js`, `lib/adminApi.js`.
**Docs:** `claude.md`, `README.md`, este relatório.
