# Klarim — Arquitetura

> Referência detalhada extraída do antigo `claude.md`. Histórico íntegro das 50
> entregas em `docs/HISTORY.md`; endpoints em `docs/API.md`; segurança em `docs/SECURITY.md`.

## 1. Visão geral

O Klarim é um **scanner passivo de segurança web** (Security Rating, não pentest) para
PMEs brasileiras, operado como plataforma **freemium** ("Guardião Digital"). Fluxo de
alto nível:

```
Descoberta (CT logs) → fila Redis → Scan (48 checks passivos) → score/semáforo
   → Enriquecimento (perfil comercial + IA + CNAE) → Perfil público /site/{dominio}
   → Notificação (alerta proativo) → Conta/monitoramento → Vigílias contínuas
```

Cada peça é **best-effort/fail-open**: uma API externa fora do ar degrada para
`INCONCLUSO`/regex-only, nunca derruba o scan nem o worker.

## 2. Containers (Docker Compose)

| Serviço | Papel | Porta |
|---|---|---|
| `postgres` | PostgreSQL 16 — toda a persistência | interno |
| `redis` | fila de scan, cache por tier, heartbeat, rate limit, OAuth MCP | interno |
| `api` | FastAPI (endpoints públicos + admin + `/mcp` montado) | `127.0.0.1:8000` |
| `worker` | scan worker async (consome a fila, escaneia, enriquece) | — |
| `discovery` | Discovery + Alert + Rescan + Vigília via `asyncio.gather` | — |
| `astro` | Astro 7 SSR (Node standalone) — site público | `127.0.0.1:4321` |
| `web` | **Nginx** — único público; TLS + roteamento + segurança | `80` / `443` |

`api` monta `./:/klarim-control` **rw** (MCP grava `worker_control.json`); `discovery`
e `worker` montam `:ro`. Imagem Python (`build: .`) é compartilhada por api/worker/
discovery; `.dockerignore` exclui `frontend/`/`web/` dela.

## 3. Nginx — front único de TLS/segurança

- **TLS Let's Encrypt self-healing:** entrypoint escolhe `http.conf` (sem cert) ou
  `https.conf.template` (com cert, via envsubst) em runtime → deploy nunca quebra por
  falta de cert. Redirect 80→443, security headers com `always`.
- **Roteamento:** rotas públicas (`/`, `/scan`, `/site`, `/ranking`, `/dashboard`,
  `/cadastrar`, …) → **Astro** (resolver dinâmico `astro:4321`); `/painel*` + `/assets/`
  → build **Vite**; `/api/` → `api:8000` (rewrite tira o prefixo); `/mcp/` → `api:8000`
  (identidade, SSE com buffering off).
- **Resolver dinâmico:** `resolver 127.0.0.11 valid=10s` + upstream em variável (`set
  $klarim_api api:8000`) → re-resolve o IP por request (o container recriado ganha IP
  novo; sem isso, 502).
- **Subdomínios:** `painel.` (mesmo build, redireciona raiz→`/painel/login`), `mta-sts.`
  (policy MTA-STS via Cloudflare).
- **Hardening:** bloqueia dotfiles/paths sensíveis (regex → 404), security headers no
  `server` com `always`. ⚠️ Um `add_header` num `location` **quebra a herança** — repetir
  os headers de segurança ao adicionar `location`. Snippet compartilhado
  `frontend/nginx/security_headers.conf`. Valide com `nginx -t` (job de CI).

## 4. Frontend — Astro (público) + React islands + Vite (/painel)

**Decisão KL-51 (menor risco):** em vez de substituir o `frontend/` Vite, foi
**adicionado** o serviço `astro`; o Nginx proxeia as rotas públicas novas → Astro e
mantém o build Vite em `/painel*`. O painel admin não mudou.

- **`web/` (Astro 7):** `output: 'server'` + `@astrojs/node` standalone; páginas
  públicas com `prerender=true` (SSG) e o fluxo de scan/contas com `prerender=false`
  (SSR). Tailwind v4 CSS-first. Ilhas React (`components/**`) para interatividade
  (`ScanFlow`, `Dashboard`, `SignupForm`…), falando com `/api/*`.
