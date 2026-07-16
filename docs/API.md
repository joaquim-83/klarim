# Klarim — Referência de API + Tools MCP

> Extraído dos decorators de rota em `api/main.py` (≈140 endpoints) + `mcp_server/tools/`
> (49 tools) e cruzado com o antigo `claude.md`. Histórico completo em `docs/HISTORY.md`.

## Autenticação e proteção

Um middleware (`_admin_auth_mw`) protege os prefixos abaixo (`_PROTECTED_PREFIXES`):

```
/targets  /scans  /alerts  /rescans  /email  /payments  /config
/discovery  /admin  /system  /analytics  /leads  /monitoring/admin
```

- **JWT admin** (`Authorization: Bearer <token>`, `typ=admin`, TTL 24h) — todos os
  prefixos acima. 401 se ausente/inválido/expirado.
- **JWT usuário** (`typ=user`, cookie `klarim_session` HttpOnly, TTL 30d) — endpoints
  `/account/*`. Aceito também via `Authorization: Bearer`.
- **Exceção pública dentro de prefixo protegido:** `POST /email/webhook`
  (`_PUBLIC_UNDER_PROTECTED`) — tem auth própria (token Hostinger).
- **Público** — tudo o mais (`/health`, `/scan/*`, `/payment/*`, `/report/*`,
  `/recovery/*`, `/webhooks/*`, `/public/*`, `/site`, `/score`, `/og`, `/card`,
  `/widget`, `/ranking`, `/notify`, `/unsubscribe`, `/monitoring/*` exceto admin,
  `/sectors`, `/cnaes/*`, `/benchmark*`, `/.well-known/oauth-*`, `/auth/login`).
- **MCP** (`/mcp/*`) tem auth própria (OAuth 2.1/PKCE + `MCP_API_KEY`), fora do JWT admin.

`_verify_token` exige `typ=admin` (um cookie de usuário assinado com o mesmo
`JWT_SECRET` **não** passa no middleware admin).

---

## Autenticação — Admin

| Método | Path | Descrição | Proteção |
|---|---|---|---|
| POST | `/auth/login` | login do operador → `{token, expires_in}` | público, rate limit 5/min/IP |

## Autenticação — Contas de usuário (`/account/*`, JWT usuário)

| Método | Path | Descrição |
|---|---|---|
| POST | `/account/signup` | cria conta (e-mail já verificado no scan + senha ≥8); vincula histórico; 5/IP/h |
| POST | `/account/verify` | verifica e-mail |
| POST | `/account/login` | login → cookie de sessão |
| POST | `/account/logout` | encerra sessão |
| POST | `/account/forgot` | código de reset por e-mail (resposta genérica; 3/e-mail/h) |
| POST | `/account/reset` | redefine senha via código |
| POST | `/account/change-password` | troca senha (confere a atual; 5/e-mail/10min) |
| GET/PUT/DELETE | `/account/me` | perfil / editar nome / excluir conta (por senha) |
| GET | `/account/subscription` | plano atual |
| GET/POST | `/account/sites` | lista / adiciona site ao monitoramento (403 se estourar `max_sites`) |
| GET/DELETE | `/account/sites/{target_id}` | detalhe / **remove self-service** (KL-71: revoga posse + desativa vigílias, sem notificação) |
| POST | `/account/sites/{target_id}/claim` | reivindica posse (KL-71: e-mail == `contact_email` **OU** domínio do e-mail == domínio do site; first-come) |
| POST | `/account/ownership/request-verification` | KL-68: envia código ao `contact_email` do alvo (nunca exposto); retorna `email_hint` mascarado; rate limit 5/h/IP |
| POST | `/account/ownership/verify` | KL-68: valida o código (3 tentativas, TTL 30 min) → dono verificado |
| GET | `/account/ownership/status?target_id=` | KL-68/71: `{is_owner, monitored, verification_available, has_pending_verification, has_other_owner}` |
| GET | `/account/scan-history` | histórico de consultas do e-mail |
| GET | `/account/vigilias` | vigílias do usuário (filtrado por `user_id`, IDOR-safe) |
| GET | `/account/vigilia-alerts` | alertas de vigília do usuário |
| POST | `/account/technician/invite` | KL-44 P3 / KL-71: convida técnico (cria vínculo + laudo — escaneia se preciso; 422 em auto-convite / dono-como-técnico / já-vinculado); 10/h/IP |
| POST | `/account/technician/revoke` | KL-44 P3: revoga vínculo de técnico |
| GET | `/account/technician/links?target_id=` | KL-44 P3: vínculos de técnico do dono |
| GET | `/account/technician/search?email=` | KL-44 P3: `{found, user_id?, name?}` (só técnicos; nunca outros dados) |
| POST | `/account/technician/accept-invite` | KL-44 P3: técnico aceita convite (`invite_code`) |
| GET | `/account/technician/clients` | KL-44 P3: sites dos clientes do técnico (dono mascarado) |
| POST | `/account/shared-report/create` | KL-44 P3: gera laudo compartilhável (`{code, url, whatsapp_url, expires_at}`) |

