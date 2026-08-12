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
| POST | `/account/signup` | **KL-82 S2** — cria conta na hora (e-mail+senha≥8, `email_confirmed=false`) + welcome com link; nasce confirmada se o e-mail já foi verificado no scan. Blocklist de descartáveis (400) + rate limit **3/h & 5/dia por IP** (KL-85) |
| POST | `/account/signup-inline` | **KL-99 Fluxo D + KL-105** — cadastro SEM senha no resultado do scan (`{email, domain}`): cria conta nível 1 (`source=inline`), **ATIVA o monitoramento na hora** (vincula site + vigílias + posse Tier 1) e **loga** (cookie). SEM confirmação prévia (converte). `{status: monitoring_active}` (+cookie) ou `{status: already_exists}` (o front dispara magic link). Welcome valida o e-mail (bounce→blocklist). Descartáveis 400 + rate limit **5/min & 30/dia por IP** |
| GET | `/account/monitoring-status?domain=` | **KL-105** — o visitante está logado e monitora ESTE domínio? `{logged_in, monitoring, user_email?}`. Auth **opcional** (sem sessão → `logged_in:false`, nunca 401); só o e-mail do próprio usuário. Rate limit 30/min/IP |
| POST | `/account/magic-link` | **KL-99** — envia link de acesso HMAC (`typ=magic`, TTL 1h) p/ conta sem senha voltar; `{status: sent\|not_found}`; rate limit 3/h/e-mail & 10/h/IP |
| POST | `/account/verify` | (fallback dormente) verifica e-mail por código de 6 dígitos |
| POST | `/account/login` | login → cookie de sessão |
| POST | `/account/logout` | encerra sessão |
| POST | `/account/forgot` | código de reset por e-mail (resposta genérica; 3/e-mail/h) |
| POST | `/account/reset` | redefine senha via código |
| POST | `/account/change-password` | troca senha (confere a atual; 5/e-mail/10min) |
| GET/PUT/DELETE | `/account/me` | perfil / editar nome / excluir conta (por senha) |
| GET | `/account/subscription` | plano atual |
| POST | `/account/upgrade` | KL-44 P6: cria cobrança PIX (free→pro/agency, pro→agency) → `{charge_id, br_code, br_code_base64}`; 10/h/IP |
| GET | `/account/upgrade/status?charge_id=` | KL-44 P6: polling do checkout (revalida na AbacatePay + ativa quando pago) |
| POST | `/account/downgrade` | KL-44 P6: downgrade self-service (free/pro); preserva dados, desativa vigílias |
| GET | `/account/payments` | KL-44 P6: histórico de pagamentos de assinatura |
| GET/POST | `/account/sites` | lista / adiciona site ao monitoramento (403 se estourar `max_sites`) |
| GET/DELETE | `/account/sites/{target_id}` | detalhe / **remove self-service** (KL-71: revoga posse + desativa vigílias, sem notificação) |
| POST | `/account/sites/{target_id}/verify/check` | **KL-99** confere a prova de controle de domínio (nível ≥2). **KL-107:** ownership check → **404** se o site não é da conta (antes vazava `200 no_pending`). `{status: verified\|not_found\|no_pending}` |
| — | (`POST /account/sites`, KL-107) | quando um TERCEIRO (is_owner=false) adiciona um site com dono verificado, o dono recebe um e-mail **transacional** `owner_notification` (`klarim@klarim.net`, texto puro, sem link de ação; dedup 1/dia/target). Só o e-mail de quem adicionou é revelado. Fire-and-forget — não altera o response |
| GET/PUT | `/account/sites/{target_id}/monitoring` | **KL-97** — estado + config das vigílias (nível ≥1 + posse). GET: cada tipo com `enabled`/`last_status`/`configurable`/`requires_plan`. PUT `{vigilias:{tipo:{enabled,threshold?}}}` liga/desliga (threshold só `score`); toggle fora do plano → 403 `requires_plan`. Vigília desligada é preservada (enabled=false) |
| GET | `/account/sites/{target_id}/vigilias/{tipo}/details` | **KL-123** — detalhe contextual de uma vigília p/ o card expansível (nível ≥1 + posse). `{tipo,label,status,summary,data,guidance,actions,history,pending_count}` em **linguagem acessível** (sem OWASP/CWE/header raw). `data` é específico ao tipo (ssl: issuer/days_left; score: delta+checks_changed+score_history; phishing: alerts[]; email: SPF/DKIM/DMARC; reputation: blacklisted; uptime: código/tempo; domain: expiry; changes: snapshot). Dado ausente → payload `unknown` gracioso. **Tipo inválido → 404**; site de outro usuário → 404 |
| POST | `/account/sites/{target_id}/vigilias/phishing/dismiss/{alert_id}` | **KL-123** — o dono marca um domínio suspeito como "não é ameaça" (nível ≥1 + posse). Descarte escopado por (id, target, user) → `typosquat_alerts.dismissed=true`; **404** se o alerta não é da conta. `{ok,dismissed,alert_id}` |
| POST | `/account/sites/{target_id}/vigilias/{tipo}/acknowledge` | **KL-123** — marca a vigília como vista/resolvida (nível ≥1 + posse). Grava `acknowledged_at` no `last_data` (some o badge do card até um novo alerta). `{ok,acknowledged,acknowledged_at}` |
| GET/PUT | `/account/notification-preferences` | **KL-97** — `{bulletin_frequency,bulletin_hour,notify_vigilia,notify_bulletin,notify_news}` (nível ≥1). `bulletin_frequency` NULL = usa a do plano; `off` não recebe; `notify_bulletin=false` não recebe (o bulletin worker respeita) |
| PUT | `/account/sites/{target_id}/profile` | **KL-98** — dono verificado (nível 3 + `is_owner`) edita o perfil público. Sanitiza (strip HTML, limites, valida CNPJ/telefone/URL); marca `edited_by_owner` + acumula `owner_edited_fields` (a IA nunca sobrescreve). Campos: company_name/description/phone/whatsapp/address/cnpj/commercial_email/business_hours/instagram/facebook/linkedin/youtube/tiktok/google_maps_url/business_type/tags |
| PUT | `/account/sites/{target_id}/visibility` | **KL-98** — dono liga/desliga a landing `/site/{domain}` (`{public_visible}`, nível 3 + posse) |
| GET/PUT | `/account/sites/{target_id}/seal` | **KL-98** — selo do site (nível 3 + posse). GET: `{enabled,style,score,semaphore,verified,variants:{badge,footer,floating}}` (cada variante com `embed_code`). PUT `{enabled,style}`. O selo público `GET /seal/{domain}` passa a devolver `enabled`/`style`/`verified` (o widget esconde se `enabled=false`) |
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
| GET | `/scan/result` | **KL-82** — resultado result-first SEM e-mail; payload FILTRADO por nível de acesso (`anonymous`/`unconfirmed`/`confirmed`/`alert_session`). Rate limit anônimo **5/h + 20/dia por IP** (429 amigável); conta logada ilimitada. Scan ≠ monitoramento (KL-78). Filtro server-side nunca vaza evidência a anonymous/unconfirmed |
| GET | `/scan/summary` | (legado) dispara/retorna o scan (exige `X-Scan-Token`, `charge_id` pago, ou sessão) |
| POST | `/scan/send-report` | envia os 2 PDFs por e-mail (rate limit 3/e-mail/h) |
| GET | `/account/confirm?token=` | **KL-82 S2** — confirma o e-mail pelo link (JWT-HMAC 30d, `typ=confirm`, idempotente); `{status: confirmed\|already\|invalid}`. Chamado pela SSR `/confirmar` |
| POST | `/account/resend-confirmation` | **KL-82 S2** — reenvia o link (exige login; rate limit 3/h por conta; no-op se já confirmado) |
| GET | `/alert-access?token=` | **KL-82 S3** — link HMAC do alerta (Fluxo 2): valida o token, cria a sessão temporária (cookie `klarim_alert` 24h, escopada a 1 site) e redireciona ao `/scan?url=` com acesso completo. Token inválido → home. Rate limit 30/h/IP |
| POST | `/account/signup-from-alert` | **KL-82 S3** — cria conta só com senha a partir do cookie de sessão do alerta (e-mail do cookie, `email_confirmed=true`/`source=hmac`, vincula+auto-verifica Tier 1). E-mail já existente → `{existing_account:true}`; sem sessão → 401 |
| GET | `/account/dashboard-summary` | **KL-86** — agrega o dashboard num único request (site primário): saúde/tendência/rank, riscos KL-20, checklist priorizado, score_history, 6 categorias, plano, perfil, vigílias. `has_site:false` → checklist de onboarding. `contact_email` nunca exposto |
| PUT | `/account/profile-confirm` | **KL-86** — o dono confirma/edita `company_name`/`phone` do perfil (marca `edited_by_admin`); só o dono do site (403 senão) |
| GET | `/scan` | (rota SSR do fluxo antigo) |
| GET | `/benchmark` · `/benchmark/{sector}` · `/benchmark/all` · `/benchmark/cnae/{division}` | KL-44 P5: médias/mediana/min/max + distribuição anônima por semáforo (setor ≥10 scans; cache 24h) |
| GET | `/seal/{domain}` | KL-44 P5: dados do selo "Monitorado por Klarim" (score + privacidade + link; público, CORS `*`, cache 1h, 60/h/IP; `seal_type=monitored`, nunca "certificado") |