- **`frontend/` (Vite):** build React do `/painel` admin, `lazy()` code-split (Recharts
  num chunk separado); ilhas admin usam `client:only="react"`.
- **CSP:** público estrito (scripts inline por hash SHA-256); `/painel` relaxado
  (`script-src 'unsafe-inline'`, painel é noindex/operator-only). Ver `docs/SECURITY.md`.

### Arquitetura de conteúdo navegável (KL-74)

Transforma os perfis-ilha (`/site/{domain}`) num ecossistema **mobile-first** (68% do
tráfego) que conduz ao scanner. Camadas:

1. **APIs públicas** (`api/main.py`, prefixo `/public/*`): `sectors` (índice),
   `sector/{slug}` (benchmark + ranking paginado + top fails + score-100), `top-fails`,
   `related` (cross-linking), `best` (vitrine score 100), `stats` (números da
   plataforma). Consultam `discovery/store.py` (`public_sector_*`, `public_related_sites`,
   `public_score_100_sites`, `public_platform_stats`) — **mesma visibilidade dos rankings
   KL-42** (`site_profile.public_visible` ≠ FALSE, `status IN ('scanned','alerted')`);
   nunca `contact_email`/CNPJ. **Cache Redis** 1h (setores/detalhe/related/best/stats) e
   24h (top-fails), com `Cache-Control public, max-age`. Rate limit **30/min/IP real**;
   SSR interno (sem `X-Forwarded-For`) isento.
2. **Páginas SSR** (`web/src/pages/`): `setores.astro`, `setor/[slug].astro`,
   `melhores.astro`, `estatisticas.astro` — todas `prerender=false`, sem ilhas (HTML puro:
   LCP baixo + zero risco de CSP). Contadores **estáticos** (a CSP pública proíbe script
   inline não-hasheado). SEO: `ItemList` + `BreadcrumbList`.
3. **Navegação contextual** no perfil: breadcrumb + `BreadcrumbList`, posição no ranking
   do setor (`get_sector_position`), seção "Outros sites do setor" (`/public/related`, SSR).
4. **`ScanCTA.astro`** reutilizável (input+botão empilham no mobile, inline em `sm:`,
   alturas ≥48px, texto ≥16px p/ não dar zoom no iOS).
5. **SEO/infra**: allowlist Nginx (`setores|setor|melhores|estatisticas`) — **antes** das
   páginas (senão caem no fallback SPA Vite); `sitemap.xml` inclui `/setor/{slug}` por
   setor ≥10 sites; footer com Setores/Melhores/Estatísticas.

## 5. Scanner engine (`scanner/`)

- **`runner.py`** roda os 48 checks em **paralelo** (`asyncio.gather` +
  `Semaphore(SCAN_MAX_CONCURRENCY=12)`), preservando ordem; carimba OWASP/CWE/LGPD por
  `check_id`. Seguro porque o rate limit de `base.fetch` é **por-domínio** (`asyncio.Lock`,
  1 req/s) — só domínios distintos se sobrepõem.
- **48 checks** (`checks/check_*.py`, descoberta dinâmica): 15 grátis (ORDER≤15) + 33
  pagos. Categorias: headers, HTTPS/HSTS/TLS/cert (+ TLS profundo 41–44 via
  `tls_analyzer` compartilhando 1 handshake), supply-chain (SRI/fontes), CORS/cookies,
  DNS/e-mail (SPF/DKIM/DMARC/DNSSEC/CAA/MTA-STS/BIMI via `dns_util`), CVE (`cve_db`
  Retire.js, check_30), content analysis passivo (45–48).
- **`scoring.py`** — score 0–100 + semáforo: 🟢 ≥90 **E** zero FAIL Alta/Crítica · 🟡
  ≥50 · 🔴 <50.
- **Cache por tier** (`cache.py`): `scan:free:<hash>` / `scan:full:<hash>` (ambos casam
  `scan:*` no flush), TTL 1h, com fallback no banco (`scans`).
