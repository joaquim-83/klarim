# Klarim — Segurança

> Consolida as "Regras invioláveis" espalhadas pelo antigo `claude.md`, o hardening de
> auto-auditoria e as decisões das auditorias (`claude/reports/auditoria_seguranca_
> 2026-07-15.md`, `fix_security_hardening.md`, `fix_auditoria*.md`). Histórico íntegro em
> `docs/HISTORY.md`.

## 1. Postura legal — só varredura passiva

O Klarim é *Security Rating* / monitoramento de superfície de ataque. **NÃO é pentest.**

- ✅ **Faz:** `GET`/`HEAD` a URLs públicas, leitura de headers, certificados SSL
  públicos, consulta DNS pública, arquivos que o servidor entrega sem autenticação,
  APIs públicas de leitura (crt.sh, HIBP, Safe Browsing, RDAP, IBGE, BrasilAPI).
- ❌ **NUNCA:** payloads de injeção (SQLi/XSS), brute-force de credenciais, acesso a
  área autenticada, exploração de vulnerabilidade, extração de dados.
- **Rede:** timeout 10s/request; **rate limit 1 req/s por domínio** (centralizado em
  `checks/base.py` — não reimplemente); **User-Agent honesto** (não se passa por
  navegador, não se esconde). Um site que bloqueia o UA honesto **não pode ser
  crawleado** — é limitação, não bug (perfil fica esparso; loga o bloqueio).

Na dúvida, trate o alvo como site de terceiro que só autorizou olhar o que é público.

## 2. Regra de segurança de 2026-07-15 (inviolável)

- **Toda implementação ou fix deve incluir revisão de segurança.**
- **Nenhum endpoint, formulário ou fluxo de dados pode ficar sem proteção** (auth,
  validação, rate limit, sanitização).
- **Empresas de cibersegurança estão entre os alvos** e interagem ativamente com a
  plataforma — assuma que tudo será sondado.

## 3. Hardening da própria plataforma (auto-auditoria)

- **Docs da API off em produção:** `/docs`/`/redoc`/`/openapi.json` só existem com
  `KLARIM_DEV_MODE=true` (senão 404).
- **Rate limit no login:** `POST /auth/login` = 5 tentativas/min por IP (`X-Real-IP` do
  Nginx); 6ª → 429.
- **Rate limit do scan anônimo (KL-82):** `GET /scan/result` sem sessão = **5/h + 20/dia por
  IP**; estourou → 429 com CTA de conta. Conta logada é ilimitada. O resultado é **filtrado
  server-side por nível de acesso** — anonymous/unconfirmed **nunca** recebem evidência/detalhe
  de check no payload (corte no backend, não blur cosmético no front).
- **Validação de domínio antes do scan (2026-07-21):** `_valid_scan_domain` rejeita input que
  não é domínio real — o scanner aceitava qualquer string (ex.: `<script>alert(1)</script>`) e
  gerava score, **refletindo o payload** no corpo da página. Extrai o hostname (tira protocolo/
  path/query), valida por regex (labels `[a-z0-9-]`, TLD alfabético ≥2, ≤253, ASCII) → **400
  `{"error":"invalid_domain"}`** em `/scan/result` e `/scan/summary` **antes de escanear**
  (barreira real; o front também valida por UX e usa `safeScanDomain` para NUNCA exibir input
  cru — só o hostname limpo). Rejeita tags/aspas/espaços, sem TLD, IPs e domínios de 1 char.
- **IP real atrás do Cloudflare (KL-82):** `_client_ip` usa **`CF-Connecting-IP`** (o `X-Real-IP`
  do Nginx é `$remote_addr` = IP do **edge** do CF, não do visitante — tornava TODOS os rate
  limits por IP inefetivos). Ordem: CF-Connecting-IP → X-Real-IP → peer.
- **Firewall de origem (KL-82):** o `443` do origin (`34.135.194.208`) só aceita **ranges do
  Cloudflare** (`klarim-allow-cf-https` v4/v6; sem 0.0.0.0/0) — impede bater direto no IP e
  **forjar** o `CF-Connecting-IP` p/ escapar do rate limit. Porta `80` aberta (ACME + redirect).
  SSH (`22`) inalterado. Ranges do CF mudam raramente → atualizar as 2 regras se necessário.
- **Criação de conta (KL-82 S2 + KL-85):** `POST /account/signup` = **3/h & 5/dia por IP**
  (`CF-Connecting-IP`) + **blocklist de e-mails descartáveis** (`api/disposable_emails.py`, 400;
  só no signup, não afeta o scan anônimo). Conta nasce `email_confirmed=false`; confirma por
  **link** (token JWT-HMAC 30d, `typ=confirm`, idempotente — nunca logado). **Anti pre-fetch
  (2026-07-21):** o e-mail linka a **página** `/confirmado?token=` (não a API); a confirmação é
  **POST-only** (`POST /account/confirm`) — o usuário clica "Confirmar meu e-mail" (form submit).
  Servidores de e-mail (Gmail/Outlook/scanners) fazem **GET** dos links → o pre-fetch renderiza só
  o formulário, **nunca confirma** (só um humano que submete o POST confirma). O POST redireciona
  para `/confirmado?status=ok` (sem o token na URL). O `GET /account/confirm` (JSON) fica só por
  compat e não é linkado em lugar nenhum. Reenvio (`/account/resend-confirmation`) 3/h por conta.
  Contas não confirmadas +30d sem
  atividade (sem site monitorado e sem re-login) são **removidas** pelo worker `trial` (1x/dia).
- **Sessão do alerta / Fluxo 2 (KL-82 S3):** o link do alerta carrega um token **HMAC-SHA256**
  (`email|target_id|domain`, 30d) — infalsificável. `/alert-access` valida e emite um cookie
  `klarim_alert` (JWT-HMAC 24h, `typ=alert_session`, httponly/secure/samesite=lax) **escopado a
  1 site**: `/scan/result` só dá acesso completo se o domínio pedido bate o da sessão (senão cai
  p/ anonymous). `signup-from-alert` cria conta só com senha (e-mail do cookie, `source='hmac'`).
  `contact_email` **nunca em claro** — só hint mascarado. Tokens `typ`-isolados (um alert-access
  não vale como sessão nem como confirm/scan token). Rate limit: alert-access 30/h, signup 5/h/IP.
