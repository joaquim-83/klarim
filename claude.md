# Klarim — Guia do Agente CLI

> **Leia este arquivo antes de tocar no código.** É o onboarding obrigatório de
> qualquer agente Claude que trabalhe no Klarim. Se algo aqui conflitar com um
> pedido, **pare e pergunte** antes de prosseguir.

**Klarim** — *"O alarme que toca antes do ataque."* Scanner **passivo** de
segurança web para **PMEs brasileiras** (hotéis, clínicas, escolas, e-commerces,
contabilidades…) que têm sistema web exposto e não têm equipe de segurança.
Plataforma **freemium** com modelo "Guardião Digital": descobre alvos, roda checks
comprováveis sem invasão, calcula um **score 0–100 + semáforo 🔴🟡🟢**, gera perfis
públicos e monitora silenciosamente — só alerta o dono quando algo importa.

> **📚 Documentação detalhada** (este arquivo é só o guia enxuto de instruções):
> - `docs/ARCHITECTURE.md` — arquitetura, containers, fluxo de dados
> - `docs/API.md` — todos os endpoints + tools MCP
> - `docs/DEPLOY.md` — deploy, CI/CD e **todas as variáveis de ambiente**
> - `docs/SECURITY.md` — políticas de segurança e postura de scanning
> - `docs/HISTORY.md` — histórico íntegro das 50 entregas (o antigo claude.md)
> - `claude/reports/KL-xxx_*.md` — relatório de cada tarefa
> - `klarim_mvp_spec.md` — especificação de produto (fonte da verdade)

---

## 1. Links e acesso

- **Produção:** https://klarim.net · **Admin:** https://painel.klarim.net
- **Repo:** https://github.com/joaquim-83/klarim.git
- **Jira (board KL):** https://igoove.atlassian.net/jira/software/c/projects/KL/boards/265/backlog
- **VM GCP:** `klarim-prod` (**e2-standard-4**, 4 vCPU/16GB, disco **200GB pd-ssd**) · zona
  `us-central1-a` · projeto `project-b08050df-fa4e-49ac-919` · deploy em `/opt/klarim` ·
  **IP estático `34.135.194.208`** (reserva `klarim-static-ip`). Migração KL-77 Fase 1
  (2026-07-19). CI/CD deploya por instance-name (secret `GCP_INSTANCE_NAME=klarim-prod`).
  A VM antiga `instance-20260706-112125` (e2-medium, IP efêmero 35.238.72.10) fica em
  standby 24h como fallback (reverter DNS no Cloudflare para 35.238.72.10 + reiniciar os
  workers dela). **OS Login está DESABILITADO** (o SSH do CI usa injeção de chave por metadata).
- **E-mail operacional:** klarimscan@gmail.com

```bash
gcloud compute ssh --zone "us-central1-a" "klarim-prod" \
  --project "project-b08050df-fa4e-49ac-919"
```

O `.env` de produção vive **apenas na VM** (`/opt/klarim/.env`), nunca no git.

---

## 2. Stack

Python 3.12 / **FastAPI** + **PostgreSQL 16** + **Redis** + **Astro 7** (SSR, Node
standalone) + **React** (islands) + **Tailwind v4** (CSS-first, sem config) +
**Nginx** (front único de TLS) + **Docker Compose** + **WeasyPrint** (PDF) +
**Resend** (e-mail) + **AbacatePay** (PIX) + **OpenAI GPT-4o mini** (enriquecimento).

---

## 3. Regras invioláveis

### Processo
- **Claude Code CLI é o executor; Claude chat é o planejador.**
- Todo pedido precisa de um card **`KL-xxx`** no Jira (exceto ajustes mínimos: typo,
  formatação). Jira transition "Done" = ID **41**.
- **Commits e código em inglês; comentários podem ser PT-BR.** Formato do commit:
  `tipo(KL-xxx): descrição`.
- **Cada tarefa gera um relatório PT-BR em `claude/reports/KL-xxx_<slug>.md`** e
  **atualiza a documentação afetada** (este arquivo, `docs/`, `README`, spec).
- **Rode `pytest` antes de concluir.** A tarefa **não está pronta até o deploy estar
  verde** (push + GitHub Actions test+deploy 100% green).

### Scanner — só varredura passiva (Security Rating, NÃO pentest)
- ✅ **Faz:** `GET`/`HEAD` a URLs públicas, leitura de headers, certificados SSL
  públicos, DNS público, arquivos servidos sem autenticação.
- ❌ **NUNCA:** payloads de injeção (SQLi/XSS), brute-force, área autenticada,
  exploração de vulnerabilidade, extração de dados.
- **Timeout 10s/request; rate limit 1 req/s por domínio** (centralizado em
  `checks/base.py` — não reimplemente). **User-Agent identifica o Klarim
  honestamente** — não se passa por navegador, não se esconde.

### Segurança (regra de 2026-07-15 — inviolável)
- **Toda implementação ou fix inclui revisão de segurança.**
- **Nenhum endpoint, formulário ou fluxo de dados pode ficar sem proteção**
  (auth, validação, rate limit, sanitização).
- Empresas de **cibersegurança estão entre os alvos** e interagem ativamente com a
  plataforma — assuma que tudo será sondado. Detalhes em `docs/SECURITY.md`.

### Dados
- **Regra de ouro:** o **AI enrichment NUNCA sobrescreve** dado extraído por regex,
  nem classificação `manual`/`ai`; só preenche campo **vazio**. `source='receita'`
  (CNAE oficial) nunca é sobrescrito pela IA.
- Quando **`scoring.py` ou um check muda**, **flush `scan:*` no Redis** da VM após o
  deploy (senão semáforos velhos servem por até 1h).
- **Não use `DATABASE_URL`** — a senha em base64 contém `/`. Use as `POSTGRES_*`
  individuais.
- **`contact_email`, `cnpj`, `whatsapp` NUNCA são expostos** na API/perfil público.

### Frontend (padrão Astro, KL-51)
- Ilhas admin: **`client:only="react"`** (não `client:load`). `AdminShell` é wrapper
  interno (prop `active`), não ilha-em-slot.
- **`<a href>`** em vez de `Link`/`NavLink`; **`window.location`** em vez de
  `useNavigate`. **Zero `react-router-dom`** no código migrado.
- **`parseUTC`** para timestamps naive do Postgres (adicionar `Z` antes de `new Date`).
- **CSP relaxada no `/painel`** (decisão KL-51: `script-src 'unsafe-inline'`, painel é
  noindex/operator-only). O **público** usa CSP estrita (scripts inline por hash SHA-256).
  **Ao adicionar/alterar um script inline público, recompute o hash e atualize
  `frontend/nginx/security_headers.conf`** (hoje: **5 hashes** — 3 do Astro + 1 anti-FOUC de tema
  do KL-87 + 1 do init do GA4/gtag do KL-92 P4). **KL-92 P4:** o Cloudflare Web Analytics
  (`static.cloudflareinsights.com/beacon.min.js`) foi **removido** — era o único script externo
  SEM SRI (travava o score 100) — e trocado por **Google Analytics 4** (`G-7WPZN66JTB`): loader
  `www.googletagmanager.com` no `script-src` + hash do init inline; `connect-src`/`img-src` liberam
  `*.google-analytics.com`. O check 13 (SRI) ganhou uma **allowlist de CDN dinâmico**
  (`SRI_ALLOWLIST_DOMAINS`: googletagmanager/google-analytics/cloudflareinsights) — esses não contam
  como FAIL (SRI inviável em bundle que o provedor atualiza sem aviso) → `klarim.net` volta a 100.
- **Tema light/dark (KL-87):** **light é o padrão**. Mecanismo: os tokens `--color-slate-*` e
  `--color-white` do Tailwind são **sobrescritos por tema** em `web/src/styles/global.css`
  (`:root`=light com a escala slate INVERTIDA; `[data-theme='dark']`=defaults). Como todo
  utilitário resolve `var(--color-slate-…)`, as páginas viram theme-aware **sem migrar classe**.
  Botões usam `text-[var(--accent-text)]` (escuro constante sobre laranja); QR PIX `bg-[#ffffff]`.
  Anti-FOUC inline no `<head>` (hash na CSP) + toggle `public/theme.js` (externo) no Header.
  **Admin (`/painel`) força `data-theme=dark`** (sem toggle). Verde/amarelo/vermelho e o laranja
  da marca (`#ff6b35`) são constantes nos 2 temas.
- **Responsivo (KL-80, 68% mobile):** alvos de toque **≥44px** (`min-h-[44px]`/`py-3`; links-texto
  pequenos → `inline-flex min-h-[44px] items-center px-1`); **inputs `text-base`** (16px, nunca
  `text-sm` — evita zoom iOS) + `h-12`; botões `w-full sm:w-auto` (empilham no mobile); **nada de
  largura fixa que estoure 375px** (dropdowns `w-full sm:w-64`); grades `grid-cols-1` → `md:`/`lg:`;
  `active:scale-95`/`[0.98]` p/ feedback tátil. Breakpoints Tailwind padrão (sm640/md768/lg1024/xl1280).
- **Container das páginas públicas (KL-89):** o `<main>` de toda página pública puxa a largura de
  **`web/src/lib/layout.js`** — **não** invente `max-w` por página. Conteúdo (listagens/scan/perfil)
  → `PAGE_CONTAINER` (expande até `lg:max-w-7xl`); formulário → `FORM_CONTAINER` (`max-w-md`); texto
  corrido → `PROSE_CONTAINER` (`max-w-3xl`, via `Page.astro`). Tailwind escaneia `.js`, então as
  classes literais dessas constantes entram no build mesmo interpoladas (`class={PAGE_CONTAINER}`).