- **Níveis de acesso ao resultado (KL-82, confiança progressiva):** `GET /scan/result`
  escaneia sem e-mail e **filtra o payload no backend** (`_access_level` + `_filter_scan_result`)
  por `anonymous` (score + barras de categoria + 1 risco) < `unconfirmed` (benchmark + 2 riscos +
  nomes de checks sem evidência) < `confirmed`/`alert_session` (48 checks completos + PDF).
  Corte server-side (nunca vaza evidência aos níveis baixos); rate limit anônimo 5/h+20/dia por IP.
- **Dois fluxos de conta (KL-82 Slice 2):** signup **sem código** — e-mail+senha → conta na hora
  (`email_confirmed=false`) + e-mail de boas-vindas com link (`/account/confirm`, token 30d). Nasce
  confirmada se o e-mail já foi verificado no scan (KL-25). `unconfirmed` vê o dashboard básico com
  banner "confirme seu e-mail"; `confirmed` desbloqueia PDF/checks detalhados. O código de 6 dígitos
  (`/account/verify`) fica como fallback dormente. Cleanup diário (worker `trial`) remove contas
  não confirmadas +30d sem atividade.
- **Fluxo 2 do alerta (KL-82 Slice 3):** o CTA do e-mail de alerta é um link HMAC
  (`/api/alert-access?token=`). Clicar prova posse do e-mail → cria uma **sessão temporária**
  (cookie `klarim_alert`, 24h, escopada a 1 site) → resultado completo daquele site sem conta →
  `signup-from-alert` converte em conta só com senha (`source='hmac'`). Tabela `alert_sessions`
  registra o funil (created/converted).
- **Enriquecimento** (`profiler.py` + `ai_enrichment.py` + `enrichment.py`): crawl
  multi-page → dados comerciais (contatos, JSON-LD, tecnologias, CNPJ) + IA (setor,
  descrição, tags, CNAEs). Best-effort, **fora do caminho síncrono** do scan, **não
  altera o score**.
- **Taxonomia ABERTA de setores (KL-84):** a IA não fica mais presa aos 48 setores fixos —
  quando um negócio não encaixa, propõe um setor novo (`is_new_sector`). O
  `discovery/sector_classification.py::process_classification` (puro) resolve sinônimos
  (`sector_synonyms.py`) → tabela **`sectors`** (official/proposed/approved/rejected/merged) →
  segue `merged_into` / cria proposta / cai em 'outro'. O admin cura em `/painel/setores`
  (aprovar publica em `/setores`; merge/reject reclassificam sites **preservando `manual`/
  `receita`**). `scripts/reclassify_sectors.py` reclassifica retroativamente pela descrição já
  extraída (sem re-scan). Os endpoints públicos de setor filtram por status (só official/approved).

## 6. Workers (container `discovery`)

`discovery/worker.py::main()` roda `asyncio.gather(DiscoveryWorker, AlertWorker,
RescanWorker, VigiliaWorker)`. Cada um relê config por ciclo (`get_setting`) e checa
`worker_control` (`is_enabled`) — pausa/retoma via MCP sem redeploy.

| Worker | Arquivo | Ciclo | Função |
|---|---|---|---|
| Discovery | `ct_poller.py` + `worker.py` | 30 min | CT log poller → filtra `.com.br` → fingerprint + e-mail + setor → registra + enfileira **todo site acessível** (KL-60) |
| Alert | `alert_worker.py` | 30 min | alerta proativo em batch (50, Resend Batch API), teto `ALERT_DAILY_LIMIT`/cota mensal, anti-bounce, kill-switch `STOP_ALERTS` |
| Rescan | `rescan_worker.py` | 24 h | reescaneia alvos ≥30 dias + e-mail de evolução; monitora sites 100 |
| Vigília | `vigilia_worker.py` | 6 h + loop uptime 5 min | 8 vigílias: core (SSL, domínio, score, e-mail, reputação) + P4 (`changes`, `phishing`) no ciclo 6 h; **`uptime`** num loop próprio de 5 min (reagenda pelo intervalo do plano: Pro 30 / Agency 5 min). Enforcement de plano; **começa pausada** |
| Bulletin | `bulletin_worker.py` | 1 h | KL-44 P3: boletim de segurança por frequência do plano (free=mensal/pro=semanal/agency=diário), às `BULLETIN_HOUR_UTC`; laudo técnico ao técnico vinculado |
| Trial | `trial_worker.py` | 1 h (age 1x/dia às `TRIAL_HOUR_UTC`) | KL-44 P6: avisos 7d/1d + downgrade silencioso de trials expirados p/ Free (desativa vigílias, preserva dados). Flag `TRIAL_EXPIRATION_ENABLED` |
| Scan | `scanner/main.py --worker` | contínuo | `blpop` da fila → escaneia → cacheia → salva + enriquece inline |

