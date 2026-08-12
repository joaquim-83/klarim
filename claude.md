# Klarim — Guia do Agente CLI

> **Leia antes de tocar no código.** Onboarding obrigatório de qualquer agente Claude no
> Klarim. Se algo aqui conflitar com um pedido, **pare e pergunte** antes de prosseguir.

**Klarim** — *"O alarme que toca antes do ataque."* Scanner **passivo** de segurança web
para **PMEs brasileiras** (hotéis, clínicas, escolas, e-commerces, contabilidades…) sem
equipe de segurança. Plataforma **freemium** "Guardião Digital": descobre alvos, roda checks
comprováveis sem invasão, calcula **score 0–100 + semáforo 🔴🟡🟢**, gera perfis públicos e
monitora silenciosamente — só alerta o dono quando algo importa.

> **📚 Documentação detalhada** (este arquivo é o guia enxuto de instruções):
> - `docs/ARCHITECTURE.md` — arquitetura, containers, fluxo de dados
> - `docs/API.md` — todos os endpoints + tools MCP (request/response completos)
> - `docs/DEPLOY.md` — deploy, CI/CD e **todas as variáveis de ambiente**
> - `docs/SECURITY.md` — políticas de segurança e postura de scanning
> - `docs/LGPD.md` — ROPA (registro de tratamento) + DPO
> - `docs/HISTORY.md` — histórico íntegro das entregas · `claude/DEPLOY_HISTORY.md` — log de deploys
> - `claude/reports/KL-xxx_*.md` — relatório de cada tarefa · `klarim_mvp_spec.md` — spec do produto

---

## 1. Links e acesso

- **Produção:** https://klarim.net · **Admin:** https://klarim.net/painel (Astro). `painel.klarim.net` → 301 ao domínio principal.
- **Repo:** https://github.com/joaquim-83/klarim.git · **Jira (board KL):** https://igoove.atlassian.net/jira/software/c/projects/KL/boards/265/backlog
- **VM GCP:** `klarim-prod` · zona `us-central1-a` · projeto `project-b08050df-fa4e-49ac-919` · deploy em `/opt/klarim` · IP estático `34.135.194.208`. Detalhes de infra/fallback em `claude/DEPLOY_HISTORY.md`.
- **E-mail operacional:** klarimscan@gmail.com
- O `.env` de produção vive **apenas na VM** (`/opt/klarim/.env`), nunca no git.

```bash
gcloud compute ssh --zone "us-central1-a" "klarim-prod" --project "project-b08050df-fa4e-49ac-919"
```

---

## 2. Stack

Python 3.12 / **FastAPI** + **PostgreSQL 16** + **Redis** + **Astro 7** (SSR, Node standalone) +
**React** (islands) + **Tailwind v4** (CSS-first, sem config) + **Nginx** (front único de TLS) +
**Docker Compose** + **WeasyPrint** (PDF) + **Resend** (e-mail) + **AbacatePay** (PIX) +
**OpenAI GPT-4o mini** (enriquecimento).

---

## 3. Regras invioláveis

### Processo
- **Claude Code CLI é o executor; Claude chat é o planejador.**
- Todo pedido precisa de um card **`KL-xxx`** no Jira (exceto ajustes mínimos: typo, formatação). Jira transition "Done" = ID **41**.
- **Commits e código em inglês; comentários podem ser PT-BR.** Formato: `tipo(KL-xxx): descrição`.
- **Cada tarefa gera um relatório PT-BR em `claude/reports/KL-xxx_<slug>.md`** e atualiza a documentação afetada.
- **Rode `pytest` + `npm run test:unit` antes de concluir.** A tarefa não está pronta até o **deploy estar verde** (push + GitHub Actions test+deploy 100% green).

### Scanner — só varredura passiva (Security Rating, NÃO pentest)
- ✅ **Faz:** `GET`/`HEAD` a URLs públicas, headers, certificados SSL públicos, DNS público, arquivos servidos sem autenticação.
- ❌ **NUNCA:** payloads de injeção (SQLi/XSS), brute-force, área autenticada, exploração de vulnerabilidade, extração de dados.
- **Timeout 10s/request; rate limit 1 req/s por domínio** (centralizado em `checks/base.py` — não reimplemente). **User-Agent identifica o Klarim honestamente** — não se passa por navegador.

### Segurança (regra inviolável de 2026-07-15)
- **Toda implementação ou fix inclui revisão de segurança.**
- **Nenhum endpoint, formulário ou fluxo de dados sem proteção** (auth, validação, rate limit, sanitização).
- Empresas de **cibersegurança estão entre os alvos** e sondam a plataforma ativamente — assuma que tudo será testado. Detalhes em `docs/SECURITY.md`.

### Dados
- **Regra de ouro:** o **AI enrichment NUNCA sobrescreve** dado extraído por regex nem classificação `manual`/`ai`; só preenche campo **vazio**. `source='receita'` (CNAE oficial) nunca é sobrescrito pela IA.
- Quando **`scoring.py` ou um check muda**, **flush `scan:*` no Redis** da VM após o deploy (senão semáforos velhos servem por até 1h).
- **Não use `DATABASE_URL`** — a senha em base64 contém `/`. Use as `POSTGRES_*` individuais.
- **`contact_email`, `cnpj`, `whatsapp` NUNCA são expostos** na API/perfil público.

### Frontend (padrão Astro, KL-51)
- Ilhas admin: **`client:only="react"`** (não `client:load`). `AdminShell` = wrapper interno (prop `active`).
- **`<a href>`** em vez de `Link`/`NavLink`; **`window.location`** em vez de `useNavigate`. **Zero `react-router-dom`** no código migrado.
- **`parseUTC`** para timestamps naive do Postgres (adicionar `Z` antes de `new Date`).
- **CSP:** `/painel` = relaxada (`script-src 'unsafe-inline'`, noindex/operator-only). **Público = CSP estrita** (scripts inline por hash SHA-256). **Ao add/alterar script inline público, recompute o hash em `frontend/nginx/security_headers.conf`.** Check 13 (SRI) tem allowlist de CDN dinâmico (`SRI_ALLOWLIST_DOMAINS`: googletagmanager/google-analytics/cloudflareinsights) → não contam FAIL.
- **Novo `.js` público (`web/public/*.js`) exige 2 passos** (senão não é servido): (1) adicionar o nome ao **allowlist do nginx** (`http.conf` + `https.conf.template`, regex de paths proxiados ao `astro`); (2) referenciar com **`?v=N`** e **bump a cada alteração** (senão o Cloudflare cacheia o HTML de erro por 4h). ⚠️ Não requisite a URL `?v=N` antes do fix estar no ar.
- **Cookies (KL-135, LGPD):** GA4 é **opt-in** — quem injeta o `gtag.js` é o `web/public/cookie-consent.js` (externo, CSP `'self'`), só se o cookie `klarim_consent` for `all`/`analytics`. Banner `CookieBanner.astro`; página `/cookies`; rodapé "Preferências de cookies" (`data-cc="reopen"`).
- **Tema light/dark (KL-87): light é o padrão.** Os tokens `--color-slate-*`/`--color-white` do Tailwind são **sobrescritos por tema** em `web/src/styles/global.css` (`:root`=light com a escala slate **INVERTIDA**; `[data-theme='dark']`=defaults) → páginas viram theme-aware **sem migrar classe**. ⚠️ **NÃO escreva componentes light-first** (`text-slate-900`/`bg-white`) — no light eles invertem e somem; use os tokens de `dashboard-v2/shared.js` ou o padrão do `/planos` (headings `text-white`, cards `bg-slate-900/60`). Botões laranja usam `text-[var(--accent-text)]` (contraste constante). Admin (`/painel`) força `data-theme=dark`. Anti-FOUC inline (hash na CSP) + toggle `public/theme.js`.
- **Responsivo (KL-80, 68% mobile):** toque **≥44px** (`min-h-[44px]`/`py-3`); **inputs `text-base`** (16px, nunca `text-sm` — evita zoom iOS) + `h-12`; botões `w-full sm:w-auto`; nada de largura fixa que estoure 375px; grades `grid-cols-1`→`md:`/`lg:`.
- **Container das páginas públicas (KL-89):** o `<main>` puxa a largura de **`web/src/lib/layout.js`** — **não invente `max-w` por página**. `PAGE_CONTAINER` (conteúdo, até `lg:max-w-7xl`) · `FORM_CONTAINER` (`max-w-md`) · `PROSE_CONTAINER` (`max-w-3xl`, via `Page.astro`). Tailwind escaneia `.js` → as classes literais das constantes entram no build.
- **Auth nas páginas Astro:** cookie **`klarim_session` HttpOnly** (o JS do cliente NÃO lê o valor). `src/middleware.js` valida server-side (`GET /account/me` Bearer) **só para `/dashboard/*`**; `header.js` + ilhas React fazem fetch client-side. **Cache importa:** páginas CDN-cacheadas (`max-age`, ex. `/security-gate`) detectam auth no CLIENTE (ilha, 1º paint = link anônimo → re-decide); `/cadastrar`/`/entrar` são `no-store` → SSR confiável (`web/src/lib/serverAuth.js`: `fetchSessionUser`/`loggedInRedirect`).