- **Resultado do scan (KL-89):** desktop e mobile entregam o **mesmo conteúdo/nível** — a
  visibilidade deriva do `access_level` (KL-82), **nunca** do dispositivo (`web/src/lib/scanView.js
  ::viewFlags`, puro/testável). Linguagem adapta pela **origem**: alerta (`alert_session`) → "Seu
  site" + CTA só senha (e-mail HMAC mascarado); orgânico → "Este site. E o seu?". O CTA de conta
  some para quem já tem conta. LGPD é o único bloco restrito a acesso completo.

### E-mail (reputação)
- **Alertas proativos:** `Klarim <alerta@klarim.net>` (`ALERT_FROM_EMAIL`/`ALERT_FROM_NAME`).
  **2026-07-20:** MIGRADO de `alerta@klarimscan.com` → `alerta@klarim.net`. O warmup do
  klarimscan.com falhou (7.419 alertas → 2 cliques; tudo no spam); `klarim.net` é aged, com
  SPF/DKIM/DMARC no Resend e entrega na inbox. **Trade-off:** o proativo (cold) passa a
  compartilhar o domínio com o transacional — **monitorar a reputação do `klarim.net`**
  (bounce/complaint em `get_email_health`); se degradar o transacional, reavaliar. O
  `ALERT_DAILY_LIMIT=30` (warmup) pode ser relaxado num domínio aged. `_proactive_from` lê o
  env a cada envio; a troca do `.env` vale ao **recriar o container** (sem rebuild).
- **Transacionais:** `klarim@klarim.net` (`RESEND_FROM`). **2026-07-21:** MIGRADO de
  `seguranca@klarim.net` → `klarim@klarim.net` — a palavra "seguranca" é keyword de phishing e,
  com domínio aged, elevava o spam score (a confirmação de conta caía no spam). `_mailer()` lê
  `RESEND_FROM` a cada envio → a troca do `.env` vale ao **recriar o container**. Reply-To
  (`scan@`) e o proativo (`alerta@`) **não mudam**.
- **Proativo respeita a blocklist; transacional pode ignorá-la mas SEMPRE registra**
  (todo e-mail passa por `KlarimMailer._send` → `email_log`).
- **E-mails proativos (alerta + "perfil consultado") = TEXTO PURO** (`text`, sem
  `html`) — menos cara de marketing, cai menos no spam; CTA → perfil público
  `/site/{domain}` com UTM. Builders em `notifier/email_client.py`
  (`build_alert_text`/`build_profile_view_text`); os templates HTML ficam só como
  referência. Linguagem freemium, sem menção a preço/pagamento/relatório.
- **Proativos levam `List-Unsubscribe` + `List-Unsubscribe-Post` (one-click RFC 8058,
  `list_unsubscribe_headers`)** — alerta/profile_view/evolution. O `GET/POST /unsubscribe`
  aceita params **opcionais** (ausentes → HTML "Link incompleto", nunca 422 JSON) e trata
  o POST one-click; a validação HMAC constant-time é inalterada. Todos os workers que
  e-mailam o `contact_email` (alert/rescan/profile-view) já filtram `status='unsubscribed'`.

---

## 4. Arquitetura (resumo)

Detalhe completo em `docs/ARCHITECTURE.md`.

### Containers (Docker Compose)
`postgres` · `redis` · `api` (FastAPI, `127.0.0.1:8000`) · `worker` (scan worker) ·
`discovery` (Discovery + Alert + Rescan + Vigília via `asyncio.gather`) · `astro`
(Astro SSR, `:4321`) · `web` (Nginx, portas 80/443 — **único público**).

### Nginx — front único de TLS/segurança
Serve o build **Astro** (rotas públicas), o build **Vite** em `/painel*`, faz proxy
`/api` e `/mcp` (com **resolver dinâmico** — `set $var` + `resolver 127.0.0.11` para
re-resolver o IP do container), TLS Let's Encrypt (self-healing http↔https),
subdomínios `painel.` e `mta-sts.`, bloqueia paths sensíveis e aplica os security
headers com `always`. ⚠️ Um `add_header` num `location` **quebra a herança** dos
headers do `server` — **repita os headers de segurança** ao adicionar um `location`.
Valide com `nginx -t` (há job de CI); config inválida **derruba o site**.

### Scanner
- **Gate de acessibilidade (KL-94):** ANTES dos 48 checks, `run_scan` confere se o site é
  acessível (`scanner/runner.py::_accessibility_gate`) — um domínio inexistente/offline NÃO pode
  receber score (os checks Tipo B dariam PASS falsos). (1) DNS resolve A/AAAA? NXDOMAIN →
  `domain_not_found`; timeout/erro → `dns_error`. (2) HTTP responde? QUALQUER resposta (200/301/
  403/503) = acessível → segue (SSL inválido NÃO aborta: `verify=False`, o check_ssl marca FAIL);
  falha de conexão → `unreachable`. Aborta com `ScanReport.status` != `ok` (score=None, results=[]).
  A API (`/scan/result`, `/scan/summary`) devolve **200** com `{status, error_detail, score:null,
  checks:[]}` (domínio válido, só inacessível — o front mostra o card certo). **Persistência:** só
  cacheia (Redis) scan `ok`; `unreachable` é gravado no Postgres (`scans.status`, score NULL) p/
  analytics de disponibilidade (KL-57); `domain_not_found`/`dns_error` NÃO são salvos.
- **Auditoria dos checks Tipo B (KL-94):** todo check que verifica a AUSÊNCIA de algo ruim usa
  `base.content_guard(resp, NAME, sev)` → **INCONCLUSO** (nunca PASS falso) se o servidor deu **5xx**
  ou o corpo é **vazio/mínimo** (<100 chars); `except` de conexão já retornava INCONCLUSO. Os checks
  multi-sonda (20/dirlist/sensitive/sourcemaps) contam respostas: **zero respostas → INCONCLUSO**
  (um arquivo ausente num site acessível segue PASS legítimo). Checks Tipo A (presença de proteção:
  SPF/HSTS/CSP/DNSSEC/… — ausência = FAIL) NÃO mudam.
- **Runner paralelizado** (`asyncio.gather` + `Semaphore(SCAN_MAX_CONCURRENCY=12)`);
  seguro porque o rate limit de `base.fetch` é **por-domínio** (1 req/s preservado).
- **48 checks passivos** = **15 grátis (ORDER≤15)** + **33 pagos** (OWASP/CWE/LGPD,
  CVE via Retire.js, TLS profundo, DNS, content analysis). Cada check é uma coroutine
  descoberta dinamicamente (ver §6).
- **8 indicadores de privacidade** (KL-44 P5, `scanner/privacy_checks.py`) rodam num
  **único GET próprio** e geram um `privacy_score` **0–8 SEPARADO** do score de segurança
  (nunca se combinam) — diagnóstico técnico, **não** conformidade LGPD (disclaimer
  obrigatório em toda superfície). São indicadores, não `check_*.py` (não entram nos 48).
- **Semáforo:** 🟢 score **≥90 E zero FAIL Alta/Crítica** · 🟡 ≥50 · 🔴 <50.
- Cache por tier no Redis (`scan:free:*` / `scan:full:*`, ambos casam `scan:*`) com
  fallback no banco.

### Workers
- **Discovery** — CT log poller (`ct_poller.py`), ciclo 30 min; enfileira **todo site
  acessível** (scan desacoplado do e-mail, KL-60).
- **Alert** — batch 50, ciclo 30 min, remetente `alerta@klarim.net` (ex-klarimscan.com, 2026-07-20),
  teto pela cota mensal / `ALERT_DAILY_LIMIT`; kill-switch `STOP_ALERTS` + `worker_control`.
- **Rescan** — ciclo 24 h, alvos ≥30 dias.
- **Vigília** (KL-44 P2/P4) — ciclo 6 h, 8 tipos: **core** (SSL, domínio, score,
  e-mail, reputação) + **avançadas P4** (`changes` integridade do site, `phishing`
  typosquat via CT logs) no ciclo 6 h; **`uptime`** roda num **loop curto próprio**
  (5 min, reagenda pelo intervalo do plano: Pro 30 min · Agency 5 min). Enforcement por
  plano; **começa pausada** (dono ativa via MCP). O discovery detecta typosquat sobre
  todo o buffer de CT logs (`is_typosquat`) → grava `typosquat_alerts` (event-driven).
- **Bulletin** (KL-44 P3) — ciclo 1 h, envia às `BULLETIN_HOUR_UTC` (13h) o boletim por
  frequência do plano (free=mensal · pro=semanal · agency=diário útil); plain text via
  `alerta@klarim.net` (proativo), + laudo técnico ao técnico vinculado via `klarim@klarim.net`.
- **Trial** (KL-44 P6) — ciclo 1 h, **age 1x/dia** às `TRIAL_HOUR_UTC` (6h): avisa 7d/1d
  antes e, no vencimento, faz **downgrade silencioso para Free** (desativa vigílias, dados
  preservados) + e-mail. Flag `TRIAL_EXPIRATION_ENABLED`. (Também há expiração *lazy* na
  leitura de `plans.get_subscription`.)
- **Scan worker** — consome a fila Redis, `WORKER_MAX_SCANS_PER_HOUR` (**KL-77: 200 na
  VM**), enriquece perfil + IA inline (~US$0,001/site) e **arquiva o response bruto no GCS**
  (KL-77 Fase 2, ver abaixo). **KL-94 (complemento):** trata o `ScanReport.status` do gate
  (`_persist_scan_report`, testável): `ok` → salva + **zera** `gate_fail_count`; `unreachable` →
  grava `scans.status='unreachable'` (score NULL, analytics) + conta falha; `domain_not_found` →
  conta falha (não salva); `dns_error` → transitório (no-op). **Retry backoff** por falha de gate
  (`targets.gate_fail_count`/`gate_next_retry`): 1ª +7d, 2ª +30d, 3ª **descarta** — MAS só se o alvo
  NUNCA teve score (`last_scan_score IS NULL`); um site que já teve score é **preservado** (nunca
  descartado, `last_scan_score` intacto — a `update_scan_result` só roda no `ok`). O worker **pula**
  o alvo enquanto `gate_next_retry` está no futuro (`gate_retry_pending`). O **alert worker exclui**
  inacessíveis (`gate_fail_count>0` / `last_scan_score IS NULL` no `_ALERT_ELIGIBLE_WHERE`) — a
  vigília (KL-44 P2) cobre uptime. Estimado: 30-50% dos ~3.000 alvos/dia falham o gate (certs CT sem
  site) → ~1.500 scans/dia a menos, fila drena mais rápido, scores mais confiáveis.