**Resiliência:** heartbeat no Redis (`worker:<name>:status`, TTL 600s) → painel mostra
🔴 se expira; watchdog em thread (`os._exit(1)` se o loop trava) + `restart:
unless-stopped`. Motivo: incidente de 08/07 (domínio travado congelou tudo por 7,5h).

## 7. Dados (PostgreSQL — `discovery/store.py`)

Schema criado idempotente no `ensure_schema` (sem Alembic). Principais tabelas:

- **Núcleo:** `targets` (alvos + setor + `last_scan_*`), `scans` (histórico, `source`,
  `scanned_by_email`), `site_profile` (perfil comercial, `public_visible`,
  `edited_by_admin`), `target_classifications` (CNAE multi-setor), `sectors` (KL-84:
  taxonomia aberta — slug/label/macro/status/merged_into/site_count; seed idempotente dos
  48 oficiais no `ensure_schema`).
- **Funil/e-mail:** `payments`, `alert_log`, `rescan_log`, `email_log` (rastreabilidade
  unificada, KL-62), `email_blocklist`, `recovery_tokens`, `scan_verifications`,
  `scan_credits`, `site_events` (tracking client-side), `access_log` (KL-92: tracking
  **server-side** por IP — fonte de verdade das métricas de visitante), `scan_leads` (PQL).
- **Contas:** `users`, `user_sites`, `password_resets`, `vigilias`, `vigilia_alerts`,
  `typosquat_alerts` (KL-44 P4), `plans`/`subscriptions`/`subscription_history` (KL-44 P1),
  `subscription_payments` (KL-44 P6: PIX de upgrade, **separada** de `payments`/relatório).
- **Pagamento de assinatura (KL-44 P6):** `POST /account/upgrade` → cobrança PIX
  transparente (AbacatePay, reusa `payments/abacatepay.py`) → QR no dashboard
  (`PlanSection`). O `POST /webhooks/abacatepay` (idempotente) ativa o plano
  (`_confirm_subscription_payment` → `plans.activate_paid` + `_sync_user_vigilias`); o
  poller `/account/upgrade/status` revalida (o redirect pode chegar antes do webhook).
  **NUNCA guarda dado de cartão/PIX** — só o `provider_charge_id`.
- **Privacidade (KL-44 P5):** sem tabela nova — os 8 indicadores vivem no
  `scans.checks_json->'privacy'` (`scanner/privacy_checks.py`, um GET próprio, `privacy_score`
  0–8 separado do de segurança). Selo público `GET /seal/{domain}` (cache Redis 1h) +
  `web/public/seal/widget.js` (estático, CORS `*`, sem tracking). Benchmark setorial rico
  (mediana + distribuição anônima) cacheado em `benchmark:{sector}`/`benchmark:all` (24h).
- **Tecnografia (KL-75):** `site_tech_stack` (tech detectada por scan — name/category/
  subcategory/version/source/confidence; UNIQUE `(target_id, scan_id, name)`),
  `site_status_log` (histórico de status do site); `targets.email_provider` +
  `targets.related_domains` (JSONB) para query rápida.
- **Operação:** `monitored_sites`, `inbox_messages`, `admin_settings` (config ao vivo).

## 8. Integrações externas