---

## 4. E-mail (reputação)

### Mapa de remetentes (`klarim.net` = 100% transacional, zero cold)
| Remetente | Domínio | Tipo |
|---|---|---|
| `klarim@klarim.net` (`RESEND_FROM`) | klarim.net | Transacional (confirmação, boas-vindas, boletim, vigília, magic link, KYC/gate, LGPD*) |
| `scan@alertas.klarim.net` / `scan@aviso.klarim.net` (`ALERT_SENDER_EMAILS`) | alertas./aviso. | Cold alert (rotação round-robin) |
| `notifica@perfil.klarim.net` (`PROFILE_VIEW_FROM_EMAIL`) | perfil. | Aviso "perfil consultado" |
| `alerta@klarim.net` (`ALERT_FROM_EMAIL`, `_proactive_from`) | klarim.net | Bulletin proativo (a quem tem conta/opt-in) |
| `privacidade@klarim.net` (`LGPD_FROM_EMAIL`) | klarim.net | Confirmação de solicitação LGPD |

*Transacional migrou de `seguranca@` → `klarim@` (a palavra "seguranca" é keyword de phishing). Os `_from` são lidos do env a cada envio → troca do `.env` vale ao **recriar o container**.

### Regra de envio ATUAL (KL-145 — Reoon FORA do fluxo de envio)
`is_safe_to_send(email, redis, store)` = **3 filtros LOCAIS**: (1) sintaxe válida, (2) domínio tem MX (cache Redis 24h, respeita `ALERT_VALIDATE_MX`, **fail-open**), (3) não está na blocklist. **Tudo que passa → ENVIA.** O status Reoon (`unknown`/`catch_all`/`safe`/…) e `email_verified` **NÃO decidem envio**. A **blocklist aprendente** (cada bounce, via webhook Resend → `email_blocklist`) aprende quem não recebe. **O lead_score NÃO decide envio — só ORDENA a fila.** `_verify_and_filter` (alert worker) aplica os 3 filtros; stats do ciclo: `eligible/valid_syntax/has_mx/not_blocklisted/blocked_*/errors`. O Reoon fica no `email_verifier` (`verify_reoon`/`verify_local`/cache) só como **enriquecimento em background** (scripts `cleanup_email_backlog.py` + saldo no `/system/status`) — NUNCA no alert worker. Toda a regra binária por-status e os gates/trust dos KL-108..KL-137 são **históricos**.

### Cold alerts (a quem NÃO tem conta)
- Módulo `notifier/cold_alert.py` + `alert_worker`. **Texto puro (text/plain, NUNCA HTML)** com **UM link CURTO** `klarim.net/a/{target_id}` (`report_link`) → API redireciona 302 p/ `/site/{domain}` e registra o clique server-side (`email_clicks`, IP mascarado /24). Sem UTM.
- 3 variantes (informativa/setorial/educativa); opt-out **por resposta** ("responda com remover") + `List-Unsubscribe` (mailto + https one-click `/remover?token=`, RFC 8058).
- **Rotação round-robin** entre `alertas.klarim.net` e `aviso.klarim.net` (`load_senders` descarta `klarim.net` cru). Envio **individual** com **cooldown 30-60s** (`ALERT_SEND_INTERVAL_MIN/MAX`) + **limite diário POR remetente** (`ALERT_SENDER_DAILY_LIMIT`, warmup 100→750; editável no painel).
- **Circuit breaker por remetente:** só **HARD bounce** > 5% (`ALERT_SENDER_MAX_BOUNCE_RATE`) com amostra ≥100 (`ALERT_SENDER_BOUNCE_MIN_SAMPLE`) sobre janela móvel de 7 dias (`email_health_by_domain(days=7)`) → remetente **pausado** no ciclo (o outro continua). **Soft bounce** (transitório: caixa cheia, `delivery_delayed`) é medido/logado como `soft_bounced` mas **NUNCA pausa**. `email_health_by_domain` devolve `hard_bounced`/`soft_bounced` separados; `bounce_rate`=hard-only. Safety net GLOBAL do KL-24 (`_check_bounce_health`→`email_health()`, all-time 8%) é query separada, inalterada.

### Profile_view ("perfil consultado")
- Remetente dedicado `notifica@perfil.klarim.net` (`_profile_view_from`; **não rotaciona** com os cold alerts — o volume destruiria o warmup deles). **Texto puro SEM links** (`build_profile_view_text(domain, target_id)` → link curto `/a/{id}`), opt-out por resposta.
- Volume: dedup por dono **1/dia** (`notify_owner:{email}` Redis) + dedup por domínio/24h + teto diário `PROFILE_VIEW_DAILY_LIMIT` (editável no painel).
- Gatilho: nasce do **evento `profile_view` HUMANO-verificado** (`track.js`→`/api/events`→`_profile_view_notify`), **não** do SSR (KL-64: o SSR gerava ~7k e-mails/dia de bots).

### Lead scoring (só ORDENA a fila — `discovery/alert_scoring.py::calculate_alert_score`, PURO)
Sinais: +30 e-mail no domínio · +10 corporativo · +20/+10/+5 por faixa de score (50-85/40-49/>85) · **tipo de e-mail** (`_email_type_factor`): pessoal **+15** · genérico neutro 0 · medium-bounce (`atendimento`/`sac`) -5 · high-bounce (`contato`) -10 · -10 descartado/score<40 · -40 domínio com bounce (só domínio próprio/corporativo; provedores gmail/outlook NÃO penalizam). Grava `targets.alert_quality_score` para TODOS (NUNCA impede scan/envio). O `run_cycle` ordena por score DESC (pessoais antes de genéricos → a blocklist aprende com os genéricos antes de mandar muito).

