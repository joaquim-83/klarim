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

- **Produção:** https://klarim.net · **Admin:** https://klarim.net/painel (KL-106: `painel.klarim.net`
  agora **redireciona 301** ao domínio principal — servia um build Vite antigo; o admin é Astro em `/painel`)
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
  `frontend/nginx/security_headers.conf`** (hoje: 3 do Astro + 1 anti-FOUC de tema do KL-87; o hash
  do init do GA4/gtag do KL-92 P4 ficou **ocioso** após o KL-135 — inofensivo, não removido). **KL-92
  P4:** o Cloudflare Web Analytics (`static.cloudflareinsights.com/beacon.min.js`) foi **removido** —
  era o único script externo SEM SRI (travava o score 100) — e trocado por **Google Analytics 4**
  (`G-7WPZN66JTB`): loader `www.googletagmanager.com` no `script-src`; `connect-src`/`img-src` liberam
  `*.google-analytics.com`. O check 13 (SRI) ganhou uma **allowlist de CDN dinâmico**
  (`SRI_ALLOWLIST_DOMAINS`: googletagmanager/google-analytics/cloudflareinsights) — esses não contam
  como FAIL (SRI inviável em bundle que o provedor atualiza sem aviso) → `klarim.net` volta a 100.
- **Consentimento de cookies (KL-135, LGPD):** o GA4 é **opt-in** — NUNCA carrega sem consentimento.
  O `Base.astro` NÃO tem mais o GA4 no `<head>` (era incondicional/opt-out); quem injeta o `gtag.js` é
  o **`web/public/cookie-consent.js`** (externo, CSP `'self'`), só se o cookie `klarim_consent` for
  `all`/`analytics`. Banner `CookieBanner.astro` (1ª visita) grava a escolha (`Path=/; SameSite=Lax;
  Max-Age=1a; Secure`). Página `/cookies` + `/privacidade` §5/§8 atualizadas; rodapé tem "Preferências
  de cookies" (`data-cc="reopen"` → `window.klarimReopenConsent`). **Ao adicionar um `.js` público
  novo, some ao allowlist do nginx** (`cookie-consent\.js` já lá) + `?v=N`.
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
- **Mapa de remetentes (após KL-101 — `klarim.net` 100% transacional, zero cold):**
  | Remetente | Domínio | Tipo |
  |---|---|---|
  | `klarim@klarim.net` (`RESEND_FROM`) | klarim.net | Transacional (confirmação, boas-vindas, boletim, vigília, magic link) |
  | `scan@alertas.klarim.net` / `scan@aviso.klarim.net` (`ALERT_SENDER_EMAILS`) | alertas./aviso.klarim.net | Cold alert (rotação KL-91) |
  | `notifica@perfil.klarim.net` (`PROFILE_VIEW_FROM_EMAIL`) | perfil.klarim.net | Aviso "perfil consultado" (KL-101) |
- **Profile_view (KL-101):** o aviso "perfil consultado" era o ÚLTIMO cold saindo por `klarim.net`
  (~15k/sem, via `_proactive_from`) — contaminava o domínio transacional. Agora sai por
  `notifica@perfil.klarim.net` (subdomínio dedicado, `_profile_view_from`; **NÃO rotaciona** com
  os cold alerts — este volume destruiria o warmup deles a 100/dia), **texto puro SEM links**
  (`build_profile_view_text(domain)`), opt-out por resposta (mailto). **Dedup por dono: 1/dia**
  (`notify_owner:{email}` no Redis) + a dedup por domínio/24h já existente + **teto diário de
  warmup** `PROFILE_VIEW_DAILY_LIMIT` (200, editável no painel; contador `profileview:daily:{date}`).
  ⚠️ `perfil.klarim.net` precisa estar **verificado no Resend** antes do deploy (senão os envios
  falham). `bulletin` segue em `_proactive_from` (`alerta@klarim.net`) — a quem tem conta/opt-in.
- **Alertas cold (KL-91 → KL-137 → KL-138):** o alerta a quem NÃO tem conta usa o **módulo cold**
  (`notifier/cold_alert.py` + `alert_worker`): **texto puro (text/plain, NUNCA HTML)** com **UM
  link CURTO** — **KL-138:** `klarim.net/a/{target_id}` (`cold_alert.report_link(target_id)`), que a
  API redireciona 302 p/ `/site/{domain}` e registra o clique server-side (`email_clicks`); substituiu
  o link direto com UTM do KL-137 (que reverteu o 'sem links' do KL-91). 3 variantes informativa/
  setorial/educativa, opt-out **por resposta** ("responda com remover"), e
  **rotação round-robin** entre 2 subdomínios verificados no Resend — `alertas.klarim.net`
  e `aviso.klarim.net` (`ALERT_SENDER_EMAILS`). O `klarim.net` fica **exclusivo do
  transacional** (isolamento de reputação; `load_senders` descarta `klarim.net` cru). Envio
  **individual** (não batch) com **cooldown 30-60s** (`ALERT_SEND_INTERVAL_MIN/MAX`) e
  **limite diário POR remetente** (`ALERT_SENDER_DAILY_LIMIT`, warmup: 100→250→500→750;
  editável no painel). **Circuit breaker por remetente (calibrado 24/07; KL-108 26/07):** **HARD
  bounce** > 5% (`ALERT_SENDER_MAX_BOUNCE_RATE`, default 5, editável no `.env`/painel) com **amostra
  ≥100** (`ALERT_SENDER_BOUNCE_MIN_SAMPLE`, era 20 — pausava remetente em warmup por 3-4 bounces
  aleatórios; ex.: aviso.klarim.net pausado com 34 envios) sobre **janela móvel de 7 dias**
  (`email_health_by_domain(days=7)` — bounces antigos saem do cálculo, o remetente se recupera após
  corrigir a lista) → remetente **pausado** no ciclo (o outro continua). **KL-108 (fix):** só o **hard
  bounce** (permanente) pausa; o **soft bounce** (transitório: caixa cheia, servidor fora,
  `delivery_delayed`) é medido/logado mas NUNCA pausa. Antes `email_health_by_domain` somava hard+soft
  num `bounce_rate` único → em 26/07 os 3 senders passaram de 5% pelo COMBINADO e foram pausados
  simultaneamente (zero cold alerts, backlog 2.683; fix emergencial `ALERT_SENDER_MAX_BOUNCE_RATE=12`
  no `.env`, depois removido). Ex. da distorção: perfil.klarim.net 1,33% hard + 5,61% soft = 6,94%
  combinado (quase pausado injustamente). Agora `email_health_by_domain` devolve `hard_bounced`/
  `soft_bounced` separados; `bounce_rate`=hard-only (é o que `flag_high_bounce` usa) e
  `soft_bounce_rate`=informativo. O safety net GLOBAL do KL-24
  (`_check_bounce_health`, all-time, 8%) usa a query SEPARADA `email_health()` (que já contava só
  `status='bounced'` hard) com `ALERT_BOUNCE_MIN_SAMPLE`=20 (amostra separada) — inalterado.
  Métricas por domínio em `get_email_health.by_domain` (all-time, com hard/soft separados) +
  `email_log.from_domain`/`template_variant`. Motivação: o
  `alerta@klarim.net` (cold + urgência + links) caía no spam. **`send_alert_for_target`
  (disparo manual) usa o mesmo formato** (1º remetente). Os builders antigos com link
  (`build_alert_text`, alert-access HMAC do KL-82 S3) **ficam no código** mas o ciclo
  automático NÃO os usa (revertível).
- ⚠️ **REGRA DE ENVIO ATUAL (KL-145 — Reoon FORA do fluxo de envio):** `is_safe_to_send(email,
  redis, store)` é **3 filtros LOCAIS**: (1) sintaxe válida, (2) domínio tem MX, (3) não está na
  blocklist. **Tudo que passa nos 3 → ENVIA** — o status de verificação Reoon (`unknown`/`catch_all`/
  `safe`/…) e o `email_verified`/`email_verify_status` **NÃO decidem mais** o envio. O Reoon
  classificava ~97% dos servidores BR como `unknown` e a regra binária por-status do KL-137 travava
  o volume em 2-8/ciclo; a **blocklist aprendente** (cada bounce, via webhook Resend, entra na
  `email_blocklist`) faz o trabalho de aprender quem não recebe. **O lead_score NÃO decide envio — só
  ORDENA a fila.** `_verify_and_filter` (alert worker) aplica os 3 filtros; stats do ciclo:
  `eligible/valid_syntax/has_mx/not_blocklisted/blocked_syntax/blocked_mx/blocked_blocklist/errors`.
  Removidos do fluxo de envio: partição sendable/unverified, cap de verificação, chamada à API Reoon,
  `_reoon_balance`, `EMAIL_VERIFY_MAX_PER_CYCLE`/`EMAIL_VERIFY_ENABLED`, e os filtros de
  `email_verify_status`/`email_verify_source` do `_ALERT_ELIGIBLE_WHERE` (KL-128/130). O MX (filtro 2)
  respeita `ALERT_VALIDATE_MX` (cache Redis 24h/domínio em prod; off em dev/testes). **O Reoon
  (`verify_reoon`/`verify_local`/cache) FICA no `email_verifier`** só como enriquecimento em
  background (scripts + saldo no `/system/status`) — NUNCA no alert worker. Toda a regra binária por-
  status (KL-137) e os gates/trust dos KL-122..KL-136 ABAIXO são **históricos** — a regra viva é esta.
- **(HISTÓRICO — KL-137, superado pelo KL-145) Regra binária por status:** `is_safe_to_send` era
  **BINÁRIA** (só `safe`/`valid`/`role` enviavam; catch_all/unknown/inbox_full/block-statuses não).
  Dependia da verificação Reoon Power e travava o volume; o KL-145 a substituiu pelos 3 filtros locais.
- **Verificação de deliverability PRÉ-envio (KL-110 — `notifier/email_verifier.py`):** o circuit
  breaker (KL-108) é REATIVO (pausa DEPOIS do bounce); a verificação é PREVENTIVA — checa se a caixa
  existe ANTES de enviar. **Camada 0 (local, custo zero):** sintaxe (email-validator, fallback regex),
  descartáveis (reusa `api.disposable_emails`), MX (dnspython, cache Redis 24h por domínio) e flag
  role-based (reusa `ROLE_BASED_PREFIXES`). Roda na **extração** (`discovery/contact.py::_is_junk`
  descarta domínio descartável → nunca vira `contact_email`; o MX já era validado no `extract_email`).
  **Camada 1 (API Reoon, `REOON_API_KEY`):** modo `power` verifica INBOX existe + catch-all + disabled
  + inbox_full + spamtrap; semáforo de **5 chamadas simultâneas**; fallback fail-open (API fora →
  `unknown`, nunca bloqueia). O **alert worker** (`_verify_and_filter`, após o lead scoring, antes do
  envio) blocklista+descarta `invalid`/`disabled`/`disposable`/`spamtrap` e aplica `is_safe_to_send`.
  **KL-128 (regra DEFINITIVA): `unknown` NUNCA envia** — o gate de score NÃO filtra `unknown` (no BR =
  servidor sem SMTP-check, bounce ~5-8%); o KL-127 tentou `unknown`→gate e o bounce voltou a **>10%**.
  **`catch_all`/`inbox_full`** seguem o gate (`> ALERT_UNSAFE_SCORE_GATE`, default **20**); `safe`/`valid`/
  `role` sempre enviam. **KL-128 no parse:** `parse_reoon_response` rebaixa **`safe`/`valid` + `is_catch_all`
  → `catch_all`** (num servidor catch-all o "safe" do Reoon não é confiável). **KL-129 (prioriza os NOVOS):**
  o cap de verificação (`EMAIL_VERIFY_MAX_PER_CYCLE`=**200**, era 120) era consumido pelos já-verificados do
  cache (unknown barrado) → 0 vaga p/ os `email_verified=false` → o pipeline girava em falso (0 sent, 0 API).
  Agora `_verify_and_filter` **particiona ANTES**: **sendable** (já-verificado aprovado → envia direto, sem
  re-tocar a API) · **blocked_known** (já-verificado barrado → descarta SEM consumir vaga) · **unverified**
  (`email_verified=false`/status vazio/TTL expirado → **prioridade** no subset `unverified[:cap]` → verifica
  via Power NESTE ciclo; excedente = `deferred`, próximo ciclo). **Domínio confiável (KL-129):** um `unknown`
  fresco cujo domínio de DESTINATÁRIO teve envio 'sent' sem bounce nas últimas 48h (`store.
  trusted_recipient_domains`) é rebaixado a `catch_all` (passa a valer o gate) — recupera volume (mega-hosts
  BR retornam unknown no SMTP-check). Kill-switch `ALERT_TRUST_DOMAIN_DOWNGRADE=false` (lido a cada ciclo). O
  canário ativo (envio 1 + recheck 24h + blocklist por domínio) fica p/ card futuro. **KL-130 — status
  TERMINAIS fora do pool de elegíveis:** a partição só prioriza DENTRO do batch buscado; mas o
  `_ALERT_ELIGIBLE_WHERE` (`get_eligible_targets_for_alert`, ordenado por `last_scan_at ASC`) trazia 173
  `unknown`+`power` velhos que **entupiam o fetch** (200) e starvavam 3.247 e-mails NOVOS (0 API, 0 sent, 10
  alertas/dia). Fix: o WHERE **exclui** `unknown`+`power` (irrecuperável — o Power não confirmou a caixa) e os
  block-statuses (defesa-em-profundidade). ⚠️ **NULL-safe (COALESCE):** `NULL = 'unknown'` vira NULL e o
  `AND NOT(...)` excluiria os não-verificados (status NULL) — o `COALESCE(...,'')` evita (validado no Postgres
  da VM: 3.444→3.272 elegíveis, 0 unknown+power, 3.247 novos preservados). Além disso, um alvo verificado como
  `unknown` via Power é marcado **`sem_contato`** (`_verify_one`, sai do pool de vez, NÃO blocklist) +
  limpeza retroativa `scripts/retire_unknown_power.py` (`store.retire_unknown_power_targets`). Log de partição
  `[alert] KL-130 partição: …`. **Sem verificação → não
  envia** (`fallback` de infra não persiste nem envia). **Modo degradado sem `REOON_API_KEY`** (dev/fallback):
  já-verificados seguem o gate, não verificados passam (MX já validado na extração). **Log estruturado por
  e-mail** (mascarado, LGPD):
  `[alert] c***@x → status=… source=… score=… gate=… → SENT|BLOCKED|SKIPPED_GATE|SKIPPED_UNVERIFIED`
  (`docker logs klarim-discovery-1`). Campo **`targets.email_verify_source`** (`power`/`quick`/`bulk`/
  `local`) registra COMO foi verificado. Cache Redis por **SHA-256** do
  e-mail (60d definitivo / 7d transitório) + cache de domínio catch-all (7d). Campos em `targets`:
  `email_verified`/`email_verify_status`/`email_verified_at`/`email_is_role_based`/`email_verify_source`.
  Lead scoring (KL-85) penaliza `catch_all` -10, `unknown` -5, `role` -15 (sem dobrar o prefixo). Stats:
  `get_email_verification_stats` (MCP) + `GET /system/email-verification-stats` (`by_status` + **`by_source`**
  + saldo Reoon). Limpeza do backlog: `scripts/cleanup_email_backlog.py` (Fase 0 local → `source=local` +
  Fase 1 bulk Reoon → `source=bulk`).