| Serviço | Uso |
|---|---|
| **Resend** | e-mail — 2 domínios: `klarim.net` (transacional) + `klarimscan.com` (proativo). Batch API + webhook Svix (bounce/complaint) |
| **AbacatePay** | PIX (R$ 19 avulso); webhook query-secret + HMAC |
| **OpenAI** | GPT-4o mini (setor/descrição/tags/CNAE; ~US$0,001/site; fail-open) |
| **APIs públicas de leitura** | crt.sh (CT/subdomínios), HIBP, Google Safe Browsing, IBGE (CNAE), BrasilAPI/ReceitaWS (CNPJ), RDAP (domínio) |

## 9. MCP Server (`mcp_server/`)

Wrapper fino sobre a API/store — operar o Klarim por linguagem natural no Claude.
SSE puro (Starlette montado no FastAPI em `/mcp`) + **OAuth 2.1 + PKCE** (KL-63,
Klarim é o próprio authorization server) + **`MCP_API_KEY` estático** como fallback.
Auth própria (`MCPAuthMiddleware`, fail-closed, constant-time) — fora do JWT admin.
49 tools por domínio (ver `docs/API.md`), todas via `_guard` (nunca derrubam a sessão).

## 10. Fluxo de dados end-to-end

1. **Discovery** lê CT logs → domínio `.com.br` → fetch + fingerprint + e-mail + setor
   → `register_target` (UPSERT) → enfileira scan.
2. **Scan worker** escaneia (48 checks) → `scoring` → cacheia → salva em `scans` +
   atualiza `targets` → **enriquece** perfil + IA + CNAE inline → **detecta o tech stack**
   (KL-75) → **arquiva o response bruto no GCS** (KL-77).
3. **Alert worker** pega alvos escaneados com falhas (ou score 100) → alerta proativo.
4. **Perfil público** `/site/{dominio}` fica indexável (sitemap, og:image, Schema.org).
5. Visitante escaneia (verificação por e-mail, KL-25) → vira **lead** (PQL, fire-and-
   forget) → **conta** (signup vincula histórico) → **monitoramento** (vigílias por plano).
6. Todo e-mail passa por `KlarimMailer._send` → `email_log` (rastreabilidade + blocklist).

### Arquivamento de responses brutos no GCS (KL-77 Fase 2)

O Postgres guarda o **veredito** de cada check + score, mas descarta o **response bruto**
(HTML da homepage no momento do scan, headers crus, snapshot DNS/SSL) — dado
irrecuperável que o KL-75 (enriquecimento expandido) vai reprocessar sem re-escanear.

- **Captura sem request extra:** o `enrich_profile` (que roda logo após `save_scan`) já
  busca a homepage (headers/html/status/tempo) e o DNS (MX/NS); com `capture_raw=True`
  ele devolve esse response ao worker. O SSL vem do **cache do `tls_analyzer`** (warm logo
  após os checks TLS; no tier gratuito, no máx. 1 handshake passivo). O caminho público/
  anônimo (`/scan/summary`) passa `capture_raw=False` — nada muda lá.
- **Upload:** `scanner/gcs_archive.py` comprime o payload (gzip) e sobe para
  `gs://klarim-raw/YYYY/MM/DD/{scan_id}.json.gz` (bucket Nearline, privado). O upload roda
  em thread (`asyncio.to_thread`) para não prender o event loop.
- **Fire-and-forget:** client GCS **lazy** (import só no 1º upload); `GCS_ENABLED=false` faz
  bypass total; qualquer erro (bucket ausente, sem permissão, rede) é logado e engolido — o
  scan já está no Postgres. Contadores por dia no Redis (`klarim:gcs:*`, TTL 48h) alimentam
  `get_gcs_archive_stats` (MCP) / `GET /admin/gcs-archive/stats` e o bloco `gcs_archive` do
  status do sistema.

### Enriquecimento tecnográfico (KL-75, Prompt 1)

O mesmo response bruto que vai para o GCS alimenta a **detecção de tech stack** — parse em
memória (regex sobre strings já carregadas), **sem request HTTP extra** (< 500ms/scan).