### Outras regras de e-mail
- **Bounce webhook `/webhooks/resend`:** permanente → `bounced` + descarta alvo + blocklist; transitório → `soft_bounced` (rastreia, não descarta). `email_log.status` é texto livre.
- **Proativo respeita a blocklist; transacional pode ignorá-la mas SEMPRE registra** (todo e-mail passa por `KlarimMailer._send` → `email_log`).
- **Cold** levam `List-Unsubscribe` (mailto + https one-click); **proativos** levam `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058). Token HMAC `generate/verify_unsubscribe_token` (SEM expiração). `POST/GET /remover` e `/unsubscribe` → marca `unsubscribed` + blocklist + evento. Rate limit 10/min/IP **só p/ tokens inválidos** (one-click válido do Gmail vem de IP compartilhado). nginx roteia `/remover` → FastAPI.
- Workers que e-mailam `contact_email` (alert/rescan/profile-view) filtram `status='unsubscribed'`.

---

## 5. Arquitetura (resumo — detalhe em `docs/ARCHITECTURE.md`)

### Containers (Docker Compose)
`postgres` · `redis` · `api` (FastAPI, `127.0.0.1:8000`) · `worker` (scan worker) · `discovery` (Discovery + Alert + Rescan + Vigília + VendorMonitor via `asyncio.gather`) · `astro` (SSR, `:4321`) · `web` (Nginx, 80/443 — **único público**). `api`/`worker`/`discovery` = **mesma imagem** (`build: .`).

### Nginx — front único de TLS/segurança
Serve o build Astro (rotas públicas), o build Vite em `/painel*`, proxy `/api` e `/mcp` (**resolver dinâmico** — `set $var` + `resolver 127.0.0.11`), TLS Let's Encrypt (self-healing http↔https), subdomínios `painel.`/`mta-sts.`. ⚠️ Um `add_header` num `location` **quebra a herança** dos headers do `server` — **repita os security headers** ao adicionar um `location`. Valide com `nginx -t` (job de CI); config inválida **derruba o site**. A CI monta cada config isolada → `nginx -t` roda por arquivo.
- **Rate limiting (KL-160):** zonas no topo de cada conf (`general 30r/s`, `api 10r/s`, `scan 2r/s`, `limit_req_status 429`). ⚠️ **Chave = `CF-Connecting-IP`** (via `map`), NUNCA `$binary_remote_addr` (atrás do CF seria a edge). Assets/MCP/SSE/OAuth sem limite.
- **Bloqueio de IP direto** (só HTTPS): `ssl_reject_handshake on` no 443 default_server + `return 444` no 80 default_server (o bloco klarim.net perdeu o `default_server`; CF manda SNI=klarim.net).
- **Paths de exploit → 404** antes do fallback SPA (`.env`/`.git`/`wp-config`/`phpmyadmin`/`adminer`/`swagger`/`graphql`/`_debug`/`xmlrpc`/…). Um path fora do allowlist cai no SPA (200+HTML) → o scanner intensifica; por isso o bloqueio explícito.
- Roteamento sem strip de prefixo (como `/remover`): `/a/{id}`, `/lgpd/request`*, `/sitemap*.xml`, `/blog/rss.xml`. (*`/api/lgpd/request` vai pelo `/api/`.)

### Scanner
- **Gate de acessibilidade (KL-94)** ANTES dos 48 checks (`scanner/runner.py::_accessibility_gate`): DNS resolve? (NXDOMAIN→`domain_not_found`; erro→`dns_error`) → HTTP responde? (qualquer 2xx/3xx/4xx/5xx = acessível; SSL inválido NÃO aborta, `verify=False`; falha de conexão→`unreachable`). Aborta com `ScanReport.status`≠`ok` (score=None). A API devolve **200** com `{status, error_detail, score:null, checks:[]}`. Persistência: só cacheia scan `ok`; `unreachable`→Postgres (score NULL, analytics); `domain_not_found`/`dns_error` não salvos.
- **Auditoria checks Tipo B (ausência de algo ruim):** `base.content_guard(resp, NAME, sev)` → INCONCLUSO (nunca PASS falso) se 5xx ou corpo vazio/mínimo (<100 chars). Multi-sonda contam respostas: zero respostas → INCONCLUSO. Checks Tipo A (presença de proteção: SPF/HSTS/CSP/DNSSEC — ausência=FAIL) não mudam.
- **Runner paralelizado** (`asyncio.gather` + `Semaphore(SCAN_MAX_CONCURRENCY=12)`); seguro porque o rate limit de `base.fetch` é **por-domínio** (1 req/s).
- **48 checks passivos** = **15 grátis (ORDER≤15)** + 33 pagos (OWASP/CWE/LGPD, CVE via Retire.js, TLS profundo, DNS, content). Cada check é coroutine descoberta dinamicamente (§7). Cache por tier (`scan:free:*`/`scan:full:*`, ambos casam `scan:*`) com fallback no banco.
- **8 indicadores de privacidade** (`scanner/privacy_checks.py`) num único GET próprio → `privacy_score` **0–8 SEPARADO** do score de segurança — diagnóstico técnico, **não** conformidade LGPD (disclaimer obrigatório). Não entram nos 48.
- **Semáforo:** 🟢 score **≥90 E zero FAIL Alta/Crítica** · 🟡 ≥50 · 🔴 <50.

### Workers (heartbeat Redis TTL 600s + watchdog `os._exit(1)` + `restart:unless-stopped`)
- **Discovery** — CT log poller (`ct_poller.py`), ciclo 30 min; enfileira todo site acessível (scan desacoplado do e-mail). Detecta typosquat sobre o buffer de CT logs → `typosquat_alerts`. Registra subdomínios de domínio-raiz já na base (`site_subdomains`, nunca escaneados).
- **Alert** — ciclo 30 min. Elegibilidade ordena `(e-mail casa domínio) DESC, last_scan_at ASC`; busca `ALERT_FETCH_CAP` (200), envia até `send_cap`, ordenado por score DESC. Kill-switch `STOP_ALERTS` + `worker_control`. Exclui inacessíveis (`gate_fail_count>0`/`last_scan_score IS NULL`).
- **Rescan** — ciclo 24 h, alvos ≥30 dias (`RESCAN_AGE_DAYS`).
- **Vigília** (KL-44 P2/P4) — ciclo 6 h, 8 tipos: core (ssl/domain/score/email/reputation) + avançadas (`changes`, `phishing`/typosquat). `uptime` num loop curto próprio (5 min, reagenda pelo plano: Pro 30min · Agency 5min). Enforcement por plano.
- **Bulletin** (KL-44 P3) — ciclo 1 h, às `BULLETIN_HOUR_UTC` (13h): free=mensal · pro=semanal · agency=diário útil; plain text via `alerta@klarim.net` + laudo técnico ao técnico vinculado via `klarim@klarim.net`.
- **Trial** (KL-44 P6) — ciclo 1 h, age 1x/dia às `TRIAL_HOUR_UTC` (6h): avisa 7d/1d antes + downgrade silencioso p/ Free no vencimento. Cleanup de contas não-confirmadas/passwordless inativas. Flag `TRIAL_EXPIRATION_ENABLED`.
- **VendorMonitor** (KL-152) — 1x/dia, re-scan de fornecedores Gate vencidos + alerta ao Enterprise se score cair.
- **Scan worker** — consome `klarim:scan_queue`, `WORKER_MAX_SCANS_PER_HOUR` (200 na VM, editável ao vivo), enriquece perfil + IA inline (~US$0,001/site) + arquiva response bruto no GCS. Trata `ScanReport.status`: `ok`→salva+zera `gate_fail_count`; `unreachable`→grava+conta falha; `domain_not_found`→conta falha. **Retry backoff** por falha de gate (`gate_fail_count`/`gate_next_retry`: +7d/+30d/descarta) — MAS só descarta se `last_scan_score IS NULL` (site com score é preservado).
- **Backfill de enriquecimento (cron root):** `scripts/enrich_all.py` batch 2.000, 6×/dia (a cada 4h), `flock -n /tmp/klarim_enrich.lock`, container `api`, log `/var/log/klarim_enrich.log`. Custo ~US$12/dia OpenAI enquanto durar o backlog — monitorar CPU/RAM.

### GCS archive (KL-77 F2) · tech detect (KL-75) · access log (KL-92)
- **GCS:** cada scan comprime (gzip) o response bruto já em memória do enrich (headers/html/dns/ssl/status, **sem request extra**) → `gs://klarim-raw/YYYY/MM/DD/{scan_id}.json.gz` (Nearline privado). Fire-and-forget (`scanner/gcs_archive.py`, `GCS_ENABLED=false`=bypass; erro logado/engolido). Contadores `klarim:gcs:*` → MCP `get_gcs_archive_stats`.
- **Tech detect:** do mesmo response bruto, `scanner/tech_detector.py::detect_tech_stack` (pura) extrai tecnografia (~50 scripts, headers, meta, DNS TXT, SSL) + `site_type` + `site_status`. `persist_tech_detection` grava (resiliente) em `site_tech_stack` (batch idempotente), `targets.email_provider`/`related_domains`/`site_type`, `site_status_log`, `company_name` só-se-vazio. Público = `GET /public/tech-summary/{domain}`.
- **Access log (fonte de verdade das métricas de visitante):** tabela **`access_log`** (IP INET, país, endpoint, status, user_id, domain_queried, is_bot/bot_reason, `source` middleware|nginx). Duas fontes disjuntas: **`api/access_log_middleware.py`** (OUTERMOST, `/api`+`/mcp`, com user_id + retroatividade) + **`api/nginx_log_parser.py`** (páginas Astro não-`/api`, lê o access_log do Nginx incrementalmente). Classificação em **`api/bot_classifier.py`** (PURO: IP próprio→autenticado→datacenter→crawler UA→rate>50/h→pré-fetch de e-mail [CIDRs Gmail/Outlook + >20 domínios distintos/h]). **Retroatividade:** uma `HUMAN_ACTION` marca não-bot todos os registros do IP no dia. **LGPD:** IP retido 90d, depois `anonymize_old_access_logs` trunca IPv4→/24 e IPv6→/48; nos responses o IP volta mascarado (completo só no banco). ⚠️ o Nginx faz `rewrite ^/api/(.*)$ /$1` → o middleware vê paths SEM `/api`.