- Heartbeat no Redis (TTL 600s) + watchdog `os._exit(1)` + `restart:unless-stopped`.
- **Backfill de enriquecimento (cron root, 2026-07-20)** — o discovery cria ~2.500 alvos/dia e o
  enrich inline do scan worker não acompanha (backlog ~16,7k sem perfil). `scripts/enrich_all.py`
  roda por **cron root na VM: batch 2.000, 6×/dia (a cada 4h — `0,4,8,12,16,20`)** ≈ 12.000/dia,
  guardado por `flock -n /tmp/klarim_enrich.lock` (sem overlap), no container `api`, log em
  `/var/log/klarim_enrich.log`. Custo ~US$12/dia OpenAI enquanto durar o backlog — **monitorar
  CPU/RAM**; sob pressão, baixar o batch p/ 1.500. Reclassificação retroativa de setores em §9 (KL-84).

### Arquivamento de responses brutos (KL-77 Fase 2)
Cada scan comprime (gzip) o **response bruto** já em memória do enrich (headers, html,
dns, ssl, status, tempo — **sem request extra**) e sobe para `gs://klarim-raw/YYYY/MM/DD/
{scan_id}.json.gz` (bucket Nearline, privado). Dado que o Postgres descarta e o KL-75 vai
reprocessar. **Fire-and-forget:** `scanner/gcs_archive.py` (client lazy, upload em thread);
`GCS_ENABLED=false` = bypass; erro é logado e engolido — **o scan nunca trava**. Captura:
`enrich_profile(..., capture_raw=True)` devolve o response ao worker (SSL vem do cache do
`tls_analyzer`); o caminho público passa `capture_raw=False` (nada muda). Contadores no
Redis (`klarim:gcs:*`, TTL 48h) → MCP `get_gcs_archive_stats` / `GET /admin/gcs-archive/stats`.

### Detecção de tech stack (KL-75 Prompt 1)
Do MESMO response bruto (após o enrich, antes do GCS), `scanner/tech_detector.py::
detect_tech_stack` (função pura) extrai tecnografia — parse em memória, **sem request extra**.
`scanner/main.py::persist_tech_detection` grava (resiliente) em `site_tech_stack` (batch,
idempotente), `targets.email_provider`/`related_domains`, `site_status_log`, e `company_name`
só-se-vazio. Público = badges `GET /public/tech-summary/{domain}`; detalhado = admin/MCP. Ver §9 KL-75.

### Access log server-side (KL-92) — fonte de verdade das métricas de visitante
O tracker.js (client-side) infla visitantes ~5x (pre-fetch de e-mail executa JS no browser do
bot). A verdade é do **servidor**, que vê o IP real. `api/access_log_middleware.py` é um
middleware HTTP (OUTERMOST — enxerga até 401) que grava CADA request não-estático na tabela
**`access_log`** com o IP REAL (`CF-Connecting-IP`), país (`CF-IPCountry`), user_id (JWT) e a
classificação bot/humano do **`api/bot_classifier.py`** (função PURA: IP próprio → autenticado →
datacenter → crawler UA → rate >50/h → padrão de pré-fetch). **Fire-and-forget:** captura
síncrona barata → `_spawn(_process_access)` (classifica + contador Redis `access_rate:{ip}` TTL
1h + enfileira) → **buffer + flush em batch** a cada 5s (`log_access_batch`). Erro nunca atrasa/
quebra o response; Redis fora → classificação de rate/pre-fetch pula (fail-open). **Retroatividade:**
uma AÇÃO HUMANA (scan/signup/login/PDF/evento, `HUMAN_ACTIONS`) marca como não-bot todos os
registros daquele IP no dia (`mark_ip_human_today`) — corrige o dev/cliente atrás de datacenter.
**LGPD:** IP retido 90d; depois o loop diário `anonymize_old_access_logs` trunca o último octeto
(`set_masklen(...,24)`). Nos responses da API o IP volta MASCARADO (1 octeto em ip-behavior, 2 em
ip-detail); o completo fica só no banco. Endpoints admin `/admin/analytics/{server-metrics,
ip-behavior,ip-detail}` + MCP `get_server_metrics`/`get_ip_behavior`/`get_ip_detail`. O tracker.js
CONTINUA para eventos de interação (scan_started etc.). Prompt 2: queries de comportamento +
dashboard usando o access_log como fonte primária. Ver §9 KL-92.

### Planos (KL-44 P1) — freemium
`PAYWALL_ENABLED` (default **`false`**): todo scan autorizado vê os **48 checks** com
detalhe; PDF sempre gratuito. Assinatura define o **monitoramento**:
- **Free** — 1 site, monitoramento mensal.
- **Pro** — R$ 19/mês (R$ 99/ano), 5 sites, semanal, vigílias.
- **Agency** — R$ 49/mês, 15 sites, diário, vigílias avançadas.
- **Reverse trial 30 dias** no signup (Pro automático; `?plan=agency` no signup começa
  trial Agency). **Upgrade self-service** via PIX (KL-44 P6): `POST /account/upgrade` →
  cobrança AbacatePay transparente (QR), webhook idempotente ativa o plano; `/account/
  downgrade` imediato. **Trial expira → downgrade silencioso p/ Free** (worker `trial`).

R$ 19 avulso (KL-27) só existe se o site **não** passou nos 48 e quer re-verificar.

### MCP Server
SSE + **OAuth 2.1 + PKCE** (KL-63) + **token estático** (`MCP_API_KEY`) como fallback.
**~49 tools** (leitura + escrita) — wrapper fino sobre a API/store, auth própria
(fail-closed), não passa pelo JWT admin.

### Integrações
Resend (2 domínios), AbacatePay (PIX), OpenAI (GPT-4o mini), APIs públicas de leitura
(crt.sh, HIBP, Google Safe Browsing, IBGE CNAE, BrasilAPI/ReceitaWS, RDAP) — todas
best-effort/fail-open (degradam para INCONCLUSO, nunca derrubam o scan).

**Google Safe Browsing API ativa (KL-59, `check_29` funcional):** `GOOGLE_SAFE_BROWSING_KEY`
configurada no `.env` da VM (2026-07-18) — `check_29_safe_browsing` retorna PASS/FAIL em vez de
INCONCLUSO. A key vive só no `.env` (gitignored), nunca no código. Scans em cache anteriores
seguem INCONCLUSO até o rescan; scans novos já pontuam o check.

---

## 5. Estrutura de diretórios

```
api/          → FastAPI: main.py (endpoints), auth_users.py, plans.py, vigilias.py,
                lead_scoring.py, oauth.py (MCP), health_checks.py, admin_analytics.py,
                access_log_middleware.py + bot_classifier.py (KL-92)
discovery/    → Workers + store.py (TargetStore, todo o schema Postgres):
                worker.py, alert_worker.py, rescan_worker.py, vigilia_worker.py,
                ct_poller.py, classifier.py, contact.py, sector_taxonomy.py, cnae.py
scanner/      → Engine: main.py (worker+CLI), runner.py, scoring.py, profiler.py,
                ai_enrichment.py, enrichment.py, tls_analyzer.py, cve_db.py,
                checks/ (check_*.py descobertos dinamicamente + classifications.py)
reporter/     → PDF WeasyPrint: generator.py, risk_messages.py, templates/
notifier/     → KlarimMailer (email_client.py) + templates/ (table-based)
payments/     → AbacatePay PIX: abacatepay.py, models.py, store.py
mcp_server/   → MCP SSE + OAuth: _base.py, server.py, auth.py, oauth.py, tools/
web/          → Astro 7 (site público + rotas do painel proxiadas)
frontend/     → build Vite (/painel admin) + config Nginx (nginx/*.conf) + assets
scripts/      → seeds, backfills, enrich_all.py, enqueue_unscanned.py
tests/        → pytest (offline por default; rede atrás de KLARIM_ONLINE=1)
claude/reports/ → relatório de cada tarefa (KL-xxx)
docs/         → ARCHITECTURE / API / DEPLOY / SECURITY / HISTORY
```

---

## 6. Convenções de código

- **`async`/`await`** para toda I/O. **Type hints** em assinaturas públicas.
  **Docstrings** no que não for trivial (o que o check verifica e o que é PASS/FAIL).
- **Migrations idempotentes** (`CREATE TABLE IF NOT EXISTS`, `ALTER … ADD COLUMN IF
  NOT EXISTS`) dentro do `ensure_schema` de `discovery/store.py` — **sem Alembic**.
- **Auth:** endpoints admin sob os prefixos protegidos (`/targets`, `/scans`,
  `/alerts`, `/rescans`, `/email`, `/payments`, `/config`, `/leads`, `/admin`…) →
  **JWT admin Bearer** (`typ=admin`). Endpoints de usuário sob **`/account/*`** →
  **JWT usuário no cookie** (`typ=user`). Os dois JWT usam o mesmo `JWT_SECRET` mas o
  `typ` **nunca é ignorado**.
- **Rate limit via Redis** (`_redis_allow`) com fallback in-memory.
- **Config editável:** `admin_settings` (banco) **>** `os.environ` (.env) **>**
  default, via `get_setting(key, default)` — **fail-open** (erro de banco nunca pausa
  worker). Ver KL-44 (§49 do HISTORY).
- **Fire-and-forget** (`_spawn`) para operações não-críticas (ingest, lead, e-mail
  em background) — nunca bloqueiam nem derrubam o chamador.