## Micro-ferramentas SEO (KL-134, `api/tools.py`) — públicas, sem auth

Landing pages de aquisição: cada endpoint reusa um check/analisador JÁ existente da engine
(nunca reimplementa) e devolve JSON simplificado em PT-BR. Única proteção: **rate limit 10/min
por IP** (`tools:rl:{ip}`, fail-open sem Redis, 429 com `Retry-After`). Timeout de **15s** por
request externo → **504** amigável. `?url=` ausente ou inválida → **400**; site inacessível →
**502**. As respostas trazem um bloco `context` com estatística real da base Klarim (copy).

| Método | Path | Reusa | Resposta |
|---|---|---|---|
| GET | `/api/tools/ssl?url=` | `scanner.tls_analyzer.get_tls_info` | `{domain, valid, days_remaining, issuer, protocol, expires_at, grade, checks[], context}` (inválido → `{valid:false, error, checks[]}`) |
| GET | `/api/tools/headers?url=` | `scanner.checks.base.fetch` | `{domain, score:"N/7", headers[{name,present,value?,importance,explanation}], context}` (7 headers de segurança) |
| GET | `/api/tools/lgpd?url=` | `scanner.privacy_checks.scan_privacy` | `{domain, score:"N/8", grade, indicators[{name,status,explanation}], disclaimer, context}` (8 indicadores técnicos reais; grade Adequado/Parcial/Atenção/Inadequado) |
| GET | `/api/tools/tech?url=` | `scanner.tech_detector.detect_tech_stack` | `{domain, technologies[{name,category,version?}], context}` (nomes/categorias amigáveis; vazio → `message`) |
| GET | `/api/tools/email?domain=` | `scanner.checks.dns_util` + seletores DKIM do check 22 | `{domain, score:"N/4", records[{name,status,value?,explanation,detail?,recommendation?}]}` (SPF/DKIM/DMARC/MX; opera sobre DNS, recebe `domain=` não `url=`) |
| GET | `/api/tools/stats` | `dashboard_summary`/`privacy_indicator_stats`/`get_tech_adoption` | agregados da base (`total_sites/profiles/scans`, `privacy{...}`, `tech{...}`, `cached_at`); cache Redis 24h (`tools:stats`) |