### Planos (freemium) — `PAYWALL_ENABLED` default `false`
Todo scan autorizado vê os **48 checks**; PDF sempre gratuito. Assinatura define o **monitoramento**:
- **Free** — 1 site, boletim mensal + **5 vigílias core ATIVAS** (ssl/domain/score/email/reputation; uptime=Pro, changes/phishing=Agency).
- **Pro** — R$ 19/mês (R$ 99/ano), 5 sites, semanal, vigílias.
- **Agency** — R$ 49/mês, 15 sites, diário, vigílias avançadas.
- **Reverse trial 30 dias** no signup (Pro; `?plan=agency`→trial Agency). **Upgrade self-service via PIX** (`POST /account/upgrade`→AbacatePay transparente, webhook idempotente; `subscription_payments`, separada de `payments`). **NUNCA guarda dado de cartão/PIX** — só o id da cobrança. Trial expira → downgrade silencioso.

### MCP Server
SSE + **OAuth 2.1 + PKCE** + token estático (`MCP_API_KEY`) como fallback. **~80 tools** (leitura + escrita) — wrapper fino sobre a API/store, auth própria (fail-closed). ⚠️ o token é propagado no evento `endpoint` (`&token=`), senão os POSTs do `/messages/` chegam sem auth (401). Tool nova → reconectar o MCP pós-deploy p/ aparecer.

### Integrações (todas best-effort/fail-open → degradam para INCONCLUSO, nunca derrubam o scan)
Resend (5 remetentes), AbacatePay (PIX), OpenAI (GPT-4o mini), Reoon (verificação de e-mail — background), APIs públicas de leitura (crt.sh, HIBP, **Google Safe Browsing** `GOOGLE_SAFE_BROWSING_KEY` ativa → check_29 PASS/FAIL, IBGE CNAE, BrasilAPI/ReceitaWS, RDAP, ViaCEP). Keys só no `.env` (gitignored).

---

## 6. Estrutura de diretórios

```
api/          → FastAPI: main.py, auth_users.py, plans.py, vigilias.py, dashboard.py,
                lead_scoring.py, oauth.py, health_checks.py, admin_analytics.py, admin_sectors.py,
                gate.py, gate_rate_limiter.py, validators.py, target_intelligence.py,
                vigilia_details.py, access_log_middleware.py, bot_classifier.py, nginx_log_parser.py
discovery/    → Workers + store.py (TargetStore, TODO o schema Postgres): worker.py, alert_worker.py,
                rescan_worker.py, vigilia_worker.py, vendor_monitor_worker.py, ct_poller.py,
                classifier.py, contact.py, alert_scoring.py, sector_*.py, subdomains.py, cnae.py
scanner/      → Engine: main.py (worker+CLI), runner.py, scoring.py, profiler.py, ai_enrichment.py,
                enrichment.py, tls_analyzer.py, tech_detector.py, gcs_archive.py, privacy_checks.py,
                cve_db.py, checks/ (check_*.py dinâmicos + classifications.py)
security_gate/→ Produto Gate (portável): engine.py, models.py, config.py, utils.py, vendor.py,
                checks/ (exposure, credentials, headers, ssl, api_security, email_security, cors,
                cookies, dns_security, tls_ciphers, … + scanner_adapter.py), formatters/
reporter/     → PDF WeasyPrint: generator.py, risk_messages.py, gate_report.py, gate_run_report.py, templates/
notifier/     → KlarimMailer (email_client.py), cold_alert.py, email_verifier.py, templates/
payments/     → AbacatePay PIX: abacatepay.py, models.py, store.py
mcp_server/   → MCP SSE + OAuth: _base.py, server.py, auth.py, oauth.py, tools/
web/          → Astro 7 (site público + rotas do painel proxiadas) — src/lib/* (lógica pura testável)
frontend/     → build Vite (/painel admin) + config Nginx (nginx/*.conf) + assets
scripts/      → seeds, backfills, enrich_all.py, security_gate.py (CLI), klarim_gate_cli.py
tests/        → pytest (offline por default; rede atrás de KLARIM_ONLINE=1)
docs/         → ARCHITECTURE / API / DEPLOY / SECURITY / LGPD / HISTORY · claude/reports/ (KL-xxx)
```

---

## 7. Convenções de código

- **`async`/`await`** para toda I/O. **Type hints** em assinaturas públicas. **Docstrings** no que não for trivial.
- **Migrations idempotentes** (`CREATE TABLE IF NOT EXISTS`, `ALTER … ADD COLUMN IF NOT EXISTS`) dentro do `ensure_schema` de `discovery/store.py` — **sem Alembic**. A API cria o schema no boot (lifespan). ⚠️ Índice que referencia coluna adicionada por `ALTER` deve vir **depois** do ALTER (senão banco fresco falha).
- **Auth:** endpoints admin sob prefixos protegidos (`/targets`, `/scans`, `/alerts`, `/rescans`, `/email`, `/payments`, `/config`, `/leads`, `/admin`…) → **JWT admin Bearer** (`typ=admin`). Usuário sob **`/account/*`** → **JWT usuário no cookie** (`typ=user`). Gate: `/gate/*` → API key (`X-API-Key`); `/account/gate/*` → JWT sessão (alguns dual-auth). Mesmo `JWT_SECRET`, mas `typ` **nunca é ignorado**.
- **Rate limit via Redis** (`_redis_allow`) com fallback in-memory.
- **Config editável:** `admin_settings` (banco) **>** `os.environ` (.env) **>** default, via `get_setting(key, default)` — **fail-open** (erro de banco nunca pausa worker).
- **Fire-and-forget** (`_spawn`) para operações não-críticas (ingest, lead, e-mail em background).
- **Testes offline** (sem rede/Postgres) com `FakeStore`. Frontend: **`node --test`** sobre lógica PURA de `web/src/lib/*.js` (não Vitest). ⚠️ `store.*` novo em endpoint compartilhado → stub no `FakeStore` (senão todo teste 500).
- **Padrão testável:** agregação BRUTA (SQL) no `store.py`; derivação PURA (cálculo/shape) em módulo separado → orquestrador fino no `main.py`. Valide SQL novo contra o Postgres 16 da VM.