- **Descadastro one-click / `/remover` (KL-102):** token **HMAC-SHA256** `typ=unsubscribe`
  (`email|domain|sender`, base64url(json).hmac[:32], **comparação constant-time**) — propósito
  ISOLADO do `alert_session`/`confirm` (um não vale pelo outro). **SEM expiração** de propósito
  (um opt-out deve funcionar sempre). **Anti-enumeração:** token inválido/ausente → página genérica
  (GET 200, POST 400), **nunca** revela se o e-mail/domínio existe; um token válido só existe se a
  Klarim o gerou (para um alvo real que e-mailamos) → sem oráculo de existência. **Rate limit
  10/min/IP SÓ nos tokens inválidos** (anti brute-force) — um token VÁLIDO é idempotente e escopado
  ao próprio e-mail, então nunca é bloqueado: o one-click do Gmail vem de IP **compartilhado** do
  Google, e bloqueá-lo faria a pessoa marcar como spam (pior p/ reputação). Só nos 3 senders cold;
  o transacional (`klarim@klarim.net`) NÃO leva o header. Opt-out por resposta ("remover") em paralelo.
- **Endpoints públicos sensíveis (KL-93 — hardening).** A varredura achou o `POST /payment/create`
  criando cobrança PIX **real** sem auth/validação/rate limit. Política por endpoint (todos por
  `CF-Connecting-IP`; `_redis_allow` com fallback in-memory, exceto onde marcado `_rl_ok`):

  | Endpoint | Proteção |
  |---|---|
  | `POST /payment/create` | e-mail obrigatório (422) · **3/h por IP** (429) · domínio precisa existir na base **com scan** (`_domain_scanned` → 404). Só então cria a cobrança (inclui o modo demo). |
  | `POST /notify/profile-view` | **1/h por (IP, domínio)** (`_rl_ok`, 429) + o teto interno de 1/domínio/24h do `_profile_view_notify` (defesa em profundidade). |
  | `POST /monitoring/offer` | **3/h por IP** (era 10) + **404** se o domínio não existe + authz (scan completo comprovado) + score-100 (já existentes). |
  | `GET /monitoring/sites` | **JWT admin** (401 sem token) — deixou de ser público (a vitrine migrou p/ Astro/KL-74; só páginas Vite legadas consumiam). |
  | `GET /report/{executive,technical}` | **5/h por IP** compartilhado (`report_dl`, 429) — o PDF é público (paywall off) mas cada chamada dispara um `_safe_scan` full (caro) → anti-crawling. |
  | `GET /scan/result` | **Sem mudança** — não há param `tier` client-controlável; o nível vem só da sessão (`_access_level`) e o corte é server-side (`_filter_scan_result`, KL-82/89). Anonymous vê nome+status dos 48 checks (padrão de scanner passivo), nunca evidência/LGPD. |

  Cleanup: `scripts/cleanup_phantom_payments.py` (idempotente, `store.delete` por charge_id) remove
  as 2 cobranças fantasma criadas no teste de segurança.
- **Exposição de superfície + paths de exploit (KL-138 — hardening, varredura 02/08).** 2 achados
  (bots já sondavam `.env`: 8 views de 1 IP):
  - **(Alta) `GET /` (nginx serve como `/api/`) vazava o mapa da API** sem auth — lista de endpoints
    (pagamento/e-mail/webhook), `scanner_version`, `payments_enabled`, `email_enabled`, `dev_mode`.
    Corrigido: resposta mínima `{"name":"Klarim API","status":"ok"}` (nenhuma flag/versão/endpoint).
  - **(Média) Fallback SPA devolvia 200+HTML a QUALQUER path** → scanner interpreta como endpoint ativo
    e intensifica. Novo `location ~*` (http.conf + https.conf.template) devolve **404** a mais paths de
    exploit (`swagger`/`redoc`/`graphql`/`_debug`/`config.json`/`actuator`/`phpmyadmin`/`wp-config`/
    `cgi-bin`/`shell`/`xmlrpc.php`/`vendor/phpunit`/`api-docs`/`v[23]/api-docs`), complementando os blocos
    já existentes (dotfiles/`.php`/`wp-admin`/`phpinfo`…). Regex validado por `nginx -t` (http+https) e
    por teste unitário Python (não pega `/a/`/`/site/`/`/setores`/`/scan`/`/blog`).
  - **Redirect curto `/a/{target_id}` (novo endpoint, KL-138):** substitui o link direto dos e-mails.
    **Anti open-redirect:** o destino é FIXO (`/site/{domain}` do próprio alvo, domínio vindo de
    `targets` — NUNCA de parâmetro de URL). Rate limit **30/min/IP** (anti-enumeração). IP **mascarado
    /24** no log de cliques (`email_clicks`, LGPD — o completo nunca é gravado). `target_id` não-inteiro
    → 422; inexistente/descartado → 404. O log de clique roda em try/except (nunca derruba o redirect).
- **Anti stored-XSS no `/events`:** `_sanitize_str`/`_sanitize_metadata` removem tags e
  esquemas (`javascript:`/`data:`), limitam tamanho/profundidade. React escapa `{}` (sem
  `dangerouslySetInnerHTML`).
- **Inbox:** corpo de e-mail externo (não confiável) renderiza em `<iframe sandbox="">`
  + `srcDoc` — **NUNCA** `dangerouslySetInnerHTML` (evita roubo do JWT do operador).