- **Testes offline** (sem rede/Postgres) com `FakeStore`.

### Como adicionar um check ao scanner
1. Crie `scanner/checks/check_<slug>.py` com as constantes de módulo `ORDER` (int —
   **≤15 é grátis**, >15 é pago), `CHECK_ID` (str), `NAME` (str) e a coroutine
   `async def check(url: str) -> CheckResult`. Descoberta é automática
   (`discover_checks()`) — **não existe lista hardcoded**.
2. Retorne `PASS`/`FAIL`/`INCONCLUSO` (INCONCLUSO é neutro no score; nunca finja PASS).
   Severidade: `CRITICA`/`ALTA`/`MEDIA`/`BAIXA`.
3. Acrescente a entrada em **`scanner/checks/classifications.py`** (OWASP/CWE/LGPD — o
   teste `test_every_check_is_mapped` falha se faltar) e em **`RISK_MESSAGES`**
   (`reporter/risk_messages.py`) + **`ACCESSIBLE`/`TECHNICAL`** (`reporter/generator.py`).
4. **Flush `scan:*` no Redis** após o deploy (novo check muda scores).
- Reutilize `checks/base.fetch` (helper HTTP + rate limiter); nunca reinvente.

### Como rodar
```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m scanner.main https://www.example.com [--json|--pdf]   # scan pela CLI
docker-compose up --build                                        # stack completa
pytest                                                           # offline
KLARIM_ONLINE=1 pytest tests/test_checks.py                      # inclui scan real
```

---

## 7. Estado atual (atualizado em 2026-07-20)

- Alvos: ~25.400 · Scans: ~8.100 · Perfis públicos: ~7.200
- **Backlog drain (2026-07-20, KL-75+KL-84):** ~16,7k alvos sem enriquecimento + 48% em `outro`.
  Enrich acelerado por cron (batch 2.000, 6×/dia — §4). Reclassificação retroativa dos ~2,2k `outro`
  com descrição rodando (`reclassify_sectors.py --scope outro`, ~26% saem de `outro`, preserva
  `manual`/`receita`). Backfill de tech stack do GCS **pendente de grant `objectViewer`** no bucket.
- Contas: 8 (6 orgânicas) · Leads: 39
- Score do próprio `klarim.net`: **100/100**
- Testes: **1503 passed** (backend pytest, KL-92 P4: +16) + **96 node --test** (frontend `test:unit`)
  · MCP tools: **61+** (KL-75: +3 tecnografia · KL-92: +3 access log server-side)
- Workers: **5/5 ativos** (discovery, alert, scan, vigília, rescan)
- Planos: 8 contas Pro trial · Vigílias: 35 (30 ok, 5 error)
- E-mail: alertas proativos migrados p/ `alerta@klarim.net` (2026-07-20; klarimscan.com falhou no spam)
- Scan rate: **200/h** (KL-77 Fase 3) · Responses brutos arquivados no GCS `gs://klarim-raw` (KL-77 Fase 2)
- Tech stack detectado por scan (KL-75 P1): `site_tech_stack` + `site_status_log` + `targets.email_provider`

> **Atualize este bloco a cada tarefa** que mude números relevantes.

---

## 8. Gotchas (evitam retrabalho)

- **CSP estrita do `klarim.net` bloqueia islands Astro** ("Astro is not defined") →
  o `/painel` usa CSP relaxada; ilhas admin são `client:only="react"`.
- **`parseUTC`:** timestamps do Postgres são naive — adicione `Z` antes de `new Date`.
- **SPA fallback do Vite** serve `200` para paths desconhecidos (não é o arquivo real).
- **Docker build na VM `e2-small` (2 vCPU, ~4GB) leva 10–50 min** — lento **≠**
  travado. Confira idade dos containers via SSH (build-then-recreate mantém o site no ar).
- **Recharts só na Overview** (island `client:only`) — não pesa no bundle público.
- **`LeadShared.jsx`:** `CLASS_META`/`ClassBadge` extraídos de `Leads.jsx` p/ evitar
  import circular.
- **Inbox:** corpo de e-mail externo renderiza em `<iframe sandbox="">` + `srcDoc` —
  **NUNCA** `dangerouslySetInnerHTML` (evita stored-XSS roubando o JWT do operador).
- **MCP SSE:** o token é propagado no evento `endpoint` (`&token=`), senão os POSTs do
  `/messages/` chegam sem auth (401).
- **`FakeStore`:** ao adicionar um `store.*` novo num endpoint compartilhado, stub o
  método no `FakeStore` (senão todo teste 500); atualize `test_mcp_server` p/ tool nova.
- **504 no `/scan/summary`:** o scan roda inline; site lento pode passar do
  `proxy_read_timeout` (180s) — o resultado ainda **cacheia**, então a retentativa pega
  o cache quente. Enriquecimento roda em **background** (fora do caminho síncrono).
- **"Escanear" no painel = síncrono** (`POST /targets/{id}/scan?sync=1`): reusa
  `get_or_scan` (escaneia+cacheia+persiste `source='admin'`) e devolve `score`/`semaphore`
  na hora. Sem `sync` o endpoint só **enfileira** (o botão antigo mostrava "enfileirado"
  sem resultado visível — daí a impressão de "não funciona").

---

## 9. Referência rápida de cards

- **KL-44** — Guardião Digital (P1 planos ✅, P2 vigílias ✅, **P3 boletim+técnico+laudo ✅**,
  **P4 vigílias avançadas ✅**, P5–P6 pendentes). P3: bulletin worker (free=mensal/pro=semanal/
  agency=diário, 13h UTC), laudo compartilhável `/laudo/{code}` (público, TTL 30d, sem PII),
  técnico vinculado (`role=technician`, e-mail do dono mascarado), templates plain text,
  Reply-To scan@. P4: uptime (loop 5 min, 3 falhas→alerta, anti-spam 1/h, recovery),
  changes (snapshot leve, alerta em mudança significativa), phishing/typosquat (CT logs +
  `is_typosquat`, `typosquat_alerts`), config `BULLETIN_ENABLED`/`BULLETIN_HOUR_UTC` no painel.
  **P5 ✅**: 8 indicadores técnicos de privacidade (`scanner/privacy_checks.py`, score 0–8
  separado + disclaimer, NUNCA "conformidade"/"certificado"); selo "Monitorado por Klarim"
  (`GET /seal/{domain}` + `web/public/seal/widget.js` sem tracking, só dono verificado);
  benchmark setorial rico (`/benchmark/{sector}`|`/all` com mediana + distribuição anônima,
  cache 24h); `/admin/privacy-stats` + MCP `get_privacy_stats`.
  **P6 ✅** (fecha o KL-44): checkout PIX self-service (`/account/upgrade` transparente +
  webhook idempotente que ativa o plano; `subscription_payments` — separada de `payments`),
  `/account/downgrade`, worker `trial` (avisos 7d/1d + downgrade silencioso p/ Free às
  `TRIAL_HOUR_UTC`), página pública `/planos`, UX de plano no dashboard (`PlanSection`:
  trial/upgrade QR/downgrade/histórico + `?upgrade=`/`?upgraded=1`), signup `?plan=`,
  `/payments/subscription-stats` + MCP `get_subscription_payment_stats`. **NUNCA guarda
  dado de cartão/PIX** — só o id da cobrança
- **KL-51** — Plataforma Astro (fases 1–5 ✅)
- **KL-52** — site_profile visível internamente ✅ (MCP `get_site_profile` + `get_target` já
  anexam o perfil; `GET /targets/{id}` inclui `profile`/`classifications`/`owner`; painel:
  seção "Perfil comercial" no detalhe do alvo (`AlvoDetalhePage`) + botão "Editar perfil"
  (`ProfileEditModal`). `contact_email` NUNCA no response — o perfil vem de `site_profile`)
- **KL-61** — Gestão de Leads / PQL ✅ · **KL-62** — email_log unificado ✅
- **KL-63** — MCP OAuth 2.1 ✅ · **KL-65** — SEO/Schema.org ✅ · **KL-66** — contato nos perfis ✅
- **KL-68** — Reivindicação de site + verificação de propriedade em tiers ✅ (auto por
  e-mail == contact_email; código 6 díg. ao contact_email; domain guard bloqueia
  monitorar domínio público/institucional; `contact_email` nunca exposto — só `email_hint`)
- **KL-69** — Gestão de usuários unificada ✅ (`/painel/usuarios` funde Clientes+Assinantes;
  admin remove site / desativa / reativa conta, com notificação; `is_active` bloqueia login;
  clean-blocked-sites notifica; termos de uso c/ domínios elegíveis; **gestão de plano no
  detalhe do usuário** — dropdown Free/Pro/Agency + estender trial + resetar free, via
  `PATCH /admin/subscriptions/{id}/plan|trial` (`account_id==users.id`; `change_plan` já
  ajusta vigílias e status))
- **KL-67** — Qualidade do profiler ✅ (validadores puros de telefone/DDD, redes sociais,
  endereço e descrição/idioma em `scanner/profiler.py::apply_quality_filters`; flag
  `low_confidence_fields`; edição admin de contatos; `POST /admin/revalidate-profiles`;
  **Reply-To=scan@klarim.net** em TODO e-mail via `_send`/`_send_batch`)
- **KL-71** — Fixes propriedade/técnico/landing ✅ (Tier 1 **auto_domain**: domínio do e-mail
  == domínio do site, exceto `PUBLIC_EMAIL_PROVIDERS`, first-come; convite de técnico
  garante laudo válido — escaneia se preciso — e valida conflito de papel (422 auto-convite/
  dono-como-técnico/já-vinculado); CTA público some com dono verificado; dashboard mostra
  `has_other_owner` + badge de técnico + link "Perfil público" + remover site self-service
  (`DELETE /account/sites/{id}` revoga posse + desativa vigílias); painel Usuários com coluna
  Perfil (owner/technician/both))