## Scan público

| Método | Path | Descrição |
|---|---|---|
| POST | `/scan/request-code` | envia código de 6 dígitos (rate limit 3/e-mail/h + 5/IP/h) |
| POST | `/scan/verify-code` | valida código → scan token (HMAC) |
| POST | `/scan/check-credit` | estado do crédito sem enviar código |
| POST | `/scan/rescan` | re-verificação (consome crédito; comparação antes/depois) |
| GET | `/scan/summary` | dispara/retorna o scan (exige `X-Scan-Token`, `charge_id` pago, ou sessão) |
| POST | `/scan/send-report` | envia os 2 PDFs por e-mail (rate limit 3/e-mail/h) |
| GET | `/scan` | (rota SSR do fluxo antigo) |
| GET | `/benchmark` · `/benchmark/{sector}` · `/benchmark/all` · `/benchmark/cnae/{division}` | KL-44 P5: médias/mediana/min/max + distribuição anônima por semáforo (setor ≥10 scans; cache 24h) |
| GET | `/seal/{domain}` | KL-44 P5: dados do selo "Monitorado por Klarim" (score + privacidade + link; público, CORS `*`, cache 1h, 60/h/IP; `seal_type=monitored`, nunca "certificado") |

## Relatórios / PDF

| Método | Path | Descrição |
|---|---|---|
| GET | `/report/executive?url=` | PDF executivo |
| GET | `/report/technical?url=` | PDF técnico |

Exigem `charge_id` pago ou scan token `full` **se** o paywall estiver ligado; com
`PAYWALL_ENABLED=false` (default freemium) o PDF é gratuito.

## Pagamento — AbacatePay PIX

| Método | Path | Descrição |
|---|---|---|
| POST | `/payment/create` | cria cobrança PIX (R$ 19) → QR |
| GET | `/payment/status?charge_id=` | polling do status + `email_status` |
| POST | `/webhooks/abacatepay` | webhook (query-secret + HMAC) |

## Recuperação de relatórios

| Método | Path | Descrição |
|---|---|---|
| POST | `/recovery/request` | gera token + envia link (resposta genérica; 3/e-mail/h) |
| GET | `/recovery/validate?token=` | lista relatórios pagos (e-mail mascarado) |
| GET | `/recovery/download?token=&charge_id=&type=` | PDF via token (validação cruzada) |

## Perfis públicos, SEO e viralidade

| Método | Path | Descrição |
|---|---|---|
| GET | `/public/profile/{domain}` | perfil agregado (sem e-mail/CNPJ/WhatsApp) |
| GET | `/public/sitemap-domains` | domínios do sitemap |
| GET | `/og/{domain}.png` | og:image dinâmico (SVG→PNG, fail-open→favicon) |
| GET | `/card/{domain}.png?format=square\|landscape` | card compartilhável |
| GET | `/widget/{domain}.js?style=` | widget "Verificado por Klarim" (embeddable) |
| GET | `/widget/event?e=&d=&s=` | beacon de impressão/clique (204) |
| GET | `/score/{domain}` | score público (JSON, CORS `*`; `null` se oculto) |
| GET | `/ranking` · `/ranking/{sector}` | rankings por setor (SEO) |
| POST | `/notify/profile-view` | notifica dono ("alguém consultou"; 1/domínio/24h) |
| GET | `/sectors` | 48 setores + 13 macro-setores |
| GET | `/cnaes/sections` · `/cnaes/divisions` | referência CNAE |
| GET | `/public/laudo/{code}` | KL-44 P3: laudo técnico público (checks + ação prioritária; sem PII; TTL 30d; 30/h/IP) |
| POST | `/contact` | formulário de contato → inbox + Resend (best-effort) |
| POST | `/events` | tracking do funil (fire-and-forget, 100/min/sessão) |
| GET | `/unsubscribe?email=&token=` | descadastro (token HMAC) |