### Como adicionar um check ao scanner
1. `scanner/checks/check_<slug>.py` com `ORDER` (int, **≤15 grátis**), `CHECK_ID`, `NAME` e `async def check(url) -> CheckResult`. Descoberta automática (`discover_checks()`, sem lista hardcoded).
2. Retorne `PASS`/`FAIL`/`INCONCLUSO` (INCONCLUSO neutro; nunca finja PASS). Severidade `CRITICA`/`ALTA`/`MEDIA`/`BAIXA`.
3. Entrada em `scanner/checks/classifications.py` (OWASP/CWE/LGPD — `test_every_check_is_mapped` falha se faltar) + `RISK_MESSAGES` (`reporter/risk_messages.py`) + `ACCESSIBLE`/`TECHNICAL` (`reporter/generator.py`).
4. **Flush `scan:*` no Redis** após o deploy. Reutilize `checks/base.fetch` (nunca reinvente o rate limiter).

### Como rodar
```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m scanner.main https://www.example.com [--json|--pdf]   # scan pela CLI
python scripts/security_gate.py https://klarim.net              # Security Gate CLI
docker-compose up --build                                        # stack completa
pytest                                                           # offline
KLARIM_ONLINE=1 pytest tests/test_checks.py                      # inclui scan real
npm run test:unit --prefix web                                   # frontend (node --test)
```

### Desenvolvimento local (KL-90 P0) — `docs/DEV.md`
Stack Docker isolada da produção; **não faz deploy/push/CI; nenhum e-mail/pagamento real** (`DRY_RUN_EMAIL=true`, Resend/AbacatePay/GCS off).
```bash
docker compose -f docker-compose.dev.yml up --build
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
```
- Arquivos (gitignored/nunca vão p/ VM): `docker-compose.dev.yml` (db :5433, redis :6380, api `--reload`, astro `npm run dev` :4321, web Nginx :3000, **sem workers**), `.env.dev`, `frontend/nginx/dev.conf` (HTTP puro, sem SSL/CSP/rate limit), `scripts/seed_dev.py` (guard `KLARIM_DEV_MODE`).
- Acesso: browser :3000 · Astro :4321 · API :8000 (`/docs` com `KLARIM_DEV_MODE=true`) · Postgres :5433 · Redis :6380.
- Seed idempotente: users (`dono@exemplo.com.br`/`dev123456`, `tecnico@agencia.com.br`, `nivel1@teste.com`, `dono3@teste.com`/`dev123456`), 5 sites (score 20–100), 50 scans, vigílias, perfis.
- ⚠️ Astro entra em crash-loop no restart pelo lock `web/.astro/dev.json` → o `command` do serviço faz `rm -f .astro/dev.json` no boot. Dev server pode ter scan incompleto do Tailwind p/ classes novas (reiniciar resolve; o build de prod gera tudo).

---

## 8. Estado atual (atualizado 2026-08-12)

- Alvos ~25.400 · Scans ~8.100 · Perfis públicos ~7.200 · Sites score 100 ~730.
- Score do próprio `klarim.net`: **100/100** (Gate: 90/100 full, só rate_limit HIGH).
- Testes: **~2317 pytest** + **~221 node --test** (`npm run test:unit`).
- Workers: **5/5 ativos** (discovery, alert, scan, vigília, rescan) + VendorMonitor.
- Scan rate 200/h · responses brutos no GCS `gs://klarim-raw`.
- E-mail: cold com rotação `alertas./aviso.klarim.net`, regra de envio = 3 filtros locais (KL-145), blocklist aprendente.
- MCP tools: **~80**. Analytics admin: aba "Visão geral" **desativada** (ver §9); 1ª aba = "Comportamento".
- Security Gate: produto vivo (§9). Blog, LGPD (`/lgpd`), sitemap dinâmico ativos.

> **Atualize este bloco a cada tarefa** que mude números relevantes.

---

## 9. Subsistemas atuais (estado vigente — não histórico)

### Fontes autoritativas de métrica (KL-95 / KL-136 / KL-150) — CRÍTICO
- **Contas criadas** = `COUNT(*) FROM users` no período (server-side; NÃO o funil do tracker.js, inflado por pre-fetch).
- **Scans manuais (Analytics/dashboard)** = `COUNT(*) FROM scans WHERE COALESCE(source,'') NOT IN ('discovery','rescan')` (só `admin`+`public`; o re-scan ~116/dia dominava o KPI).
- **Scans (`/system/status`)** = `scan_today_stats` = TODOS os scans do dia (incl. discovery+rescan). A divergência dashboard×system_status é **esperada por design**.
- **`sent_month`** (`count_proactive_emails_this_month`, cota mensal) = PROATIVO (alert_log+rescan_log), mês-**calendário UTC**. **`email_metrics.sent_week`** = `email_log`, todos os tipos, 7 dias móveis. No dia 1 do mês `sent_month < sent_week` é ESPERADO.
- **Analytics "Visão geral" (KL-150):** período default `7d`, **fuso de BRASÍLIA** (BRT UTC-3; `resolve_period('today')` = meia-noite BRT → instante UTC). **Visitantes BR** = `COUNT(DISTINCT ip_address)` do Brasil, `is_bot=false` **E** sem UA de bot (`_BOT_UA_RE`, tira `Klarim Security Gate`/`KlarimScanner`/wp-scanners); **Bots filtrados** = COUNT de **REQUISIÇÕES** `is_bot=true` OU UA de bot (meio milhão/dia é plausível). Cada KPI tem tooltip (ⓘ).
- **⚠️ Aba "Visão geral" DESATIVADA (10/08):** os KPIs de visitante do `access_log` divergem do GA4 e as queries de `al_server_metrics` são pesadas (deixavam o painel lento). Comentada (não deletada) em `web/src/components/admin/AdminAnalytics.jsx`; 1ª aba = "Comportamento" (só `ip-behavior`; os blocos que chamavam `server-metrics` também comentados). Backend intacto. Reativar quando a fonte for a **API do GA4**.

### Scan público — resultado por nível de acesso (KL-82/89)
- `GET /scan/result` escaneia anônimo e devolve payload **filtrado server-side** (`api/main.py::_filter_scan_result`) por `access_level`: `anonymous` < `unconfirmed` < `confirmed`/`alert_session`. **Regra vigente (mostrar valor antes de pedir conta):** score/semáforo, compartilhar+PDF, benchmark, **TODOS os riscos** (linguagem de negócio), categorias e checks por nome/status são **abertos em TODO nível**; **evidência técnica** só no acesso completo; **LGPD só na conta `confirmed`**. Flags puras em `web/src/lib/scanView.js::viewFlags` — derivam SÓ do nível, **nunca** do dispositivo (desktop==mobile).
- **Resultado instantâneo (P0):** serve scan <24h existente (Redis OU banco) SEM re-escanear (`get_recent_only`, `max_age=1440min`); `refresh=1` força novo. Cai no lookup FREE (15 checks) se não houver FULL → `partial=True` no payload. Rate limit anônimo **5/h + 20/dia por IP** só conta scans REAIS (cache é grátis).
- Rate limit em `/scan/result` NÃO é client-controlável (nível vem só da sessão via `_access_level`).