- **KL-74** — Arquitetura de conteúdo navegável ✅ (transforma os perfis-ilha em ecossistema
  mobile-first que conduz ao scanner). **5 endpoints públicos** `/public/{sectors,sector/{slug},
  top-fails,related,best,stats}` (só sites `public_visible`; nunca `contact_email`; rate limit
  30/min/IP real, SSR interno isento; cache Redis 1–24h). **4 páginas Astro SSR**: `/setores`
  (índice + ItemList), `/setor/{slug}` (benchmark + ranking paginado + top fails + score-100 +
  Breadcrumb/ItemList), `/melhores` (vitrine score 100 por setor), `/estatisticas` (contadores
  estáticos — CSP proíbe script inline não-hasheado). Navegação contextual no perfil
  (`/site/{domain}`): breadcrumb + `BreadcrumbList`, **posição no ranking** do setor, seção
  "Outros sites do setor" (cross-linking via `/public/related`, SSR). `ScanCTA.astro`
  reutilizável (input+botão empilham no mobile, inline em `sm:`, alturas ≥48px). Rotas na
  allowlist Nginx (`setores|setor|melhores|estatisticas`) + sitemap (`/setor/{slug}` por setor)
  + footer (Setores/Melhores/Estatísticas). **Mobile-first** (68% do tráfego): 375px primeiro,
  toque ≥44px, sem hover-only, body ≥16px.
- **KL-20** — Mensagens de risco dinâmicas por falha e setor ✅ (estende `reporter/risk_messages.py`
  — base de 48 checks já existia — com dimensão **setorial** (`SECTOR_RISK_MESSAGES`/`MACRO_RISK_MESSAGES`/
  `CHECK_SECTOR_RISK`, lookup slug>macro>default), `build_risk_summary`/`build_benchmark_line` (puras;
  benchmark do KL-74 vem do chamador). Integra: e-mail de alerta (riscos setorizados + benchmark +
  **CTA duplo** perfil+`/setor/{slug}`), boletim (linha de negócio na ação prioritária), PDF exec/téc
  (`sector` opcional em `generate_*_pdf`), dashboard (`/account/sites/{id}` → `risk_summary`/`benchmark`
  + seção "Riscos para o seu negócio" no `SiteDetail`). Linguagem de negócio, sem multa, plain text, máx 3)
- **KL-81** — Redesign da landing como buscador ✅ (`index.astro` minimalista: hero
  "**Pesquise qualquer site.** / Descubra em 30 segundos." + input com lupa + botão "Pesquisar →"
  + "Relatório completo. 100% gratuito.", centralizado verticalmente `flex min-h-screen flex-col`
  → hero + footer apenas; removidas Como funciona/checks/benchmark/Para quem. Posicionamento:
  buscador de segurança "pesquise qualquer site", não "seu site é seguro?". Busca segue `GET /scan?url=`)