- **Nginx bloqueia paths sensíveis:** dotfiles (`/.env`, `/.git`), extensões perigosas
  (`.php/.sql/.bak/.log/.yaml…`), paths de outros frameworks (`wp-admin`, `phpinfo`…) e
  diretórios suspeitos → 404. ACME (`/.well-known/acme-challenge/`) e `security.txt`
  usam `location ^~`/`=` para vencer o regex.
- **Security headers com `always`** (aparecem até em 4xx/5xx). ⚠️ Um `add_header` num
  `location` **quebra a herança** de todos os do `server` — **repita os de segurança**
  em cada `location` com `add_header` próprio (snippet `security_headers.conf`).
- **Resolver dinâmico no proxy** (`/api/`, `/mcp/`): evita o 502 quando o container `api`
  é recriado com IP novo. `resolver 127.0.0.11` + upstream em variável.
- **CSP:** público **estrito** (`script-src 'self'` + **5 hashes** SHA-256 dos scripts inline —
  ao mexer neles, recalcule os hashes); `/painel` **relaxado** (`unsafe-inline`, é noindex/
  operator-only). `style-src` mantém `unsafe-inline`. COOP `same-origin`, COEP `require-corp`,
  CORP `same-origin`, Permissions-Policy restritiva. **KL-92 P4:** Cloudflare Web Analytics
  (`beacon.min.js`, único script externo SEM SRI → travava o score 100) → **Google Analytics 4**:
  `script-src` inclui `www.googletagmanager.com` + o hash do init inline; `connect-src`/`img-src`
  liberam `*.google-analytics.com`. Check 13 (SRI) tem **allowlist** de CDN dinâmico
  (googletagmanager/google-analytics/cloudflareinsights) — SRI é inviável em bundle que o provedor
  atualiza sem aviso, então não conta como FAIL.
- **Anonimização LGPD do access_log (KL-92):** IP retido 90d; depois `anonymize_old_access_logs`
  trunca IPv4 → `/24` **e IPv6 → `/48`** (KL-92 P4). Pre-fetchers de e-mail (Gmail/Outlook/EOP,
  ranges `66.102`/`66.249`/`40.9x`/`104.47`, ou >20 domínios distintos/h) são classificados
  `email_prefetch` (não inflam visitantes).

## 4. Autenticação e autorização

- **Duas superfícies distintas, mesmo `JWT_SECRET`, `typ` diferente:**
  - **Admin/operador** — `POST /auth/login` (`{username,password}` do `.env`/hash bcrypt
    no banco), JWT Bearer `typ=admin`, TTL 24h. `_verify_token` exige `typ=admin`.
  - **Usuário** — `/account/*`, senha bcrypt, JWT `typ=user` em cookie `klarim_session`
    (HttpOnly, Secure, SameSite=Lax), TTL 30d. `verify_user_token` exige `typ=user`.
  - Um cookie de usuário **jamais** passa no middleware admin (o `typ` nunca é ignorado).
- **Middleware** protege os prefixos: `/targets`, `/scans`, `/alerts`, `/rescans`,
  `/email`, `/payments`, `/config`, `/discovery`, `/admin`, `/system`, `/analytics`,
  `/leads`, `/monitoring/admin`. Exceção pública: `POST /email/webhook` (token próprio).
- **MCP** — auth própria (OAuth 2.1/PKCE S256 obrigatório + `MCP_API_KEY` estático),
  fail-closed, constant-time; `redirect_uri` sempre validada; code one-time 60s; refresh
  rotacionado; rate limits. Fora do JWT admin.
- **IDOR-safe:** `/account/vigilia*` e TODO `/account/sites/{id}/*` filtram por `user_id`
  (ownership via `user_sites` → 404 se não for da conta). **Enforcement de plano é
  servidor-autoritativo** (403 no `POST /account/sites`, nunca só no frontend).
- Trocar a senha do admin ou rotacionar o token MCP **invalida os refresh tokens OAuth**.

### Auditoria da área logada (KL-107, 24/07) — 2 achados corrigidos
- **IDOR no `verify/check`:** `POST /account/sites/{id}/verify/check` era o ÚNICO
  `/account/sites/{id}/*` sem ownership check — devolvia `200 {no_pending}` para site de outro
  usuário (enumerava quais têm verificação de domínio pendente). **Fix:** `get_user_site` no início
  → **404** (constant-time via SQL, como os demais). `verify/start` já bloqueava.
- **Adição por terceiro (decisão de produto = permitir + avisar):** `POST /account/sites` deixa um
  não-dono monitorar (is_owner=false) um site com dono verificado — o modelo agência→técnico (KL-70)
  depende disso; **não se bloqueia**. Em vez disso, o **dono verificado é notificado** por e-mail
  transacional (`owner_notification`, texto puro, informativo, **sem link de ação**), com dedup
  1/dia/target. A notificação **só revela o e-mail** de quem adicionou (nunca id/plano/dados de
  conta); é fire-and-forget (falha não impede a adição). O não-dono nunca vê painel/dados/perfil.

### Níveis de conta (KL-99) — conta sem senha + gate progressivo

- **`users.account_level`** (eixo distinto do `access_level` do KL-82): **1** = sem senha ·
  **2** = com senha · **3** = dono verificado por controle de domínio. Contas legadas → 2
  (backfill). `password_hash` é **nullable** (nível 1 não tem senha).
- **Conta sem senha só nasce com prova de posse do e-mail:** Fluxo C (clique no link HMAC do
  alerta) ou Fluxo D (`signup-inline` + confirmação por e-mail POST-only) ou `/cadastrar` (só
  e-mail + confirmação). Rate limits: alert auto-create **5/h/IP**, signup-inline **3/h/IP**.
- **`@require_level(n)`** (server-authoritative, 403 `{error:insufficient_level, required_level,
  current_level}`): **nível ≥ 2** para `PUT /account/me`, `DELETE /account/sites/{id}`,
  `POST /account/technician/invite`, `POST /account/upgrade`; **nível ≥ 3** para
  `PUT /account/profile-confirm` (editar perfil público). O corpo do 403 só expõe o
  `current_level` do próprio caller.