### Conta / níveis / conversão
- **`users.account_level`** (KL-99): 1 sem senha · 2 com senha · 3 dono verificado por domínio (eixo distinto do `access_level`). `password_hash` nullable; `users.source` (`signup`|`hmac`|`inline`|`security-gate`). `@require_level(n)` gateia (403 `insufficient_level`).
- Fluxos sem senha: link do alerta (`GET /alert-access` → sessão view-only 24h/7d; `POST /account/monitor-from-alert` cria conta no consentimento) · `POST /account/signup-inline` (ativa monitoramento na hora, KL-105) · `/cadastrar` só e-mail · **magic link** (`POST /account/magic-link`). Verificação de domínio 2→3: `POST /account/sites/{id}/verify/{start,check}` (meta_tag/html_file/dns_txt; anti-SSRF: fetch usa domínio de `targets`, corpo nunca volta ao usuário).
- **Dashboard v2** (`web/src/components/dashboard-v2/`, `/dashboard`): 1 fetch `GET /account/dashboard-summary?site_id=` (lógica em `api/dashboard.py`, `build_*` PURAS + `asyncio.gather`). Ramifica por `account_type` (developer PURO → só seção Gate; both → Gate + sites; owner → normal). Modo técnico (`_build_technician_view`): `site_id` de cliente exige `technician_link` ativo (senão 404), 48 checks + evidência, `owner_email` mascarado. `contact_email`/cnpj/whatsapp nunca no payload.

### Owner management (KL-97/98) — `_owned_site` (auth + `_require_level` + `get_user_site`)
- `GET/PUT /account/sites/{id}/monitoring` — liga/desliga vigílias por-tipo (plan-gated, 403 `requires_plan`). `GET/PUT /account/notification-preferences` (`users.bulletin_frequency`/`bulletin_hour`/`notify_*`; frequência efetiva = override do user > plano).
- `PUT /account/sites/{id}/profile` — dono edita 15 campos (`_sanitize_owner_profile`, valida CNPJ/telefone/URL → 422). **Preservação contra IA:** `merge_ai_into_profile` pula `owner_edited_fields`; upsert com CASE por-campo. `PUT .../visibility` (landing on/off). **Selo:** `GET/PUT .../seal` (badge/footer/floating); público `GET /seal/{domain}` + `web/public/seal/widget.js`.
- **Vigílias expansíveis (KL-123):** `GET .../vigilias/{tipo}/details` + `POST .../phishing/dismiss/{alert_id}` + `POST .../{tipo}/acknowledge`. Derivação pura em `api/vigilia_details.py` (`build_<tipo>`); linguagem acessível (sem OWASP/CWE raw).

### Admin — alvos, analytics, intelligence
- **Filtros de alvos (KL-104 P2):** `GET /targets` com 15 filtros combinando AND (100% parametrizados) via `TargetStore._target_filters` (compartilhado com `count_targets_filtered`); `GET /targets/tech-list`. Front: `web/src/lib/admin/alvosFilters.js` (URL⇄estado) + `AlvosFilters.jsx`.
- **Visão 360° (KL-104 P3):** `GET /admin/targets/{id}/intelligence` (monitoramento/funil/visitantes/timeline) — agregações `ti_*` no store, montagem PURA em `api/target_intelligence.py`, degradação graciosa (`_try`/`_safe_section`), timeline com cursor. IP sempre mascarado /24.
- **Analytics (KL-83/92/95):** módulo `api/admin_analytics.py`, 8+ endpoints `/admin/analytics/*` (períodos today/7d/30d/90d/custom, cache Redis, rate 30/min). `access_log` = fonte primária; `site_events`/tracker.js = complemento das interações. Deep-linking `DomainLink` (domínio → `/painel/alvos/{id}`).
- **Segurança da plataforma (KL-160):** `POST /admin/security-scan` (assíncrono, cooldown 5min, usa `load_config("security-gate.yml")` = mesma config do CLI) → tabela **`platform_security_scans`** (dedicada). `PlatformSecurityCard.jsx` na página Sistema. ⚠️ o `adminApi.js::req()` no `!resp.ok` só extrai o `detail` do JSON — nunca joga o body HTML de erro na mensagem.

### Conteúdo público / SEO
- **Conteúdo navegável (KL-74):** `/public/{sectors,sector/{slug},top-fails,related,best,stats}` (só `public_visible`; nunca `contact_email`; rate 30/min/IP; cache 1–24h). Páginas Astro `/setores`, `/setor/{slug}`, `/melhores`, `/estatisticas`. `/public/best` devolve `total` real (`count_score_100_sites`, mesma query de `stats.score_100_count`). ⚠️ pós-mudança de contadores: **flush `public:best`+`public:stats`** no Redis.
- **Landing (KL-81/103):** buscador minimalista ("Pesquise qualquer site"), stats bar ao vivo (`web/public/landing-stats.js`), 6 pills de setor, dual-card empresa×dev.
- **Taxonomia de setores (KL-84):** tabela `sectors` (status official/proposed/approved/rejected/merged), IA propõe, admin cura em `/painel/setores`. Público filtra por status. `scripts/reclassify_sectors.py`.
- **Sitemap dinâmico (KL-131):** FastAPI serve `/sitemap.xml` (sitemapindex) + `/sitemap-{static,sectors,profiles-N,blog}.xml` (≤10k perfis/página, cache Redis 1h). Nginx roteia `/sitemap*.xml` → FastAPI. `robots.txt` bloqueia `/dashboard/`, `/api/account/`, `/webhooks/`, `/remover`.
- **SEO perfis (KL-132):** `web/src/lib/seo.js` (`profileTitle`/`profileDescription`/`formatDomainName`). JSON-LD Organization+WebSite+BreadcrumbList; setor = CollectionPage. **NÃO** re-adicionar Review em WebSite (Search Console reprovou).
- **Blog (KL-133):** tabela `blog_posts` (slug/markdown/status), público `GET /blog/posts`, `/blog/posts/{slug}`, `/blog/rss.xml`; admin `POST/PUT/DELETE /admin/blog/posts`; 5 MCP tools. Astro `/blog` + `/blog/{slug}` com `web/src/lib/blog.js::renderMarkdown` (**marked + sanitize-html**, strip de `<script>`/`on*=`). Nginx: `blog` na allowlist + `/blog/rss.xml` → FastAPI.
- **Micro-ferramentas SEO (KL-134, `api/tools.py`):** 5 tools públicas de aquisição (sem auth) que reusam checks existentes e devolvem JSON PT-BR simplificado: `GET /api/tools/{ssl,headers,lgpd,tech,email,stats}`. SSL→`tls_analyzer.get_tls_info`; headers→`base.fetch` (7 headers); lgpd→`privacy_checks.scan_privacy` (8 indicadores reais); tech→`tech_detector.detect_tech_stack`; email→`dns_util`+seletores DKIM (SPF/DKIM/DMARC/MX, recebe `domain=`). Rate limit **10/min/IP** (`tools:rl:{ip}`, fail-open, 429+Retry-After); timeout 15s→504; `/tools/stats` cacheado 24h (`tools:stats`) de `dashboard_summary`/`privacy_indicator_stats`/`get_tech_adoption`. Builders puros/testáveis; **nenhum check alterado** (só chamados). **P2 (frontend) ✅:** 5 landing pages SEO em `/ferramentas/{verificar-ssl,verificar-headers,teste-lgpd,detectar-tecnologias,verificar-email}` + índice `/ferramentas` — casca única `layouts/ToolLayout.astro` (Base+Header+nav-entre-ferramentas+ilha+FAQ+Footer), ilha React `components/tools/ToolPage.jsx` (`client:load`, input→fetch→resultado inline + CTA ao scanner completo `/scan?url=`) + `Results.jsx` (5 renderizadores) + `ToolCta.jsx`. Lógica pura em `web/src/lib/tools.js` (`TOOLS`/`buildToolUrl`/`parseToolError`/`formatScore`/`FAQS`). **FAQPage JSON-LD** + accordion `<details>` (CSP-safe) por página. "Ferramentas" no dropdown "Para empresas" (`nav.js EMPRESA_LINKS`) + Footer. ⚠️ `ferramentas` na allowlist nginx (http.conf + https.conf.template).
- **Metodologia/transparência:** `/metodologia` (KL-100), descadastro `/remover` (KL-102).

