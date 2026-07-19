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
- **Responsivo (KL-80, 68% mobile):** alvos de toque **≥44px** (`min-h-[44px]`/`py-3`; links-texto
  pequenos → `inline-flex min-h-[44px] items-center px-1`); **inputs `text-base`** (16px, nunca
  `text-sm` — evita zoom iOS) + `h-12`; botões `w-full sm:w-auto` (empilham no mobile); **nada de
  largura fixa que estoure 375px** (dropdowns `w-full sm:w-64`); grades `grid-cols-1` → `md:`/`lg:`;
  `active:scale-95`/`[0.98]` p/ feedback tátil. Breakpoints Tailwind padrão (sm640/md768/lg1024/xl1280).

### E-mail (isolamento de reputação — nunca misturar)
- **Alertas proativos:** `alerta@klarimscan.com` (domínio isolado, em warmup,
  `ALERT_DAILY_LIMIT=30`).
- **Transacionais:** `seguranca@klarim.net`.
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
- **Alert** — batch 50, ciclo 30 min, remetente `klarimscan.com`, teto pela cota
  mensal / `ALERT_DAILY_LIMIT`; kill-switch `STOP_ALERTS` + `worker_control`.
- **Rescan** — ciclo 24 h, alvos ≥30 dias.
- **Vigília** (KL-44 P2/P4) — ciclo 6 h, 8 tipos: **core** (SSL, domínio, score,
  e-mail, reputação) + **avançadas P4** (`changes` integridade do site, `phishing`
  typosquat via CT logs) no ciclo 6 h; **`uptime`** roda num **loop curto próprio**
  (5 min, reagenda pelo intervalo do plano: Pro 30 min · Agency 5 min). Enforcement por
  plano; **começa pausada** (dono ativa via MCP). O discovery detecta typosquat sobre
  todo o buffer de CT logs (`is_typosquat`) → grava `typosquat_alerts` (event-driven).
- **Bulletin** (KL-44 P3) — ciclo 1 h, envia às `BULLETIN_HOUR_UTC` (13h) o boletim por
  frequência do plano (free=mensal · pro=semanal · agency=diário útil); plain text via
  `klarimscan.com`, + laudo técnico ao técnico vinculado via `seguranca@klarim.net`.
- **Trial** (KL-44 P6) — ciclo 1 h, **age 1x/dia** às `TRIAL_HOUR_UTC` (6h): avisa 7d/1d
  antes e, no vencimento, faz **downgrade silencioso para Free** (desativa vigílias, dados
  preservados) + e-mail. Flag `TRIAL_EXPIRATION_ENABLED`. (Também há expiração *lazy* na
  leitura de `plans.get_subscription`.)
- **Scan worker** — consome a fila Redis, `WORKER_MAX_SCANS_PER_HOUR` (subir p/ 100 na
  VM), enriquece perfil + IA inline (~US$0,001/site).
- Heartbeat no Redis (TTL 600s) + watchdog `os._exit(1)` + `restart:unless-stopped`.

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
                lead_scoring.py, oauth.py (MCP), health_checks.py
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

## 7. Estado atual (atualizado em 2026-07-18)

- Alvos: ~25.400 · Scans: ~8.100 · Perfis públicos: ~7.200
- Contas: 8 (6 orgânicas) · Leads: 39
- Score do próprio `klarim.net`: **100/100**
- Testes: **1035+ passed** · MCP tools: **49+**
- Workers: **5/5 ativos** (discovery, alert, scan, vigília, rescan)
- Planos: 8 contas Pro trial · Vigílias: 35 (30 ok, 5 error)
- E-mail: `klarimscan.com` verificado, warmup ativo

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
  ("Este site", não "Seu site"). **Deferido:** Bloco 2 (signup sem confirmação + `/confirmar` +
  welcome link), Blocos 3+4 (Fluxo 2 do alerta), cleanup cron de contas não confirmadas.
- **KL-64** — Analytics tracker (pendente)

Histórico completo (o que/porquê de cada peça) em **`docs/HISTORY.md`** e nos
relatórios em `claude/reports/`.