- **`set-password`** (nível 1→2) recusa se já há senha (não sobrescreve conta alheia); a sessão
  já prova identidade → não pede senha atual.
- **Fluxo C — link do alerta NÃO cria conta nem loga (fix 2ª rodada):** o clique dá só uma
  **sessão de visualização** view-only (`alert_session`, 24h, escopada a 1 site) — não vira conta.
  A conta só nasce com **consentimento explícito** ("Sim, monitorar" → `POST /account/monitor-from-
  alert`, sem senha, nível 1). E-mail já com conta → `{existing_account}` (não auto-loga conta com
  senha). TTL do `alert_access` = 7 dias.
- **Magic link (KL-99, login sem senha):** `POST /account/magic-link` gera um token HMAC **TTL 1h**
  (`typ='magic'`), enviado ao e-mail; `GET /account/magic-access` valida → sessão real. Rate limit
  3/h por e-mail + 10/h por IP. Janela curta (1h) e escopo mínimo (só login) limitam link vazado.
  Resolve a conta nível 1 (sem senha) que sai e precisa voltar.
- **Verificação de domínio (nível 2→3):** `verify/start` gera `token_urlsafe(32)` (256-bit);
  `verify/check` (rate limit **10/h/IP**) busca meta tag / arquivo / DNS TXT. **Anti-SSRF:** o
  domínio vem de `targets` (site público já escaneado), não de input cru, e o corpo **nunca**
  volta ao usuário (só o boolean do match) → sem exfiltração; UA honesto, timeout 10s, ≤3 redirects.
- **Cleanup:** conta nível 1 + sem senha + não confirmada + >30d + sem re-login é removida pelo
  `trial_worker` (`delete_unconfirmed_passwordless_accounts` — o vínculo de site PENDENTE do Fluxo
  D a isentava da limpeza do KL-82).

## 5. Privacidade de dados

- **`contact_email`, `cnpj`, `whatsapp` NUNCA são expostos** na API/perfil público
  (`_PUBLIC_PROFILE_FIELDS` filtra). O perfil público só mostra dado que o site já
  publica + o score — nunca detalhe PASS/FAIL dos checks.
- **Resultado gratuito nunca vaza** detalhe dos checks pagos (headline, evidência,
  impacto, correção) — só status ✅/❌ nos 15 grátis e 🔒 nos pagos.
- Score de site oculto (`public_visible=FALSE` ou descartado) → `/score` devolve `null`,
  some do sitemap e do `/public/profile`.
- Landing do perfil pode ser editada pelo admin (`edited_by_admin`) — o enrich
  automático **preserva** esses campos.

### Verificação de propriedade (KL-68)

- **`contact_email` nunca é exposto.** A auto-verificação Tier 1 (`_ownership_method`,
  no servidor) valida por (1) e-mail == `contact_email` → `auto_email`, ou (2) **domínio do
  e-mail == domínio do site** → `auto_domain` (KL-71), **exceto provedores públicos**
  (`PUBLIC_EMAIL_PROVIDERS`: gmail/hotmail/…, pois `email@gmail.com` não prova ser dono de
  gmail.com). O Tier 2 envia um código de 6 dígitos **ao `contact_email`** e o frontend só
  recebe o `email_hint` **mascarado** (`con****@empresa.com.br`).
- **First-come-first-served:** um site tem no máximo 1 dono verificado; `site_has_owner`
  bloqueia (409) um segundo usuário e desliga o `auto_domain` — impede sequestro de perfil.
  O perfil público some com o CTA "Reivindicar" quando já há dono (`owner_verified` → só
  "Monitorar"); o dashboard informa `has_other_owner` sem expor quem é o dono.
- **Convite de técnico (KL-71):** valida conflito de papel (422 em auto-convite,
  dono-como-técnico e já-vinculado); o e-mail do dono ao técnico é sempre **mascarado**.
- **Remoção self-service** (`DELETE /account/sites/{id}`, JWT do usuário): o próprio dono
  remove seu site — revoga a posse e desativa as vigílias, **sem** notificação (diferente
  do remove-site admin, que notifica).
- **Código:** 6 dígitos CSPRNG, TTL 30 min, **3 tentativas** (depois `failed`),
  comparação constant-time (`hmac.compare_digest`).
- **Rate limit:** `POST /account/ownership/request-verification` 5/h/IP;
  `/verify` 10/10min/IP (Redis + fallback in-memory).
- **Domain guard** (`api/domain_guard.py`): domínios públicos/institucionais
  (gmail.com, google.com, `.gov.br`, `.edu.br`…) **não** podem ser monitorados nem
  reivindicados (422 no `POST /account/sites`; sem CTA no perfil público). O **scan
  continua livre** (vitrine da plataforma) — só o monitoramento/reivindicação é bloqueado.
- Email de verificação é **transacional** (`klarim@klarim.net`), nunca proativo.

### Laudo compartilhável + técnico vinculado (KL-44 P3)

- **Laudo público `/public/laudo/{code}`**: **sem PII** — nunca `contact_email`, e-mail/dados
  do dono ou internos do alvo; só domínio + score + checks técnicos. Rate limit **30/h/IP**
  (anti-scraping). **Código CSPRNG** (`secrets.choice`), **TTL 30 dias** (dados de segurança
  não são eternos). Página Astro SSR com `noindex` (link privado, não é conteúdo de SEO).
- **E-mail do dono mascarado** para o técnico (`d***o@x.com.br`); `technician/search` só
  devolve `{found, user_id, name}` de quem é `role='technician'`, nunca outros dados.
- **Boletim do dono** = plain text via `alerta@klarimscan.com` (proativo, respeita blocklist);
  **laudo/convite ao técnico** = transacional via `klarim@klarim.net`. Todos com
  Reply-To `scan@klarim.net` e registrados no `email_log`. Endpoints novos: rate limit
  Redis+fallback (invite 10/h, shared-report 20/h, laudo 30/h).

### Pagamento de assinatura (KL-44 P6)