- **`_proactive_from` (`alerta@klarim.net`, `ALERT_FROM_EMAIL`):** após o KL-101, resta só o
  **bulletin** proativo (a quem tem conta/opt-in). O profile_view saiu daqui (→ perfil.klarim.net);
  o cold alert saiu no KL-91 (→ alertas./aviso.). **2026-07-20:** migrado de `klarimscan.com` →
  `klarim.net`. Lido do env a cada envio; troca do `.env` vale ao **recriar o container**.
- **Transacionais:** `klarim@klarim.net` (`RESEND_FROM`). **2026-07-21:** MIGRADO de
  `seguranca@klarim.net` → `klarim@klarim.net` — a palavra "seguranca" é keyword de phishing e,
  com domínio aged, elevava o spam score (a confirmação de conta caía no spam). `_mailer()` lê
  `RESEND_FROM` a cada envio → a troca do `.env` vale ao **recriar o container**. Reply-To
  (`scan@`) e o proativo (`alerta@`) **não mudam**.
- **Proativo respeita a blocklist; transacional pode ignorá-la mas SEMPRE registra**
  (todo e-mail passa por `KlarimMailer._send` → `email_log`).
- **Bounce transitório = `soft_bounced` (fix 24/07):** o webhook `/webhooks/resend` marcava só bounce
  PERMANENTE (`bounced` + descarta alvo + blocklist) e **ignorava** os transitórios (`transient`/`soft`/
  `temporary`/`delivery_delayed`) com um mero `print` → o evento sumia (gap de contadores 78 Resend vs
  25 DB). Agora o transitório é **rastreado como `soft_bounced`** no `email_log` (o operador vê; o
  circuit breaker conta) **sem descartar o alvo nem blocklist**. `email_log.status` é texto livre (sem
  CHECK) → não precisa migração. Ambos os ramos logam se o `email_id` do Resend não casa com
  `email_log.email_id` (diagnostica update silencioso). Os contadores da página Alertas ("Hoje"/etc.)
  agora contam **tentativas** (sent+bounced+soft_bounced+complained, `blocked` não), com breakdown
  `{key}_sent`/`{key}_bounced` (`_email_stats_fn`) — o card mostrava 289 escondendo 22 bounces; agora
  311 com "22 bounced ⚠️" no `StatCard`. Health check do Resend não chama mais `GET /domains` (401 com
  key send-only) — HEAD ao host sem auth (`check_resend`).
- **E-mails proativos (alerta + "perfil consultado") = TEXTO PURO** (`text`, sem
  `html`) — menos cara de marketing, cai menos no spam; CTA → perfil público
  `/site/{domain}` com UTM. Builders em `notifier/email_client.py`
  (`build_alert_text`/`build_profile_view_text`); os templates HTML ficam só como
  referência. Linguagem freemium, sem menção a preço/pagamento/relatório.
- **Cold (KL-102) levam `List-Unsubscribe` = mailto (opt-out por resposta) + https one-click**
  (`/remover?token=`, RFC 8058, `build_cold_unsubscribe_headers`) — os 3 senders cold (alertas./
  aviso./perfil.), NUNCA o transacional. Token HMAC (`generate/verify_unsubscribe_token`, propósito
  `unsubscribe`, SEM expiração, codifica email+domínio+remetente). `POST/GET /remover` marca o alvo
  `unsubscribed` + blocklist + evento `email_log` (`type=unsubscribe`, `from_domain`=sender, target_id
  → setor via join). Rate limit 10/min/IP **só p/ tokens inválidos** (o one-click válido do Gmail vem
  de IP compartilhado — nunca bloquear opt-out legítimo). nginx roteia `/remover` → FastAPI.
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
- **Free** — 1 site, boletim mensal + **as 5 vigílias core ATIVAS** (ssl/domain/score/email/
  reputation — KL-106; uptime é Pro, changes/phishing são Agency).
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

### Desenvolvimento local (KL-90 P0) — testar antes de subir
Stack Docker **isolada da produção** para desenvolver frontend + API localmente (o
sistema nunca rodava local — era deploy direto). **Não faz deploy/push/CI; nenhum
e-mail/pagamento real sai** (`DRY_RUN_EMAIL=true`, Resend/AbacatePay/GCS off). Guia
completo em **`docs/DEV.md`**.
```bash
docker compose -f docker-compose.dev.yml up --build                    # sobe db/redis/api/astro/web
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev   # dados de teste
```
- **Arquivos** (todos gitignored/ignorados p/ prod): `docker-compose.dev.yml` (db :5433,
  redis :6380, api hot-reload `--reload`, astro `npm run dev` :4321, web Nginx :3000 — **sem
  workers**), `.env.dev` (`.env.*` já no `.gitignore`), `frontend/nginx/dev.conf` (HTTP puro,
  sem SSL/CSP/rate limit), `scripts/seed_dev.py`. A produção segue em `docker-compose.yml` +
  `frontend/nginx/{http.conf,https.conf.template}` — os `*dev*` **nunca** vão para a VM.
- **Acesso:** browser http://localhost:3000 (Nginx) · Astro http://localhost:4321 · API
  http://localhost:8000 (`/docs` liga com `KLARIM_DEV_MODE=true`) · Postgres :5433 · Redis :6380.
- **A API cria o schema no boot** (`ensure_schema` no lifespan) — é o único container que "migra".
- **Seed** (idempotente): 3 users (`dono@exemplo.com.br`/`dev123456` = 5 sites Pro trial ·
  `tecnico@agencia.com.br` · `novo@teste.com.br` não-confirmado), 5 sites (score 20–100), 50 scans
  (histórico + 48 checks no mais recente), 10 vigílias, perfis públicos + fillers p/ benchmark.
  Riscos derivam dos checks FAIL (KL-20); `loja-exemplo` (score 42) falha SPF/HSTS/CSP com fix
  por plataforma. `scripts/seed_dev.py` recusa rodar fora de dev (guard `KLARIM_DEV_MODE`/host).

---

## 7. Estado atual (atualizado em 2026-07-20)

- Alvos: ~25.400 · Scans: ~8.100 · Perfis públicos: ~7.200
- **Backlog drain (2026-07-20, KL-75+KL-84):** ~16,7k alvos sem enriquecimento + 48% em `outro`.
  Enrich acelerado por cron (batch 2.000, 6×/dia — §4). Reclassificação retroativa dos ~2,2k `outro`
  com descrição rodando (`reclassify_sectors.py --scope outro`, ~26% saem de `outro`, preserva
  `manual`/`receita`). Backfill de tech stack do GCS **pendente de grant `objectViewer`** no bucket.
- Contas: 8 (6 orgânicas) · Leads: 39
- Score do próprio `klarim.net`: **100/100**
- Testes: **2065 passed** (backend pytest; KL-141 completo — P4 CI/notificação +11) + **142 node --test** (frontend `test:unit`)
- Páginas públicas: `/metodologia` (KL-100) · descadastro `/remover` (KL-102) · landing com social proof ao vivo (KL-103)
  · MCP tools: **61+** (KL-75: +3 tecnografia · KL-92: +3 access log server-side)
- **Níveis de conta (KL-99):** `users.account_level` (1 sem senha · 2 com senha · 3 dono verificado
  por domínio); contas legadas → 2. Conta sem senha: Fluxo C (link do alerta) / Fluxo D (signup-inline)
  / `/cadastrar` só e-mail. **Não deployado** (aguarda validação do dono).
- Workers: **5/5 ativos** (discovery, alert, scan, vigília, rescan)
- Planos: 8 contas Pro trial · Vigílias: 35 (30 ok, 5 error)
- E-mail: **alertas cold com rotação (KL-91)** — `alertas.klarim.net`/`aviso.klarim.net`, texto puro
  sem links, cooldown 30-60s, limite/remetente (warmup 100/dia). Transacional segue em `klarim@klarim.net`
- Scan rate: **200/h** (KL-77 Fase 3) · Responses brutos arquivados no GCS `gs://klarim-raw` (KL-77 Fase 2)
- Tech stack detectado por scan (KL-75 P1): `site_tech_stack` + `site_status_log` + `targets.email_provider`

> **Atualize este bloco a cada tarefa** que mude números relevantes.

---

## 8. Gotchas (evitam retrabalho)

- **Alert worker cold: os melhores leads primeiro + fetch desacoplado do send (fix livelock 2026-07-23).**
  A elegibilidade ordena `(e-mail casa o domínio do site) DESC, last_scan_at ASC` — senão a frente
  (mais antiga) é e-mail genérico não-matching (score 15 < threshold 20) que entope a fila e manda 0
  (os leads bons — e-mail no domínio, score 45-60 — ficam no fundo). O ciclo busca `ALERT_FETCH_CAP`
  (200) candidatos e envia até o `send_cap` (throttle+cooldown+cotas), ordenados por score DESC. Cada
  skip por baixa qualidade LOGA o motivo (`[alert] skip lead …` + sinais, e-mail mascarado). O `-15`
  de prefixo role-based (`contato@`) NÃO foi mexido (os domain-match passam mesmo assim).
- **Deploy = api+discovery+worker rodam `ensure_schema` CONCORRENTE → risco de DeadlockDetected**
  (ALTER/CREATE INDEX disputam `AccessExclusiveLock`). O `ensure_schema` **retenta** erro
  transitório de DDL (`_is_transient_ddl`, 6× backoff); o scan worker **não** zera mais o `store`
  se falhar (bug 2026-07-23: `store=None` permanente → escaneava sem persistir até restart — o
  `print` do score fica FORA do `if store is not None`, mascarando). Fila do scan = **`klarim:scan_queue`**
  (não `scan_queue`); persistência real se vê em `targets.last_scan_at`/tabela `scans`, não só no log.
- **CSP estrita do `klarim.net` bloqueia islands Astro** ("Astro is not defined") →
  o `/painel` usa CSP relaxada; ilhas admin são `client:only="react"`.
- **`parseUTC`:** timestamps do Postgres são naive — adicione `Z` antes de `new Date`.
- **SPA fallback do Vite** serve `200` para paths desconhecidos (não é o arquivo real).
- **Arquivo `.js` público novo (`web/public/*.js`) NÃO é servido em produção sem 2 passos** (KL-90
  fix, 2026-07-22): (1) o `web` (nginx) tem um **allowlist explícito** de paths proxiados ao `astro`
  (`https.conf.template`/`http.conf`, regex `…|track\.js|theme\.js|header\.js|planos-auth\.js`); um
  arquivo fora da lista cai no `location / { try_files $uri /index.html }` (root do Vite) → serve o
  **index.html do Vite (text/html)** → com `nosniff`, o browser **bloqueia o script**. **Adicione o
  nome do arquivo ao allowlist.** (2) Referencie com **`?v=N`** (como `theme.js?v=2`) e **bump a cada
  alteração** — senão o Cloudflare cacheia o HTML de erro por 4h. ⚠️ **Não requisite a URL `?v=N`
  antes do fix estar no ar** (o CF cacheia o erro naquela chave → precisa de outra versão).
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
  **fator de TIPO de e-mail (KL-146):** pessoal +15 · genérico neutro 0 · medium-bounce (`atendimento`/
  `sac`) -5 · high-bounce (`contato`) -10 (`_email_type_factor`, SUBSTITUI a penalidade role-based -5
  do KL-136 — ver KL-146) ·
  `MISMATCH_FREE_PENALTY` free de terceiro (**0 desde 2026-07-20**, era -20 — PMEs BR usam gmail como
  e-mail comercial; o -20 barrava leads legítimos) · -10 descartado/score<40
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
- **KL-95** — Corrige 4 divergências de métricas do dashboard Analytics (contavam requests à API em
  vez de ações reais) ✅. **Definição das métricas (fonte autoritativa, não o access_log):**
  **"Contas criadas"** = `COUNT(*) FROM users` no período (não POST /signup, que incluía tentativas/
  rate-limits); **"Scans"** = `COUNT(*) FROM scans WHERE source IS DISTINCT FROM 'discovery'` (scans
  MANUAIS — exclui o worker automático e o ruído de MCP/bots do access_log). Aplicado em
  `al_server_metrics` (KPIs) e `al_daily_series` (tendência — cada métrica da sua tabela: visitantes
  do access_log, scans de `scans`, contas de `users`). **Reclassificação retroativa** de pre-fetch de
  e-mail (o classificador do KL-92 P4 só marca IPs novos): `store.reclassify_prefetch_bots(ranges)`
  (`UPDATE … is_bot=true WHERE is_bot=false AND ip_address <<= ANY(ranges::cidr[])`, idempotente) via
  `scripts/reclassify_prefetch_bots.py` (one-off) **e no boot da API** (`_reclassify_prefetch_bots_bg`,
  pega ranges recém-adicionados). **Jornada pré-signup** exclui polling/admin no SQL
  (`_JOURNEY_EXCLUDE`: `/admin/%`,`/painel/%`,`/mcp/%`,`/account/me`,`/events`,`/health` — some o
  `/admin/inbox/unread-count`) + **dedup de passos consecutivos** iguais na derivação (10x o mesmo
  path → 1). +7 testes; SQL (`<<= ANY(::cidr[])`, scans/users) validado contra Postgres 16.