> Nota: o tool LGPD expõe os **8** indicadores reais de `privacy_checks` (a spec citava 7 + DMARC;
> DMARC pertence ao tool de e-mail). Nenhum dado é fabricado.

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
| POST | `/webhooks/abacatepay` | webhook (query-secret + HMAC): relatório (KL-27) **e** assinatura (KL-44 P6, idempotente → ativa plano; `.expired` → marca expirado) |

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
| GET | `/public/sitemap-domains` | domínios do sitemap (legado; o sitemap ao vivo é o `/sitemap*.xml` do KL-131) |
| GET | `/sitemap.xml` | **KL-131** — **sitemapindex** (`application/xml`): aponta p/ `/sitemap-static.xml`, `/sitemap-sectors.xml` e N × `/sitemap-profiles-{page}.xml` (N=ceil(perfis/10k)). Cache Redis 1h. O nginx roteia `/sitemap*.xml`→FastAPI |
| GET | `/sitemap-static.xml` | **KL-131** — páginas estáticas indexáveis (~8 URLs) |
| GET | `/sitemap-sectors.xml` | **KL-131** — 1 URL por setor com perfil público (`/setor/{slug}`, exclui 'outro'). Cache 1h |
| GET | `/sitemap-profiles-{page}.xml` | **KL-131** — 1 página de ≤10k perfis (`/site/{domain}`, `ORDER BY domain`), `<lastmod>` do último scan. Cache 1h; page fora de 1..1000 → 404 |
| GET | `/sitemap-blog.xml` | **KL-133** — sub-sitemap dos posts de blog publicados (`/blog/{slug}`). No sitemapindex. Cache 1h |
| GET | `/blog/posts?page=&per_page=&category=` | **KL-133** — lista pública de posts **publicados** (paginada; sem o corpo markdown). `{posts, total, page, per_page, has_more}`. RL 30/min/IP |
| GET | `/blog/posts/{slug}` | **KL-133** — post público por slug (corpo markdown incluído). **404 se não publicado**. RL 30/min/IP |
| GET | `/blog/rss.xml` | **KL-133** — RSS 2.0 dos 20 últimos posts publicados (`application/rss+xml`). O nginx roteia `/blog/rss.xml`→FastAPI (exato); `/blog`+`/blog/{slug}` vão ao Astro |
| POST/PUT/DELETE/GET | `/admin/blog/posts[/{id}]` | **KL-133** — CRUD admin (JWT via prefixo `/admin`). POST cria (default draft; slug gerado do título; 409 se slug duplicado; 422 sem title/content). PUT partial (publicar → seta `published_at`; mudar conteúdo → recalcula reading_time). DELETE = arquiva (soft). GET = lista todos os status. RL 10/min/IP. **Também disponível via MCP** (5 tools em `blog.py`) |
| GET | `/og/{domain}.png` | og:image dinâmico (SVG→PNG, fail-open→favicon) |
| GET | `/card/{domain}.png?format=square\|landscape` | card compartilhável |
| GET | `/widget/{domain}.js?style=` | widget "Verificado por Klarim" (embeddable) |
| GET | `/widget/event?e=&d=&s=` | beacon de impressão/clique (204) |
| GET | `/score/{domain}` | score público (JSON, CORS `*`; `null` se oculto) |
| GET | `/ranking` · `/ranking/{sector}` | rankings por setor (SEO) |
| GET | `/public/sectors` | KL-74: índice de setores com perfil público (≥10 sites): count, média, mediana, distribuição por semáforo, nº score 100. Cache Redis 1h |
| GET | `/public/sector/{slug}?page=&limit=&sort=` | KL-74: detalhe do setor — benchmark + ranking paginado (`sort`=score_desc\|score_asc\|domain_asc) + top fails + sites com score 100. Cache 1h |
| GET | `/public/top-fails?sector=&limit=` | KL-74: checks que mais falham no setor (dos últimos scans públicos). Cache 24h |
| GET | `/public/related?domain=&limit=` | KL-74: sites relacionados (mesmo setor, exclui o domínio; completa com outros). Cache 1h |
| GET | `/public/best` | KL-74: vitrine dos sites com score 100, agrupados por setor. Cache 1h |
| GET | `/public/stats` | KL-74: números da plataforma (total sites/scans/score 100, distribuição, setores mais seguros/oportunidade). **KL-103:** + 3 contadores agregados p/ a social proof da landing — `sites_analyzed` (targets status≠'discovered'), `sectors` (distinct sector≠'outro'), `public_profiles` (site_profile public_visible). Público, sem auth, **só números (sem PII)**, cache Redis 1h + rate limit 30/min. Consumido via `/api/public/stats` |
| POST | `/notify/profile-view` | notifica dono ("alguém consultou"; 1/domínio/24h) |
| GET | `/sectors` | 48 setores + 13 macro-setores |
| GET | `/cnaes/sections` · `/cnaes/divisions` | referência CNAE |
| GET | `/public/laudo/{code}` | KL-44 P3: laudo técnico público (checks + ação prioritária; sem PII; TTL 30d; 30/h/IP) |
| GET | `/public/tech-summary/{domain}` | KL-75: resumo tecnográfico PÚBLICO — só badges booleanos (`has_analytics/cdn/payment/chat/captcha/ecommerce`, `email_provider`, `site_status`, `tech_count`). **Nunca** o stack detalhado (valor agregado). Rate limit 30/min/IP; respeita `public_visible`. Cache 1h |
| POST | `/contact` | formulário de contato → inbox + Resend (best-effort) |
| POST | `/events` | tracking do funil (fire-and-forget, 100/min/sessão) |
| GET/POST | `/unsubscribe?email=&token=` | descadastro (token HMAC constant-time). Params **opcionais**: ausentes → página HTML "Link incompleto" (nunca 422 JSON — evita ruído do pre-fetch de bots). **POST** = one-click RFC 8058 (`List-Unsubscribe-Post`) |
| GET | `/remover?token=` | **KL-102** — página de confirmação do descadastro dos e-mails cold. Token HMAC (propósito `unsubscribe`, codifica email+domínio+remetente, sem expiração). Inválido/ausente → 200 com mensagem genérica (anti-enumeração) |
| POST | `/remover?token=` | **KL-102** — confirma o descadastro: form do browser OU one-click do Gmail/Yahoo (body `List-Unsubscribe=One-Click`). Marca o alvo `unsubscribed` + blocklist + evento `email_log` (`type=unsubscribe`, `from_domain`=sender, target_id). Rate limit 10/min/IP **só p/ tokens inválidos** (o one-click válido nunca é bloqueado). Inválido → 400 |
| GET | `/a/{target_id}` | **KL-138** — redirect CURTO dos links de e-mail (cold + profile_view). Registra o clique server-side (`email_clicks`: target_id + timestamp + IP mascarado /24) e responde **302 → `/site/{domain}`**. Destino FIXO (domínio vem de `targets`, não de parâmetro → **sem open redirect**). `target_id` não-inteiro → 422; inexistente/descartado → 404. Rate limit **30/min/IP** (anti-enumeração). Roteado pelo nginx (`location ~ ^/a/` → FastAPI, sem strip) |
| GET | `/metodologia` | **KL-100** — página estática (Astro SSG) de transparência: o que a Klarim faz/não faz, base legal, direitos do dono. No footer + sitemap |