### LGPD (KL-161) — canal de direitos / DSAR
- Página `/lgpd` + ilha `LGPDForm.jsx` (lê `?tipo=`). **`POST /lgpd/request`** (público, sem conta): valida tipo∈{acesso,correcao,exclusao,portabilidade,revogacao,outra} + e-mail + nome + descrição≥10; **CPF opcional** (inválido→warning, não bloqueia); rate limit 3/e-mail/dia; grava em **`lgpd_requests`** (UUID=protocolo; pending→in_progress→resolved/denied) + 2 e-mails (titular via `privacidade@klarim.net` texto puro **até 15 dias úteis** + operador `klarimscan@gmail.com`, `LGPD_ADMIN_EMAIL`). Anti-enumeração. DPO em `/privacidade` §1. ROPA em `docs/LGPD.md`. Rodapé "Seus direitos (LGPD)"; perfil "Remover meus dados" → `/lgpd?tipo=exclusao`.

### Security Gate — produto para devs externos (módulo `security_gate/`, portável)
Scanner de EXPOSIÇÃO/config pós-deploy (**passivo — GET/HEAD/DNS/handshake TLS; NÃO envia payload de ataque; não é DAST**). Reusa checks do scanner via `scanner_adapter.py` (importa, não move/altera o scanner nem toca o scan público).
- **Engine** `engine.py::run_all(url, timeout, checks, config, spa_fingerprint)` — headers anti-cache em todo request, UA honesto; check que estoura vira ERROR isolado. `models.py`: `GateReport` score 100 − penalidades (CRIT-20/HIGH-10/MED-5/LOW-2); `passed` = sem FAIL crítico (= exit code do CI).
- **19 checks** em `checks/`: exposure, credentials (valor NUNCA logado/armazenado — só tipo+localização+severidade), headers, ssl, api_security, email_security (SPF/DKIM/DMARC via scanner), cors, cookies, redirect, rate_limit (rajada **concorrente**), error_disclosure, https_redirect, jwt_analysis (só decodifica), form_security, dns_security (DNSSEC+CAA), dependencies, tls_ciphers, subdomain (takeover), infrastructure_urls (Cloud Run/Heroku/localhost/IP privado). Relatório em camadas **Surface** (server+DNS) vs **Deep** (exposição+código).
- **SPA fallback (KL-147/160):** probe de controle (HEAD path aleatório) → captura fingerprint (ETag OU Content-Type+Content-Length OU **Last-Modified** — Cloudflare remove ETag); checks spa-aware (exposure/api) comparam cada 200 → mesmo fingerprint = fallback (PASS), diferente = exposição (FAIL). Evita falsos positivos massivos.
- **CLI/config:** `scripts/security_gate.py` + `scripts/klarim_gate_cli.py` (standalone, só `httpx`); `security-gate.yml` (config da Klarim, testa `/api/scan/` no rate_limit). **CI:** job `security-gate` no `deploy.yml` (`needs:[deploy]`, roda contra klarim.net LIVE, **não bloqueia** — o site já subiu; falha → e-mail via `scripts/security_gate_notify.py`, operador decide rollback).

#### Produto (conta dev) — schema, auth, planos, KYC, rate limit
- **Conta única:** `users.account_type` (owner|developer|both). Schema: `gate_plans`, `gate_api_keys` (**só SHA-256 + prefixo `KLM_xxxx`**, nunca em claro; `grace_expires_at` p/ rotação 1h), `gate_projects` (só escaneia se `verified`), `gate_runs`, `gate_invites`, `gate_vendors`, `gate_vendor_scans`, `gate_audit_log` (toda ação, NUNCA o valor da key), + colunas KYC em `users` (`cpf`, `address`/`address_data` JSONB, `phone`, `phone_verified`, `kyc_completed`, `suspended`, `company_cnpj`/`contract_url`/`enterprise_notes`).
- **Planos:** Free (4 checks, 5 scans/h) · Pro (9, 50/h) · Team (18/`["all"]`, 200/h) · Enterprise (all, ∞). Plano **efetivo** = trial > associado > Free. **Default = Free SEM trial** (KL-158). Seed idempotente (`ON CONFLICT DO NOTHING`) → contas Pro pré-existentes precisam de check novo via admin de planos.
- **KYC progressivo:** `POST /account/kyc` — CPF (`api/validators.py::validate_cpf`, módulo 11) + endereço **estruturado** (CEP/ViaCEP, `address_data` JSONB `{cep,street,number,complement?,neighborhood,city,state}`, UF∈27) + telefone. `kyc_completed` = CPF válido + endereço + telefone + **`email_confirmed`** (a única verificação real; `phone_verified` = SMS futuro). Sem KYC → scan devolve `basic` (score+categorias, sem detalhes); com KYC → `complete`. CPF **sempre mascarado** em log/PDF/admin.
- **Rate limiting (`api/gate_rate_limiter.py`, Redis, fail-open):** (1) IP 10/h · (2) conta/h por plano · (3) domínio **por-conta** (Free 30min · Pro 5min · Team/Ent 0 = skip; key `gate:rl:domain:{account_id}:{domain}`) · (4) intervalo entre domínios diferentes · (5) rpm por key (free 10/pro 30/team 60/ent 120). **Abuso:** >20 domínios distintos/24h → conta **suspended** (audit); `suspended`→403 em `/gate/scan`.
- **Endpoints:** `POST /gate/register` (cria dev + key 1× + trial), `POST /gate/scan` (roda no servidor; projeto verificado exigido exceto plano `scan_third_party`), `GET /gate/runs[/{id}]`, `GET /gate/runs/{id}/report` (PDF, exige KYC), `POST /account/gate/{activate,regenerate-key,upgrade,kyc}`, `GET /account/gate/status`, convites `POST /account/gate/invite` + `/gate/invite/{token}/accept`, verificação `POST /gate/projects/{id}/verify/{start,check}`. Admin `/admin/gate/{plans,accounts,audit}`. **Gate-verify ≠ owner-verify do KL-99** (tabelas separadas); propagação lazy via `propagate_scanner_verification` (conta que já provou posse no scanner → projeto Gate `verified` method=`scanner`).
- **Enterprise (KL-152):** due diligence de fornecedores (`gate_vendors`/`gate_vendor_scans`), scan de terceiro **redige** paths/credenciais (só categoria+severidade+contagens), PDF comparativo (`reporter/gate_report.py`, base64 Redis TTL 1h), VendorMonitor worker. CNPJ obrigatório.
- **Frontend:** landing `/security-gate` (SSR, tabela de planos ao vivo via `GET /gate/plans` público, Schema.org SoftwareApplication) · portal `/dashboard/gate` (`GatePortal.jsx`: status, novo scan, KYC banner, API key, projetos, histórico, upgrade PIX) · docs públicas `/docs/gate/{github-actions,gitlab-ci,bitbucket,jenkins,manual,api,troubleshooting}` (Astro `.md` + `DocsLayout.astro` + `web/public/docs-copy.js`) · admin `/painel/gate-plans`. Onboarding wizard scan-first. Lógica pura em `web/src/lib/gate/*.js` (`ux.js`, `snippets.js`, `address.js`, `nav.js`, `docsNav.js`).