## Admin — gestão de alvos

| Método | Path | Descrição |
|---|---|---|
| GET | `/targets` · `/targets/{id}` · `/targets/stats` | lista/detalhe/stats (filtros + `search`) |
| POST | `/targets/add` | adiciona alvo (source=manual) + enfileira scan |
| POST | `/targets/{id}/scan` | `?sync=1` → varredura **síncrona** (devolve `score`/`semaphore`); sem `sync` → enfileira |
| POST | `/targets/{id}/rescan` · `/alert` · `/discard` | ações |
| GET | `/targets/{id}/profile` · `/classifications` · `/payments` | anexos |
| PUT | `/targets/{id}/profile` | edita perfil — texto **+ contatos** (phone/whatsapp/address/socials) + `clear_fields` (KL-67); marca `edited_by_admin`, limpa `low_confidence_fields` |
| POST | `/admin/revalidate-profiles?dry_run=` | KL-67: aplica os filtros de qualidade aos perfis existentes (sem re-scrape); dry-run conta o impacto |
| PATCH | `/targets/{id}/classify` · `/email` · `/status` · `/profile/visibility` | edições inline |
| POST | `/admin/classify-batch` · `/reclassify-domains` · `/reclassify-all` | classificação em massa |
| GET | `/admin/reclassify-status` | progresso |

## Admin — scans, alertas, rescans, pagamentos, leads

| Método | Path | Descrição |
|---|---|---|
| GET | `/scans` · `/scans/{id}` · `/scans/stats` · `/scans/daily` | scans (filtros: `offset`, `from_date`, `to_date`, `distinct_url`, `source`) |
| GET | `/scans/{id}/report/{executive\|technical}` | PDF sem gating |
| GET | `/alerts` · `/alerts/stats` · `/alerts/daily` | alertas |
| GET | `/rescans` · `/rescans/stats` | rescans |
| GET | `/payments/list` · `/payments/stats` | pagamentos (com `target_id`) |
| GET | `/leads` · `/leads/{id}` · `/leads/stats` · `/leads/funnel` | leads (PQL) |
| PATCH | `/leads/{id}` | edita tags/notes/opted_out (só isso) |
| POST | `/leads/recalculate` | recalcula scores |
| POST | `/targets/{id}/revoke-ownership` | KL-68: admin override — remove o selo de dono do alvo |
| GET | `/admin/ownership-stats` | KL-68: donos verificados, por método, funil, taxa |
| GET | `/admin/bulletin-stats` | KL-44 P3: boletins (total/hoje/semana/por freq/técnico) |
| GET | `/admin/technician-links` | KL-44 P3: vínculos dono↔técnico |
| POST | `/admin/clean-blocked-sites?dry_run=` | KL-68/69: remove vínculos de domínio público/institucional (+ notifica os donos) |
| POST | `/admin/users/{user_id}/remove-site` | KL-69: `{target_id, notify}` — remove site do usuário (revoga posse, notifica) |
| POST | `/admin/users/{user_id}/deactivate` | KL-69: `{notify}` — `is_active=false` (bloqueia login) |
| POST | `/admin/users/{user_id}/reactivate` | KL-69: `{notify}` — `is_active=true` |
| POST | `/admin/scan-and-report` | escaneia + ingere + (opcional) e-mail |
| POST | `/admin/resend-alert` · `/send-report` · `/resend-payment` | reenvios (ignora throttle) |
| POST | `/admin/clean-emails` · `/process-bounces` | manutenção de e-mail |
| GET | `/email/log` | log unificado de e-mails (KL-62) |
| POST | `/email/test` · `/send-alert` · `/send-report` | disparos |

## Admin — inbox, config, planos, vigílias, workers, sistema