**KL-104 P1 — deep linking:** os responses de `analytics_events` (`GET /admin/analytics/events` legado / aba Consultas de perfil) e `aa_events` (`GET /admin/analytics/events`) passam a incluir **`target_id`** (de `site_events.target_id`) — o admin usa p/ transformar o domínio em link ao detalhe do alvo (`DomainLink` → `/painel/alvos/{id}`). Sem endpoint novo; só campo adicionado. Auth admin inalterada.

> **KL-74 — endpoints `/public/*` de conteúdo:** rate limit **30/min por IP real**; chamadas SSR
> internas (container Astro → API, sem `X-Forwarded-For`) **não** contam (senão o IP único do
> container estouraria o teto). Só listam sites com perfil público (`site_profile.public_visible`
> ≠ FALSE, `status IN ('scanned','alerted')`) — **nunca** `contact_email`/CNPJ. Badge ✓ só com
> `owner_verified`. Cache Redis agressivo (1–24h) + `Cache-Control public, max-age`.

## Admin — gestão de alvos

| Método | Path | Descrição |
|---|---|---|
| GET | `/targets` · `/targets/{id}` · `/targets/stats` | lista/detalhe/stats. **KL-104 P2:** 15 filtros que combinam com AND — `status`/`platform`/`sector`/`source`/`search`/`low_confidence` + `score` (`0-49`/`50-89`/`90-100`/`sem`) · `semaphore` (`verde`/`amarelo`/`vermelho`/`sem`) · `lead_score` (`alto`/`medio`/`baixo`/`sem`) · `has_email`/`monitored`/`owner_verified`/`has_ai_profile` (bool 3-estados) · `site_type`/`tech` (CSV multi) · `last_scan` (`hoje`/`7d`/`30d`/`nunca`). Response inclui `total` (filtrado) + `total_all` (geral, cache 1h). Tudo parametrizado. O detalhe anexa `profile` (site_profile, KL-52), `classifications` (CNAE) e `owner` |
| GET | `/targets/tech-list` | KL-104 P2: top-20 tecnologias (nome) p/ o dropdown do filtro Tecnologia (cache 1h) |
| POST | `/targets/add` | adiciona alvo (source=manual) + enfileira scan |
| POST | `/targets/{id}/scan` | `?sync=1` → varredura **síncrona** (devolve `score`/`semaphore`); sem `sync` → enfileira |
| POST | `/targets/{id}/rescan` · `/alert` · `/discard` | ações |
| GET | `/targets/{id}/profile` · `/classifications` · `/payments` | anexos |
| GET | `/targets/{id}/tech-stack` | KL-75: stack DETALHADO (nomes/versões/fonte) + `email_provider` + `related_domains` + `status_history` |
| GET | `/admin/targets/{id}/intelligence?before=&limit=` | **KL-104 P3** — Visão 360° numa chamada: `monitoring` (quem monitora + vigílias + dono verificado + técnico), `funnel` (6 etapas + e-mails enviados/summary + lead score), `visitors` (consultas/IPs **mascarados /24** + cross-site com `target_id` + fontes de tráfego), `timeline` (eventos de scans/alertas/perfil/status/descoberta em ordem DESC, paginação por cursor `before`+`has_more`/`next_cursor`). Cada seção é **isolada** — falha vira `null`/`{error}`, nunca quebra o response. IP completo NUNCA sai do backend |
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
| GET | `/alerts` · `/alerts/stats` · `/alerts/daily` | alertas. **`/alerts/stats`** (e `/alerts/profile-view-stats`): `{today, week, month, total}` = **tentativas** (sent+bounced+`soft_bounced`+complained; `blocked` não) + breakdown `{key}_sent`/`{key}_bounced` (fix 24/07 — antes só `sent`, escondia bounces). `soft_bounced` = bounce transitório rastreado sem descartar o alvo |
| GET | `/rescans` · `/rescans/stats` | rescans |
| GET | `/payments/list` · `/payments/stats` | pagamentos de relatório (com `target_id`) |
| GET | `/payments/subscription-stats` | KL-44 P6: receita de assinaturas (total/por plano/status/recentes) |
| GET | `/admin/config` | KL-44 P6: `TRIAL_EXPIRATION_ENABLED` (bool), `TRIAL_HOUR_UTC` |
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
| GET | `/admin/gcs-archive/stats` | KL-77: saúde do arquivamento de responses brutos no GCS (arquivos/bytes hoje, último upload, erros) |
| GET | `/monitoring/admin/list` · `/stats` · POST `/{id}/status` | sites monitorados |
| GET | `/system/status` · `/system/activity` · `/system/email-health` | operação em tempo real |
| GET | `/system/email-verification-stats` | KL-110/**125**: verificação de deliverability — `by_status` (`email_verify_status`), **`by_source`** (KL-125: power/quick/bulk/local/unverified — precisão da fonte), role-based, saldo Reoon. Regra KL-125: `unknown` NUNCA envia (`is_safe_to_send`=False); o alert worker reverifica via Power os `unknown` de fonte não-power |
| GET | `/discovery/status` | estado do CT poller |
| GET | `/config` | params operacionais (sem segredos) |
| GET | `/analytics/funnel\|abandoned\|campaigns\|pages\|events\|public-scans` | analytics (KL-21, legado) |

### Analytics admin redesenhado (KL-83, `api/admin_analytics.py`) — todos admin-only, `period=today\|7d\|30d\|90d\|custom` (`start`/`end` ISO, ≤90d), rate limit 30/min/IP, cache Redis 5 min (exceto events/sessions)

| Método | Path | Descrição |
|---|---|---|
| GET | `/admin/analytics/metrics` | 6 KPIs (visitors, scans, accounts, conversion, pv/sessão, alert_click_rate) com value/previous/change_pct/sparkline |
| GET | `/admin/analytics/trend` | séries diárias (`?metrics=visitors,scans,accounts`) para o gráfico de tendência |
| GET | `/admin/analytics/funnel` | funil de 7 etapas + `by_campaign` + `conversion_from_previous` + gargalo + comparação c/ período anterior |
| GET | `/admin/analytics/events` | stream paginado com filtros AND (`type` multi, `domain`, `campaign`, `path`) + contadores; **não cacheado** |
| GET | `/admin/analytics/sessions` | eventos agrupados por sessão (converted/duration); **não cacheado** |
| GET | `/admin/analytics/pages` | páginas (views/sessions/bounce/next_page/conversion/delta) + grupos (Prompt 2 no front) |
| GET | `/admin/analytics/journeys` | top caminhos normalizados (`/site/{domain}`, `/setor/{slug}`, `alerta`, `[saiu]`) |
| GET | `/admin/analytics/funnel-by-sector` | funil segmentado por `targets.sector` |
| GET | `/admin/analytics/alert-quality` | **KL-85** — qualidade do lead scoring: distribuição do `alert_quality_score`, quanto seria filtrado (<20), médias, alertas enviados no período. `click_rate` por faixa / `top_disqualify_reasons` exigem log por-envio (não na Parte 1) → nulos |
| GET | `/admin/analytics/events/export` | **KL-64** — export CSV (StreamingResponse, `text/csv`, `Content-Disposition`), mesmos filtros da aba Eventos + `is_human`; cursor `fetchmany(1000)`, teto 10k (`X-Truncated: true` + linha de aviso). Colunas: `timestamp,event_type,page,domain,campaign,session_id,is_human,referrer` |

> **KL-64 — filtro de bots (`is_human`):** todos os endpoints acima que leem `site_events` filtram
> **`(is_human = TRUE OR is_human IS NULL)` por padrão** — só humanos verificados; `is_human IS NULL`
> preserva o histórico. `?include_bots=true` desliga o filtro (debug; toggle no admin). O tracker
> (`public/track.js`) só marca `is_human=true` após interação humana (scroll/click/5s), então o
> pre-fetch de servidores de e-mail não é contado. Coluna `site_events.is_human BOOLEAN` (NULL default).
> As 2 MCP tools (`get_analytics_metrics`, `get_analytics_funnel`) aceitam `include_bots`.

### Access log server-side (KL-92, `api/admin_analytics.py` + `access_log_middleware.py` + `bot_classifier.py`) — admin-only, cache Redis 5 min, rate 30/min/IP

Fonte de verdade das métricas de visitante (o tracker.js infla ~5x com pre-fetch de e-mail). Um
middleware HTTP grava cada request não-estático na tabela `access_log` com o IP real
(`CF-Connecting-IP`), país (`CF-IPCountry`), user_id (JWT) e a classificação bot/humano do
`bot_classifier` (IP próprio → autenticado → datacenter → crawler UA → rate >50/h → pré-fetch).
Gravação fire-and-forget (buffer + flush batch 5s). Retroatividade: ação humana marca o IP não-bot
no dia. LGPD: IP retido 90d depois anonimizado (trunca último octeto); **IP mascarado em todo
response** (1 octeto em ip-behavior, 2 em ip-detail), completo só no banco.

**P3 — duas fontes (cobertura completa):** o middleware só vê `/api`+`/mcp` (~12% do tráfego). O
parser `api/nginx_log_parser.py` lê o access_log do Nginx (páginas Astro: `/`, `/scan`, `/site/*`,
`/setor/*`) e insere na MESMA tabela — pulando `/api`/`/mcp`/assets (disjunto → sem duplicar).
Coluna `access_log.source` (`middleware`|`nginx`). Classificação do parser via `classify_bot_simple`
(sem contexto de request). Sem novos endpoints/params — os 3 endpoints acima passam a refletir 100%
do tráfego. **Fix P0:** `hourly_heatmap` usava alias SQL reservado (`hour`) → 500; corrigido.

| Método | Path | Descrição |
|---|---|---|
| GET | `/admin/analytics/server-metrics` | `?period=today\|7d\|30d\|90d` — visitantes BR/total (IPs únicos, `is_bot=false`), `bots_filtered`, scans, contas, PDFs, `alert_clicks_br`, `profiles_viewed_br`, `unique_domains_queried`, `top_countries`, `top_endpoints`, `hourly_distribution` (24h densa). **P2:** + `server_funnel` (visitante→perfil→scan→conta→PDF + `conversion_rates`), `top_domains` (≤20), `daily_series` (tendência), `hourly_heatmap` (grade 7×24) |
| GET | `/admin/analytics/ip-behavior` | `?period=…` — `multi_site_visitors` (consultaram >1 domínio), `returning_visitors` (ativos em >1 dia), `avg_sites_per_visitor`, `top_multi_site_ips`/`top_returning_ips` com `ip_masked` (1º octeto). **P2:** + `pre_signup_journey` (por IP, -24h a +7d), `typical_journey`, `post_signup_retention` (D1/D3/D7). Cache **10 min** |
| GET | `/admin/analytics/ip-detail` | `?ip={ip}` (IP completo, admin-only; 422 se inválido) — first/last seen, dias ativos, domínios consultados, ações, user_id, is_bot, timeline. `ip` no response mascarado (2 octetos) |

> **P2 (jornada/retenção):** chaveadas por **IP**, não user_id — no `POST /signup` a conta ainda não tem cookie (`user_id` NULL); o user_id é recolhido das requests pós-signup.
> **MCP:** `get_server_metrics` (omite `hourly_distribution`/`daily_series`/`hourly_heatmap`), `get_ip_behavior` (omite a lista detalhada de jornadas), `get_ip_detail(ip)`.

### Taxonomia aberta de setores (KL-84, `api/admin_sectors.py`) — admin-only (prefixo `/admin` → middleware JWT)

| Método | Path | Descrição |
|---|---|---|
| GET | `/admin/sectors?status=all\|proposed\|official\|approved\|rejected\|merged` | contadores (`stats`: por status, total classificado, 'outro' + %), `emerging` (propostos) e `taxonomy` (official+approved, ou o status filtrado) |
| GET | `/admin/sectors/{slug}/examples?limit=` | domínios de exemplo do setor (ajuda a curadoria); 404 se o setor não existe |
| POST | `/admin/sectors/{slug}/approve` | aprova um setor proposto → `approved` (passa a aparecer em `/setores`). Body opcional `{label, macro_sector}`. 404 se não-proposto, 422 macro inválida |
| POST | `/admin/sectors/{slug}/merge` | body `{merge_into}` — mescla o proposto num setor official/approved: reclassifica os sites (exceto `manual`) e devolve `reclassified_count`. 422 destino inválido / self |
| POST | `/admin/sectors/{slug}/reject` | rejeita o proposto → `rejected`; os sites voltam para 'outro' (exceto `manual`) |

> **Público (KL-84):** `/public/sectors` e `/public/sector/{slug}` passam a filtrar pela tabela `sectors` — só `official`/`approved` aparecem; setor `proposed`/`rejected`/`merged` → **404**. Os rótulos dos setores aprovados novos vêm da tabela (cache 1h em-processo).

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

## Security Gate — produto para devs (KL-151, `api/gate.py`)

Duas famílias de auth: **API key** (header `X-API-Key: KLM_…`) para o dev/CLI em `/gate/*`; **JWT de
usuário** (cookie) para o dashboard em `/account/gate/*`. A key vive só como **SHA-256** (nunca em
claro) e é exibida **UMA VEZ** (no registro/regeneração). Domínio só escaneia se **verificado** (desafio
de domínio OU convite do dono). Planos (Free/Pro/Team/Enterprise) limitam scans/dia, domínios e checks —
enforcement **no servidor**. O endpoint de scan, a CLI e o frontend são os Prompts 2-4.

| Método | Path | Auth | Descrição |
|---|---|---|---|
| POST | `/gate/register` | público | Cria conta `developer` + API key (1×) + 1º projeto + trial Pro 14d. E-mail já com conta → **409 estruturado** (`account_exists`+`login_url`; `activate_after_login=true` se o Gate ainda não está ativo) |
| **POST** | **`/account/gate/activate`** | **JWT (nível ≥1)** | **Ativa o Gate numa conta EXISTENTE (owner→both) + API key (1×, se não houver) + trial Pro 14d. Idempotente (`already_active`)** |
| **GET** | **`/account/gate/status`** | **JWT (401 se sem sessão)** | **Estado p/ a landing/dashboard decidir o que renderizar. KL-153: `{logged_in, gate_active, is_developer, kyc_completed, has_api_key, api_key_prefix, has_projects, projects_count, plan, plan_slug, scans_used_hour, scans_limit_hour, access_level, suspended, dashboard_url}`** |
| **POST** | **`/account/kyc`** | **JWT (email confirmado)** | **KL-153/KL-163 P2 — KYC progressivo. Body `{cpf, address?, phone?}`. **`address` aceita um OBJETO estruturado** `{cep, street, number, complement?, neighborhood, city, state}` (KL-163 P2 → gravado em `address_data` JSONB; CEP normalizado `00000-000`, UF validada, `complement` opcional; **422** se CEP/UF inválidos ou campo obrigatório ausente) **OU uma string** de texto livre (legado → `address` TEXT, ≥10 chars). `kyc_completed=TRUE` só com CPF válido + endereço válido + telefone + e-mail confirmado. 422 CPF inválido · 409 CPF de outra conta · 403 sem e-mail confirmado. `phone_verified`=TRUE quando há telefone (verificação por SMS é futura — o telefone aparece "(não verificado)" no admin)** |
| **POST** | **`/account/gate/upgrade`** | **JWT (nível ≥2)** | **KL-153 — upgrade de plano do Gate via PIX (AbacatePay, avulso mensal). Body `{plan: pro\|team}`. Retorna `{checkout_url, plan, price_display, charge_id, br_code, br_code_base64}`. 409 se já no plano. Webhook `gate:{slug}` ativa o `gate_plan_id`** |
| POST | `/account/gate/regenerate-key` | JWT | Revoga as keys ativas e emite uma nova (1×) |
| GET | `/account/gate/keys` | JWT | Lista keys (prefixo/estado/uso — nunca o valor) |
| **POST** | **`/gate/scan`** | **API key ou JWT** | **KL-153: roda a engine no SERVIDOR contra a URL. `project_id` opcional (ausente → casa por domínio OU **scan avulso** sem projeto, exige e-mail confirmado). ANTES do scan: conta suspensa→403; rate limit 3 camadas (IP→conta→domínio→intervalo)→429 `{limit_type, retry_after_seconds, upgrade_url}`+`Retry-After`; abuso (>20 domínios/24h)→suspende. Resultado **filtrado por KYC**: sem KYC→resumido (`score`+`categories`+`kyc_required_for_details`); com KYC→completo (`results`+`history`+`ci_snippet`). Persiste em `gate_runs`, audit com `cpf`/`score`/`passed`** |
| **GET** | **`/gate/runs`** | **API key** | **Runs da conta (sumário; filtro `project_id`, `limit`)** |
| **GET** | **`/gate/runs/{id}`** | **API key** | **Detalhe do run (com `results`); 404 se não é da conta** |
| **GET** | **`/gate/runs/{id}/pdf`** | **API key** | **Exporta o run como PDF no formato FORNECEDOR (vendor-style, categorias resumidas) — KL-152 P3** |
| **GET** | **`/gate/runs/{id}/report`** | **API key ou JWT + KYC** | **KL-163 — PDF DETALHADO do run (cabeçalho + todas as categorias com cada check + recomendação nas falhas + resumo + rodapé paginado). `application/pdf` `attachment` (filename `klarim-gate-{domínio}-{data}.pdf`). Validação: run inexistente→404; run de OUTRA conta→403; **conta sem KYC→403** ("Complete seu cadastro para gerar relatórios"). O **CPF entra SEMPRE mascarado** (`***.***.NNN-NN`) — nunca em claro no documento. KL-163 P2: o cabeçalho inclui **cidade/UF** (do `address_data`), NUNCA o endereço completo (rua/número)** |
| **POST** | **`/gate/vendors`** | **API key + Enterprise** | **Cria fornecedor + roda o 1º scan (terceiro REDIGIDO). 403 sem Enterprise — KL-152 P3** |
| **GET · GET/{id} · PUT/{id} · DELETE/{id}** | **`/gate/vendors[...]`** | **API key + Enterprise** | **Lista/detalhe(+histórico)/edita/remove fornecedor** |
| **POST** | **`/gate/vendors/{id}/scan`** | **API key + Enterprise** | **Re-escaneia o fornecedor (atualiza score/status)** |
| **POST** | **`/gate/vendors/report`** | **API key + Enterprise** | **PDF comparativo dos fornecedores (`vendor_ids`,`title`) → link temporário (1h)** |
| **GET** | **`/gate/vendors/report/{report_id}`** | **API key + Enterprise** | **Baixa o PDF comparativo (base64 no Redis, TTL 1h)** |
| GET · POST | `/gate/projects` | API key | Lista projetos (+ plano + checks permitidos) · cria projeto (respeita o limite de domínios) |
| POST | `/gate/projects/{id}/verify/start` | API key | Gera o desafio (meta_tag/dns_txt/html_file) |
| POST | `/gate/projects/{id}/verify/check` | API key | Confere o desafio → `verified` (rate limit 10/h/IP) |
| POST | `/account/gate/invite` | JWT (dono nível 3 + posse do domínio) | Convida um dev por e-mail (token, TTL 7d) |
| GET | `/gate/invite/{token}` | público | Info do convite (domínio/status/dev tem conta) |
| POST | `/gate/invite/{token}/accept` | JWT (o dev convidado) | Aceita → projeto `verified` (method=invite) |
| DELETE | `/account/gate/invite/{id}` | JWT (dono) | Revoga + REMOVE o projeto do dev + e-mail ao dev (P4) |
| GET | `/account/gate/invites` | JWT | Convites emitidos pelo dono |
| GET | `/gate/plans` | público | Planos ativos (a landing renderiza ao vivo) |
| GET | `/account/gate/key-info` | JWT | Metadados da key ativa (prefixo/datas — nunca o valor) |
| **GET** | **`/admin/gate/audit`** | **JWT admin** | **Audit log de todas as contas (filtros `account_id`/`action`) (P4)** |
| **GET** | **`/account/gate/audit`** | **API key/JWT** | **Audit log da própria conta (ownership) (P4)** |
| GET · PUT · POST | `/admin/gate/plans[/{id}]` | JWT admin | Admin de planos (P3) |
| GET · POST | `/admin/gate/accounts[/{id}/plan]` | JWT admin | Contas dev + atribuir plano (P3) |
| **POST** | **`/admin/gate/accounts/{id}/enterprise`** | **JWT admin** | **CNPJ/contrato/notas Enterprise (P4)** |

> **KL-153 — registro direto como developer:** `POST /account/signup` aceita `source: "security-gate"`
> → cria a conta como `developer`, concede Free + trial Pro 14d e devolve a **API key** (1×) no corpo
> (`{user, account_type: "developer", api_key}`) para o wizard mostrar. Qualquer outro valor/ausência →
> comportamento normal (owner). Reusa o mesmo provisionamento do `/gate/register`.

---

## Tools MCP (80) — `mcp_server/tools/`

Wrapper fino sobre a API/store; auth própria (OAuth 2.1/PKCE + `MCP_API_KEY`). Todas
passam por `_guard` (nunca derrubam a sessão).

- **system.py** — `get_system_status`, `get_email_health`, `get_email_verification_stats` (KL-110),
  `get_discovery_status`,
  `get_config`, `get_gcs_archive_stats` (KL-77), `get_dashboard_stats`,
  `get_enrichment_status`, `get_user_accounts`, `get_email_log`,
  `get_ownership_stats` (KL-68), `admin_remove_user_site` (KL-69, write),
  `get_bulletin_stats` + `list_technician_links` (KL-44 P3)
- **targets.py** — `list_targets`, `get_target`, `get_target_stats`, `search_targets`,
  `add_target`, `update_target_email`, `update_target_status`, `update_target_sector`,
  `classify_targets_batch`, `get_target_classifications`, `get_site_profile`,
  `toggle_profile_visibility`, `update_site_profile`
- **scans.py** — `list_scans`, `get_scan`, `get_scan_stats`, `scan_url`
- **alerts.py** — `list_alerts`, `get_alert_stats`, `send_alert_to_target`
- **payments.py** — `list_payments`, `get_payment_stats`
- **analytics.py** — `get_funnel`, `get_rescan_stats`, `send_report_to_email`,
  `get_analytics_metrics` + `get_analytics_funnel` (KL-83), `get_lead_scoring_stats` (KL-85),
  `get_privacy_stats` (KL-44 P5), `get_sector_stats` (KL-84: saúde da taxonomia + emergentes),
  `classify_target_sector` (KL-84, write: reclassifica 1 alvo por IA sem re-scan),
  `get_server_metrics` + `get_ip_behavior` + `get_ip_detail` (KL-92: access log server-side)
- **workers.py** — `pause_worker`, `resume_worker`, `get_worker_control`,
  `set_alert_throttle`, `set_discovery_config`, `set_scan_config`
- **monitoring.py** — `list_monitored_sites`, `offer_monitoring`
- **leads.py** — `list_leads`, `get_lead_stats`, `get_lead_funnel`
- **inbox.py** — `search_inbox`
- **subscriptions.py** — `list_subscribers`, `get_subscription_stats`
- **vigilia.py** — `get_vigilia_stats`, `list_vigilia_alerts`, `get_typosquat_alerts` (KL-44 P4)
- **tech.py** (KL-75) — `get_tech_adoption` (adoção de uma tech, opc. por setor),
  `get_site_tech_stack` (stack completo por domínio), `get_site_status_history`
  (histórico ativo/parked/abandonado/fora_do_ar por site)
- **gate.py** (KL-151 P2, visão admin) — `list_gate_projects` (todos/por conta), `get_gate_project`,
  `create_gate_project` (extrai o domínio da URL), `list_gate_runs` (por projeto/conta),
  `get_gate_run` (score/findings/checks/metadados)