- **Função pura** `scanner/tech_detector.py::detect_tech_stack(headers, html, dns, ssl)` →
  `{technologies, email_provider, dns_provider, related_domains, site_status,
  verified_platforms, company_name, schema_types}`. 6 grupos de detecção: headers HTTP
  (servidor/backend/CDN/plataforma) + cookies, ~50 scripts (analytics/marketing/pagamento/
  chat/e-commerce/CMS/segurança/…), meta tags (OG/verificações/generator/RSS), DNS
  (provedor de e-mail via MX, DNS via NS, plataformas verificadas via TXT), SSL SAN
  (domínios relacionados) + issuer (CA) + organização (nome legal), e status do site
  (`ativo`/`parked`/`abandonado`/`fora_do_ar`/`bloqueado`/`dominio_inativo`).
- **Gravação** (`scanner/main.py::persist_tech_detection`, **resiliente** — nunca trava o
  scan): batch INSERT em `site_tech_stack` (idempotente via UNIQUE `(target_id, scan_id,
  name)` + `ON CONFLICT DO NOTHING`); `targets.email_provider`/`related_domains`; preenche
  `site_profile.company_name` **só se vazio** (nunca sobrescreve regex/IA/manual); histórico
  em `site_status_log`. O `enrich_profile` ganhou 1 lookup DNS TXT (só no `capture_raw`).
- **Backfill** `scripts/backfill_tech_stack.py` reprocessa os responses já arquivados no GCS
  (a partir de 2026-07-19) pela MESMA função — sem re-scan.
- **Exposição:** stack detalhado só em API autenticada/admin (`GET /targets/{id}/tech-stack`)
  e MCP (`get_tech_adoption`/`get_site_tech_stack`/`get_site_status_history`); o público vê
  só badges booleanos (`GET /public/tech-summary/{domain}`, 30/min/IP). Dados técnicos são
  públicos (headers/certificados); o **valor está na agregação** (market share por setor).

### Pipeline de access log server-side (KL-92)

O tracking client-side (`site_events` via `public/track.js`) infla visitantes ~5x porque
pre-fetches de e-mail executam JavaScript no browser do bot. O KL-92 adiciona uma **fonte de
verdade server-side**: um middleware HTTP que classifica bot/humano pelo **IP real**, sem
depender de código no client.

- **Middleware** `api/access_log_middleware.py` (registrado após o auth → **OUTERMOST**,
  enxerga até respostas 401). Ignora assets estáticos (`should_log`). Para cada request
  relevante: extrai IP (`CF-Connecting-IP`→`X-Real-IP`→peer), país (`CF-IPCountry`), user_id
  (JWT de usuário) e `domain_queried` (`/site/{d}`, `/scan?url=`, ou `request.state.
  domain_queried` que o handler seta p/ POST). **Fire-and-forget:** a captura é síncrona e
  barata; o processamento pesado roda em background (`_spawn`) — o response volta na hora.
- **Classificação** `api/bot_classifier.py::classify_bot` (função **pura**): IP próprio
  (`34.135.194.208` nunca é bot) → usuário autenticado (logou = humano) → datacenter (~30
  CIDRs estáticos AWS/GCP/Azure/DigitalOcean/Hetzner, sem lookup externo) → crawler declarado
  no UA → rate >50/h sem conta (contador Redis `access_rate:{ip}`, TTL 1h) → padrão de
  pré-fetch (US + `/site/*` sem navegação). Retorna `(is_bot, bot_reason)`.
- **Gravação bufferizada:** cada registro entra num buffer em memória; um loop drena em
  **batch INSERT** a cada 5s (`store.log_access_batch`). Volume estimado 200-500 req/min →
  ~300-700k linhas/dia. Erro de banco = log perdido, nunca bloqueia o request.
- **Retroatividade:** quando um IP faz uma **ação humana** (scan/signup/login/PDF/evento,
  `HUMAN_ACTIONS`), `mark_ip_human_today` marca como não-bot todos os registros daquele IP no
  dia — corrige o falso-positivo do dev/cliente real atrás de um IP de datacenter.
- **LGPD:** o IP é dado pessoal → retido 90 dias e depois anonimizado (loop diário
  `anonymize_old_access_logs` trunca o último octeto via `set_masklen(…,24)`). Nos responses
  da API o IP volta **mascarado** (1 octeto em ip-behavior, 2 em ip-detail); o completo fica
  só no banco (ip-detail aceita IP completo como parâmetro, admin-only).