---

## 10. Gotchas (evitam retrabalho)

- **`ensure_schema` CONCORRENTE no deploy** (api+discovery+worker) → risco de `DeadlockDetected` (ALTER/CREATE INDEX disputam `AccessExclusiveLock`). O `ensure_schema` **retenta** erro transitório de DDL (`_is_transient_ddl`, 6× backoff). Fila do scan = **`klarim:scan_queue`** (não `scan_queue`); persistência real em `targets.last_scan_at`/tabela `scans`, não só no log.
- **NUNCA editar arquivo via `docker exec`** (causou divergência de containers no incidente KL-127). `api`/`worker`/`discovery` = mesma imagem; deploy com `--force-recreate` garante uniformidade. Validação pós-deploy: `diff`/md5 dos módulos entre containers = vazio.
- **NULL-safe em WHERE de status:** `NULL = 'x'` vira NULL e um `AND NOT(...)` excluiria as linhas de status NULL — use `COALESCE(col,'')` (validar contagem antes/depois no Postgres da VM).
- **CSP estrita bloqueia islands Astro** ("Astro is not defined") → `/painel` usa CSP relaxada; ilhas admin `client:only="react"`. **Recharts só na Overview admin** (island `client:only`; público usa SVG puro — CSP bloqueia libs que injetam estilo).
- **`parseUTC`:** timestamps do Postgres são naive — adicione `Z` antes de `new Date`.
- **SPA fallback** (Vite `/painel` e Astro público) serve `200`+HTML p/ paths desconhecidos → um `.js` público fora do allowlist do nginx vira HTML (bloqueado por `nosniff`). Ver §3 Frontend.
- **Docker build na VM leva minutos** — lento ≠ travado. Confira idade dos containers via SSH (build-then-recreate mantém o site no ar).
- **`LeadShared.jsx`:** `CLASS_META`/`ClassBadge` extraídos de `Leads.jsx` (evita import circular).
- **Inbox:** corpo de e-mail externo em `<iframe sandbox="">` + `srcDoc` — **NUNCA** `dangerouslySetInnerHTML` (stored-XSS roubaria o JWT do operador).
- **504 no `/scan/summary`:** scan roda inline; site lento pode passar do `proxy_read_timeout` (180s) — o resultado ainda cacheia, a retentativa pega o cache quente.
- **"Escanear" no painel = síncrono** (`POST /targets/{id}/scan?sync=1`, reusa `get_or_scan`, `source='admin'`). Sem `sync` só enfileira.
- **CI travado sem deploy:** o job `deploy` tem `needs:[test,…]` — se o `test` falha (ex.: testes desatualizados após mudar uma regra), o deploy NUNCA roda e a VM fica atrás. Alinhe os testes ao mudar comportamento.

---

## 11. Índice de cards (traço rápido — detalhe em `claude/reports/KL-xxx_*.md` e `docs/HISTORY.md`)

- **KL-20** riscos dinâmicos por falha/setor · **KL-24** safety net global de bounce · **KL-25/27** fluxo de código/avulso (dormentes)
- **KL-44** Guardião Digital (P1 planos · P2 vigílias · P3 boletim+laudo · P4 vigílias avançadas · P5 privacidade+selo+benchmark · P6 checkout PIX+trial worker) ✅
- **KL-51** plataforma Astro · **KL-52** site_profile interno · **KL-54→KL-84** taxonomia de setores aberta
- **KL-57** analytics de disponibilidade/eventos · **KL-59** Google Safe Browsing ativo · **KL-60** scan desacoplado do e-mail
- **KL-61** leads/PQL · **KL-62** email_log unificado · **KL-63** MCP OAuth 2.1 · **KL-64** analytics anti-bot · **KL-65** SEO/Schema · **KL-66** contato nos perfis
- **KL-67** qualidade do profiler · **KL-68/71** reivindicação + verificação de propriedade (tiers, auto_domain) · **KL-69** gestão de usuários · **KL-70** agência→técnico
- **KL-74** conteúdo navegável · **KL-75** tecnografia (tech_detector, site_type, subdomínios) · **KL-77** VM e2-standard-4 + GCS archive + scan 200/h
- **KL-78** scan≠monitoramento · **KL-80** responsivo mobile-first · **KL-81** landing buscador · **KL-82** confiança progressiva (níveis de acesso) · **KL-83** analytics admin
- **KL-84** setores dinâmicos · **KL-85** lead scoring de qualidade · **KL-86/90** dashboard v2 · **KL-87** tema light/dark · **KL-89** fix de conversão (layout/visibilidade)
- **KL-91** rotação de senders cold · **KL-92** access log server-side (fonte de verdade) · **KL-93** hardening de endpoints públicos · **KL-94** gate de acessibilidade + auditoria Tipo B
- **KL-95** métricas corretas (fontes autoritativas) · **KL-96** desativa alerta antigo + contadores · **KL-97/98** gestão do dono (monitoramento/perfil/selo) · **KL-99** conta sem senha + 3 níveis
- **KL-100** /metodologia · **KL-101** isolar profile_view (perfil.klarim.net) · **KL-102** List-Unsubscribe + /remover · **KL-103** landing social proof · **KL-104** deep-linking + filtros de alvos + visão 360°
- **KL-105** conversão scan (signup-inline ativa monitoramento) · **KL-106** vigílias core no Free + painel.→301 · **KL-107** segurança (IDOR/aviso ao dono) · **KL-108** circuit breaker hard vs soft
- **KL-110** verificação Reoon pré-envio (hoje background) · **KL-122** gate de score configurável · **KL-123** vigílias expansíveis · **KL-124** deploy `--force-recreate` + rollback
- **KL-125/127/128/129/130/136/137** evolução do pipeline de e-mail (**superados pelo KL-145** — regra viva em §4) · **KL-131** sitemap dinâmico · **KL-132** SEO programático · **KL-133** blog
- **KL-138** hardening (redirect curto /a/{id}, blocos de exploit) · **KL-141** Security Gate engine (P1-P4) · **KL-145** desacopla Reoon (3 filtros locais) · **KL-146** priorizar e-mails pessoais
- **KL-147** Gate SPA fingerprint · **KL-149** +13 checks do Gate · **KL-150** navegação/UX + Analytics fuso Brasília + aba Visão geral desativada · **KL-151** Gate produto (KYC/planos/API keys/enterprise, P1-P4)
- **KL-152** Gate onboarding + docs + enterprise workflow · **KL-153** Gate KYC progressivo + rate limit 3 camadas + separa públicos empresa×dev · **KL-154** Gate importa SPF/DKIM/DMARC
- **KL-155** domain rate limit por plano · **KL-156→KL-159** fixes Gate (dropdowns, KYC email_confirmed, upgrade PIX, plano visível, default Free, auth-state) · **KL-160** nginx rate limit + Gate SPA fix + admin security scan
- **KL-161** conformidade LGPD (/lgpd, DPO, ROPA) · **KL-163** PDF de run do Gate + endereço estruturado KYC · **KL-26** cobertura de testes transversais
- **KL-134** micro-ferramentas SEO ✅ (P1 backend: 5 tools públicas `/api/tools/*` + rate limiter + stats; P2 frontend: `/ferramentas/*` landing pages + nav + FAQ Schema.org)

Histórico completo em **`docs/HISTORY.md`**; deploys em **`claude/DEPLOY_HISTORY.md`**; relatórios em `claude/reports/`.

# KL-124 pipeline test: 2026-07-28T10:19:29Z