- **NUNCA armazena dado de cartão/PIX** — o Klarim só recebe o webhook de confirmação;
  `subscription_payments` guarda apenas o `provider_charge_id` (id da cobrança AbacatePay).
- **Webhook idempotente:** `_confirm_subscription_payment` só transiciona de `pending`
  (`mark_subscription_payment`); receber o mesmo evento 2× não ativa/cobra 2×. Validação
  em camadas: query-secret obrigatório + HMAC opcional (`ABACATEPAY_HMAC_STRICT`). Responde
  sempre 200 (evita retries infinitos).
- **Upgrade/downgrade** exigem **JWT de usuário**; upgrade rate-limited (10/h/IP, Redis +
  fallback). `_PLAN_RANK` garante que upgrade só sobe e downgrade só desce (servidor-
  autoritativo). **Downgrade preserva dados** (sites/scans/histórico) — só desativa features.
- **Trial expira → downgrade silencioso p/ Free** (worker `trial`): nunca bloqueia nem
  apaga dados; e-mails de aviso/expiração são **transacionais** (`klarim@klarim.net`,
  Reply-To `scan@klarim.net`, registrados no `email_log`).

### Posicionamento legal — indicadores de privacidade (KL-44 P5)

- **Diagnóstico técnico, NÃO certificação.** Os 8 indicadores de privacidade
  (`scanner/privacy_checks.py`) são **fatos técnicos** de varredura passiva. É **proibido**
  usar "LGPD Compliant", "Em conformidade", "Certificado", "Aprovado", "Site Seguro".
  Sempre: "Indicadores técnicos", "Monitorado por", "Diagnóstico".
- **Disclaimer obrigatório** em TODA superfície com menção a LGPD/privacidade (perfil
  público, laudo, dashboard, boletim) — `PRIVACY_DISCLAIMER` (uma fonte só, reexposta pela
  API). O selo/widget não leva disclaimer (muito pequeno), mas o perfil que ele linka leva.
- **`privacy_score` (0–8) é SEPARADO** do score de segurança (0–100) — nunca se combinam.
- **Selo factual** — `seal_type="monitored"`, nunca "certificado"/"aprovado". O widget
  **não coleta dados dos visitantes** do site do cliente (1 GET de leitura; CORS `*`).
- **Benchmark anônimo** — só médias/mediana/distribuição por setor (≥10 scans), **nunca**
  nomeia sites. `contact_email`/PII continuam fora de qualquer payload.

### Vigílias avançadas (KL-44 P4)

- **100% passivo:** `uptime` é um GET honesto (User-Agent `KlarimScanner/1.0`); `changes`
  faz 1 GET e compara um snapshot leve (hash de conteúdo/headers, contagem de scripts/forms);
  `phishing`/typosquat só **lê os CT logs públicos** que o discovery já consome — nunca
  registra domínio, nunca sonda o suspeito. Nenhum check novo altera o score de segurança.
- **Anti-spam:** uptime exige **3 falhas consecutivas** antes de alertar (anti-glitch) e
  **1 alerta de "fora do ar" por hora**; envia 1 alerta de recuperação ao voltar. `changes`
  só alerta em mudança **significativa** (conteúdo >30%, título, headers, scripts↑, forms↑).
- **Enforcement de plano** é servidor-autoritativo: `uptime` é Pro+, `changes`/`phishing`
  são Agency; o worker desativa a vigília se o plano deixa de permitir (nunca por erro
  transiente de lookup). E-mail de vigília é **proativo** → respeita a blocklist (KL-24/62),
  Reply-To `scan@klarim.net`, registrado no `email_log`.
- **Config ao vivo:** `BULLETIN_ENABLED` (bool) e `BULLETIN_HOUR_UTC` no painel
  (`admin_settings` > `.env`), relidos por ciclo — sem redeploy. `/admin/typosquat-alerts`
  exige JWT admin.

### Gestão de usuários (KL-69)

- **Enforcement de `is_active` no login:** `POST /account/login` retorna **403** para conta
  desativada (`is_active=false`), mesmo com senha correta — mensagem aponta para
  `klarim@klarim.net`.
- **Ações admin** (`/admin/users/{id}/remove-site|deactivate|reactivate`, `/admin/clean-
  blocked-sites`) exigem **JWT admin** (prefixo `/admin`) + **rate limit** 30/min/IP
  (Redis + fallback). Remover um site **não** apaga a conta (segue ativa); revoga a posse
  (`ownership_verifications.status='revoked'`, auditoria).
- **Notificações** de site removido / conta desativada / reativada são **transacionais**
  (`klarim@klarim.net`), registradas no `email_log` (`site_removed` / `account_deactivated`
  / `account_reactivated`).

## 6. Reputação de e-mail (anti-bounce, KL-24/62)

- **Mapa de remetentes (2026-07-21).** Nunca misturar transacional e proativo.

  | Endereço | Uso | Env |
  |---|---|---|
  | `klarim@klarim.net` | **Transacional** — confirmação/boas-vindas, convites, vigílias/boletim ao técnico, senha, avisos de conta | `RESEND_FROM` |
  | `alerta@klarim.net` | **Proativo (cold)** — alertas em batch ao dono, "perfil consultado", boletim ao dono | `ALERT_FROM_EMAIL` |
  | `scan@klarim.net` | **Reply-To de TODOS** os e-mails + inbox (Hostinger, painel Inbox) | `REPLY_TO_DEFAULT` |

  **`klarim@` (transacional) migrou de `seguranca@` em 2026-07-21:** "seguranca" é keyword de
  phishing e elevava o spam score (confirmação caía no spam). `_mailer()` lê `RESEND_FROM` a
  cada envio → trocar o `.env` + **recriar o container**. O proativo (`alerta@klarim.net`, migrado
  de `alerta@klarimscan.com` em 2026-07-20) e o Reply-To (`scan@`) **não mudaram**.
- **Validação de MX na captação** (`contact.py`): só aceita e-mail com registro MX
  (tri-estado `ok|no_mx|unknown`, fail-open no timeout).