- **KL-82** — Confiança progressiva (Slice 1 ✅ de 4): scan **result-first** sem gate de e-mail
  (o antigo email+código de 6 díg. matava 97% da conversão). `GET /scan/result` escaneia anônimo e
  devolve o payload **filtrado server-side** por **nível de acesso** — `anonymous` (score+barras por
  categoria sem números+1 risco; benchmark/checks travados) < `unconfirmed` (benchmark+2 riscos+
  nomes dos checks sem evidência+PDF travado) < `confirmed`/`alert_session` (tudo). NUNCA vaza
  evidência aos níveis baixos (corte no backend, não blur). Rate limit anônimo **5/h + 20/dia por
  IP** (conta logada ilimitada); scan ≠ monitoramento (KL-78). Migração `users.email_confirmed`
  (`link`/`hmac`/`code`; sem DEFAULT → backfill idempotente `WHERE IS NULL`). Front: `ScanFlow.jsx`
  result-first + `ScanResultDetail.jsx` (`client:load`, CSP-safe: accordion `<details>`, blur CSS,
  share `<a>`/JS-ilha); fluxo de código KL-25 fica **dormente** (fallback). Linguagem neutra pública
  ("Este site", não "Seu site").
  **Slice 2 ✅** (+ KL-85 P2/P3): signup **sem código** — e-mail+senha → conta na hora
  (`email_confirmed=false`) + e-mail de boas-vindas com **link** (JWT-HMAC 30d, `typ=confirm`,
  idempotente). **Anti pre-fetch (2026-07-21):** o e-mail linka a **página** `/confirmado?token=`
  (não a API); a confirmação é **POST-only** (`POST /account/confirm`, o clique "Confirmar meu
  e-mail" num `<form method=POST>`) → o pre-fetch dos servidores de e-mail (GET) renderiza só o
  botão, **nunca confirma**; o POST confirma + redireciona p/ `/confirmado?status=ok|already|invalid`
  (feedback claro, sem token na URL). `/confirmar?token=` (legado) só redireciona p/ `/confirmado?
  token=` (não chama a API). `GET /account/confirm` (JSON) fica só por compat. `POST /account/resend-
  confirmation` (3/h/conta), banner no dashboard p/ conta não confirmada. Se o e-mail já foi
  verificado no scan (KL-25) nasce confirmada.
  **KL-85:** blocklist de descartáveis (`api/disposable_emails.py`, só no signup) + rate limit
  **3/h & 5/dia por IP** (via `CF-Connecting-IP`). Welcome = transacional `klarim@klarim.net`
  (NÃO o `alerta@` de warmup — regra de isolamento). Cleanup diário no `trial` worker
  (`delete_unconfirmed_inactive_accounts`: não-confirmada +30d, sem site e sem re-login; FK CASCADE).
  **Slice 3 ✅** (fecha o KL-82 — Fluxo 2 do alerta): o CTA do e-mail de alerta vira link HMAC
  `/api/alert-access?token=` (`notifier.email_client.build_alert_access_link`, contrato testado
  com `api.main._verify_alert_access_token` — mesmo segredo/esquema). O clique cria uma **sessão
  temporária** (cookie `klarim_alert`, JWT-HMAC 24h, `typ=alert_session`, **escopada a 1 site**)
  → resultado COMPLETO daquele site sem conta; `/scan/result` valida o escopo (outro domínio →
  cai p/ anonymous, nunca vaza checks). `POST /account/signup-from-alert` cria conta **só com
  senha** (e-mail do cookie, `email_confirmed=true` `source='hmac'`, vincula+auto-verifica Tier 1);
  e-mail já com conta → `{existing_account}`. Tabela `alert_sessions` (funil: created/converted),
  `contact_email` nunca em claro (só hint mascarado). Frontend: `AlertSignup` no `ScanResultDetail`.
- **KL-86** — Redesign do dashboard (6 blocos de valor, zero espaço vazio) ✅. **1 request**
  `GET /account/dashboard-summary` agrega tudo do site **primário** (1º monitorado): saúde
  (score+tendência±2+rank no setor), riscos KL-20 (top 3), checklist priorizado
  (`_build_checklist`: e-mail não confirmado/score caiu/vigília com erro/SSL≤30d/perfil
  incompleto/corrigir top-risco/compartilhar; "Tudo em dia 👏" quando sem urgência),
  evolução (score_history dos scans → `ScoreChart` SVG), 6 categorias (`_dashboard_categories`
  reusa `_build_categories`), plano + perfil. Reusa build_risk_summary/sector_benchmark/
  get_sector_position — **nenhuma feature nova, só exposição**. `contact_email` nunca no payload.
  Frontend `Dashboard.jsx` reescrito: grid 2/3+1/3 no desktop com **placement explícito**
  (`lg:col-start`/`lg:row-start`) → mobile empilha na ordem saúde→checklist→riscos→categorias→
  evolução→plano (checklist sobe). Bloco 6 = `PlanSection` reusado. Onboarding do perfil
  (`PUT /account/profile-confirm`, dono edita company_name/phone → `edited_by_admin`). Linguagem
  "Pesquisar" (não "Verificar"), "Olá, {empresa}". Sem site → buscador + checklist reduzido.
- **KL-89** — Fix de conversão (Prompt 1 de 2 — layout, primeira tela, linguagem) ✅. Tudo
  frontend; reaproveita os 4 níveis do KL-82 (backend inalterado). **(1) Container expandido**:
  `web/src/lib/layout.js` centraliza a largura das páginas públicas (fim dos `max-w` ad-hoc por
  página). `PAGE_CONTAINER` (`max-w-2xl md:max-w-5xl lg:max-w-7xl mx-auto px-4/6/8`) em
  scan/perfil/setores/setor/melhores/estatisticas/planos; `FORM_CONTAINER` (max-w-md) em
  cadastrar/entrar/recuperar/contato; `PROSE_CONTAINER` (max-w-3xl, via `Page.astro`) em
  termos/privacidade/sobre. `index` (hero KL-81) e `confirmar` seguem centralizados estreitos
  **de propósito**. **(2) Desktop == mobile**: a "tabela de visibilidade" virou flags puras em
  **`web/src/lib/scanView.js`** (`viewFlags`) — derivam SÓ do nível, NUNCA do dispositivo; acabou
  o "desktop mostra tudo / mobile esconde". **Tabela de visibilidade FINAL (correção urgente —
  mostrar VALOR antes de pedir conta):** só **LGPD** tem cadeado (e só p/ quem não é conta
  confirmada). Score/semáforo, compartilhar+PDF, **benchmark**, **TODOS os riscos** (linguagem de
  negócio = o que converte), barras de categoria e **checks detalhados** são abertos em TODO nível;
  a **evidência técnica** dos checks só no acesso completo (`confirmed`/`alert_session`); **LGPD só
  na conta `confirmed`** (anônimo, não-confirmado E o visitante do link do alerta veem só o título
  🔒). O corte é server-side (`api/main.py::_filter_scan_result`): quem não pode ver
  evidência/LGPD nunca recebe o dado. Segurança: nome+PASS/FAIL de check é padrão de scanner
  passivo público (SSL Labs/Observatory) e coerente com "pesquise qualquer site" (KL-81); só a
  evidência exploit-útil e a LGPD ficam gated. **(3) Primeira tela reorganizada** (`ScanResultDetail.jsx`): score+semáforo → frase
  contextual → **compartilhar + PDF na MESMA linha** (WhatsApp/LinkedIn/Copiar/📄PDF) → **CTA de
  conta acima do fold** → riscos → benchmark → barras+checks → (abaixo) LGPD. Layout 2 colunas no `lg`
  (relatório 2/3 + CTA `sticky` 1/3) que empilha no mobile na ordem acima (mesmo conteúdo). O CTA
  **some** para quem já tem conta (`unconfirmed`→confirme e-mail; `confirmed`→"+monitorar"). PDF é
  público (paywall off) → `reportUrls` monta a URL no front, disponível em TODO nível.
  **(4) Linguagem contextual por ORIGEM** (`scoreHeadline`/`ctaCopy`/`shareLabel`): alerta
  (`access_level=alert_session`) → "**Seu** site" + CTA **só senha** (e-mail do cookie HMAC,
  mostrado **mascarado** `j***o@x.com` via `maskEmail`, real nunca no HTML); orgânico → "**Este**
  site. E o seu?" + e-mail+senha (signup inline `/api/account/signup`). +26 testes `node --test`
  (`scanView.test.js` + `layout.test.js`), ligados no `npm run test:unit` (CI).
  **Correções pós-entrega ✅:** (1) **riscos ANTES de detalhes** no resultado (linguagem de negócio
  primeiro); (2) **LGPD travado em desktop E mobile** p/ anonymous/unconfirmed (`showPrivacy=full`);
  (3) **botão PDF com destaque** brand (`bg-brand-500`, `text-[var(--accent-text)]` p/ contraste
  light/dark); (4) e-mail HMAC mascarado + só-senha idêntico em mobile e desktop (as flags derivam
  só do nível, nunca do device); (5) **benchmark PÚBLICO** — visível sem cadeado em TODO nível
  (`showBenchmark=true` + 1 linha no `_filter_scan_result` que inclui o agregado nacional no payload
  anônimo; não é PII); (6) **scanner com progresso real por categoria** (`SCAN_CATEGORIES` +
  `getCategoryStatus` puros: as 6 camadas avançam ○→⏳→✅ pelo % global, com beat de 100% antes do
  resultado). +6 testes.
  **Correção urgente de conversão ✅** (as correções acima tinham travado demais o resultado):
  agora a regra é **mostrar valor antes de pedir conta**. `_filter_scan_result` reescrito — TODOS
  os riscos + categorias com contagem + checks por nome/status vão para **todos** os níveis;
  evidência técnica só no acesso completo; **LGPD só na conta `confirmed`** (`alert_session` = link
  do email = 🔒). `viewFlags`: `showAllRisks=true` p/ todos, `showEvidence=full`, `showPrivacy=level
  ==='confirmed'`, removido `categoriesMode`. `ScanResultDetail`: `RisksSection` sem gate,
  `CategoriesSection` unificada (barras de proporção + accordion; evidência só se `showEvidence`).
  Logado (`SiteDetail` / `/account/sites/{id}`) já entrega o relatório completo (48 checks c/
  evidência, PDF exec+téc, benchmark setorial+ranking, evolução) — sem mudança. `/site/{domain}`
  (KL-74) intacto (só o container mudou).
  **P0 — resultado instantâneo ✅** (o link do alerta re-escaneava, 60s+ de espera → desistência):
  `GET /scan/result` agora serve um scan **< 24h** já existente (cache Redis OU banco) na hora, SEM
  re-escanear — o alerta é enviado DEPOIS do scan, o dado já existe. `get_recent_only(url, full,
  max_age_minutes=_SCAN_RESULT_MAX_AGE_MIN=1440)` roda antes do scan; `refresh=1` (botão "Atualizar
  análise") força scan novo (`get_or_scan`/`_safe_scan` ganham `force`). Vale p/ QUALQUER domínio
  com scan recente (não só alerta). **⚠️ Gotcha crítico (o 1º fix falhou por isto):** o scan POR
  TRÁS DO ALERTA é o do **worker de discovery**, que grava só o tier **FREE (15 checks)**
  (`scanner/main.py`: `full = source not in ("discovery","public")`). Exigir `full=True` no lookup
  fazia `_tier_ok(15>=48)` reprovar → **re-escaneava sempre**. Fix: `/scan/result` tenta o FULL e,
  se não houver, **cai no lookup FREE** (`get_recent_only(full=False)`) e serve o scan de 15 mesmo
  assim — instantâneo > completo; o "Atualizar" pega os 48. (URL casa: worker grava `https://{domain}`,
  alerta manda `https://{domain}`.) **2º gotcha:** servir o scan free pelo builder padrava os 33
  pagos como INCONCLUSO (tela parecia quebrada: "DNS 0/7"). `_full_scan_result` agora inclui **só os
  checks que rodaram** (15 free / 48 full) + `partial=True` no free → front mostra "Análise rápida ·
  Ver análise completa (48) →". O **rate limit anônimo (5/h + 20/dia) só conta scans REAIS** —
  servir do cache é grátis. Payload ganha `from_cache`; front mostra "Última análise: {data} ·
  Atualizar análise →" (ação secundária no `ScoreHero`). **P1 — scanner não trava em 94% ✅:**
  `ProgressStep` já mostra as 6 categorias avançando (○→⏳→✅, KL-89 fix 6); passados ~25s aparece
  um aviso de que as últimas verificações consultam serviços externos (reputação/Safe Browsing) e
  podem demorar — some a impressão de "travou". (Resultado parcial via SSE fica p/ o KL-90.)
- **KL-83** — Redesign do Analytics admin (Prompt 1 de 2) ✅. Módulo dedicado
  **`api/admin_analytics.py`** (não toca o analytics antigo do KL-21): **8 endpoints**
  `/admin/analytics/{metrics,trend,funnel,events,sessions,pages,journeys,funnel-by-sector}`,
  admin-only (prefixo `/admin` → middleware JWT), período `today/7d/30d/90d/custom`
  (≤90d, sem futuro), rate limit 30/min/IP, cache Redis 5 min (events/sessions não cacheiam).
  **Arquitetura testável:** agregações BRUTAS (SQL) em `discovery/store.py` (`aa_*`,
  parametrizadas); **derivação PURA** (%, sparkline, conversão inter-etapa, normalização de
  jornada, bounce/next_page) no módulo → 34 testes unitários (validação de período, cálculos,
  shape, paginação, cache; SQL validado na VM). 3 índices novos em `site_events`. Front:
  `AdminAnalytics.jsx` (abas #overview/#events completas + #pages/#journeys "Em breve"):
  6 cards KPI+sparkline (Recharts), gráfico de tendência, funil por campanha com gargalo;
  stream de eventos com filtros combináveis + contadores + toggle "por sessão" + export CSV.
  2 MCP tools (`get_analytics_metrics` sem sparkline, `get_analytics_funnel`).
  **Prompt 2 ✅** (fecha o KL-83): abas **Páginas** (tabela ordenável 7-col, busca debounce,
  agrupar-por-tipo colapsável, Δ colorido, click→`#events?path=`) e **Jornadas** (top-10
  caminhos com breadcrumbs coloridos por tipo de passo, funil por setor ordenável, drill-down de
  sessões com "ver todas →" `#events?group=session`). Componentes extraídos
  (`analytics/{SessionCard,SortableTable,PaginationBar}.jsx`) + lógica pura
  (`lib/admin/analyticsUtils.js`: sort/paginate/journeyStepKind/cores/parse-hash) com **15 testes
  `node --test`** (sem deps novas; `npm run test:unit` no CI antes do build). Navegação cruzada
  entre abas via hash. Nenhum "Em breve" restante.
- **KL-85** — Lead scoring de qualidade de alerta (Parte 1 ✅; Partes 2/3 já no KL-82 S2).
  `discovery/alert_scoring.py::calculate_alert_score(target, email, domain_bounced)` — função
  **pura** (testável) → `{score, signals}`. Sinais: +30 e-mail no domínio · +10 corporativo ·
  +20/+10/+5 por faixa de score (50-85/40-49/>85) · +15 setor de alto clique (vazio por ora) ·
  `MISMATCH_FREE_PENALTY` free de terceiro (**0 desde 2026-07-20**, era -20 — PMEs BR usam gmail como
  e-mail comercial; o -20 barrava leads legítimos) · -15 prefixo role-based · -10 descartado/score<40
  · -40 domínio com bounce **só p/ domínio próprio/corporativo** (2026-07-20: provedores genéricos
  gmail/outlook/… NÃO são penalizados por bounce — um bounce em joao@gmail.com não diz nada sobre
  maria@gmail.com; `_domain_bounced` curto-circuita free). Coluna `targets.alert_quality_score`
  (gravada para TODOS os avaliados, mesmo filtrados;
  NUNCA impede scan). Alert worker: `_apply_alert_scoring` grava o score + filtra abaixo do
  threshold (`ALERT_SCORE_THRESHOLD`, default 20, editável no painel) — **fail-safe** (bug de
  scoring mantém o alvo); bounce por domínio com cache Redis 24h; stats `skipped_low_quality`/
  `avg_alert_score` (no `get_system_status`). Script `scripts/backfill_alert_scores.py` (batch
  500 + histograma). Endpoint `GET /admin/analytics/alert-quality` + MCP `get_lead_scoring_stats`.
  Admin: coluna "Alert" na lista de alvos (badge colorido) + breakdown dos sinais no detalhe.
  24 testes backend + testes de worker/endpoint.
- **KL-84** — Taxonomia ABERTA de setores ✅ (troca os 48 setores fixos do KL-54 por taxonomia
  dinâmica: a IA propõe setores novos, o admin cura, o 'outro' cai). Tabela **`sectors`**
  (slug/label/macro/status ∈ official·proposed·approved·rejected·merged/merged_into/site_count),
  seed idempotente dos 48 oficiais no `ensure_schema` (`store.seed_sectors`, site_count via
  GROUP BY). **`discovery/sector_synonyms.py`** resolve sinônimos ANTES da tabela (advocacia→
  juridico, pousada→hotel…). **`discovery/sector_classification.py::process_classification`**
  (pura, testável): resolve sinônimo → tabela (segue `merged_into`, rejeitado→'outro') → cria
  proposta se `is_new_sector` → fallback 'outro'; slug sanitizado ([a-z0-9_], máx 50), macro
  validada. Prompt da IA (`ai_enrichment.build_system_prompt(known)`, lista dinâmica cache 1h)
  ganha `is_new_sector`/`sector_label`/`macro_sector_suggestion`; setor novo **preserva** o slug
  (não vira 'outro'). **5 endpoints admin** `/admin/sectors[/{slug}/{examples,approve,merge,
  reject}]` (`api/admin_sectors.py`, admin-only): merge/reject reclassificam sites **preservando
  `manual`/`receita`**. Público: `/public/sectors` e `/public/sector/{slug}` filtram por status
  (só official/approved; proposto/rejeitado/merged → 404). Script **`scripts/reclassify_sectors.py`**
  (`--scope outro|all --dry-run --limit --batch`, ≤500 IA/h, usa a descrição JÁ extraída — sem
  re-scan, sem tocar score/checks; roda **manual na VM**). Página admin `/painel/setores`
  (`SetoresPage.jsx`: emergentes com aprovar/merge/rejeitar + taxonomia viva). 2 MCP tools
  (`get_sector_stats`, `classify_target_sector`). 37 testes offline.
- **KL-77** — Escala da VM + arquivamento de scans. **Fase 1 ✅** (VM e2-small→e2-standard-4,
  IP estático `34.135.194.208`, CI por instance-name). **Fase 2 ✅** — arquiva o response
  bruto de cada scan no GCS (`gs://klarim-raw/YYYY/MM/DD/{scan_id}.json.gz`, Nearline privado)
  para o KL-75 reprocessar sem re-escanear: `scanner/gcs_archive.py` (puro + testável, client
  lazy, upload em thread, `GCS_ENABLED=false`=bypass, fire-and-forget); captura sem request
  extra via `enrich_profile(capture_raw=True)` (headers/html/dns já buscados + SSL do cache do
  `tls_analyzer`); SA com `objectCreator` apenas + ADC preferível; contadores Redis
  (`klarim:gcs:*`, TTL 48h) → MCP `get_gcs_archive_stats` + `GET /admin/gcs-archive/stats` +
  bloco `gcs_archive` no status. **Fase 3 ✅** — scan rate 50→**200/h** (`WORKER_MAX_SCANS_PER_HOUR`,
  editável ao vivo); rate limit por-domínio 1 req/s inalterado. 18 testes offline.
- **KL-75** — Enriquecimento tecnográfico (**Prompt 1 ✅ + Prompt 2 ✅** — completo).
  Extrai inteligência tecnográfica do MESMO response bruto que o KL-77 captura —
  parse em memória, **sem request extra** (< 500ms/scan). **`scanner/tech_detector.py::
  detect_tech_stack(headers, html, dns, ssl)`** — função PURA → `{technologies, email_provider,
  dns_provider, related_domains, site_status, verified_platforms, company_name, schema_types}`.
  6 grupos: headers/cookies (servidor/backend/CDN/plataforma), ~50 scripts (analytics/marketing/
  pagamento/chat/e-commerce/CMS/segurança/social/infra), meta tags (OG/verificações/generator/RSS),
  DNS (email_provider via MX · dns_provider via NS · plataformas via TXT), SSL (SAN→related_domains,
  issuer→CA, organização OV/EV→company_name), status (`ativo`/`parked`/`abandonado`/`fora_do_ar`/
  `bloqueado`/`dominio_inativo` via `classify_site_status`). Gravação em `scanner/main.py::
  persist_tech_detection` (**resiliente** — nunca trava o scan; após enrich, antes do GCS): batch
  INSERT em **`site_tech_stack`** (idempotente, UNIQUE `(target_id,scan_id,name)` + ON CONFLICT),
  `targets.email_provider`/`related_domains`, `site_status_log`, e `company_name` **só se vazio**
  (nunca sobrescreve regex/IA/manual). `enrich_profile` ganhou 1 lookup DNS TXT (só `capture_raw`);
  `tls_analyzer` extrai `subject_o` (organização). Público = badges booleanos `GET /public/tech-
  summary/{domain}` (30/min/IP, respeita `public_visible`); detalhado só admin (`GET /targets/{id}/
  tech-stack`) + 3 MCP tools (`get_tech_adoption`/`get_site_tech_stack`/`get_site_status_history`).
  Backfill `scripts/backfill_tech_stack.py` reprocessa os responses do GCS (≥2026-07-19) sem re-scan.
  **Prompt 2 ✅:** (Grupo 7) `site_type` — classify_site_type DENTRO de detect_tech_stack (mesmo HTML,
  sem 2ª passagem): institucional/ecommerce/saas/portal/blog/parked/abandonado, por sinais de
  login/OAuth/pricing/API-docs/registro/footer (OAuth reusa as technologies) — prioridade parked>
  abandonado>saas>ecommerce>portal>blog>institucional; gravado em `targets.site_type` (persist
  reclassifica com o status autoritativo). (Grupo 8) subdomínios via CT logs: o discovery agora
  **registra** subdomínio de domínio raiz JÁ na base em vez de descartar (`site_subdomains` +
  `targets.subdomain_count`) — `discovery/subdomains.py` (classify_subdomain puro, `DomainCache` em
  memória recarregado por ciclo ~1.8MB, `register_subdomain`/`process_subdomains` fail-safe, teto
  `SUBDOMAIN_MAX_PER_CYCLE=2000`); o poller (`ct_poller.subdomain_of`) captura subdomínios num buffer
  separado (`flush_subdomains`), o worker drena e registra no fim do ciclo. **Subdomínios NUNCA são
  escaneados** (ético). Público ganha `site_type`+`subdomain_count`; admin/MCP ganham a lista
  (`get_site_subdomains`) — CT log é público mas a lista é premium. 100 testes offline (51+49).
  **Dados p/ KL-57:** market share de tech/site_type por setor, correlação stack×score, sites
  parked/abandonados, staging exposto, SaaS com score baixo (risco LGPD).
- **KL-64** — Analytics correto (filtro de bots + fix do funil de e-mails + export CSV) ✅.
  **Causa raiz comum:** pre-fetch de servidores de e-mail (Gmail/Outlook, Chrome real, a Cloudflare
  não marca como bot) crawleando os links dos alertas e os perfis inflava tudo. **(1) E-mails
  profile_view (~7.000/dia!):** o `/site/[domain].astro` disparava `POST /notify/profile-view` NO SSR
  a cada render → todo bot que abria um perfil gerava e-mail ao dono (a query do funil já filtrava por
  período — o VOLUME é que era bot). Fix: o gatilho saiu do SSR → nasce do **evento `profile_view`
  HUMANO-verificado** (`track.js` → `/api/events` → `_profile_view_notify`). Bots não interagem → não
  geram e-mail. **(2) Filtro is_human:** `track.js` reescrito — NÃO dispara `page_view` no load;
  espera **interação real** (scroll/click/mousemove/touchstart/keydown), aí dispara com
  `verified_human:true` (eventos de AÇÃO disparam na hora com o flag). **2026-07-20: removido o
  fallback de 5s** (`?v=65`) — pre-fetches de e-mail ficam 5+s renderizando e passavam (inflavam
  visitantes ~5x: 603 interno vs 101 Cloudflare); agora SÓ interação conta, sem exceção. Coluna `site_events.is_human`
  (NULL=histórico preservado) + índice parcial; `verified_human`→`log_event(is_human)`; filtro
  **`(is_human=TRUE OR is_human IS NULL)` DEFAULT em TODAS as queries de site_events** dos 8 endpoints
  (`aa_*`) + 2 MCP tools; `include_bots=true` desliga (debug); toggle no admin. `users`/`alert_log`/
  `email_log` NÃO levam o filtro. **(3) Export CSV** `/admin/analytics/events/export` — server-side,
  `StreamingResponse`, cursor `fetchmany(1000)`, mesmos filtros + is_human, teto **10k** (+`X-Truncated`
  + linha de aviso), anti CSV-injection, admin-only; front usa `adminDownload` (Bearer+blob). 26 testes
  (19 backend + 7 tracker via `vm`). **Gotcha:** a data de análise do funil já era correta — o card
  supunha bug de período; o real era o volume de e-mail bot.
- **KL-92** — Tracking server-side por IP (Prompt 1 ✅ + 2 ✅ + 3 ✅ + 4 ✅). A defesa client-side do KL-64 depende
  de código que roda no browser do bot — insuficiente. A fonte de verdade das métricas de visitante
  passa a ser o **servidor**. Tabela **`access_log`** (IP INET, país, endpoint, método, status,
  domain_queried, user_id, UA, referrer, response_time, is_bot/bot_reason) + 6 índices, no
  `ensure_schema`. **`api/access_log_middleware.py`** (middleware HTTP OUTERMOST, registrado após o
  auth → enxerga 401): ignora assets (`should_log`), extrai IP real (`CF-Connecting-IP`)/país
  (`CF-IPCountry`)/user_id (JWT)/domínio (`/site/{d}`, `/scan?url=`, ou `request.state.domain_queried`);
  **fire-and-forget** — captura barata → `_spawn(_process_access)` (classifica + INCR Redis
  `access_rate:{ip}` TTL 1h + enfileira) → **buffer + flush batch 5s** (`log_access_batch`). Erro
  jamais atrasa/quebra o response (tudo em try/except fora do caminho síncrono); Redis fora → rate/
  pré-fetch pulam (fail-open). **`api/bot_classifier.py`** (PURO): `classify_bot` na ordem IP próprio
  (34.135.194.208 nunca é bot) → **usuário autenticado** (logou = humano) → **datacenter** (~30 CIDRs
  AWS/GCP/Azure/DO/Hetzner, sem lookup) → **crawler UA** → **rate >50/h** sem conta → **padrão de
  pré-fetch** (US + `/site/*` sem navegação). **Retroatividade:** uma `HUMAN_ACTION` (scan/signup/
  login/PDF/evento) chama `mark_ip_human_today` → marca não-bot todos os registros do IP no dia
  (corrige dev/cliente atrás de nuvem). **LGPD:** IP retido 90d, depois `anonymize_old_access_logs`
  (loop diário) trunca o último octeto; nos responses o IP volta **mascarado** (1 octeto ip-behavior,
  2 ip-detail), completo só no banco. 3 endpoints admin `/admin/analytics/{server-metrics,ip-behavior,
  ip-detail}` (agregações `al_*` no store, derivação pura no módulo, cache 5min, rate 30/min) + 3 MCP
  (`get_server_metrics`/`get_ip_behavior`/`get_ip_detail`). O tracker.js **continua** para eventos de
  interação. **Gotcha:** o Nginx faz `rewrite ^/api/(.*)$ /$1` → o middleware vê paths SEM `/api`
  (`/scan/result`, `/events`); `HUMAN_ACTIONS` e a extração de domínio usam os paths já sem prefixo.
  **Prompt 2 ✅** (comportamento + migração do dashboard): 6 store methods novos — `al_server_funnel`
  (funil server-side visitante→perfil→scan→conta→PDF), `al_top_domains`, `al_daily_series` (tendência),
  `al_hourly_heatmap` (7×24), `al_pre_signup_journeys` + `al_retention` (D1/D3/D7). ⚠️ **Jornada/retenção
  são chaveadas por IP, NÃO por user_id:** no POST /signup a conta ainda não tem cookie → `user_id` é
  NULL; o user_id é recolhido das requests PÓS-signup. `server-metrics` ganhou `server_funnel`+
  `top_domains`+`daily_series`+`hourly_heatmap`; `ip-behavior` ganhou `pre_signup_journey`+
  `typical_journey`+`post_signup_retention` (cache 10min — self-JOIN é mais pesado). Derivações PURAS no
  módulo (`assemble_server_funnel`/`_daily_series`/`_retention`/`_pre_signup_journeys`/`_hourly_heatmap`).
  **Dashboard** (`web/src/components/admin/AdminAnalytics.jsx`): a aba **Visão geral** usa `server-metrics`
  como **fonte primária** dos KPIs (Visitantes BR/Scans/Contas/Bots filtrados/Conversão via IP real, não
  o tracker inflado; Clique-em-alertas fica do tracker), com **fontes independentes** (server-metrics +
  metrics + funnel em `useAsync` separados — uma falhar não zera a outra), **tendência** do `daily_series`,
  **toggle de funil email/server** (estado no hash `#overview?funnel=server`) e **badge de fonte**
  `📡 server`/`📱 tracker` por card. Nova aba **Comportamento**: top domínios, visitantes multi-site,
  jornada pré-signup (típica + exemplos), retenção D1/D3/D7 e mapa de calor 7×24. Lógica pura em
  `web/src/lib/admin/analyticsUtils.js` (`dailySeriesToTrend`/`serverFunnelStages`/`retentionBars`/
  `heatColor`/`DATA_SOURCE`). **Testes:** +22 offline (11 backend derivações/endpoints + 11 `node --test`).
  `get_server_metrics` MCP omite `hourly_distribution`/`daily_series`/`hourly_heatmap`; `get_ip_behavior`
  omite a lista detalhada de jornadas (economia de tokens). access_log é a **fonte primária**;
  site_events/tracker.js segue como **complemento** das interações frontend (as duas coexistem).
  **Prompt 3 ✅** (fix bloqueador + cobertura completa): **P0** — `al_hourly_heatmap` usava `hour` (palavra-chave
  do Postgres) como alias sem aspas → **syntax error → 500 no server-metrics** (5/6 cards quebrados); fix
  `AS hr` + **GROUP BY POSICIONAL** (`1, 2`). **P1 (gap de cobertura)** — o middleware FastAPI só vê o tráfego
  da API (~12%); as páginas Astro (landing, `/scan`, `/site/*`, `/setor/*`) passam pelo Nginx **direto** ao
  container Astro sem tocar no FastAPI → visitantes subcontados (~12 vs ~100 reais). Solução **hybrid** (o
  Nginx vê 100%): **`api/nginx_log_parser.py`** lê incrementalmente o access_log do Nginx e insere na MESMA
  tabela `access_log`. O middleware **continua** cobrindo `/api`+`/mcp` (com `user_id` + retroatividade); o
  parser cobre **só** páginas não-`/api`/`/mcp` → conjuntos **disjuntos, zero duplicata**. Coluna
  `access_log.source` (`middleware`|`nginx`). Nginx ganhou `log_format klarim` +
  `access_log /var/log/klarim/access.log` (contexto http via `frontend/nginx/log_format.conf` → conf.d; os
  **server blocks ficam intactos** → CI `nginx -t` segue verde; o stdout p/ docker logs continua). Volume
  `klarim-nginx-logs` compartilha o log web(rw)→api(rw). Parser: regex do `log_format`, **pula assets +
  `/api` + `/mcp`**, extrai domínio (reusa `extract_domain`), classifica com **`classify_bot_simple`**
  (sem rate/endpoint: IP próprio→datacenter→crawler→**US=`prefetch_likely`**; a retroatividade do middleware
  corrige), `source='nginx'`. Leitura **incremental** (offset+inode p/ rotação); ao passar de 50MB **trunca**
  (seguro: Nginx abre logs em `O_APPEND`). Loop 30s no lifespan; fail-safe. **⚠️ Não desliguei o middleware**
  (o card sugeria) — mantê-lo preserva `user_id`+retroatividade para o funil (`/scan/result`,`/account/signup`
  são `/api`); o parser pular `/api` já evita duplicata. **+27 testes** (parse_line puro, classify_simple,
  parser incremental/rotação/truncação, guardas do fix P0). SQL validado contra Postgres 16 real + `nginx -t`
  local (HTTP+HTTPS) + contrato log_format↔regex validado end-to-end.
  **Prompt 4 ✅** (fecha o KL-92 — 5 pendências): (1) **Cloudflare Web Analytics → GA4** (o
  `beacon.min.js` era o único script externo sem SRI → travava o score 100): removido do
  `Base.astro` + CSP; GA4 `G-7WPZN66JTB` no `<head>` (loader `googletagmanager.com` + init inline
  hasheado); check 13 (SRI) com **allowlist de CDN dinâmico** → klarim.net volta a 100. (2)
  **Pre-fetch de e-mail** no `bot_classifier`: `_EMAIL_PREFETCH_CIDRS` (66.102/66.249/40.9x/104.47
  Gmail/Outlook/EOP) + regra **>20 domínios distintos/h** (set Redis `access_domains:{ip}`) →
  `email_prefetch` (antes de datacenter; em `classify_bot` e `classify_bot_simple`). (3) **Parser
  Nginx** já entregue no Prompt 3 (40k linhas capturadas em prod; visitors_br 26→56, pega `/`,
  `/site/*`, `/setor/*`) — mantido o hybrid (não desliguei o middleware: sem duplicata + preserva
  user_id/retroatividade). (4) **LGPD IPv6**: `anonymize_old_access_logs` trunca IPv4→/24 **e
  IPv6→/48** (>90d). (5) **Tendência com zeros** já entregue no Prompt 2 (`assemble_daily_series`
  densifica os dias). +16 testes. GA4-hash e IPv6-SQL validados; CSP via `nginx -t` local.
- **KL-93** — Hardening de endpoints públicos expostos sem auth ✅. Varredura de segurança achou o
  **`POST /payment/create` criando cobrança PIX REAL** sem nenhuma proteção. **Fixes:** (P0)
  `/payment/create` agora exige **e-mail** (422), **rate limit 3/h por IP** (429, via `_redis_allow`),
  e **domínio existente na base + com scan** (`_domain_scanned` checa `last_scan_at`/`last_scan_score`
  → 404) — validações rodam ANTES do demo/cobrança. Script `scripts/cleanup_phantom_payments.py`
  (idempotente, apaga por charge_id via `store.delete`) remove as 2 cobranças fantasma do teste.
  (P1) `/notify/profile-view` → rate limit 1/h por (IP,domínio); `/monitoring/offer` → RL 10→3/h + 404
  se o domínio não existe (já tinha authz + score-100); **`/monitoring/sites` → agora exige JWT admin**
  (401; era "público" mas só páginas Vite legadas o usavam — a vitrine migrou p/ Astro/KL-74);
  `/report/{executive,technical}` → rate limit **5/h por IP** compartilhado (`report_dl`, cada chamada
  dispara `_safe_scan` full, caro). **Decisão (mantida KL-89):** `/scan/result` **NÃO** foi alterado —
  não existe param `tier` client-controlável (o nível vem só da sessão via `_access_level`; a filtragem
  `_filter_scan_result` é server-side/autoritativa). Downgrade p/ 15 checks reverteria a correção de
  conversão do KL-89 (mostrar valor antes de pedir conta) — o "bypass" do card não existe. +16 testes
  (com/sem auth, rate limit, domínio inexistente). Política por endpoint em `docs/SECURITY.md`.

Histórico completo (o que/porquê de cada peça) em **`docs/HISTORY.md`** e nos
relatórios em `claude/reports/`.