| Método | Path | Descrição |
|---|---|---|
| GET | `/admin/inbox` · `/{id}` · `/unread-count` | inbox `scan@klarim.net` (filtros `box`, `source`) |
| POST | `/admin/inbox/{id}/read\|star\|archive` | ações do inbox |
| GET/PUT | `/admin/config` · `/admin/config/{key}` | params editáveis ao vivo (KL-44) |
| POST | `/admin/config/reset/{key}` | reseta param |
| PATCH | `/admin/password` | troca senha do admin |
| POST | `/admin/rotate-mcp-token` | rotaciona `MCP_API_KEY` |
| GET | `/admin/system-info` · `/admin/dashboard-stats` · `/admin/clients` | dashboards |
| GET/PUT | `/admin/plans` · `/{id}` | planos (KL-44) |
| GET/PATCH/POST | `/admin/subscriptions*` | assinaturas: `PATCH /{id}/plan` (muda plano + ajusta vigílias via `_sync_user_vigilias`; `free` zera status/vigílias avançadas), `/{id}/trial` (estende N dias), `/{id}/status`, `/bulk`, `/{id}/history`, `/stats`. `account_id == users.id` — a página **Usuários** gere plano por aqui |
| GET | `/admin/vigilias` · `/{id}` · `/stats` · `/admin/vigilia-alerts` | vigílias (KL-44 P2/P4: 8 tipos) |
| GET | `/admin/typosquat-alerts` | KL-44 P4: domínios suspeitos (typosquat/phishing) + stats |
| GET | `/admin/privacy-stats` | KL-44 P5: distribuição PASS/FAIL por indicador de privacidade |
| GET/POST | `/admin/workers/control` · `/pause` · `/resume` | controle de workers |
| GET | `/monitoring/admin/list` · `/stats` · POST `/{id}/status` | sites monitorados |
| GET | `/system/status` · `/system/activity` · `/system/email-health` | operação em tempo real |
| GET | `/discovery/status` | estado do CT poller |
| GET | `/config` | params operacionais (sem segredos) |
| GET | `/analytics/funnel\|abandoned\|campaigns\|pages\|events\|public-scans` | analytics |

## Monitoramento (público)

| Método | Path | Descrição |
|---|---|---|
| POST | `/monitoring/offer` | oferta (confere score 100 no servidor; 10/h/IP) |
| GET | `/monitoring/status` · `/monitoring/sites` | estado / listagem pública (sem PII) |
| POST | `/monitoring/approve` | aprova (token uso único) |
| GET | `/monitoring/remove?domain=&token=` | remove (HMAC) |

## Sistema e OAuth (público)

| Método | Path | Descrição |
|---|---|---|
| GET | `/health` | health check |
| GET | `/` | landing (proxy Astro no Nginx) |
| GET | `/.well-known/oauth-protected-resource` · `/oauth-authorization-server` | metadata OAuth (RFC 9728/8414) |

Webhooks: `POST /webhooks/abacatepay` (AbacatePay), `POST /webhooks/resend` (Resend
Svix), `POST /email/webhook` (Hostinger, token próprio fail-closed).

---

## Tools MCP (49) — `mcp_server/tools/`

Wrapper fino sobre a API/store; auth própria (OAuth 2.1/PKCE + `MCP_API_KEY`). Todas
passam por `_guard` (nunca derrubam a sessão).

- **system.py** — `get_system_status`, `get_email_health`, `get_discovery_status`,
  `get_config`, `get_dashboard_stats`, `get_enrichment_status`, `get_user_accounts`,
  `get_email_log`, `get_ownership_stats` (KL-68), `admin_remove_user_site` (KL-69, write),
  `get_bulletin_stats` + `list_technician_links` (KL-44 P3)
- **targets.py** — `list_targets`, `get_target`, `get_target_stats`, `search_targets`,
  `add_target`, `update_target_email`, `update_target_status`, `update_target_sector`,
  `classify_targets_batch`, `get_target_classifications`, `get_site_profile`,
  `toggle_profile_visibility`, `update_site_profile`
- **scans.py** — `list_scans`, `get_scan`, `get_scan_stats`, `scan_url`
- **alerts.py** — `list_alerts`, `get_alert_stats`, `send_alert_to_target`
- **payments.py** — `list_payments`, `get_payment_stats`
- **analytics.py** — `get_funnel`, `get_rescan_stats`, `send_report_to_email`
- **workers.py** — `pause_worker`, `resume_worker`, `get_worker_control`,
  `set_alert_throttle`, `set_discovery_config`, `set_scan_config`
- **monitoring.py** — `list_monitored_sites`, `offer_monitoring`
- **leads.py** — `list_leads`, `get_lead_stats`, `get_lead_funnel`
- **inbox.py** — `search_inbox`
- **subscriptions.py** — `list_subscribers`, `get_subscription_stats`
- **vigilia.py** — `get_vigilia_stats`, `list_vigilia_alerts`, `get_typosquat_alerts` (KL-44 P4)