- **Blocklist central** (`email_blocklist`, por e-mail). **Webhook Resend** (Svix, assinatura
  validada → 401 se inválida): bounce **permanente** → `bounced` + descarta + bloqueia; complaint →
  unsubscribe + bloqueia. **Bounce transitório** (`transient`/`soft`/`temporary`/`delivery_delayed`,
  fix 24/07) → marcado `soft_bounced` no `email_log` (rastreável, conta no circuit breaker) MAS **não
  descarta o alvo nem blocklist** (pode ser caixa cheia temporária). Antes o transitório era ignorado
  (evento sumia); ambos os ramos logam se o `email_id` não casa (diagnostica update silencioso).
- **Auto-pause GLOBAL** se o bounce rate all-time passa de `ALERT_MAX_BOUNCE_RATE` (8%, amostra
  `ALERT_BOUNCE_MIN_SAMPLE`=20). **Circuit breaker POR REMETENTE** (KL-91): 5% sobre **janela de 7d**
  com amostra `ALERT_SENDER_BOUNCE_MIN_SAMPLE`=100 (fix 24/07 — não pausa remetente em warmup por
  poucos bounces; bounces antigos saem da janela).
- **Cota mensal** (`ALERT_MONTHLY_LIMIT`, 45k dos 50k Resend Pro) — nunca estourar; reserva
  para transacionais.
- **Regra por construção (KL-62):** todo e-mail passa por `KlarimMailer._send` →
  registra em `email_log`. **Proativo respeita a blocklist; transacional pode ignorá-la
  mas SEMPRE registra.** O log é fire-and-forget; a checagem de blocklist é fail-open.
- **Reply-To (KL-67):** `_send`/`_send_batch` injetam **`Reply-To: scan@klarim.net`**
  (`setdefault`) em TODO e-mail — `seguranca@`/`alerta@` são só-envio (Resend), as respostas
  caem no inbox Hostinger (`scan@`, painel Inbox). `send_contact` mantém o seu (visitante).

## 7. Segredos

- **Nunca commitar** `.env`, chaves SSH, service account keys. Tudo sensível vive em
  **GitHub Secrets** ou no `/opt/klarim/.env` da VM.
- CI autentica no GCP por **Workload Identity Federation (OIDC, keyless)** — org policy
  proíbe chaves de SA.
- Config editável: `admin_settings` (banco) > `.env` > default; nenhuma resposta expõe
  senha/hash/token inteiro (token MCP sempre mascarado).

## 8. Webhooks

| Webhook | Validação |
|---|---|
| `POST /webhooks/abacatepay` | query-secret obrigatório + HMAC (defense-in-depth) |
| `POST /webhooks/resend` | assinatura **Svix** (`RESEND_WEBHOOK_SECRET`); 401 se inválida |
| `POST /email/webhook` (Hostinger) | token próprio, **fail-closed** (sem env ⇒ 401); loga nomes de headers (nunca valores) em 401 |

## 9. Checklist de revisão de segurança (toda feature nova)

- [ ] O endpoint/fluxo tem **auth** apropriada (admin JWT / user JWT / token próprio / público consciente)?
- [ ] **Validação de input** (formato, tamanho, tipo) e **sanitização** onde há dado de terceiro?
- [ ] **Rate limit** (Redis `_redis_allow` com fallback)?
- [ ] Risco de **IDOR** (filtra por `user_id`/dono)? Enforcement server-side, não só no front?
- [ ] Expõe **dado sensível** (e-mail/CNPJ/WhatsApp/detalhe de check pago)? Se sim, remover.
- [ ] Se toca o scanner: **100% passivo** (só GET/HEAD/DNS público)?
- [ ] Se envia e-mail: passa por `KlarimMailer._send` (log + blocklist)? Proativo respeita blocklist?
- [ ] Se mexe no Nginx: `nginx -t` ok? Security headers preservados em cada `location`?
- [ ] Se muda scoring/check: **flush `scan:*` no Redis** após deploy?
- [ ] Corpo HTML de terceiro nunca no DOM (só `<iframe sandbox>`)?

## 10. Security Gate — scanner de exposição pós-deploy (KL-141 · KL-147 · KL-149)

Módulo `security_gate/` (SEPARADO do `scanner/` público; roda no CI pós-deploy contra `klarim.net`
via `scripts/security_gate.py`). **NÃO é DAST** — só GET/HEAD/DNS/handshake TLS; **não envia payload
de ataque** (sem SQLi/XSS). Alvos **autorizados** apenas (a própria Klarim no CI). Score 0-100; o CI
reprova (job vermelho + e-mail) só em **FAIL CRÍTICO** (`--fail-on critical`).

**18 categorias de check:**
| Categoria | Verifica | Sev. máx |
|---|---|---|
| headers | HSTS/CSP/X-Frame/nosniff/Referrer/Permissions-Policy, versão no Server | HIGH |
| ssl | validade do cert, protocolo (TLS ≥1.2) | CRITICAL |
| exposure | `.env`/`.git`/wp-config/painéis/docs/debug/backup/source-map/dir-listing/.htaccess (KL-147: guard de SPA fallback por fingerprint ETag/CL) | CRITICAL |
| credentials | ~50 patterns de segredo + entropia no HTML/JS (nunca guarda o VALOR) | CRITICAL |
| api | raiz da API não lista endpoints; endpoints protegidos exigem 401/403 | CRITICAL |
| **cors** (KL-149) | reflexão de Origin arbitrário / wildcard + credentials | CRITICAL |
| **cookies** | HttpOnly/Secure/SameSite nos cookies de SESSÃO (ignora analytics/consent) | CRITICAL |
| **https_redirect** | HTTP → HTTPS (301/302) | CRITICAL |
| **open_redirect** | `?redirect=/?next=…` p/ domínio externo (sonda benigna, não navega) | HIGH |
| **rate_limit** | mini-burst de 10 GETs → algum 429? (endpoints configuráveis; roda por ÚLTIMO) | HIGH |
| **error_disclosure** | stack trace/caminho interno em 404/5xx (triggers benignos, **sem injeção**) | HIGH |
| **jwt** | `alg:none` / sem `exp` / PII no payload (só DECODIFICA — **nunca forja**) | CRITICAL |
| **form_security** | `<form action>` para domínio externo | CRITICAL |
| **dns** | DNSSEC + CAA | MEDIUM |
| **dependencies** | libs JS com CVE conhecida (base local, sem API externa) | HIGH |
| **tls_ciphers** | ciphers fracos RC4/DES/3DES/NULL/EXPORT (força TLS ≤1.2 + confere cipher negociado — anti falso-positivo do TLS 1.3) | CRITICAL |
| **subdomain** | subdomain takeover (CNAME p/ PaaS desativado, por fingerprint) | CRITICAL |
| **infrastructure** | URLs de backend/PaaS (Cloud Run/Heroku/Lambda…), túnel de dev (ngrok), localhost, IP privado, k8s interno hardcoded no HTML/JS — **nunca alerta o próprio domínio** | CRITICAL |