- **Exposição:** `GET /admin/analytics/{server-metrics,ip-behavior,ip-detail}` (agregações
  `al_*` no store, derivação pura no módulo, cache 5 min) + MCP `get_server_metrics`/
  `get_ip_behavior`/`get_ip_detail`. O tracker.js **continua** para eventos de interação.
  ⚠️ O Nginx faz `rewrite ^/api/(.*)$ /$1` → o middleware vê os paths **sem** o prefixo `/api`.

**Duas fontes (KL-92 P3) — cobertura completa sem duplicar.** O middleware só vê o tráfego que
chega ao FastAPI (`/api`, `/mcp` ≈ 12% do total); as páginas SSR do Astro (landing, `/scan`,
`/site/*`, `/setor/*`) passam pelo Nginx **direto** ao container Astro. Como o Nginx vê 100%,
**`api/nginx_log_parser.py`** lê o access_log dele e insere na MESMA tabela `access_log`:

- O Nginx loga em `frontend/nginx/log_format.conf` (`log_format klarim` + `access_log
  /var/log/klarim/access.log`, contexto http via conf.d — os **server blocks ficam intactos**,
  o stdout p/ `docker logs` continua). Volume `klarim-nginx-logs` compartilha o arquivo
  web(rw)→api(rw). O IP real vem do `CF-Connecting-IP`, o país do `CF-IPCountry`.
- O parser roda a cada 30s (loop no lifespan), lê **incrementalmente** (offset + inode p/
  detectar rotação), e ao passar de 50 MB **trunca** o arquivo (seguro: o Nginx abre logs em
  `O_APPEND`, a próxima escrita vai para o offset 0). Fail-safe.
- **Disjunção:** o parser **pula assets + `/api` + `/mcp`** (já cobertos pelo middleware) →
  nenhuma duplicata. Registros do parser levam `source='nginx'`; os do middleware,
  `source='middleware'` (coluna `access_log.source`). Classifica com `classify_bot_simple`
  (sem rate/endpoint — só IP próprio → datacenter → crawler → US=`prefetch_likely`); a
  retroatividade do middleware (quando o MESMO IP faz uma ação humana em `/api`) corrige.
- O middleware **não** foi desligado (mantém `user_id` + retroatividade para o funil, cujas
  etapas — `/scan/result`, `/account/signup` — são `/api`). **P0 fix:** `al_hourly_heatmap`
  usava `hour` (palavra-chave do Postgres) como alias sem aspas → 500 no server-metrics; agora
  `AS hr` + `GROUP BY 1, 2` (posicional).

### Reivindicação de site + verificação de propriedade em tiers (KL-68)

Do perfil público `/site/{domain}`, o dono reivindica o site (CTA condicional ao login,
ilha React `ClaimSite`). A propriedade tem **2 tiers**, ambos server-authoritative:

- **Tier 1 — auto (`auto_email`):** no signup/login/add-site, se o e-mail da conta ==
  `contact_email` do alvo (comparado **no servidor**, `_email_owns_target`), o vínculo
  `user_sites` vira dono verificado na hora. Nunca expõe o `contact_email`.
- **Tier 2 — código (`code_verification`):** `POST /account/ownership/request-verification`
  envia um código de 6 dígitos **ao `contact_email`** (transacional, `seguranca@klarim.net`);
  o usuário digita → `POST /account/ownership/verify`. TTL 30 min, 3 tentativas, tabela
  `ownership_verifications`.
- **First-come-first-served:** 1 dono por site (`site_has_owner` bloqueia o 2º). Admin
  revoga em `POST /targets/{id}/revoke-ownership`.
- **Domain guard** (`api/domain_guard.py`, puro): domínio público/institucional
  (gmail.com, `.gov.br`…) **não é monitorável** — 422 no add-site, sem CTA no perfil; o
  **scan continua livre**. Limpeza retroativa: `POST /admin/clean-blocked-sites`.