- **KL-90** — Dashboard v2 (**P0 dev local ✅**, **P1 endpoint ✅**, **P2 frontend ✅**, **iteração de UX ✅**,
  **P3 swap → produção ✅**). **P3 (2026-07-22, commit `6bbf1d2`, CI 4/4 verde):** o v2 assumiu
  **`/dashboard`** (`index.astro` monta `DashboardV2`; o antigo `account/Dashboard.jsx` foi removido;
  `SiteDetail` mantido). **`/dashboard/v2` → 301 `/dashboard`** via `middleware.js` (antes da auth).
  Header global (avatar+busca) já em todas as páginas públicas. Validado em prod: públicas 200, health ok,
  redirect 301, dashboard-summary 401 sem auth, **zero erro/CSP no console**, **workers 4/4 alive**,
  **score klarim.net=100 🟢**. Sem flush Redis (o dashboard-summary não é cacheado). Scripts externos
  `header.js`/`planos-auth.js` (CSP `script-src 'self'`, sem hash inline). **Iteração de UX (2026-07-22, 9 itens, tudo em `/dashboard/v2` + Header/Planos/Conta):**
  (1) **Header global logado** — avatar + dropdown (nome/e-mail, Meu dashboard, Minha conta, Sair) +
  **busca persistente**; a lógica saiu do `<script>` inline (era 1 dos 5 hashes da CSP) p/ **externo
  `web/public/header.js`** (coberto por `script-src 'self'`, sem hash). (2) `AddSiteModal` (`POST
  /account/sites`). (3) **`MonitoredSitesPanel`** fixo/sticky (fim do dropdown) + histórico de
  pesquisados (`/account/scan-history`). (4) **`ScoreCard` consolidado** — score+status+ações+perfil
  público+landing+**Vincular Técnico** (`TechnicianModal`→`/account/technician/invite`); StatusPanel
  removido. (5) **`MonitoringSection`** — status das vigílias (`/account/vigilias`) + o-que-monitoramos
  (derivado, honesto: `/account/vigilias` é read-only → sem toggle-save, liga ao plano) + boletim.
  (6) **`Collapsible`** — Riscos/Checklist recolhidos por padrão. (7) **Planos logado** (`planos.astro`
  + externo `web/public/planos-auth.js`): banner do plano atual + "✓ Seu plano" + upgrade/downgrade/
  atual. (8) **Conta** `max-w-2xl`→`max-w-4xl` (o resto de `AccountSettings` já existia; lista-de-
  sites/notificações/export deferidos). (9) **`ExploreSection`** (setor/ranking/estatísticas/melhores).
  Layout novo do `DashboardV2`: painel de sites à esquerda (`lg:flex`+`lg:w-72`/`lg:flex-1`) + conteúdo
  à direita. "Meu dashboard" aponta p/ `/dashboard/v2` (volta a `/dashboard` no swap). Validado no
  navegador (temas, zero erro) + build + test:unit 96. **Ajustes visuais pós-validação:** gráfico de
  evolução com **eixo Y auto-escalado** ao intervalo (sem espaço vazio) + altura compacta; `ExploreSection`
  removida do dashboard (fica no repo p/ voltar depois); card do score com só **"Ver landing page"** (o
  "Ver perfil público" era redundante). **Correção de regressões (v2 = superset da produção):** a produção
  vive em 2 páginas (`/dashboard` Dashboard.jsx + `/dashboard/site/[id]` SiteDetail.jsx) e o v2 tinha
  perdido features do site-detail. Restauradas **reusando os componentes de produção**: `PlanSection`
  (checkout PIX/QR + countdown + downgrade + histórico) e `TechnicianSection` (convite/revogar/**laudo**
  `/laudo/{code}`+WhatsApp) — este último no modal "Vincular Técnico"; + novos `SealSection` (selo
  `/seal/widget.js`, gated por plano), `TechnicianClients` (role=technician → "Sites dos meus clientes" +
  badge), `ConfirmEmailBanner`; **remover site** (✕ no painel → `DELETE /account/sites`), aviso
  `has_other_owner` no add. **Affordance:** `Collapsible` com chevron que rotaciona + "expandir/recolher" +
  hover; Riscos abre por padrão com o 1º risco expandido. **Não portadas** (dependem do backend
  `dashboard-summary` expor mais dados; ficam no site-detail): indicadores de privacidade LGPD, 48 checks
  com evidência, ownership verification. **P2:** Dashboard v2 em **`/dashboard/v2`** (`web/src/pages/dashboard/v2.astro`), rota
  SEPARADA que coexiste com `/dashboard` (o antigo, **não modificado**; o swap é o P3). ⚠️ O prompt
  dizia `/painel/dashboard-v2`, mas o dashboard do USUÁRIO vive em `/dashboard/*` (auth por cookie de
  usuário via `src/middleware.js`); `/painel/*` é o painel do OPERADOR (admin) — como o endpoint é
  user-auth, a página tem que ficar sob `/dashboard/`. 10 componentes React em
  `web/src/components/dashboard-v2/` (+ `shared.js` tokens/helpers, `FixInline.jsx`): `DashboardV2`
  (orquestrador: 1 fetch, seletor de site, skeleton, erro+retry, banners offline/score-100, toast,
  scan+re-fetch), `SiteSelector`, `ScoreCard` (anel do semáforo + tendência PT + benchmark),
  `StatusPanel` (riscos/SSL/online + PDF/Compartilhar/Escanear), `CategoryBar` (6 pills → checks
  expandem), `RisksList` (accordion KL-20 por severidade → "Como corrigir"), `FixInline` (abas
  WordPress/Nginx/Apache, auto-seleciona pelo `site_type`), `Checklist`, `ScoreHistory` (**gráfico
  SVG, não recharts** — CSP estrita do público bloqueia libs que injetam estilo; mesma escolha do
  KL-86), `PlanCard`, `EmptyDashboard`. Progressive disclosure em 3 camadas + F-pattern; tema
  claro/escuro via utilitários theme-aware (KL-87) + `text-[var(--accent-text)]` nos botões laranja;
  `client:load` (padrão do dashboard atual). PDF=`/api/report/executive?url=`; Escanear=`/scan/result?
  refresh=1`. Validado no navegador (troca de site, accordion, temas, zero erro no console) + `npm run
  build` + `test:unit` 96. **Gotcha do dev (P2):** o Astro entra em crash-loop no restart por causa do
  lock `web/.astro/dev.json` (bind mount sobrevive ao restart) → o `command` do serviço `astro` agora
  faz `rm -f .astro/dev.json` no boot. E o dev server pode ter **scan incompleto do Tailwind** para
  classes NOVAS de arquivos recém-criados (reiniciar resolve; o build de produção gera tudo). **P0:**
  stack Docker local (`docker-compose.dev.yml` + `.env.dev` + `frontend/nginx/
  dev.conf` + `scripts/seed_dev.py`), detalhes na §6 e em `docs/DEV.md`. **P1 — `GET /account/
  dashboard-summary?site_id={id}`** reescrito para a shape v2 (SUBSTITUI o payload do KL-86; o
  front antigo `web/src/components/account/Dashboard.jsx` será reescrito no P2). Toda a lógica
  vive em **`api/dashboard.py`** (funções `build_*` PURAS/testáveis + orquestrador
  `build_dashboard_summary` com queries em paralelo via `asyncio.gather` → ~50ms). O handler em
  `api/main.py` virou uma casca fina que delega. Response: `sites[]` (todos, p/ o seletor) +
  `selected_site_id` (`?site_id=` ou primário = 1º; site de outro usuário → **404**) + `site`
  (score/semáforo/trend PT subindo·caindo·estavel·primeiro/next_scan/is_online/site_type/
  ssl_days) + `benchmark` (rank + média setorial, fallback global) + `risks[]` (FAIL em
  linguagem de negócio KL-20, ordenados por severidade, com **`fix_inline` {wordpress,nginx,
  apache}**) + `categories[]` (6 grupos fixos: tls/headers/supply/dns/content/osint com passed/
  total/status + checks aninhados com evidence/risk_message/fix_inline) + `score_history` +
  `checklist` (derivado dos FAIL alta/crítica + perfil/selo/compartilhar, máx 5) + `plan`
  (features v2) + `monitoring` (vigílias/boletim/selo/técnico) + `profile`. **`fix_inline` é um
  mapa CANÔNICO por nº de check em `api/dashboard.py::FIX_INLINE`** (~25 checks; produção, não
  depende do seed) — `title`/`description`/`risk_message` vêm do `RISK_MESSAGES` (KL-20). Sem
  site → payload reduzido (`has_site:false` + plano + checklist add_site/confirm_email).
  `contact_email`/cnpj/whatsapp NUNCA no payload. Helpers KL-86 (`_dashboard_categories`/
  `_build_checklist`/`_score_trend`/`_vigilia_summary`/`_new_user_checklist`) ficam órfãos (não
  removidos — cleanup futuro); testes do endpoint migraram p/ `tests/test_kl90_dashboard_summary.py`
  (20) + `tests/test_kl86_dashboard.py` reduzido aos 7 testes de helper puro.
  **Fix login+técnico (2026-07-22, commits `1d8730f`…`c0e4531`):** persistência do login no header
  (o allowlist do Nginx não proxyava `/header.js`/`/planos-auth.js` p/ o Astro → SPA fallback servia
  HTML → o browser bloqueava o script; fix no allowlist + `?v=3` — a `?v=2` foi envenenada por ter
  sido testada antes do deploy). **Experiência do técnico (2026-07-22):** o "Ver →" da lista "Sites
  dos meus clientes" abre o **dashboard técnico** do site do cliente (não o perfil público). Backend:
  `build_dashboard_summary` ganhou ramo **modo técnico** (`api/dashboard.py::_build_technician_view`)
  — quando `site_id` não é site próprio, exige um `technician_link` **ativo** deste técnico (senão
  **404**, nunca 500/vaza) e devolve a resposta técnica completa (48 checks com **evidência**
  primária + `fix_inline` por plataforma + PDF técnico + benchmark + riscos + histórico + vigílias do
  dono **read-only**), `technician_mode:true`, `owner_email` **mascarado** (`_mask_email`),
  **sem** plan/checklist/conta do dono. Toggle "Receber alertas deste site":
  `PUT /account/technician/notifications` + coluna `technician_links.receive_alerts` (default true);
  a vigília (`_emit_alert`) faz **CC best-effort** aos técnicos que optaram
  (`get_alert_technicians_for_domain`, só e-mail do técnico). Frontend:
  `TechnicianView.jsx` (banner "🔧 Visualizando como técnico · {domain} · Dono: {mascarado}" +
  "← Voltar"), `CategoryBar technical` (evidência primária), `ScoreCard technician` (PDF técnico, sem
  Compartilhar/Vincular), `TechnicianClients` → `/dashboard?site_id={id}`. **Gotcha:** o mount do
  `DashboardV2` lia sempre `load(null)` (ignorava `?site_id=`) → o deep-link caía no dashboard do
  próprio técnico; fix: `initialSiteId` da URL → `load(initialSiteId || null)` (owner sem param
  inalterado). +2 testes (`test_technician_mode`, `…_unlinked_404`); relatório em
  `claude/reports/KL-90_experiencia_tecnico_dashboard.md`.
- **KL-99** — Conta sem senha + 3 níveis de confiança + verificação de domínio ✅ (validado local;
  **deploy pendente de validação do dono**). Elimina a fricção do `/cadastrar` (convertia 1,1%).
  **Modelo:** `users.account_level` (1 sem senha · 2 com senha · 3 dono verificado por controle de
  domínio), eixo **distinto** do `access_level` do KL-82. Backfill: **toda conta existente → 2**
  (`ADD COLUMN … DEFAULT 2`). `password_hash` **nullable**; `users.source` (`signup`|`hmac`|`inline`).
  **Fluxo C (`GET /alert-access`):** o clique no link HMAC dá só uma **sessão de visualização**
  (`alert_session`, view-only, 24h) — **NÃO** cria conta nem loga (fix 2ª rodada). A conta nasce no
  **consentimento**: `POST /account/monitor-from-alert` ("Sim, monitorar") cria conta SEM senha
  (nível 1, `source=hmac`, confirmada) + vincula site + ativa vigílias + loga; e-mail já com conta →
  `{existing_account}`. `MonitorConsent.jsx` (`mode="alert"` cria · `mode="account"` = logado add site).
  **Magic link** (conta sem senha volta / esqueceu senha): `POST /account/magic-link` (TTL 1h) +
  `GET /account/magic-access` → sessão → `/dashboard`; botão "Enviar link de acesso" no `/entrar`.
  **Fluxo D (`POST /account/signup-inline {email,domain}`):** conta nível 1 (`source=inline`, não
  confirmada) + domínio vinculado como site PENDENTE (sem vigília) + e-mail de confirmação; 3/h/IP;
  `{status:confirmation_sent|already_exists}`. A **confirmação do e-mail ATIVA o monitoramento**
  (`_activate_monitoring_on_confirm`) e **loga** a conta sem senha → dashboard (senão ficaria presa:
  não há senha p/ o `/entrar`). `InlineSignup.jsx`. **`POST /account/signup`** com senha opcional
  (sem senha → nível 1). **`/cadastrar`** virou 1 campo (só e-mail). **`POST /account/set-password`**
  (1→2; 400 se já tem senha, 422 se não conferem). **`@require_level(n)`** (`_require_level`, 403
  `{error:insufficient_level, required_level, current_level}`) gateia: nível ≥2 em `PUT /account/me`,
  `DELETE /account/sites/{id}`, `technician/invite`, `upgrade`; nível ≥3 em `profile-confirm`.
  **Verificação de domínio (2→3):** `POST /account/sites/{id}/verify/{start,check}` (meta_tag/
  html_file/dns_txt; token `token_urlsafe(32)`; check 10/h/IP) → `mark_site_verified` + `account_
  level=3` + `targets.owner_verified`. **Estende** a tabela `ownership_verifications` do KL-68
  (colunas `token`/`domain`; TTL 7d no INSERT — o fluxo de código do KL-68 fica intacto). **Anti-SSRF:**
  o fetch usa o domínio de `targets` (não input cru) e o corpo nunca volta ao usuário (só o match).
  **Cleanup:** `delete_unconfirmed_passwordless_accounts` no `trial_worker` (nível 1 + sem senha +
  não confirmada + >30d + sem re-login — o site PENDENTE do Fluxo D a isentava da limpeza do KL-82).
  **Frontend:** `web/src/components/scan/{InlineSignup,MonitorConsent}.jsx` (2 variantes por SESSÃO,
  não por dispositivo; `ctaCopy`/`AccountCTA` removidos), `dashboard-v2/LevelPrompt.jsx`
  (`useLevelGate` intercepta a ação e re-executa após o modal + `SetPasswordModal`/`VerifyDomainModal`/
  `LevelBadge`). **Testes:** +27 backend (`test_kl99_levels.py`) + 98 `node --test`. **Seed dev:**
  `nivel1@teste.com` (sem senha) · `dono3@teste.com`/`dev123456` (dono verificado nível 3). Relatório:
  `claude/reports/KL-99_conta_sem_senha_niveis.md`. **TTL do `alert_access` = 7 dias**
  (`_ALERT_ACCESS_TTL` em `notifier/email_client.py` + `api/main.py`, em sincronia). **2ª rodada
  (fix crítico):** o link do alerta NÃO cria conta (só sessão view-only) — a conta nasce no
  consentimento (`monitor-from-alert`); + magic link para conta sem senha voltar; + layout do
  resultado em 2 colunas (score compacto + CTA acima do fold).
- **KL-91** — Módulo de e-mail cold com rotação de subdomínios ✅ (validado local; **deploy
  pendente de validação do dono**). Corrige os alertas caindo no spam (urgência + links
  trackáveis + domínio único). **`notifier/cold_alert.py`** (PURO/testável): 3 variantes de
  **texto puro SEM links** (1 informativa · 2 setorial com média · 3 educativa; acentuação PT-BR
  correta — texto sem acento parece MAIS spam), opt-out **por resposta** (header
  `List-Unsubscribe: <mailto:scan@klarim.net?subject=remover>`, SEM One-Click — inválido com
  mailto), `choose_variant` (com setor→1/2/3, sem→1/3), `load_senders`/`pick_sender`
  (round-robin pelo de menor volume; guard descarta `klarim.net` cru — isolamento), `flag_high_bounce`
  (circuit breaker: bounce >5% amostra ≥20 → pausa o remetente). **Rotação** entre
  `alertas.klarim.net` + `aviso.klarim.net` (`ALERT_SENDER_EMAILS`, verificados no Resend);
  `klarim.net` fica **exclusivo do transacional**. `alert_worker.run_cycle` reescrito de **batch →
  envio individual** com **cooldown 30-60s** (`ALERT_SEND_INTERVAL_MIN/MAX`, 0 em dev/testes) +
  **limite diário por remetente** (`ALERT_SENDER_DAILY_LIMIT`, warmup 100→750; editável no painel)
  + deadline de ciclo (não estoura o intervalo). `KlarimMailer.send_cold_alert` (texto puro,
  from rotacionado, log com `template_variant`+`from_domain`); **`DRY_RUN_EMAIL`** curto-circuita
  `_send_sync` (dev simula sem Resend, grava email_log). Schema: `email_log.template_variant`.
  Store: `count_alerts_sent_today_by_domain`, `email_health_by_domain`. `/system/email-health`
  (+ MCP `get_email_health`) ganham **`by_domain`** (sent/delivered/bounced/bounce_rate/status por
  remetente). `send_alert_for_target` (disparo manual) usa o mesmo formato cold (1º remetente).
  **Opt-out por resposta = manual por ora** (Opção A: respostas caem no inbox `scan@klarim.net`,
  operador põe na blocklist). Threshold do lead scoring (KL-85) **não** foi mexido (gerido à parte).
  Builders antigos (`build_alert_text` + alert-access HMAC KL-82 S3) **ficam no código** (o ciclo
  não os usa; revertível). +33 testes (`test_kl91_cold_alert.py` +24; `test_alert_worker.py`
  reescrito p/ envio individual). Relatório: `claude/reports/KL-91_modulo_email_rotacao.md`.

- **KL-96** — Desativar alerta antigo + corrigir contadores ✅. **Itens 1–2 já estavam resolvidos
  pelo KL-91:** o `run_cycle` só envia por `send_cold_alert` (subdomínios cold; nenhum `send_alert`/
  `_proactive_from`), e o webhook do Resend já bota todo bounce na blocklist (0 fora). A premissa
  "785/dia de alerta antigo via alerta@klarim.net" era **`profile_view`** (769/dia, 0,7% bounce), não
  alertas — os 8 alertas de hoje pelo path antigo foram ANTES do deploy KL-91. **Itens 3–6 (fonte única
  = email_log):** havia 3 fontes p/ "alertas enviados" (alert_log/email_log/count_proactive) divergindo.
  `store.alert_stats` reescrito de **alert_log → email_log** (`email_type IN ('alert','alert_score100')`,
  dia-calendário) — alinha a página Alertas com o funil do Analytics (que já usava email_log) e o
  Sistema (`/system/status` usa o mesmo `alert_stats`). `aa_metrics_raw.alerts_sent` + `analytics_funnel`
  (MCP get_funnel) também migrados p/ email_log. **Abas:** "Alertas enviados" ganhou coluna **REMETENTE**
  (`email_log.from_domain` via LEFT JOIN por email_id; badge verde p/ cold `alertas.`/`aviso.`, cinza
  p/ antigo); "Consultas de perfil" ganhou **contadores próprios** (`profile_view_stats` +
  `GET /alerts/profile-view-stats`). §6 "Contas criadas" já vinha de `users` (KL-95) — só verificado.
  +5 testes (`test_kl96_counters.py`); SQL validado no Postgres 16 da VM. **Recomendação (próximo card):**
  isolar `profile_view` (~15k/sem cold no domínio transacional `klarim.net`) num subdomínio próprio +
  validação MX — é o risco de reputação real (não dá p/ usar `alertas.`/`aviso.`: quebraria o warmup).

- **KL-101** — Isolar profile_view no subdomínio `perfil.klarim.net` ✅ (código pronto + testado;
  **deploy PENDENTE de o dono verificar `perfil.klarim.net` no Resend** — senão os envios falham).
  O aviso "perfil consultado" era o último cold saindo por `klarim.net` (~15k/sem, `_proactive_from`).
  Agora: remetente dedicado `notifica@perfil.klarim.net` (`_profile_view_from`, `PROFILE_VIEW_FROM_
  EMAIL`; **não rotaciona** com os cold alerts do KL-91), `build_profile_view_text(domain)` **texto
  puro SEM links** + opt-out por resposta (mailto, como o KL-91), `email_log.from_domain=perfil.klarim.net`.
  **Volume:** dedup por dono **1/dia** (`notify_owner:{email}` Redis) + dedup por domínio/24h (já
  existia) + **teto diário de warmup** `PROFILE_VIEW_DAILY_LIMIT=200` (editável no painel, contador
  `profileview:daily:{date}`). `klarim.net` fica **100% transacional**. +8 testes
  (`test_kl101_profile_view.py`). Relatório: `claude/reports/KL-101_isolar_profile_view.md`.

- **KL-100** — Página pública `/metodologia` ✅. Transparência da varredura passiva: 6 seções (o que
  faz / o que NÃO faz / base legal [Art. 154-A CP, Marco Civil, LGPD Art. 7 IX, links planalto.gov.br
  em nova aba] / dados consultados / direitos do dono / identificação do scanner). Astro SSG
  (`web/src/pages/metodologia.astro` + `Page.astro`), link no `Footer.astro` (todas as páginas),
  no `sitemap.xml.js` e na allowlist do nginx (`metodologia` em http.conf + https.conf.template).
  Linha "Saiba mais sobre nossa metodologia: klarim.net/metodologia" (texto, não link) no rodapé dos
  4 templates cold (3 variantes de alerta + profile_view).
- **KL-102** — List-Unsubscribe (RFC 8058) + `/remover` ✅. Testes de deliverability (10/10) apontavam
  ausência do header `List-Unsubscribe` nos senders cold (Gmail/Yahoo exigem p/ >5k/dia). Agora os 3
  senders cold (não o transacional) levam `List-Unsubscribe: <mailto:scan@klarim.net?subject=remover>,
  <https://klarim.net/remover?token=...>` + `List-Unsubscribe-Post: One-Click`. Token HMAC em
  `notifier/email_client.py` (`generate/verify_unsubscribe_token`, propósito `unsubscribe` — não colide
  com o alert_session do KL-82; SEM expiração; base64url(json).hmac). `POST/GET /remover` (FastAPI,
  roteado pelo nginx): marca `unsubscribed` + blocklist + evento `email_log`; GET = página de
  confirmação; POST = form OU one-click do Gmail. **Segurança:** HMAC constant-time, anti-enumeração
  (nunca revela se email/domínio existe; token inválido → 200/400 genérico), rate limit 10/min/IP só
  nos tokens INVÁLIDOS (o one-click válido nunca é bloqueado). Opt-out por resposta ("remover") segue
  em paralelo. +14 testes. Relatórios: `claude/reports/KL-100_metodologia.md`, `KL-102_list_unsubscribe.md`.

- **KL-103** — Landing: social proof (números ao vivo) + pills de setores acima do fold ✅. Nota:
  a remoção das seções abaixo do fold e do texto "13.000+" já fora feita no **KL-81** (o "estado
  atual" do card era um snapshot antigo). Feito: (1) **`/public/stats` estendido** com 3 contadores
  agregados (`sites_analyzed` = targets status≠'discovered'; `sectors` = distinct sector≠'outro';
  `public_profiles` = site_profile public_visible) — `store.public_landing_counts`, mesmo cache Redis
  1h + rate limit 30/min do KL-74; **só números, sem PII**. (2) **Stats bar** na landing (fallback
  ESTÁTICO no HTML SSG + `web/public/landing-stats.js` atualiza ao vivo; formata pt-BR, arredonda >1000
  p/ centena inferior + "+"). (3) **6 pills de setor** (`/setor/{slug}`) + "+43 setores" → `/setores`;
  clique rastreado (`sector_pill_click` em `_KNOWN_EVENTS`). (4) **meta/og/twitter** com a escala
  ("50.000+ sites analisados em 49 setores"). `landing-stats.js` na allowlist do nginx (`?v=1`).
  **Decisão:** script vanilla externo (não React island) — SSG-friendly (número no HTML antes do JS),
  CSP-safe, ~1KB (a landing não tinha bundle JS). +4 testes. Relatório: `claude/reports/KL-103_landing_social_proof.md`.

- **KL-104 P1** — Deep linking entre páginas do admin ✅ (Parte 1 de 3). Componente reutilizável
  **`DomainLink`** (`ui.jsx`): um domínio/URL em tabela do admin vira `<a href="/painel/alvos/{id}">`
  (sem `targetId` → texto puro, nunca link quebrado). Aplicado em **Scans** (`s.target_id`), **Alertas
  enviados** (`a.target_id`), **Consultas de perfil** (+ mantém link "perfil ↗" público), **Analytics/
  Eventos** e **Sites monitorados** (Clientes + Usuários, `s.target_id`). Detalhe do alvo
  (`AlvoDetalhePage`) ganhou **links de saída** (nova aba): "Ver perfil público" (`klarim.net/site/
  {domain}`, só se `profile.public_visible`) e "Ver último scan" (`klarim.net/scan?url={domain}`, só se
  `last_scan_at`). Backend: `target_id` adicionado aos responses de `analytics_events` + `aa_events`
  (site_events já tinha a coluna). **Não coberto (nota):** Leads (lista é por e-mail, sem domínio) e
  Comportamento/IPs (access_log → join com targets; a Parte 3 cobre o comportamento por-alvo).
  Frontend-only + 2 campos `target_id`. +2 testes. Relatório: `claude/reports/KL-104_p1_deep_linking.md`.
- **KL-104 P2** — Filtros avançados na página Alvos ✅ (Parte 2 de 3). 10 filtros novos + os 5
  antigos, todos combinando com **AND** e **100% parametrizados** (zero injeção; valor fora do
  dicionário é descartado). O coração é **`TargetStore._target_filters(f)`** (staticmethod PURA)
  → `(where, params)`, compartilhado por `list_targets(**filters)` **e** `count_targets_filtered`
  (a contagem bate com a lista). Filtros: `score` (faixas + `sem`), `semaphore` (derivado do score),
  `lead_score` (`alert_quality_score`), `has_email`/`monitored`/`owner_verified`/`has_ai_profile`
  (bool **3-estados**: sim/não/todos — `monitored` via `EXISTS user_sites`, `has_ai_profile` via
  `EXISTS site_profile.description`), `site_type`/`tech` (CSV → `= ANY(%s)`; `tech` via EXISTS lazy
  no `site_tech_stack`), `last_scan` (`hoje`/`7d`/`30d`/`nunca`). **`GET /targets`** ganhou os params
  + response `total` (filtrado) + `total_all` (geral, cache Redis 1h); **`GET /targets/tech-list`**
  (top-20 tecnologias p/ o dropdown, cache 1h) fica ANTES de `/targets/{id}`. 3 índices parciais
  (last_scan_score/last_scan_at/owner_verified). Frontend: lógica pura em
  **`web/src/lib/admin/alvosFilters.js`** (URL⇄estado⇄params, testável), UI em
  **`AlvosFilters.jsx`** (linha 1 fixa + linha 2 colapsável + barra "N de X" + "Limpar filtros";
  toggles 3-estados, multi-select via `<details>` CSP-safe). `AlvosPage` sincroniza os 15 filtros
  com a **URL** (`replaceState`, deep-link/bookmark), debounce 300ms, paginação pelo `total`, e
  registra **`admin_filter_used`** (KL-57, sem PII — só os nomes dos filtros). Perf validada em
  prod: filtro combinado em **11ms** (<2s p/ 50k). +11 backend + 9 `node --test`. Relatório:
  `claude/reports/KL-104_p2_filtros_avancados.md`.
- **KL-104 P3** — Visão 360° do alvo ✅ (Parte 3 de 3, **fecha o card**). **`GET /admin/targets/{id}/
  intelligence`** (JWT admin) junta numa chamada o que exigia 4-5 páginas: **4 seções isoladas**
  (monitoramento · funil · visitantes · timeline). Agregações brutas (SQL) em `discovery/store.py`
  (`ti_*`, 1 conexão por método → falha isolada); **montagem PURA** em **`api/target_intelligence.py`**
  (funil, classificação de fonte de tráfego, mascaramento de IP, merge/paginação da timeline) +
  orquestrador com **degradação graciosa** (`_try` engole erro de sub-query → `null`; `_safe_section`
  → `{error}`; tabela ausente nunca derruba). **Monitoramento:** monitors (`user_sites`→`users`,
  e-mail é dado admin), vigílias (por `site_domain`), dono verificado (`owner_verified`+
  `ownership_verifications`), técnico (`technician_links`). **Funil:** 6 etapas derivadas de
  timestamps reais (discovered/scanned/alerted/account_created/monitoring/paid — paid via
  `payments.target_url`), e-mails enviados+summary (`email_log`), lead score (`alert_quality_score`).
  **Visitantes:** consultas/IPs únicos + top IPs **mascarados /24** (`mask_ip(ip,3)`, LGPD KL-92 — IP
  completo NUNCA sai) + cross-site (outros domínios do mesmo IP, 1 query batch `ANY(::inet[])`, com
  `target_id` p/ DomainLink) + fontes de tráfego (por `referrer`). **Timeline:** UNION lógico em
  Python de scans/alertas/perfil-consultado(`endpoint LIKE '/site/%'`)/status/descoberta, ordem DESC,
  **cursor** (`before`+`has_more`/`next_cursor`); `email_log.sent_at` (TIMESTAMPTZ) normalizado via
  `AT TIME ZONE 'UTC'` p/ não misturar naive/aware no merge. 2 índices `access_log(domain_queried,
  created_at)`/`(ip_address,created_at)`. Frontend: `TargetIntelligence.jsx` (4 `<details>` CSP-safe,
  funil visual laranja/cinza-tracejado, "Carregar mais" por cursor, cross-site = DomainLinks) montado
  no topo do `AlvoDetalhePage`. Os 8 padrões SQL validados no Postgres 16 da VM. +18 backend. Relatório:
  `claude/reports/KL-104_p3_visao_360.md`.
- **KL-105** — Frontend de conversão do resultado do scan (Fluxo D) ✅. **Mudança-chave:** o
  **`POST /account/signup-inline`** deixou de exigir confirmação de e-mail (que matava a conversão,
  lição KL-89) — agora **ativa o monitoramento na hora** (vincula site + vigílias + posse Tier 1,
  igual ao `monitor-from-alert`) e **loga** (cookie), retornando `{status: monitoring_active}` (+cookie)
  ou `{status: already_exists}` (o front dispara um **magic link** automático). O welcome valida o
  endereço: um bounce cai na blocklist → alertas futuros suprimidos (protege a reputação sem bloquear
  a conversão). Rate limit **5/min & 30/dia por IP** (era 3/h). Novo **`GET /account/monitoring-status
  ?domain=`** (auth **opcional**, 30/min/IP) → `{logged_in, monitoring, user_email?}` — o CTA do
  logado usa p/ escolher entre "adicionar ao monitoramento" (estado C) e "você já monitora" (estado B).
  Frontend (o layout 2 colunas + InlineSignup/MonitorConsent já vinham do KL-99): `InlineSignup.jsx`
  reescrito — sucesso inline "Monitoramento ativado!" (sem redirect), `already_exists` → dispara magic
  link + "enviamos um link de acesso", botão desabilitado até e-mail válido (`isValidEmail`), CTA
  `border-2 border-brand-500`, texto legal (Termos/Privacidade), 4 eventos KL-57 (`inline_signup_shown/
  click/success/existing`). `MonitorConsent.jsx` (account mode) busca `monitoring-status` → estado B.
  O `/entrar` já tinha magic link (KL-99). +6 backend (`test_kl99_levels.py`) + 1 `node --test`
  (`isValidEmail`). **Consideração de segurança (documentada):** ativar monitoramento sem confirmação
  é um trade-off de consentimento — mitigado pela validação por bounce; follow-up possível: gatear o
  1º alerta em `email_confirmed`. Relatório: `claude/reports/KL-105_conversao_scan.md`.
- **KL-97 + KL-98** — Gestão do dono no dashboard (monitoramento/notificações + perfil público/selo) ✅.
  Compartilham `user_sites`/`vigilias`/`site_profile` + auth. **Ownership em TODO endpoint** (`_owned_site`:
  auth + `_require_level` + `get_user_site`; nível ≥1 p/ monitoramento, **nível 3 + `is_owner`** p/ perfil/selo).
  **KL-97:** `GET/PUT /account/sites/{id}/monitoring` — liga/desliga vigílias por-tipo (`set_vigilia_enabled`,
  cria se não existe, threshold do score no `last_data` JSONB), **plan-gated** (toggle fora do plano → 403
  `requires_plan`; `_VIGILIA_MIN_PLAN`); `list_site_vigilias` traz TODAS (habilitadas ou não).
  `GET/PUT /account/notification-preferences` — novas colunas em `users` (`bulletin_frequency` NULL=plano ·
  `bulletin_hour` · `notify_vigilia/bulletin/news`); `list_users_due_bulletin` reescrito p/ **frequência
  EFETIVA** (override do user > plano; `off`/`notify_bulletin=false` não recebem; `immediate`→daily).
  **KL-98:** `PUT /account/sites/{id}/profile` — dono edita 15 campos + tags; `_sanitize_owner_profile`
  (strip HTML via `_sanitize_str`, limites, valida CNPJ/telefone/URL → 422); `update_site_profile_fields(...,
  actor='owner')` marca `edited_by_owner` + acumula `owner_edited_fields` (dedup via `ARRAY(SELECT DISTINCT
  unnest(...))`). **Preservação contra a IA:** `merge_ai_into_profile` pula `owner_edited_fields` E o
  `upsert_site_profile._upd` ganhou CASE por-campo (`'col' = ANY(owner_edited_fields)`) — o dono, mesmo
  limpando um campo, não é sobrescrito. `PUT /account/sites/{id}/visibility` (dono liga/desliga a landing).
  **Selo:** colunas `site_profile.seal_enabled`/`seal_style`; `GET/PUT /account/sites/{id}/seal` (variantes
  badge/footer/floating com `embed_code`); o público `GET /seal/{domain}` devolve `enabled`/`style`/`verified`
  e o `web/public/seal/widget.js` ganhou `data-style` (footer=barra, floating=fixed) + esconde se `enabled=false`.
  4 eventos KL-57 (`vigilia_toggled`/`bulletin_frequency_changed`/`profile_edited`/`seal_configured`).
  Frontend: `MonitoringConfig.jsx` (modal: toggles + threshold + notif) e `ProfileEditor.jsx` (form + preview
  ao vivo + selo + visibilidade), abertos por "⚙️ Configurar"/"✏️ Editar perfil" na `MonitoringSection`.
  +18 backend (`test_kl97_98_owner.py`); SQL (array-dedup/jsonb_set/eff_freq) validado no Postgres 16 da VM.
  Relatório: `claude/reports/KL-97_98_gestao_dono.md`.
- **Fix ProfileEditor + vigílias default + KL-106** ✅. **(1) ProfileEditor** (regressão KL-98): os
  campos não pré-preenchiam (o `initial` do dashboard era parcial) → agora faz `GET /account/sites/{id}`
  no mount e popula do `profile`; modal alargada (`Modal` ganhou `size="xl"`=max-w-3xl) + grid
  `md:grid-cols-2` (2 colunas desktop, empilha no mobile). **(2) Vigílias ativas por padrão:** a raiz
  era uma **corrida** — `_create_account_record` criava o trial Pro via `_spawn` (fire-and-forget), mas
  `_create_site_vigilias` rodava logo depois e `_vigilia_allowed_types` lia a assinatura antes dela
  existir → fallback 'free' (sem vigílias) → **5 users ficaram sem vigília nenhuma**. Fix: (a) o trial
  agora é **awaited** (não `_spawn`) no `_create_account_record`; (b) o plano **Free passa a incluir as
  5 vigílias core** (`UPDATE plans` idempotente — o seed é `ON CONFLICT DO NOTHING`) → mesmo na corrida,
  o fallback free já habilita as 5; (c) `_VIGILIA_MIN_PLAN` reduzido a `{uptime:pro, changes/phishing:
  agency}`; (d) `scripts/backfill_vigilias.py` (idempotente) cria as faltantes + reativa as desligadas,
  respeitando o plano. **(3) KL-106:** `ScoreCard` "Ver landing page →" apontava para `https://{domain}`
  (site real do cliente) → corrigido p/ o **perfil Klarim** (`profileUrl`=/site/{domain}) + link separado
  "Visitar site ↗" p/ o site real (nova aba, noreferrer); `painel.klarim.net` → **301** ao domínio
  principal (o bloco nginx que servia o build Vite antigo virou redirect; `nginx -t` valida na CI). +1
  backend. Relatório: `claude/reports/fix_profileeditor_vigilias_kl106.md`.
- **KL-107** — Segurança (auditoria 24/07: 10 testes IDOR/escalação passaram; 2 achados) ✅.
  **Achado 1 (IDOR):** `POST /account/sites/{id}/verify/check` devolvia `200 {no_pending}` para site de
  OUTRO usuário (permitia enumerar quais têm verificação pendente) — era o único `/account/sites/{id}/*`
  sem ownership check. Fix: `get_user_site` no início → **404** (igual aos demais; `verify/start` já
  tinha). **Achado 2 (Opção B — permitir + avisar):** `POST /account/sites` deixa um terceiro monitorar
  (is_owner=false) um site com dono verificado — o modelo agência→técnico (KL-70) depende disso, então
  não se bloqueia; mas o **dono é avisado**. `store.get_site_owner(target_id)` (is_owner+verified_at) →
  se houver dono ≠ quem adicionou, `_notify_owner_site_added` (fire-and-forget, nunca derruba o add):
  e-mail **transacional** `klarim@klarim.net` (`send_owner_site_added`, TEXTO PURO, informativo, **sem
  link de ação**), `email_type='owner_notification'`, **dedup 1/dia/target** (Redis). Só vaza o e-mail
  de quem adicionou (nunca id/plano). Evento KL-57 `owner_notification_sent` (`_KNOWN_EVENTS`; a
  contagem real vem do email_log). +9 backend. Relatório: `claude/reports/KL-107_seguranca.md`;
  achados em `docs/SECURITY.md`.
- **KL-108** — Circuit breaker separa HARD de SOFT bounce ✅. Em 26/07 os 3 senders cold passaram de
  5% pelo bounce rate COMBINADO (hard+soft) e foram pausados juntos → **zero cold alerts, backlog
  2.683** (fix emergencial `ALERT_SENDER_MAX_BOUNCE_RATE=12` no `.env`, depois removido). Soft bounces
  são transitórios (caixa cheia, `delivery_delayed`) e não deviam pausar. **Fix:**
  `store.email_health_by_domain` agora conta `hard_bounced` (`status='bounced'`) e `soft_bounced`
  (`status='soft_bounced'`) em FILTERs **separados**; `bounce_rate`=**hard-only** (é o que
  `cold_alert.flag_high_bounce` usa p/ pausar), `soft_bounce_rate`=informativo, `bounced` (=hard+soft)
  mantido por compat. `flag_high_bounce` lê `hard_bounced` e **loga hard/soft separados por remetente**
  (`[alert] sender {dom}: hard=X% soft=Y% … → PAUSED|ok`). Ex.: perfil.klarim.net 1,33% hard + 5,61%
  soft = 6,94% combinado → com hard-only fica **ATIVO** (antes pausava injustamente). `get_email_health`
  MCP/`/system/email-health` propagam os campos separados. O safety net GLOBAL do KL-24
  (`_check_bounce_health`→`email_health()`, all-time 8%) tem query **própria** que já contava só hard —
  inalterado. +10 testes offline (`test_kl108_hard_soft_bounce.py` +6, `test_kl91_cold_alert.py` +4).
  **Pós-deploy manual na VM:** remover `ALERT_SENDER_MAX_BOUNCE_RATE=12` do `.env` (default 5% hard-only
  já mantém perfil ativo e pausa alertas/aviso). Relatório: `claude/reports/KL-108_hard_soft_bounce.md`.
- **KL-110** — Verificação de e-mail pré-envio (local + API Reoon) ✅. Ataca a CAUSA do bounce (6-8%
  hard): e-mails ruins entravam na fila sem validar deliverability; o KL-108 só reagia depois.
  **`notifier/email_verifier.py`** (fail-open, testável): **Camada 0 local** (`verify_local`: sintaxe
  via email-validator c/ fallback regex · descartáveis reusando `api.disposable_emails` · MX via
  dnspython c/ cache Redis 24h/domínio · flag role-based) + **Camada 1 Reoon** (`verify_reoon`/
  `verify_api`: modo `quick`/`power`, semáforo 5, fallback `unknown`). `verify_email` = pipeline
  cache→C0→C1 (cache SHA-256 do e-mail, 60d/7d; cache de domínio catch-all). `is_safe_to_send`
  (invalid/disabled/disposable/spamtrap→nunca; **`unknown`→nunca [KL-128]**; catch_all/inbox_full→só
  score>`ALERT_UNSAFE_SCORE_GATE` [default 20, KL-122]; safe/valid/
  role→sim). Integrações: (1) **extração** (`discovery/contact.py::_is_junk` descarta descartável — o
  MX já era filtrado no `extract_email`); (2) **alert worker** (`_verify_and_filter`, após lead
  scoring/antes do envio: verifica ≤`EMAIL_VERIFY_MAX_PER_CYCLE`=60/ciclo os melhores leads,
  blocklista+descarta os ruins, `is_safe_to_send` gate; **no-op sem `REOON_API_KEY`**); (3) **lead
  scoring** (KL-85: catch_all -10, unknown -5, role -15 sem dobrar prefixo). 4 colunas em `targets`
  (email_verified/email_verify_status/email_verified_at/email_is_role_based). `GET /system/email-
  verification-stats` + MCP `get_email_verification_stats` (+ saldo Reoon). Limpeza retroativa:
  `scripts/cleanup_email_backlog.py` (Fase 0 local custo-zero + Fase 1 bulk Reoon, `--dry-run`/
  `--local-only`/`--api-limit`). **+41 testes.** Segurança: `REOON_API_KEY` só no `.env` (nunca em
  log/frontend), cache por hash SHA-256, semáforo 5, fail-open. **Deploy:** a verificação Power ativa
  só quando o dono configurar `REOON_API_KEY` na VM; rodar então o cleanup e, após bounce hard <5% em
  7d, remover o `ALERT_SENDER_MAX_BOUNCE_RATE` emergencial. Relatório: `claude/reports/KL-110_email_verification.md`.

- **KL-26** — Cobertura de testes transversais (cross-módulo, **zero mudança em código de produção**) ✅.
  6 conjuntos, **+100 backend + 12 frontend**: **`tests/test_e2e_flows.py`** (fluxos e2e — dono
  verificado→perfil→selo, técnico monitora sem editar, unsubscribe completo→blocklist→worker pula,
  prontidão de cold alert, ciclo de pagamento PIX); **`tests/test_multi_tenant.py`** (IDOR bidirecional
  em todos os `/account/sites/{id}/*` → 404, escalação vertical user→/admin → 401, vazamento de dados,
  mass assignment `extra='ignore'`); **`tests/test_score_regression.py`** (score/semáforo determinístico
  via `compute_score` + fixtures de `CheckResult`; guarda de mudança de peso/threshold — alerta
  intencional); **`tests/test_scanner_edge_cases.py`** (timeout/redirect/conn-error→INCONCLUSO,
  `content_guard`, gate de acessibilidade KL-94, parser robusto, 1 check ruim não derruba o scan);
  **`tests/test_email_pipeline.py`** (circuit breaker hard/soft KL-108, verificação→decisão KL-110,
  List-Unsubscribe KL-102, rotação KL-91, bounce webhook→blocklist→`_validate_batch`);
  **`web/src/lib/scanView.test.js`** (+12: edge cases de `viewFlags`/`scoreHeadline`/`getCategoryStatus`
  + mapeamento dos 3 estados do CTA). **Achado (não-bug):** o `conftest` não resetava
  `_account_cfg_hits` (`_cfg_rate_limit`, 10/60s/user) → 429 espúrio entre testes que reusam o
  mesmo user_id (latente, exposto pelos transversais); corrigido no `conftest` (test-infra). **Nota:**
  o frontend do KL-26 estende `web/src/lib/scanView.test.js` (o caminho `web/src/__tests__/` da spec não
  existe); sector-pills/stats-bar do KL-103 são DOM-only (`landing-stats.js`, sem função pura) — não
  testáveis em `node --test`. Relatório: `claude/reports/KL-26_cobertura_testes.md`.
- **KL-122** — Gate de envio `unknown`/`catch_all` configurável + configs operacionais documentadas ✅.
  Commita o patch aplicado direto em produção em 27/07/2026: o gate de `is_safe_to_send` para status de
  deliverability INCERTA (`unknown`/`catch_all`/`inbox_full`) caiu de `lead_score>50` (KL-110) → **>20**.
  O 50 bloqueava ~3.895 e-mails elegíveis (2.757 `unknown` + 1.138 `catch_all`), muitos de provedores BR
  legítimos (Locaweb/Hostinger/UOL) que não respondem ao SMTP check da Reoon. Agora **configurável por
  env** `ALERT_UNSAFE_SCORE_GATE` (default 20, lido a cada chamada → ajuste sem deploy; fail-safe p/ valor
  inválido). `safe/invalid/disabled/disposable/spamtrap/role` **inalterados** (só o branch incerto mudou).
  `docs/DEPLOY.md` ganhou a seção "Valores operacionais atuais" (ALERT_DAILY_LIMIT=500, ALERT_SENDER_DAILY_
  LIMIT=500, ALERT_SENDER_MAX_BOUNCE_RATE=10, ALERT_UNSAFE_SCORE_GATE=20, PROFILE_VIEW_DAILY_LIMIT=500 — o
  que faz / onde é lido / default / quando ajustar). Testes do KL-110 atualizados p/ o novo default (>20,
  não >=) + env var. Relatório: `claude/reports/KL-122_gate_configuravel.md`.
- **KL-123** — Vigílias expandíveis: card clicável com dados contextuais, ações e orientação ✅. Os cards
  de vigília (`MonitoringSection.jsx`) mostravam só label + status; agora **expandem** com o detalhe que
  antes só existia no e-mail de alerta. **3 endpoints** (nível ≥1 + posse via `_owned_site`):
  `GET /account/sites/{id}/vigilias/{tipo}/details` (tipo inválido → 404), `POST .../phishing/dismiss/
  {alert_id}` ("não é ameaça" → `typosquat_alerts.dismissed=true`, escopado por id+target+user → 404 se
  não é da conta), `POST .../{tipo}/acknowledge` (grava `acknowledged_at` no `last_data` → some o badge).
  **Arquitetura testável:** derivação PURA em **`api/vigilia_details.py`** (`build_<tipo>` por tipo →
  `{status,summary,data,guidance,actions,pending_count}`, reusa `check_num`/`norm_status`+`RISK_MESSAGES`);
  o orquestrador `_build_vigilia_details` em `api/main.py` faz as queries (reusa `last_data` da vigília +
  `get_recent_scans_with_checks` + `get_site_typosquat_alerts` + `get_site_profile.certificate_authority`).
  **Dados por tipo:** ssl (issuer/validade/dias), domain (expiração), score (delta + **checks que mudaram**
  PASS↔FAIL entre 2 scans + `score_history`), email (SPF/DKIM/DMARC acessível), reputation (blacklists),
  uptime (código/tempo/falhas), changes (snapshot), phishing (lista de `typosquat_alerts` com ação por
  domínio). **Linguagem acessível** (regra do card): NADA de OWASP/CWE/header raw — orientação prática.
  4 store methods novos (`get_site_typosquat_alerts`/`dismiss_typosquat_alert`/`get_site_vigilia_alerts`/
  `acknowledge_vigilia`; SQL espelha padrões já validados: FILTER, `jsonb_set`+`to_jsonb`). O `status` do
  SSL/domínio **espelha o worker de vigília** (crítico SSL só ≤1 dia) p/ o detalhe não "virar vermelho" ao
  expandir um card amarelo. Front: **`web/src/components/dashboard-v2/VigiliaDetail.jsx`** (lazy-load no 1º
  expand, cacheia no state, badge de `pending_count`, dismiss **otimista** sem reload, acknowledge, mobile
  ≥44px, CSS spinner) + lógica pura **`web/src/lib/vigiliaDetail.js`** (`statusMeta`/`showBadge`/`applyDismiss`/
  `emailStateLabel`, 7 testes `node --test`). KL-57: eventos `vigilia_expand`/`vigilia_dismiss`/
  `vigilia_action_click` no `_KNOWN_EVENTS`. **+20 backend + 7 node**. Relatório:
  `claude/reports/KL-123_vigilias_expansiveis.md`.
- **KL-124** — CI/CD: `--force-recreate` (escopado) + rollback automático no `deploy/deploy.sh` ✅.
  O `up -d` sem `--force-recreate` só recria containers cuja **imagem** mudou; o layer cache do Docker
  (COPY . . com checksums iguais) podia não detectar mudança em `.py` e manter o container antigo
  rodando código velho (incidente do KL-123: código novo na VM confirmado por `git log`, containers
  antigos — precisou de `--force-recreate` manual). **Fix:** (1) o recreate agora é `docker compose up
  -d --force-recreate --no-deps api astro web worker discovery` (precedido de um `up -d --remove-orphans`
  que garante db/redis no ar) — **escopado aos 5 apps** para NÃO reiniciar postgres/redis a cada deploy
  (decisão do dono: preserva "zero downtime na camada de dados"; a spec pedia `--force-recreate` cru, que
  recriaria TUDO incl. db/redis). (2) **Rollback automático:** guarda `PREV_COMMIT=$(git rev-parse HEAD)`
  antes do pull; se o health check (API `/health` ou Astro `/`) falhar, `git checkout $PREV_COMMIT` +
  rebuild + recreate dos apps + `exit 1` (função `rollback()`). Após rollback o repo fica em **HEAD
  destacado** no PREV_COMMIT — o próximo deploy de CI reavança (`git pull --ff-only`) quando o fix chegar.
  (3) Log final `Deploy OK: commit <sha> em <ts>`. `deploy.sh` continua válido p/ deploy manual
  (`sudo bash deploy/deploy.sh`). `docs/DEPLOY.md` §2/§3 atualizados. **Validação do pipeline (4 jobs +
  --force-recreate nos logs + health) = pendente de push.** Relatório: `claude/reports/KL-124_deploy_force_recreate_rollback.md`.
- **KL-125** — Reverificação Power dos `unknown` + `email_verify_source` + bloqueio definitivo ✅.
  55 de 86 bounces (64%) em 3 dias vieram de e-mails `unknown` da **Bulk API** (menos precisa p/
  servidores BR — reverificados via Power, muitos são `disabled`). O gate do KL-122 (`unknown` envia se
  score>20) tratava `unknown` como "incerto mas talvez válido"; na prática `unknown` = alto risco de
  bounce. **Fix:** (1) `is_safe_to_send` — **`unknown` NUNCA envia** (independente do score); só
  `catch_all`/`inbox_full` seguem o gate por score (separados do `unknown`). (2) Coluna
  `targets.email_verify_source` (`power`/`quick`/`bulk`/`local`) registra a precisão da fonte;
  `update_target_email_verification` ganhou `source` (COALESCE — não sobrescreve com NULL). (3)
  `_verify_and_filter` reescrito: **Regra 1** — `unknown` de fonte não-power é **reverificado via Power**
  (resolveu→usa; block→blocklist+descarta; `unknown` 2×→não envia, NÃO blocklist, grava `source=power`);
  **Regra 2** — `unknown`/`source=power` → skip (não regasta crédito); **Regra 3** — demais status seguem
  o fluxo (fresh→cache, senão Power). Resultado `fallback` (Reoon fora) NÃO é persistido (não condena o
  alvo → retry). `unknown` além do teto de verificação é pulado (volta ao topo e é reverificado). Sem
  `REOON_API_KEY` a reverificação é no-op, mas um `unknown` conhecido ainda não é enviado. (4) Cleanup:
  Fase 0 → `source=local`, Fase 1 → `source=bulk`. (5) Stats ganham `by_source`. (6) KL-57: contadores de
  conversão `reverified_safe`/`reverified_blocked`/`reverified_unknown` no log/stats do ciclo (calibra a
  confiança na Bulk). **`safe`/`catch_all`/`role`/`invalid`/`disabled`/`disposable`/`spamtrap` inalterados.**
  **+8 backend** (`test_kl125_unknown_reverify.py`) + testes do KL-110/pipeline atualizados. **Fix
  emergencial 28/07 (já aplicado na VM):** 3.703 unknowns resetados + cache limpo; o worker reverifica via
  Power. Relatório: `claude/reports/KL-125_unknown_reverify.md`. **⚠️ SUPERADO pelo KL-127** — a premissa
  "unknown=ruim, bloquear" estava errada p/ o mercado BR e zerou os alertas; o KL-127 volta o `unknown` ao
  gate de score e remove a reverificação/Regra 2 (código simplificado).
- **KL-127** — Solução DEFINITIVA do pipeline de verificação de e-mail ✅. O pipeline de alertas travou
  **4×** desde o KL-110; a regra do KL-125 (`unknown` NUNCA envia) matou 100% dos alertas — `unknown` no
  mercado BR = "servidor não respondeu ao SMTP check" (Locaweb/Hostinger/UOL/Titan), **incerto e não ruim**
  (dados: Power safe/role **0%** bounce, catch_all 2,9%, unknown ~5-8% — mas bloquear tudo = zero alertas).
  Patches manuais nos containers (`if False`/`# DESABILITADO`) tinham divergido `api` de `discovery`. **Fix
  (zero código morto):** (1) `is_safe_to_send` — regra ÚNICA: safe/valid/role→envia; block-statuses→nunca;
  **`unknown`/`catch_all`/`inbox_full`→gate `> ALERT_UNSAFE_SCORE_GATE`** (default 20). (2) `_verify_and_filter`
  reescrito e SIMPLIFICADO: removidas a Regra 2 (unknown/power skip), a reverificação de `unknown` e o
  "unknown 2× → drop" do KL-125 — decisão única (fresco→cache, senão Power; block→blocklist+descarta; senão
  gate). `rest_kept` = já-verificados (`email_verified`, status não-vazio; `unknown` permitido). **Sem
  verificação → não envia**; `fallback` de infra não persiste nem envia; modo degradado sem key (já-verif
  gated, não-verif passa). (3) **Log estruturado por e-mail** (`logging`, mascarado): `status=… source=…
  score=… gate=… → SENT|BLOCKED|SKIPPED_GATE|SKIPPED_UNVERIFIED`. (4) `tests/test_kl127_pipeline_integration.py`
  (+7: mix de 200 → 170 enviados, tudo-unknown-score>20 → 200, tudo-unknown-score<20 → 0, 100 safe+100 disabled
  → 100, boundary `>20`, sem-verificação não envia, **guard anti `if False`**); testes KL-110/pipeline
  atualizados p/ o gate; `test_kl125_unknown_reverify.py` removido. (5) Docker: `api`/`worker`/`discovery`
  usam a MESMA imagem (`build: .`) → o deploy com `--force-recreate` (KL-124) garante código uniforme;
  **nunca** editar arquivo via `docker exec` (causou o incidente) — doc em `docs/ARCHITECTURE.md`. **Validação
  pós-deploy:** `diff` de `alert_worker.py`/`email_verifier.py` entre containers = **vazio**; `grep -c "if
  False"` = 0; alertas voltam a sair (sent_today cresce); log mostra a decisão por e-mail. Relatório:
  `claude/reports/KL-127_pipeline_definitivo.md`. **⚠️ Ajustado pelo KL-128** (o `unknown`→gate do KL-127
  fez o bounce voltar a >10%).
- **KL-128** — Regra DEFINITIVA de validação de e-mail + fix do deploy que não propagou ✅. **(A) Causa do
  deploy travado:** os commits `49e5286` (bloquear `unknown`) + `e1a8626` (rebaixar safe+catch_all) foram
  pushados a `origin/main`, mas o **job `test` do CI falhou** — o código mudou p/ `unknown`=blocked mas os
  testes do KL-127 ainda esperavam `unknown`=gate → o job `deploy` (`needs: [test,…]`) nunca rodou → a VM
  ficou 2 commits atrás (KL-127 `94aec55`). **(B) Regra definitiva:** `is_safe_to_send` — **`unknown` NUNCA
  envia** (o gate de score não filtra `unknown`, que no BR é servidor sem SMTP-check → bounce ~5-8%, subiu a
  >10% no KL-127); `catch_all`/`inbox_full` seguem o gate (`>20`); safe/valid/role sempre. **`parse_reoon_
  response` rebaixa `safe`/`valid` + `is_catch_all` → `catch_all`** (num servidor catch-all o "safe" do Reoon
  não é confiável — ataca o bounce na origem). O worker: `unknown` barrado tanto no subset quanto no **`rest`**
  (agora aplica o mesmo gate pelo cache — antes o rest deixava `unknown` verificado passar); `_is_fresh` exige
  **status não-vazio** (verificado-sem-status não vira `safe` fantasma). **(C) Fix:** docstrings/comentários
  atualizados (eram do KL-127), **zero código morto** (`grep "if False"`=0, guard de teste), testes alinhados
  (`unknown`→False; +3 `parse_reoon` demote; +6 casos parametrizados de `_verify_and_filter`). **1889 pytest
  passed.** Validação pós-deploy: `diff`/md5 de `email_verifier.py`+`alert_worker.py` entre containers = vazio;
  `is_safe_to_send(unknown,100)` → `False`. Relatório: `claude/reports/KL-128_regra_definitiva_email.md`.
- **KL-129** — Prioriza a verificação dos NOVOS no subset + filtra unknowns + canário por domínio ✅.
  **Bug (alertas parados 3+h):** o cap de verificação (120/ciclo) era consumido pelos e-mails **já
  verificados** do cache (unknown/catch_all barrados pelo gate KL-128) → **0 vaga** p/ os `email_verified=
  false` → pipeline girava em falso (`eligible 200, from_cache 120, skipped_gate 120, sent 0, verified 0`).
  **Fix (`discovery/alert_worker.py::_verify_and_filter` reescrito):** particiona ANTES de montar o subset —
  **sendable** (já-verificado aprovado → envia direto, sem re-API) · **blocked_known** (já-verificado barrado
  → descarta **sem consumir vaga**) · **unverified** (`email_verified=false`/status vazio/TTL expirado →
  **prioridade** no `subset=unverified[:cap]` → Power NESTE ciclo; excedente `deferred`). Removido o conceito
  de `rest`. **Cap 120→200** (`EMAIL_VERIFY_MAX_PER_CYCLE`, default 200, editável ao vivo no painel via
  `_reload_settings`). **Domínio confiável (item 4, parcial):** `store.trusted_recipient_domains(domains,48h)`
  (envio 'sent'/'delivered' sem bounce/complaint em 48h, domínio de destinatário via `split_part(to_email)`);
  um `unknown` fresco de domínio confiável é rebaixado a `catch_all` (passa a valer o gate) — recupera volume
  dos mega-hosts BR (Locaweb/Hostinger retornam unknown no SMTP-check). Kill-switch
  `ALERT_TRUST_DOMAIN_DOWNGRADE=false` (lido a cada ciclo). **Canário ativo** (envio 1 + recheck 24h +
  blocklist por domínio + coluna `email_log.is_canary`) **deferido** p/ card futuro (permitido pelo card).
  Novas stats: `blocked_known`/`deferred`/`trust_downgraded`; log de ciclo `[alert] verify KL-129: …`. **+10
  backend** (`test_kl129_subset_priority.py`: prioridade dos novos, unknown não consome vaga, novo verificado
  e enviado no mesmo ciclo, 0 API se tudo unknown, deferimento, trust↓/gate, cap por env) + testes KL-127/128
  ajustados às novas stats. SQL validado no Postgres 16 da VM. **1899 pytest passed.** `docs/DEPLOY.md`
  atualizado. Relatório: `claude/reports/KL-129_prioriza_novos_subset.md`.
- **KL-130** — Exclui status TERMINAIS do pool de elegíveis + destrava 3.247 e-mails novos ✅. Mesmo com a
  partição do KL-129, o log dava `verified: 0 (API)`: o `get_eligible_targets_for_alert` (fetch 200, ordenado
  por `last_scan_at ASC`) trazia **173 `unknown`+`power`** velhos que enchiam o batch → a partição não via os
  NOVOS (3.247 `email_verified=false` presos; ex.: `bengazzi2012@hotmail.com`, target 70444, 0 alertas). A
  API Reoon E a key estavam OK — o bug era a QUERY. **Fix:** (1) `_ALERT_ELIGIBLE_WHERE` **exclui**
  `unknown`+`power` + block-statuses. ⚠️ **NULL-safe com `COALESCE(...,'')`** — sem ele o `NULL='unknown'` vira
  NULL e o `AND NOT(...)` excluía os 3.247 não-verificados (status NULL); **o 1º draft caiu de 3.444→92
  elegíveis** e foi pego validando no Postgres da VM (após COALESCE: 3.444→3.272, 0 unknown+power, bengazzi
  passa). (2) alvo verificado `unknown` via Power → **`sem_contato`** (`_verify_one`, sai do pool; NÃO
  blocklist) + `store.retire_unknown_power_targets` + `scripts/retire_unknown_power.py` (limpeza retroativa dos
  173). (3) log de partição `[alert] KL-130 partição: N sendable, N blocked_known, N unverified …` +
  contador `retired_unknown`. **Investigação (item 3):** a partição do KL-129 estava correta — o `verified:0`
  era 100% causado pela query. **+5 backend** (`test_kl130_exclude_terminals.py`: WHERE NULL-safe, worker
  aposenta unknown+power, safe/disabled não aposentam, método de retire). SQL validado no Postgres 16 da VM.
  **1904 pytest passed.** Relatório: `claude/reports/KL-130_exclui_terminais_pool.md`.
- **KL-131** — Sitemap dinâmico (index + sub-sitemaps) p/ 43k+ perfis, servido pelo FastAPI ✅. O sitemap
  Astro era um **único urlset** (33.232 URLs, SSR pesado por request, `Cache-Control` DUPLICADO — 3600 do
  Astro + 300 do nginx) e o `sitemap-index.xml`/`sitemap-0.xml` caíam no fallback SPA (HTML). Agora o
  **FastAPI** serve: `GET /sitemap.xml` (**sitemapindex** → static + sectors + N páginas de perfis, N =
  ceil(total/10k)), `/sitemap-static.xml`, `/sitemap-sectors.xml` (`/setor/{slug}`, exclui 'outro'),
  `/sitemap-profiles-{page}.xml` (≤10k perfis, `ORDER BY domain` p/ paginação estável por OFFSET). Cache
  **Redis 1h** (`sitemap:index`/`:sectors`/`:profiles:N`; não cacheia vazio), `Content-Type application/xml`,
  **UM só `Cache-Control`** (no nginx). Store: `count_visible_profiles` + `get_visible_profiles_for_sitemap`
  (mesma elegibilidade do `list_public_profile_domains`). **Nginx:** nova `location ~ ^/sitemap[...]\.xml$`
  → FastAPI **sem strip de prefixo** (como `/remover`) em http.conf + https.conf.template; `sitemap\.xml`
  **removido** da allowlist Astro; `web/src/pages/sitemap.xml.js` **deletado**. `robots.txt` ganhou
  `/dashboard/`, `/api/account/`, `/webhooks/`, `/remover`. `nginx -t` OK (http + https renderizado). **+7
  backend.** Relatório: `claude/reports/KL-131_132_sitemap_seo.md`.
- **KL-132** — SEO programático dos perfis (títulos, meta, Schema.org, internal linking) ✅. **`web/src/lib/
  seo.js`** (puro/testável): `profileTitle` → "**{empresa} é seguro? Score {score}/100 | Klarim**" (≤60,
  trunca o nome; capta buscas de reputação), `profileDescription` → score + **semáforo em texto**
  (Excelente/Atenção/Crítico) + "**48 pontos**" (≤155), `formatDomainName` (`lotusforme.com.br`→`Lotusforme`).
  Usados em `site/[domain].astro` (`fullTitleOverride`/`description`). **JSON-LD:** mantém Organization +
  WebSite (site-wide, Base.astro) + BreadcrumbList; **NÃO re-adiciona o Review em WebSite** (o Search Console
  reprovou em 17/07 — decisão do dono); páginas de setor ganham **`CollectionPage`** (tipo válido). **Internal
  linking** ("Outros sites do setor", KL-74) e **canonical** (Base.astro, `path`) já existiam. **+8 node**
  (`seo.test.js`). ⚠️ Validar no **Rich Results Test** pós-deploy. Relatório: `claude/reports/KL-131_132_sitemap_seo.md`.
- **KL-133** — Blog editorial (conteúdo no banco, publicação via MCP) ✅. Captura busca informacional
  ("meu site é seguro?") com o dado proprietário de 74k sites. **INFRA apenas** (o card pede 1 draft de
  teste, não artigos). **Tabela `blog_posts`** (`ensure_schema`): slug único, title/subtitle/content
  (markdown)/meta_description/og_image/category/tags[]/status(draft/published/archived)/author/
  data_snapshot(JSONB)/reading_time_min/published_at. Helpers puros `_blog_slugify` (sem acento, [a-z0-9-])
  + `_blog_reading_time` (ceil(palavras/200)). **Store:** create/update (publicar seta `published_at`;
  mudar conteúdo recalcula reading_time)/archive/get_by_id/get_by_slug(published_only)/list_published/
  list_all. **API** (`api/main.py`): público `GET /blog/posts` (paginado, sem corpo), `GET /blog/posts/{slug}`
  (404 se draft), `GET /blog/rss.xml` (RSS 2.0, 20 últimos); admin (JWT via prefixo `/admin`)
  `POST/PUT/DELETE /admin/blog/posts` (+`GET` lista) — rate limit 30/min público, 10/min admin.
  **`/sitemap-blog.xml`** no sitemapindex (KL-131). **MCP:** `mcp_server/tools/blog.py` (5 tools:
  create/update/list/get/archive; registradas no `__init__`) — **reconectar o MCP** pós-deploy p/ aparecerem.
  **Frontend Astro SSR:** `/blog` (listagem paginada) + `/blog/{slug}` (artigo com `web/src/lib/blog.js::
  renderMarkdown` = **marked + sanitize-html**, allowlist estrita → strip de `<script>`/`<iframe>`/`on*=`/
  `javascript:`; Schema.org **Article**, OG article, CTA de scan, sidebar por categoria, breadcrumb,
  canonical). **Nginx:** `blog` na allowlist Astro (http+https) + `location = /blog/rss.xml` → FastAPI
  (exato, vence a regex; `/blog/{slug}` vai ao Astro). Deps novas: `marked` + `sanitize-html` (SSR).
  Draft de teste: `scripts/seed_blog_draft.py` (idempotente, NÃO publica). **+14 backend + 10 node**;
  `nginx -t` OK, build OK. Relatório: `claude/reports/KL-133_blog.md`.
- **KL-136** — Saúde operacional do pipeline de alerta (funil travado: 200 elegíveis → 4 enviados,
  bounce 14%) ✅. **6 fixes:** **(1 P0) Lead scoring role penalty -15 → -5** (`discovery/alert_scoring.py`,
  env `ALERT_ROLE_PENALTY`, lido a cada chamada): no BR `contato@`/`vendas@`/`sac@` é o e-mail PADRÃO de
  PME, NÃO baixa qualidade — o -15 barrava a maioria dos leads action-zone (`contato@`: +10 corp +20
  action -15 = 15 < threshold 20 → rejeitado; com -5 = 25 > 20 → passa). Vale p/ os DOIS sinais de caixa
  de função (`role_based_prefix` por prefixo + `email_role_account` do status `role` da Reoon) via
  `_role_penalty()` — MESMA penalidade, nunca DOBRAM (o 2º só entra se o prefixo não penalizou; sem
  duplicação). **(2 P0) Gate SEPARADO por status** (`notifier/email_verifier.py::is_safe_to_send`): o
  `catch_all` ganhou gate PRÓPRIO `ALERT_CATCH_ALL_SCORE_GATE` (default **30**, `>`) — servidor catch-all
  aceita tudo no SMTP mas a caixa pode não existir → respondia por ~37% dos bounces; o gate 20 herdado do
  `inbox_full` era permissivo demais. `inbox_full` segue `ALERT_UNSAFE_SCORE_GATE` (20). `unknown` continua
  SEMPRE bloqueado (KL-128); safe/valid/role sempre enviam. **(3 P1) Breakdown de `blocked_known`**
  (`discovery/alert_worker.py`): coleta o status EFETIVO de cada já-barrado e loga
  `[alert] blocked_known breakdown: {catch_all: N, disabled: M, …}` (revela o que polui o fetch). A query
  `_ALERT_ELIGIBLE_WHERE` já exclui todos os terminais (unknown+power, disabled/invalid/disposable/spamtrap);
  a partição do KL-129 já filtra `blocked_known` ANTES do subset (não consomem vaga de verificação) —
  confirmado, sem regressão. **(4 P0) Fail-safe de saldo Reoon** (`email_verifier` + `alert_worker` +
  `api/main.py`): se `REOON_API_KEY` existe mas o saldo está ESGOTADO (0/negativo), o worker NÃO verifica
  novas caixas — **defere TODAS as não-verificadas** (`cap=0`) em vez do fail-open do KL-110 que enviava
  SEM verificar (causa dos 12 bounces "sem verificação"). Saldo `None` (ilegível) = fail-open (não bloqueia);
  só consulta o saldo se há caixas novas a verificar (cache Redis 1h `reoon:balance`, compartilhada API↔worker).
  `GET /system/status` ganhou bloco **`email_verification`** (`reoon_balance` + `reoon_balance_warning`
  [<1000 OU None] + `unverified_count` + `verified/deferred_last_cycle` + `reoon_exhausted`). **Nota (não-bug):**
  `by_source` só mostra `power` porque os 16k unverified têm `email_verify_source=NULL` (campo do KL-125 nunca
  backfilled) — ausência de backfill, não bug. **(5 P1) Diagnóstico de re-scan** (`store.rescan_diagnostics`
  + `rescan_worker`): quando o ciclo acha `eligible: 0`, loga o funil `engajados → com_email → elegíveis →
  recentes_demais` (revela se o problema é a janela `RESCAN_AGE_DAYS`=30 ou o pool). Critério real de
  elegibilidade (documentado): `status IN ('scanned','alerted') AND contact_email IS NOT NULL AND last_scan_at
  < NOW() - N days`. **(6 P2) Divergências de métrica:** `count_proactive_emails_this_month` com boundary
  explicitamente **UTC** (`date_trunc('month', NOW() AT TIME ZONE 'UTC')` — as colunas `sent_at`/`rescanned_at`
  são `TIMESTAMP` naive-UTC). Fontes autoritativas documentadas abaixo. **+23 testes** (`test_kl136_
  operational_health.py`) + KL-85/110/127/129/130 atualizados aos novos defaults. **1956 pytest passed.**
  Relatório: `claude/reports/KL-136_saude_operacional.md`.

  **Fontes autoritativas de métrica (KL-95 + KL-136):** **Contas criadas** = `COUNT(*) FROM users` no período
  (server-side, KL-95 — NÃO o funil do tracker.js, inflado por pre-fetch; server_metrics é autoritativo).
  **Scans (Analytics/dashboard)** = `COUNT(*) FROM scans WHERE source IS DISTINCT FROM 'discovery'` (MANUAIS,
  KL-95); **scans (`/system/status`)** = `scan_today_stats` = TODOS os scans do dia (`scanned_at >= hoje`, incl.
  o worker discovery) — medem coisas diferentes por design (a divergência dashboard×system_status é esperada).
  **`sent_month`** (`count_proactive_emails_this_month`, cota mensal) = PROATIVO (alert_log + rescan_log),
  mês-**calendário UTC**; **`email_metrics.sent_week`** = `email_log`, TODOS os tipos, 7 dias móveis — no dia 1
  do mês `sent_month` < `sent_week` é ESPERADO (fontes/janelas diferentes, NÃO bug).
- **KL-137** — Simplificação RADICAL do pipeline de e-mail (reverte a complexidade acumulada nos
  KL-108..KL-136) ✅. O pipeline consumiu 10 cards e piorou (bounce oscilando, volume 4-400/dia); os
  e-mails sem link geravam ~7 visitas/semana. **(P1) Link no e-mail** (mantendo **text/plain**, NÃO
  HTML — decisão 02/08): as 3 variantes cold (`notifier/cold_alert.py::report_link`/`_report_link_block`)
  e o `profile_view` (`email_client.build_profile_view_text`) ganham UM link ao perfil do site
  (`klarim.net/site/{domain}?utm_source=alerta|profile_view&utm_medium=email`, só `source`+`medium`).
  **(P2) `is_safe_to_send` BINÁRIA** (`notifier/email_verifier.py`): `status in SENDABLE_STATUSES`
  (`{safe,valid,role}`) → envia; **todo o resto NÃO** (catch_all/unknown/inbox_full/block). `lead_score`
  fica na assinatura por compat mas é **ignorado**. **(P3) Lead scoring só ORDENA** (`alert_scoring.py`
  + `alert_worker._apply_alert_scoring`): removido o filtro por threshold (`skipped_low_quality`) — todo
  e-mail sendable é enviado, o score define só a ORDEM (maior primeiro; excedente do cap → próximo ciclo).
  Removidas as penalidades de deliverability (`catch_all` -10, `unknown` -5); mantida a de `role` (-5,
  `ALERT_ROLE_PENALTY`) e a de bounce-domínio (-40). **(P4) Limpeza:** removidos `_unsafe_score_gate`/
  `_catch_all_gate` (+ `ALERT_UNSAFE_SCORE_GATE`/`ALERT_CATCH_ALL_SCORE_GATE`), `trusted_recipient_domains`
  + trust-downgrade (`ALERT_TRUST_DOMAIN_DOWNGRADE`), `ALERT_SCORE_THRESHOLD`, o "aposentar unknown→
  sem_contato" em ciclo, e os counters `skipped_low_quality`/`skipped_gate`/`blocked_known`/
  `trust_downgraded`/`retired_unknown`. **`_verify_and_filter` reescrito** (~50 linhas de condicionais →
  regra binária): particiona frescos (`from_cache`) vs não-verificados → verifica via Power até o cap
  (`EMAIL_VERIFY_MAX_PER_CYCLE`; excedente `deferred`) → aplica a regra binária → `sendable`/`blocked`.
  **MANTIDOS:** circuit breaker hard-bounce (KL-108), verificação Reoon Power (decisão binária), blocklist
  (invalid/disabled/disposable/spamtrap), List-Unsubscribe (KL-102), rotação de senders (KL-91), cache de
  verificação, fail-safe de saldo Reoon (KL-136: saldo 0 → defere), o SQL `_ALERT_ELIGIBLE_WHERE` (que já
  exclui unknown+power) e `retire_unknown_power_targets` (limpeza retroativa via script). **Testes:**
  `test_kl91`/`test_kl101`/`test_alert_plain_text` (link presente, continua text/plain), `test_kl110`/`127`/
  `129`/`130`/`136` reescritos p/ a regra binária, `test_kl85`/`test_alert_worker` (scoring não filtra).
  **1948 pytest passed.** **Pós-deploy:** apagar `ALERT_UNSAFE_SCORE_GATE`/`ALERT_CATCH_ALL_SCORE_GATE`/
  `ALERT_TRUST_DOMAIN_DOWNGRADE` do `.env` da VM (ignoradas, mas confundem). Relatório:
  `claude/reports/KL-137_simplificacao_pipeline.md`.
- **KL-145** — Desacoplar o Reoon do envio: 3 filtros (sintaxe + MX + blocklist) ✅. O Reoon consumiu
  10 cards e ~5.000 créditos e entregava 2-8 envios/dia (bounce 4,4%) — classificava ~97% dos servidores
  BR como `unknown` (inútil como filtro) e a regra binária por-status do KL-137 barrava quase tudo. **A
  decisão de envio voltou a ser LOCAL e barata:** `notifier/email_verifier.py::is_safe_to_send(email,
  redis, store)` = **3 filtros** — (1) `_is_valid_syntax` (Camada 0, email-validator/regex), (2)
  `_email_has_mx` (Camada 0, DNS MX com cache Redis 24h/domínio, **fail-open**: só `no_mx` definitivo
  rejeita), (3) `_is_blocklisted` (`store.is_email_blocked`, a blocklist alimentada pelo webhook de
  bounce). Tudo que passa nos 3 → ENVIA; o **status Reoon e `email_verified` NÃO decidem mais**.
  `discovery/alert_worker.py::_verify_and_filter` reescrito (removidos: partição sendable/unverified,
  cap de verificação, chamada à API no envio, `_reoon_balance`, `email_verify_max`/`email_verify_enabled`/
  `email_verify_ttl_days`, gates de score, trust-downgrade e o `email_verified`/`email_verify_status`
  como condição). Novo stats do ciclo: `eligible/valid_syntax/has_mx/not_blocklisted/blocked_syntax/
  blocked_mx/blocked_blocklist/errors` (log `[alert] KL-145: N eligible → N syntax → N MX → N not
  blocklisted → N sendable`). O MX (filtro 2) respeita `ALERT_VALIDATE_MX` (off em dev/testes; o
  `_validate_batch` já cobre MX/blocklist com self-heal). **`_ALERT_ELIGIBLE_WHERE`** (store) perdeu os
  filtros de `email_verify_status`/`email_verify_source` (KL-128/130) — a blocklist (tabela dedicada) faz
  o trabalho. **`/system/status.email_verification`** agora expõe o funil `send_filter` + o saldo Reoon do
  enriquecimento em background. **MANTIDOS:** Reoon no `email_verifier` (verify_reoon/verify_local/cache,
  usados por `scripts/cleanup_email_backlog.py` + saldo no status — enriquecimento em background, NUNCA no
  envio), circuit breaker hard-bounce (KL-108), blocklist + webhook de bounce, lead scoring (só ORDENA,
  KL-137), link no e-mail (KL-137/138), List-Unsubscribe (KL-102), rotação de senders (KL-91),
  `retire_unknown_power_targets` (limpeza retroativa via script). **Testes:** novo
  `test_kl145_three_filters.py`; `test_kl110`/`kl130`/`email_pipeline`/`kl136`/`e2e_flows`/`alert_worker`
  atualizados p/ os 3 filtros; `test_kl127`/`test_kl129` (subset-priority Reoon) removidos. **2028 pytest
  passed.** Relatório: `claude/reports/KL-145_desacopla_reoon.md`.
- **KL-146** — Priorizar e-mails pessoais sobre genéricos no lead scoring ✅. Dados de produção:
  `contato@` gera 66% dos bounces (8,7% de taxa) vs. e-mails pessoais (3,6%). A solução NÃO é filtrar
  (a regra de envio do KL-145 = sintaxe+MX+blocklist é **inalterada**) — é **REORDENAR**: pessoais
  primeiro, genéricos depois (a blocklist aprende com os bounces dos genéricos antes de enviar muitos).
  **1 arquivo:** `discovery/alert_scoring.py`. Novo **`_email_type_factor(email)`**: pessoal **+15** ·
  genérico neutro **0** (`comercial`/`vendas`/`suporte`/`info`/… + a UNIÃO com `ROLE_BASED_PREFIXES`,
  p/ `noreply`/`financeiro` nunca virarem +15) · medium-bounce (`atendimento`/`sac`) **-5** · high-bounce
  (`contato`) **-10**. **SUBSTITUI** a penalidade role-based do KL-136 (`_role_penalty`/`ALERT_ROLE_PENALTY`,
  **removidos** — não acumula). Integrado em `calculate_alert_score` (sinais `email_type_personal`/
  `email_type_generic`/`email_type_generic_medium_bounce`/`email_type_generic_high_bounce`); um prefixo
  que PARECE pessoal mas a Reoon confirmou `role` é rebaixado a 0 (`email_type_role_verified`, não premia
  como pessoa). Efeito na fila (mesmo domínio/score, action_zone): `joao@` 45 > `comercial@` 30 > `contato@`
  20 — o `_apply_alert_scoring` grava o score e o `run_cycle` ordena por score DESC. **Nada é bloqueado por
  tipo** (`is_safe_to_send` inalterada; volume total idêntico, só a ORDEM muda). **Testes:** novo
  `test_email_type_factor` (parametrizado) + `test_personal_ranks_above_generic_action_zone` +
  `test_run_cycle_personal_before_generic_real_scoring`; `test_kl85`/`kl110`/`kl136` atualizados (todo
  e-mail pessoal ganha +15). **2043 pytest passed.** Docs: `docs/DEPLOY.md` (`ALERT_ROLE_PENALTY`
  superada). Relatório: `claude/reports/KL-146_priorizar_pessoais.md`.
- **KL-138** — Hardening: remover exposição de endpoints + bloquear paths de exploit + redirect curto
  nos e-mails ✅ (varredura 02/08; bots já sondam `.env`). **Fix 1 (Alta):** `GET /` (que o nginx serve
  como `/api/`) devolvia sem auth o **mapa completo** da API (endpoints de pagamento/e-mail/webhook +
  `scanner_version`/`payments_enabled`/`email_enabled`/`dev_mode`) → agora só `{"name":"Klarim API",
  "status":"ok"}`. **Fix 2 (Média):** nginx bloqueia MAIS paths de exploit ANTES do fallback SPA (que
  devolvia 200+HTML → scanner intensifica) — novo `location ~*` em `http.conf` + `https.conf.template`
  (`wp-config|phpmyadmin|swagger|redoc|graphql|_debug|config\.(json|yml|php)|dump\.sql|database\.sql|
  xmlrpc\.php|cgi-bin|shell|eval-stdin|vendor/phpunit|actuator|api-docs|v[23]/api-docs` → 404), complementa
  os blocos já existentes (`.env`/`.git`/`.DS_Store`/`.htaccess`/`.htpasswd` caem em `location ~ /\.`;
  `wp-admin`/`phpinfo`/`server-status` já cobertos). `nginx -t` validado local (http + https renderizado).
  **Fix 3 — redirect curto `/a/{target_id}`:** `GET /a/{id}` (FastAPI, roteado pelo nginx `location ~ ^/a/`
  sem strip, como `/remover`) valida o id (inteiro→422, inexistente/descartado→404), registra o clique
  server-side e **302 p/ `/site/{domain}`**. **Segurança:** destino **FIXO** (o domínio vem de `targets`,
  NÃO de parâmetro de URL → **sem open redirect**), rate limit **30/min por IP** (`_redis_allow`, anti-
  enumeração), IP **mascarado /24** no log (LGPD, `mask_ip(ip,3)`), clique nunca derruba o redirect
  (try/except). Tabela nova **`email_clicks`** (`target_id`/`clicked_at`/`ip_masked`, 2 índices) +
  `store.get_target_domain`/`log_email_click`. Os 3 templates cold (`cold_alert.report_link(target_id)`
  → `build_cold_email(..., target_id=)`) + `profile_view` (`build_profile_view_text(domain, target_id)`)
  passaram a usar o link curto `/a/{target_id}` (**sem UTM** — o rastreio virou server-side, substitui o
  UTM do KL-137). **+8 backend** (`test_kl138_hardening.py`) + testes de e-mail atualizados; regex do nginx
  validado por Python (bloqueia exploit, não pega `/a/`/`/site/`/`/setores`/`/scan`/`/blog`). **1956 pytest
  passed.** Relatório: `claude/reports/KL-138_hardening.md`; achados em `docs/SECURITY.md`.
- **KL-141** — Security Gate: scanner de EXPOSIÇÃO/config pós-deploy (NÃO é DAST — não envia payload de
  ataque; verifica o que ficou exposto após o deploy). Módulo **novo e SEPARADO** `security_gate/`
  (portável, futuro pacote pip; NÃO dentro de `scanner/`). **Prompt 1/4 ✅** (de 4): engine + models +
  checks de exposição/headers/SSL. `run_all(url, timeout, checks, deploy_ts) → GateReport`
  (`engine.py`, só orquestra; **headers anti-cache em TODO request** + UA honesto `Klarim Security
  Gate/1.0`; check que estoura vira ERROR isolado). `models.py`: `Result`/`GateReport` (score 100−
  penalidades CRIT-20/HIGH-10/MED-5/LOW-2; `passed`=sem FAIL crítico → exit code do CI; counts) +
  `Config` (semente p/ Prompt 3). **exposure.py (novo):** 11 grupos (KL-139 checks 1-3,5-12) — HEAD
  primeiro (não baixa body — princípio KL-139), 200 no grupo → FAIL + `break`; `directory_listing` faz
  GET limitado (2000 chars, não armazenado) p/ distinguir listagem real de fallback de SPA. **headers.py
  + ssl.py REUSAM o scanner** (rule 2): ssl importa `scanner.tls_analyzer.get_tls_info`+`WEAK_PROTOCOLS`
  (reuso real do handshake); headers importa o threshold `HSTS_MAX_AGE_RECOMMENDED` mas valida local (os
  checks do scanner são coroutines acopladas ao próprio fetch — não há validador puro; o Gate usa 1
  response). **+41 testes** (`test_kl141_gate_engine.py`); **1997 pytest passed**. ⚠️ **Falso positivo de
  SPA:** rodando real contra `klarim.net` (score 65, passed=True, 7s) flagou `/adminer`/`/docs`/`/_profiler`
  /`/main.js.map` — o Astro/Vite devolve 200+HTML para paths fora do blocklist do nginx (KL-138). É RUÍDO
  (HIGH/MEDIUM, não bloqueia o `passed`, que só olha CRÍTICO); a correção adequada é o **allowlist do
  config YAML no Prompt 3** (ou estender o 404 do nginx). Prompt 1 relatório:
  `claude/reports/KL-141_p1_security_gate_engine.md`.
  **Prompt 2/4 ✅ — check de credenciais** (`security_gate/checks/credentials.py`; registrado no engine
  como `"credentials"`, no default order). **Regra inviolável:** o VALOR da credencial NUNCA é
  armazenado/logado/transmitido — o `Result` só tem tipo+localização(arquivo:linha)+severidade (teste
  dedicado falha se qualquer fragmento do segredo vazar no `detail`/`path`). Cobertura completa: ~50
  patterns fixos em 7 categorias (payment/cloud/baas_database/ai_ml/auth_identity/communication/generic
  — Stripe/AWS/Google/Azure/Supabase/Firebase/Mongo/Postgres/OpenAI/Anthropic/JWT/NextAuth/SendGrid/
  Slack/Twilio/GitHub/GitLab/npm/private-keys/…) + **entropia** (reforço: atribuição a variável de nome
  "de segredo" com valor entropia>4.5 e len>20 → MEDIUM; o gate de LHS-secreto evita flood em JS
  minificado). **Fontes:** HTML + **TODOS** os `<script src>` mesma-origem (sem limite, dedup) + crawl
  de até 9 páginas internas (10 no total); CDN de terceiro ignorado. **Anti-FP:** placeholders (YOUR_/
  xxx/changeme/…), `<code>`/`<pre>`/doc (só em HTML), valores curtos/vazios; `pk_test_`=LOW. **Dogfooding
  real (klarim.net): score 100, 0 findings, ~16s** (nenhum FP nos bundles minificados). **+29 testes**
  (`test_kl141_credentials.py`); os 2 testes de engine do P1 atualizados p/ 4 checks. Relatório:
  `claude/reports/KL-141_p2_credentials.md`.
  **Prompt 3/4 ✅ — CLI + config YAML + API security + formatters + allowlist.** CLI executável
  `scripts/security_gate.py <url> [--fail-on/--timeout/--checks/--config/--json/--quiet]` (exit 0 passou
  · 1 falhou [FAIL ≥ `--fail-on`, por RANK, não string] · 2 erro). Config `security_gate/config.py`
  (`GateConfig` + `load_config`: YAML → args da CLI; **`import yaml` lazy**, core sem dep; `pyyaml` no
  requirements) + **`security-gate.yml`** commitado (config da Klarim). Check **API security**
  (`api_security.py`): raiz `/api/` não lista endpoints (valida o KL-138) + `protected_endpoints`
  respondem 401/403 (200 sem auth → FAIL CRITICAL). Formatters `terminal.py` (terminal ícones/score/
  veredito, `--quiet` omite PASS; + `format_json`). Engine: `run_all(..., config)` passa `config` a
  TODOS os checks (assinatura `check(client,url,config=None)`); `api` no default order. **Falsos
  positivos de SPA resolvidos de fato:** o allowlist do card sozinho é whack-a-mole (o SPA 200 tudo);
  a solução real é o **Content-Type guard** (novo, HEAD-only) no `check_exposure` — recurso não-HTML
  (`.map`/`.sql`/`.json`/`.yml`/`.env`/`.config`…) que responde `text/html` = fallback de SPA → não é
  exposição (zero falso NEGATIVO; `.php`/`.axd` de fora pois phpinfo/elmah reais são HTML) + allowlist
  só p/ os HTML-capazes (painéis/UI/debug). **Dogfooding `python scripts/security_gate.py https://
  klarim.net`: score 100/100 🟢, 0 findings, ~16s.** **+35 testes** (`test_kl141_cli_config.py`); engine
  P1/P2 atualizados p/ `(client,url,config)` + 5 checks. Relatório: `claude/reports/KL-141_p3_cli_config_api.md`.
  **Prompt 4/4 ✅ (COMPLETO) — GitHub Actions + notificação.** Job **`security-gate`** no
  `.github/workflows/deploy.yml` (`needs:[deploy]`, `if:success()`): roda o Gate contra `klarim.net` LIVE
  **DEPOIS** do deploy → **NÃO bloqueia** (o site já está no ar); reprovar → job vermelho (exit 1/2 via
  `pipefail`) + e-mail, operador decide rollback (o Gate nunca reverte). `--json | tee gate-report.json`
  vira artifact; `pip install -r requirements.txt` (o Gate importa de `scanner/` → puxa dnspython/gcs/
  cryptography). **`scripts/security_gate_notify.py`** (e-mail Resend + webhook, `if:failure()`; fail-safe
  sem key → só avisa; nunca vaza o valor da credencial). Badge no README. ⚠️ **`RESEND_API_KEY` NÃO é
  secret do repo** (o deploy usa só o `.env` da VM) → o e-mail só envia quando o dono adicionar o secret;
  não bloqueia (o notify só roda em falha). **+11 testes** (`test_kl141_notify.py`). Relatório:
  `claude/reports/KL-141_p4_github_actions.md`. **KL-141 COMPLETO** (engine+5 checks+CLI+config+formatters+
  CI). **KL-139** (catálogo) coberto (exposição 1-3,5-12 + credenciais 4 + headers/ssl/api) — fecha junto.
- **KL-147** — Security Gate: detecção de SPA fallback por fingerprint (ETag / Content-Type+Content-Length) ✅.
  O Gate gerava falsos positivos massivos em SPAs que devolvem **200+index.html para QUALQUER path**
  (`sistema.igoove.com.br`: 0/100 com 14 findings, todos falsos exceto HSTS). O `_is_spa_fallback_nonhtml`
  (KL-141 P3) só pegava extensões não-HTML listadas — não cobria paths **sem extensão** (`/admin`,
  `/swagger`) nem extensões novas. **Solução — probe de controle:** ANTES dos checks, o engine
  (`security_gate/engine.py::_detect_spa_fallback`) faz **1 HEAD** num path aleatório que certamente não
  existe (`/_klarim_gate_probe_{uuid}`); se responde 200, captura o **fingerprint** (ETag + Content-Type +
  Content-Length) do index.html. Os checks `exposure` e `api` (spa-aware; headers/ssl/credentials não —
  sem paths a comparar) recebem o fingerprint e, para cada 200, `security_gate/utils.py::
  matches_spa_fingerprint` compara: **mesmo ETag** (ou mesmo CT+CL sem ETag) → fallback → PASS; diferente →
  exposição real → FAIL. **1 request extra por scan** (só se algum check spa-aware roda). Guard-chain do
  exposure: allowlist (KL-141 P3) → **fingerprint (KL-147)** → Content-Type nonhtml (KL-141 P3). O
  `api_security` marca o 200-que-casa-fallback como PASS ("fallback de SPA (não é endpoint real)").
  **Validação real (obrigatória):** klarim.net **100/100 🟢** (nginx 404 → sem fingerprint, inalterado) ·
  sistema.igoove.com.br **90/100 🟢** (era 0/100; agora só HSTS ausente — 14 falsos positivos eliminados) ·
  Traka Cloud Run **63/100 🟡** (404 → sem fingerprint, inalterado; findings são headers ausentes reais).
  **+20 testes** (`test_kl147_spa_fingerprint.py`); engine tests do KL-141 atualizados p/ a nova assinatura
  `(client,url,config,spa_fingerprint)` dos checks spa-aware. **2063 pytest passed.** Relatório:
  `claude/reports/KL-147_spa_fingerprint.md`.

Histórico completo (o que/porquê de cada peça) em **`docs/HISTORY.md`** e nos
relatórios em `claude/reports/`.
# KL-124 pipeline test: 2026-07-28T10:19:29Z