Config por YAML (`security-gate.yml`): allowlist de exposição, endpoints protegidos, endpoints do
rate limit, ligar/desligar cada check. Passividade e limites (teto de 10 requests no rate limit,
zero forja de token, nenhum payload de injeção) são invioláveis.

## 11. Security Gate — produto para devs (KL-151)

- **API key NUNCA em claro.** No banco vive só o **SHA-256** (`gate_api_keys.key_hash`) + um prefixo
  `KLM_xxxx` (para identificar sem expor). A key completa (`KLM_` + 32 hex = 36 chars) é gerada com
  `secrets.token_hex` e **exibida UMA VEZ** — no registro (`/gate/register`) ou na regeneração
  (`/account/gate/regenerate-key`, que revoga TODAS as ativas antes). Auth = hash da key recebida no
  header `X-API-Key` comparado ao banco; key inativa/revogada → 401.
- **Checks e limites são enforcados no SERVIDOR** (`api/gate.py`), nunca no client: `get_allowed_checks`
  (o plano decide quais checks rodam), `enforce_scan_limit` (429), `enforce_domain_limit` (403). Um
  cliente que peça um check fora do plano é ignorado pelo servidor.
- **Domínio só escaneia se verificado.** Verificação por controle de domínio (reusa o KL-99:
  meta_tag/dns_txt/html_file; o corpo do site nunca volta ao usuário) OU por **convite do dono**.
- **Convite dono→dev:** só um dono **nível 3** que possui o domínio VERIFICADO (`user_owns_verified_domain`)
  pode convidar; token `secrets.token_urlsafe(32)`, TTL 7 dias, status efetivo resolvido por relógio.
  O aceite exige o dev LOGADO cujo e-mail == o convidado (anti-hijack). Revogar remove o projeto do dev
  (perde o acesso ao scan) **+ avisa o dev por e-mail** (KL-151 P4). Rate limit 10/h/IP no convite e na verificação.
- **Rotação de key com grace period (P4):** a regeneração revoga a antiga com `grace_expires_at=NOW()+1h`
  — a key antiga ainda autentica por 1h (CI em andamento não quebra); depois disso → 401. `authenticate_api_key`
  aceita uma key revogada só DENTRO do grace.
- **Rate limit por MINUTO por key (P4):** `gate_rpm:{key}:{min}` no Redis — free 10 · pro 30 · team 60 ·
  enterprise 120 req/min (além do teto de scans/dia). Fail-open sem Redis.
- **Enterprise — scan de terceiro redigido (P4):** um plano com `scan_third_party` pode escanear domínio
  NÃO verificado, mas o resultado **redige** `path` e o detalhe de **credenciais/exposição** (`_redact_third_party`)
  — só a categoria + severidade do risco vaza (nunca o caminho/segredo do terceiro).
- **Audit log (P4, `gate_audit_log`):** CADA ação do Gate (scan/key/project/invite/plan/enterprise) é
  registrada com IP/UA. **NUNCA guarda o valor da API key — só o prefixo.** `target_domain` obrigatório em
  todo scan (compliance Enterprise). Leitura admin (todas as contas) e dev (só a própria, ownership).

## 12. Security Gate — KYC + rate limiting + scan avulso (KL-153)

- **KYC progressivo (`POST /account/kyc`).** O dev roda scans sem KYC (resultado RESUMIDO); o KYC
  (CPF + endereço ≥10 chars + telefone + **e-mail confirmado** — KL-156) libera o resultado COMPLETO.
  **KL-156:** o `email_confirmed` (código no signup) é a ÚNICA verificação de identidade REAL —
  `phone_verified` era auto-setado ao preencher o telefone (placeholder de SMS) e NÃO gateia mais
  (`_kyc_complete(cpf, address, phone, email_confirmed)`). O endpoint já devolvia 403 sem e-mail
  confirmado; a condição de `kyc_completed` reforça (defesa-em-profundidade). `validate_cpf` (`api/validators.py`)
  valida **formato + os 2 dígitos verificadores** (módulo 11) — NÃO consulta a Receita Federal; rejeita
  sequências repetidas. CPF guardado **formatado** (`000.000.000-00`), **único** (`idx_users_cpf` parcial);
  CPF de outra conta → **409**. Exige **e-mail confirmado** (403 senão). `kyc_completed`/`kyc_completed_at`
  gravados só quando os 3 campos estão presentes; `kyc_completed_at` nunca é sobrescrito (COALESCE).
- **Audit por CPF (compliance).** O `gate_audit_log` ganhou `cpf`/`url_scanned`/`domain`/`score`/`passed`
  — cada scan é rastreável ao CPF do dev (NULL quando sem KYC). O CPF entra no audit, nunca em log de app.
- **Rate limiting de 3 camadas + intervalo + abuso (`api/gate_rate_limiter.py`, tudo em Redis).** Verificado
  ANTES do scan, fail-fast na ordem: **(0)** conta `suspended` → 403 em TODO endpoint Gate · **(1)** IP
  10/h · **(2)** conta por plano/h (free 5 · pro 50 · team 200 · enterprise ∞) · **(3)** cooldown por
  domínio **POR CONTA** (`gate:rl:domain:{acct}:{domain}`, KL-155 — key por `account_id`, sem interferência
  entre usuários), TTL **por plano** · **(4)** intervalo mínimo entre domínios DIFERENTES por conta (free
  5min · pro 1min · team/ent 0). 429 → `{detail, retry_after_seconds, limit_type, current_plan,
  upgrade_url}` + header `Retry-After`. **Detecção de abuso:** > 20 domínios distintos/24h
  (`gate:rl:distinct:{acct}`) → conta **suspensa** + audit `abuse_detected` + warning de app. **Sem Redis →
  fail-open** (não bloqueia; igual ao `_enforce_rpm`). Complementa (não substitui) o RPM-por-key e o teto de
  scans/dia do KL-151.

  **Camada 3 — TTL do cooldown por domínio, por plano (KL-155, `DOMAIN_TTL_BY_PLAN`):**

  | Plano | TTL | Comportamento |
  |---|---|---|
  | Free | 1800s (30 min) | 1 scan por domínio a cada 30 min |
  | Pro | 300s (5 min) | 1 scan por domínio a cada 5 min |
  | Team | — | **SKIP** (a camada não se aplica; nenhuma key setada) |
  | Enterprise | — | **SKIP** |

  A key é **por conta** (`{acct}:{domain}`), então o cooldown é por-usuário (como as demais camadas) e um
  Free e um Pro no MESMO domínio não interferem. Resolve o falso 429 de um CI com 2 pushes do mesmo domínio
  em 30 min (o CI da própria Klarim usa a CLI, não a API, então não era afetado).
- **Scan avulso (sem projeto).** `POST /gate/scan` sem projeto casando o domínio faz um scan **avulso**:
  exige conta autenticada + **e-mail confirmado**, sem verificação de domínio (a barreira do KL-151 valia
  só para o modelo de projetos). URL pública qualquer; o rate limiting e o audit aplicam normalmente. Scans
  COM projeto verificado seguem idênticos (backward compat).
- **Upgrade via PIX (`POST /account/gate/upgrade`).** Nível ≥2 (pagamento exige senha). Gera cobrança PIX
  avulsa mensal na AbacatePay (recorrência é escopo futuro); reusa a tabela `subscription_payments` com o
  plano prefixado `gate:{slug}` → o webhook confirmado ativa o `gate_plan_id` (`plan_upgraded` no audit).
  NUNCA guarda dado de cartão/PIX (só o id da cobrança).

## 13. Rate limiting na aplicação + bloqueio de IP direto + self-scan (KL-160)

Defesa DoS/abuso na origem, **independente do Cloudflare** (que protege via domínio, não via IP).

- **Rate limiting no Nginx** (`frontend/nginx/http.conf` + `https.conf.template`, no TOPO de cada
  arquivo). Zonas: `general 30r/s` (burst 50) em `/` + páginas Astro · `api 10r/s` (burst 20) em
  `/api/` · `scan 2r/s` (burst 5) em `/api/scan/`. Excedente → **429** (`limit_req_status 429`).
  Assets (`_astro`/`/assets`), MCP/SSE e OAuth ficam **sem** limite (não throttlar bursts de assets
  nem o SSE). ⚠️ **A chave é o IP REAL do cliente** (`CF-Connecting-IP` via `map`), NÃO
  `$binary_remote_addr` — atrás do CF o `$remote_addr` é a edge do Cloudflare, e usá-lo criaria UM
  bucket para o site inteiro (throttle global). Sem CF (acesso direto), o `map` cai no `$remote_addr`.
  As zonas ficam **no próprio arquivo de config** (não num conf.d à parte) porque o `nginx -t` da CI
  monta cada config sozinho; como só um config é carregado por vez (entrypoint HTTP×HTTPS), não há
  zona duplicada.
- **Bloqueio de IP direto** (só HTTPS): `server { listen 443 ssl default_server; ssl_reject_handshake
  on; }` recusa o handshake TLS de SNI desconhecido (sem cert); `server { listen 80 default_server;
  return 444; }` fecha a conexão HTTP. O bloco do klarim.net **perdeu** o `default_server` — o CF
  conecta com SNI=klarim.net e casa o server real; scanners no IP direto batem no reject/444.
- **Paths de admin/debug** (Fix A): novo `location ~* ^/(adminer|_profiler|phpMyAdmin|pma|dbadmin|
  sql|mysql|cpanel|webmail|roundcube|squirrelmail|horde|wp-content/debug) { return 404; }` — o bloco
  do KL-138 cobria `/admin(/|$)` mas deixava `/adminer` passar (virava SPA fallback → falso positivo
  no Gate).
- **Fingerprint de SPA por `last-modified`** (Fix B, `security_gate/`): o guard do KL-147 usava o
  ETag como comparador; o Cloudflare **remove** o ETag → o Gate reportava `/adminer`/`/_profiler`
  (200+index.html) como exposição. Agora o probe captura `last_modified` e um 200 com o MESMO
  Last-Modified + Content-Type HTML é tratado como fallback (não exposição).
- **Self-scan da plataforma** (`POST /admin/security-scan`, admin-only): roda o Gate contra
  klarim.net (assíncrono, cooldown 5 min), salva em `platform_security_scans` (tabela DEDICADA, não
  `gate_runs`), painel em Sistema com histórico + detalhe. Alerta o operador se score < 80 ou CRÍTICO.
- **DNSSEC:** ativo (`✅ Dnssec Ok — zona assinada` no Gate real). O finding NoNameservers do card
  não se reproduz — DNSSEC habilitado no Cloudflare + DS no registrador. Config manual, sem código.
