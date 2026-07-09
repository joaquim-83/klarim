# claude.md — Guia do projeto Klarim para agentes Claude

> **Leia este arquivo antes de tocar no código.** Ele é o onboarding obrigatório
> para qualquer agente Claude (CLI ou chat) que trabalhe no Klarim. Se algo aqui
> conflitar com um pedido, **pare e pergunte** antes de prosseguir.

---

## 1. Visão geral

**Klarim** — *"O alarme que toca antes do ataque."*

Scanner **passivo** de segurança web para **PMEs brasileiras** (hotéis, clínicas,
escolas, e-commerces, condomínios, contabilidades) que têm sistema web exposto e
não têm equipe de segurança.

Como funciona, em uma frase: o Klarim descobre alvos por **fingerprinting de
plataforma** (Duda, WordPress, Wix, CRA…), executa **checks de segurança
comprováveis sem invasão**, calcula um **score 0–100** e gera relatórios em dois
níveis:

- **Relatório executivo (semáforo 🔴🟡🟢)** — para o dono do negócio; linguagem
  acessível, foco em risco de negócio e LGPD.
- **Relatório técnico** — para dev/agência; detalhe de cada check, headers,
  paths testados e recomendações de correção.

**Modelo de negócio (bottom-up):** vende barato ao dono do negócio (**R$ 19–49**,
decisão de impulso). O dono encaminha o relatório para a **agência** que fez o
site. Quando várias agências recebem relatórios de vários clientes, elas procuram
o Klarim organicamente — a venda B2B acontece **sem prospecção**.

A especificação completa de produto vive em [`klarim_mvp_spec.md`](./klarim_mvp_spec.md).

---

## 2. Stack e infraestrutura

| Camada | Tecnologia |
|--------|-----------|
| Scanner | **Python 3.12** + `httpx` + `ssl` + `cryptography` |
| API | **FastAPI** + `uvicorn` |
| Fila | **Redis** (`klarim:scan_queue`) |
| Banco | **PostgreSQL 16** |
| Frontend | **React + Tailwind** (futuro) |
| PDF | **WeasyPrint** |
| Infra | **GCP Compute Engine `e2-small`**, **Docker Compose** |
| Deploy | Docker (`Dockerfile` compartilhado por API e Worker) + GitHub Actions |

**Links do projeto:**

- **Repositório:** https://github.com/joaquim-83/klarim.git
- **Jira (board KL):** https://igoove.atlassian.net/jira/software/c/projects/KL/boards/265/backlog

**VM de produção (GCP):**

| Campo | Valor |
|-------|-------|
| Instância | `instance-20260706-112125` |
| Zona | `us-central1-a` |
| Projeto | `project-b08050df-fa4e-49ac-919` |
| Diretório de deploy | `/opt/klarim` |

Acesso SSH:

```bash
gcloud compute ssh --zone "us-central1-a" "instance-20260706-112125" \
  --project "project-b08050df-fa4e-49ac-919"
```

O `.env` de produção vive **apenas na VM** (`/opt/klarim/.env`), nunca no git.
Detalhes de provisionamento e deploy: seção **8** e `claude/reports/KL-3_gcp-deploy-cicd.md`.

---

## 3. Estrutura de diretórios

```
klarim/
├── claude.md               # ESTE arquivo — guia obrigatório para agentes
├── claude/                 # governança: session summaries + task reports
│   ├── README.md
│   ├── sessions/           # resumos de sessão do chat planejador (Claude chat)
│   └── reports/            # relatórios de cada tarefa do Claude CLI (KL-xxx)
├── klarim_mvp_spec.md      # especificação de produto (fonte da verdade)
├── docker-compose.yml      # PostgreSQL + Redis + API + Worker
├── Dockerfile              # imagem compartilhada (API/Worker)
├── .env.example            # variáveis de ambiente (sem segredos)
├── requirements.txt
├── README.md
├── scanner/                # engine de varredura
│   ├── main.py             # entry point do worker + CLI
│   ├── runner.py           # orquestra os checks em sequência + score
│   ├── scoring.py          # cálculo de score 0–100 + semáforo
│   └── checks/             # um módulo por check
│       ├── base.py         # CheckResult, rate limit, helper HTTP, parse HTML
│       └── check_*.py      # os checks (descobertos dinamicamente)
├── reporter/               # geração de PDF (WeasyPrint + Jinja2)
│   ├── generator.py        # generate_executive_pdf() / generate_technical_pdf()
│   ├── templates/          # executive.html + technical.html
│   └── assets/logo.svg     # logo Klarim (beacon)
├── frontend/               # interface web (React + Vite + Tailwind v4)
│   ├── src/pages/          # Landing, Scan, Result, Report
│   ├── src/components/     # Logo, Semaphore, Header, Footer, ...
│   ├── nginx.conf          # serve estático + proxy /api → api:8000
│   └── Dockerfile          # build Vite → Nginx (serviço web no compose)
├── payments/               # pagamento AbacatePay PIX (KL-7)
│   ├── abacatepay.py       # client v2 + verify_webhook_signature
│   ├── models.py           # Charge, PaymentStatus, PRICING
│   └── store.py            # persistência (Postgres + fallback memória)
├── notifier/               # e-mail via Resend (KL-8)
│   ├── email_client.py     # KlarimMailer (alerta / relatório / teste)
│   └── templates/          # alert.html + report_delivery.html (table-based)
├── api/                    # API HTTP (FastAPI)
│   └── main.py             # semáforo + PDFs + fluxo de pagamento PIX
└── tests/                  # pytest
    ├── test_checks.py      # unit tests dos checks + teste online opt-in
    ├── test_reporter.py    # geração de PDF (offline, guardado por libs nativas)
    └── test_payments.py    # client/store/gating de pagamento (offline)
```

---

## 4. Regras do projeto (invioláveis)

### 4.1 Legal — só varredura passiva

O Klarim é um serviço de *Security Rating* / *Monitoramento de Superfície de
Ataque*. **NÃO é pentest.** Portanto:

- ✅ **Faz:** requisições HTTP `GET`/`HEAD` a URLs públicas, leitura de headers,
  leitura de certificados SSL públicos, consulta DNS pública, acesso a arquivos
  que o servidor entrega sem autenticação.
- ❌ **NUNCA faz:** payloads de injeção (SQLi/XSS), brute-force de credenciais,
  acesso a áreas autenticadas, exploração de vulnerabilidades, extração de dados.

Qualquer código que viole isto **não entra no repositório.** Na dúvida, trate o
alvo como um site de terceiros que não autorizou nada além de olhar o que é
público.

### 4.2 Interface dos checks

Todo check em `scanner/checks/` **deve** seguir exatamente esta interface:

```python
async def check(url: str) -> CheckResult
```

- `CheckResult` (ver `scanner/checks/base.py`) carrega:
  `name`, `status` (`PASS` / `FAIL` / `INCONCLUSO`),
  `severity` (`CRITICA` / `ALTA` / `MEDIA` / `BAIXA`), `evidence` (string).
- **Descoberta dinâmica:** os checks são descobertos automaticamente por
  `scanner/checks/__init__.py` (`discover_checks()`). Para adicionar um, crie
  `check_<slug>.py` com as constantes de módulo `ORDER` (int), `CHECK_ID` (str) e
  `NAME`, e a coroutine `check`. **Não existe lista hardcoded** e o score em
  `scoring.py` funciona com qualquer número de checks.
- Um check que não conseguiu avaliar retorna **`INCONCLUSO`** — nunca finge um
  `PASS`. `INCONCLUSO` é neutro no score.

**O número de checks é dinâmico e cresce com o projeto** — nunca trate um número
específico como identidade do produto. Conjunto atual (**15**):

| # | Check | Módulo | Severidade |
|---|-------|--------|-----------|
| 01 | HTTPS ativo | `check_https.py` | Crítica |
| 02 | HSTS presente | `check_hsts.py` | Alta |
| 03 | Certificado SSL válido | `check_ssl.py` | Crítica |
| 04 | TLS 1.2+ only | `check_tls.py` | Alta |
| 05 | Content-Security-Policy | `check_csp.py` | Alta |
| 06 | X-Frame-Options | `check_xfo.py` | Média |
| 07 | X-Content-Type-Options | `check_xcto.py` | Média |
| 08 | Server header exposto | `check_server.py` | Média |
| 09 | Source maps expostos | `check_sourcemaps.py` | Crítica |
| 10 | Arquivos sensíveis | `check_sensitive.py` | Crítica |
| 11 | Directory listing | `check_dirlist.py` | Alta |
| 12 | Meta tags default | `check_metatags.py` | Baixa |
| 13 | SRI ausente em scripts externos | `check_sri.py` | Alta |
| 14 | Scripts de fontes arriscadas | `check_risky_sources.py` | Alta |
| 15 | Domínios externos em excesso | `check_external_domains.py` | Média/Alta |

Checks 13–15 (supply chain, KL-2) fazem parse **passivo do HTML servido**;
scripts injetados por JavaScript em runtime não são vistos por um GET simples.

### 4.3 Rede

- **Timeout de 10s por request.**
- **Rate limit de 1 req/s por domínio** (centralizado em `checks/base.py`; não
  reimplemente por check).
- **User-Agent identifica o Klarim honestamente** — não se passa por navegador,
  não se esconde. Ver `USER_AGENT` em `checks/base.py`.

### 4.4 Idioma e governança

- **Commits em inglês.** **Código em inglês.** **Comentários podem ser PT-BR.**
- **Todo prompt do Claude Code deve ter um card `KL-xxx` no Jira associado**
  (exceto ajustes mínimos, ex.: typo, formatação).
- **Cada tarefa gera um relatório em `claude/reports/KL-xxx_<slug>.md`** e
  **atualiza a documentação relevante** (README, este arquivo, spec).

---

## 5. Convenções de código

- **`async`/`await`** para toda I/O (rede, disco). Os checks são coroutines.
- **Type hints** em assinaturas públicas.
- **Docstrings** em módulos e funções não triviais (o que o check verifica e o
  que significa PASS/FAIL).
- **Testes com `pytest`**; testes de rede ficam atrás de flag (`KLARIM_ONLINE=1`)
  para o CI continuar hermético.
- Não reinvente o helper HTTP nem o rate limiter — use `checks/base.fetch`.

---

## 6. Como rodar

```bash
# Ambiente
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Scan pela CLI (relatório legível ou JSON)
python -m scanner.main https://www.example.com
python -m scanner.main https://www.example.com --json

# Scan + gerar PDFs (executivo + técnico) no diretório atual
python -m scanner.main https://www.example.com --pdf

# Stack completa (Postgres + Redis + API + Worker)
docker-compose up --build

# Testes
pytest                                        # offline
KLARIM_ONLINE=1 pytest tests/test_checks.py   # inclui scan real
```

---

## 7. Fluxo de trabalho de uma tarefa (checklist para o agente)

1. Confirme que existe um card **`KL-xxx`** no Jira para o pedido.
2. Leia este `claude.md` e a parte relevante de `klarim_mvp_spec.md`.
3. Implemente respeitando as regras da seção 4.
4. Rode `pytest` (e um scan real quando fizer sentido).
5. Escreva o relatório em `claude/reports/KL-xxx_<slug>.md`.
6. Atualize a documentação afetada (README, este arquivo, spec).
7. Commit em inglês no formato `tipo(KL-xxx): descrição` e push.

---

## 8. Deploy e CI/CD (GCP)

### Provisionamento (uma vez)

Na VM (ver dados na seção 2), instalar Docker + plugin Compose, criar
`/opt/klarim` e clonar o repositório. Passo a passo completo em
`claude/reports/KL-3_gcp-deploy-cicd.md` (Partes 1–2).

### Deploy manual

```bash
gcloud compute ssh --zone "us-central1-a" "instance-20260706-112125" \
  --project "project-b08050df-fa4e-49ac-919"
# na VM (deploys são operações root — mesmo caminho do CI):
sudo bash /opt/klarim/deploy/deploy.sh
```

`deploy/deploy.sh` marca `/opt/klarim` como `safe.directory`, faz `git pull` →
`docker compose down` → `up -d --build` → `docker compose ps` → health check em
`http://localhost:8000/health`.

### CI/CD automático

`.github/workflows/deploy.yml` roda a **todo push para `main`**:

1. **Job `test`** — Python 3.12, `pip install -r requirements.txt`, `pytest`.
   Se falhar, **bloqueia o deploy** (`deploy` tem `needs: test`).
2. **Job `deploy`** — autentica no GCP via **Workload Identity Federation**
   (`google-github-actions/auth`, sem chave), conecta na VM via
   `gcloud compute ssh` e executa `deploy/deploy.sh`.

**Autenticação keyless (WIF).** O projeto proíbe chaves de service account (org
policy `iam.disableServiceAccountKeyCreation`), então o CI autentica por OIDC —
nenhuma credencial de longa duração. Recursos criados (KL-3):

- SA `klarim-deploy@project-b08050df-fa4e-49ac-919.iam.gserviceaccount.com`
  com `roles/compute.instanceAdmin.v1` **e** `roles/iam.serviceAccountUser` na
  SA da VM (`10946387758-compute@developer…`, exigido pelo `gcloud compute ssh`).
- Pool `github-pool` + provider `github-provider` (issuer GitHub), com condição
  travada no repo `joaquim-83/klarim`.
- Binding `roles/iam.workloadIdentityUser` da SA só para esse repo.

**Secrets no GitHub** (configurar manualmente — o repo nunca guarda credenciais):
`GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`, `GCP_PROJECT_ID`, `GCP_INSTANCE`, `GCP_ZONE`.

**Regra de segurança:** nunca commitar chaves SSH, service account keys ou o
`.env` de produção. Tudo sensível vive em GitHub Secrets ou na VM.

---

## 9. Relatórios PDF (`reporter/`)

O relatório PDF é **o produto que o Klarim vende**. O módulo `reporter/` converte
um `ScanReport` em dois PDFs via **Jinja2 (HTML) → WeasyPrint (PDF)**:

- **Executivo** (`generate_executive_pdf`) — 1-2 páginas, dono do negócio:
  semáforo grande, linguagem acessível, bloco LGPD, lista de problemas em
  linguagem humana, referral.
- **Técnico** (`generate_technical_pdf`) — 3-5 páginas, dev/agência: tabela de
  todos os checks, detalhamento de cada FALHA (evidência + impacto + correção com
  exemplo de código) e inventário (domínios externos, scripts sem SRI, fontes
  arriscadas, headers HTTP).

Ambas são `async` (renderização roda em `asyncio.to_thread`) e retornam `bytes`.

- **Conteúdo por check:** `ACCESSIBLE` (frases de negócio) e `TECHNICAL`
  (impacto + correção) em `reporter/generator.py`, indexados por `check_id`. Ao
  adicionar um check novo, acrescente a entrada nos dois dicionários.
- **Identidade visual:** paleta dark (`#0D1117` fundo, `#FF6B35` alerta,
  `#00D26A` ok, `#E6EDF3` texto). CSS embutido nos templates.
- **WeasyPrint** precisa de libs nativas (pango/cairo) — já no `Dockerfile` e no
  job `test` do CI. Localmente, em macOS: `brew install pango`.

Uso: CLI `--pdf`, ou endpoints `GET /report/executive?url=` e
`GET /report/technical?url=` (retornam `application/pdf`).

---

## 10. Interface web (`frontend/`)

Frontend **React + Vite + Tailwind v4**, servido como build estático pelo
**Nginx**, que também faz proxy de `/api` para a API FastAPI.

- **Telas:** `Landing` (`/`, input de scan), `Scan` (`/scan?url=`, loading com
  mensagens rotativas), `Result` (`/result?url=`, semáforo + severidades + LGPD +
  CTA), `Report` (`/report?url=`, download dos dois PDFs). Roteamento client-side
  com `react-router-dom`; SPA fallback no Nginx (`try_files … /index.html`).
- **API:** todas as chamadas vão para `/api/...`. Em produção o Nginx encaminha
  para `http://api:8000/`; em dev o proxy do Vite faz o mesmo (`vite.config.js`).
- **Paleta:** definida em `src/index.css` via `@theme` do Tailwind v4 (gera
  utilitários `bg-klarim-*`, `text-klarim-*`). **Não há `tailwind.config.js`** —
  v4 é CSS-first.
- **Como rodar:**
  ```bash
  cd frontend
  npm install          # gera/atualiza o package-lock.json (necessário p/ npm ci)
  npm run dev          # dev server (proxy /api → localhost:8000)
  npm run build        # build de produção → dist/
  ```
- **Docker:** serviço `web` no `docker-compose.yml` (build `./frontend`, portas
  **80** e **443**). A API foi rebaixada para `127.0.0.1:8000` (só o Nginx é
  público). O deploy na VM constrói a imagem do frontend (Vite) durante
  `docker compose up`.

### HTTPS (Let's Encrypt) — KL-6

O Nginx é **self-healing** quanto a TLS: o entrypoint
(`frontend/docker-entrypoint.d/40-klarim-tls.sh`) escolhe a config em runtime —
**`DOMAIN` vazio ou sem certificado ⇒ HTTP** (`nginx/http.conf`); **`DOMAIN`
definido + certificado presente ⇒ HTTPS** (`nginx/https.conf.template` via
envsubst), com redirect 80→443 e os security headers (HSTS, CSP, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy). Assim o deploy **nunca quebra** por
falta de certificado.

**Emitir o certificado (uma vez, na VM, após o DNS apontar para o IP):**
```bash
sudo bash /opt/klarim/deploy/setup-https.sh <dominio>   # ex.: klarim.com.br
```
O script usa **webroot** (sem downtime), grava `DOMAIN=<dominio>` no `.env` da VM
e recria o `web` em HTTPS.

**Renovação:** automática — o `deploy.sh` roda `certbot renew` a cada deploy
(deploy-hook recria o `web`); o pacote `certbot` também instala um timer.
Volumes: `/etc/letsencrypt` e `/var/www/certbot` (host) montados no `web` (ro).
Firewall GCP: `klarim-allow-http` (80) + `klarim-allow-https` (443), tag `http-server`.

**Subdomínio `painel.klarim.net` (dashboard admin).** O mesmo certificado cobre
`klarim.net`, `www.klarim.net` **e** `painel.klarim.net` (SAN adicionado com
`certbot certonly --webroot -w /var/www/certbot -d klarim.net -d www.klarim.net -d
painel.klarim.net --cert-name klarim.net --expand`). O `https.conf.template` tem um
**server block dedicado** para `painel.${DOMAIN}` (443): serve o mesmo build React,
faz proxy `/api/` → `api:8000`, aplica os mesmos security headers e **redireciona a
raiz `/` → `/painel/login`**. O bloco 80 inclui `painel.${DOMAIN}` (ACME + redirect
para HTTPS). Em modo sem-cert (`http.conf`, catch-all), o subdomínio também
funciona e a raiz redireciona ao login. Sem nova regra de firewall (mesmo IP/porta).
Acesso: `https://painel.klarim.net` (equivalente a `https://klarim.net/painel`).

### Hardening de segurança (auto-auditoria)

O Klarim pratica o que prega — a superfície de ataque real é minimizada:

- **Docs da API desligados em produção.** O FastAPI só expõe `/docs`, `/redoc` e
  `/openapi.json` quando `KLARIM_DEV_MODE=true` (senão `docs_url/redoc_url/
  openapi_url=None` ⇒ **404**). Evita mapear a API inteira num request.
- **Rate limit no login.** `POST /auth/login` limita **5 tentativas/min por IP**
  (via `X-Real-IP` do Nginx); a 6ª retorna **429** com `Retry-After`. In-memory
  (`_login_attempts`); mover para Redis se houver múltiplos workers.
- **Sanitização anti stored-XSS no `/events`.** `_sanitize_str`/`_sanitize_metadata`
  removem tags HTML e esquemas (`javascript:`/`data:`), limitam tamanho e
  profundidade antes de gravar. O React já escapa `{}` (sem `dangerouslySetInnerHTML`).
- **Nginx bloqueia paths sensíveis** (`http.conf` + os blocos 443 do
  `https.conf.template`): `location` regex retorna **404** para dotfiles
  (`/.env`, `/.git`…), extensões perigosas (`.php|.sql|.bak|.log|.ya?ml|.toml|
  .ini|.conf|.config`) e paths de outros frameworks (`phpinfo`, `wp-admin`,
  `administrator`…) — em vez de 200 com a SPA. O ACME usa `location ^~
  /.well-known/acme-challenge/` para ter prioridade sobre os regex (não quebra a
  renovação). `/api/` e `/painel/` **não** são afetados. Valide a sintaxe com
  `nginx -t` antes de deployar (config ruim derruba o `web`).

---

## 11. Pagamento — AbacatePay PIX (`payments/`)

Fluxo: semáforo grátis → CTA → cria cobrança PIX → QR code inline → polling do
status → PAID → libera o download dos PDFs. Webhook confirma server-side (redundância).

- **`payments/abacatepay.py`** — client httpx da AbacatePay v2 (`create_pix_charge`,
  `check_payment`, `create_webhook`, `simulate_payment` [dev], `verify_webhook_signature`).
  Timeout 15s, retry/backoff em 5xx. Valores em **centavos**.
- **`payments/store.py`** — persistência de cobranças em **PostgreSQL** (tabela
  `payments`, psycopg2 em thread) com **fallback em memória** se não houver DB.
- **`payments/models.py`** — `Charge`, `PaymentStatus`, `PRICING` (MVP usa
  `standard` = R$ 29).

**Endpoints** (`api/main.py`): `POST /payment/create` (retorna QR), `GET
/payment/status?charge_id=` (polling), `POST /webhooks/abacatepay` (query-secret
obrigatório + HMAC defense-in-depth). Os `/report/*` exigem `charge_id` **pago**
senão **402** — exceto em modo livre.

**Modo livre (PDFs sem pagamento):** `KLARIM_DEV_MODE=true` **ou**
`ABACATEPAY_API_KEY` vazia (sem chave não há como cobrar → não bloqueia). Com a
chave configurada e dev mode off, o pagamento é exigido.

**Variáveis de ambiente** (no `.env` da VM, **nunca commitadas**):
`ABACATEPAY_API_KEY`, `ABACATEPAY_WEBHOOK_SECRET`, `KLARIM_DEV_MODE`
(opcional `ABACATEPAY_HMAC_STRICT`). Chave `abc_dev_...` = sandbox;
`simulate_payment` só funciona com chave dev.

**Webhook:** registrar o endpoint como
`https://klarim.net/api/webhooks/abacatepay?webhookSecret=<secret>` (o mesmo
valor de `ABACATEPAY_WEBHOOK_SECRET`).

---

## 12. E-mail — Resend (`notifier/`)

Dois usos: **alerta gratuito** (anzol do funil, semáforo) e **entrega do
relatório** pago (2 PDFs anexados).

- **`notifier/email_client.py`** — `KlarimMailer` com `send_alert`, `send_report`,
  `send_test`. SDK `resend` (síncrono) encapsulado em `asyncio.to_thread`.
  Templates Jinja2 **table-based** (compatível com Gmail/Outlook), paleta dark.
- **Endpoints** (`api/main.py`): `POST /email/test`, `POST /email/send-alert`
  (scan + alerta), `POST /email/send-report` (exige cobrança paga; anexa PDFs).
- **Envio automático:** ao confirmar pagamento (webhook **ou** polling), se a
  cobrança tem `buyer_email` e ainda não enviou, dispara o e-mail do relatório em
  **background** (`asyncio.create_task`), idempotente (`report_email_sent`). Falha
  é só logada — o cliente sempre pode baixar o PDF no site (fallback).
- **Fluxo de compra:** a tela `/pay` pede o e-mail **antes** de gerar a cobrança;
  ele é salvo em `payments.buyer_email`.

**Variáveis** (`.env` da VM, **nunca commitadas**): `RESEND_API_KEY`,
`RESEND_FROM`. Sem domínio verificado, `RESEND_FROM=Klarim <onboarding@resend.dev>`
só envia para o e-mail dono da conta Resend. Para enviar a qualquer destinatário,
**verificar o domínio** `klarim.net` no painel Resend (registros DNS SPF/DKIM/DMARC
na Hostinger — ver `claude/reports/KL-8_email-resend.md`) e trocar para
`Klarim <seguranca@klarim.net>`. A chave fornecida é **send-only** (não gerencia
domínios via API — isso é feito no painel).

---

## 13. Cache de scan + feedback de e-mail (KL-9)

**Cache (Redis).** Cada scan leva ~30s. `scanner/cache.py` (`ScanCache`) cacheia
o `ScanReport` no Redis (mesma instância do compose, `REDIS_URL`) com **TTL 1h**.
Chave: `scan:<sha256(url normalizada)[:16]}` (url em lowercase, sem `/` final).
Serialização JSON via `ScanReport.to_dict()`/`from_dict()`. A API usa
`get_or_scan(url)` (dentro de `_safe_scan` e da task de e-mail): (1) cache hit →
instantâneo; (2) **fallback no banco** — em cache miss, reusa o scan mais recente
(< 1h) da tabela `scans` (`get_recent_scan_checks` → `from_dict` → reaquece o
cache), **sem reescanear**; (3) só então escaneia de novo. Redis/banco fora do ar
degrada com elegância. Resultado: o link do e-mail e o **PDF pós-pagamento carregam
em < 3s** mesmo se o cache Redis já expirou.

**Feedback de e-mail.** A cobrança ganhou `email_status`
(`null|pending|sending|sent|failed`). `GET /payment/status` devolve `buyer_email`
+ `email_status`. Transições: create com e-mail → `pending`; ao confirmar
pagamento → `sending` (antes de agendar a task); task → `sent`/`failed`. O
frontend (`/report`) faz polling e mostra o banner (enviando → enviado/falhou);
`/pay` mostra "Enviando relatório para <e-mail>…" antes de redirecionar.

---

## 14. Recuperação de relatórios (KL-10)

Cliente que pagou mas não recebeu o relatório (e-mail no spam, trocou de
aparelho, perdeu o link) recupera o acesso em `klarim.net/recuperar` via link
temporário por e-mail.

- **Tabela `recovery_tokens`** (`token`, `buyer_email`, `expires_at`, ...) — token
  `secrets.token_urlsafe(48)` (64 chars), **TTL 24h**, reutilizável até expirar.
- **Endpoints:** `POST /recovery/request` (gera token + envia link — **sempre**
  resposta genérica, não revela se o e-mail existe), `GET /recovery/validate?token=`
  (lista os relatórios pagos, e-mail mascarado), `GET /recovery/download?token=&charge_id=&type=`
  (PDF via token, com **validação cruzada**: o charge precisa pertencer ao e-mail
  do token, senão 401).
- **Segurança:** resposta genérica (anti-enumeração), **rate limit 3/e-mail/hora**,
  token seguro, TTL 24h, validação cruzada, e-mail mascarado (`h***l@example.com`).
  O `POST /recovery/request` roda em **background** (`_spawn`) para o tempo de
  resposta não vazar se o e-mail existe.
- **Frontend:** `/recuperar` (solicitar) e `/recuperar/acesso?token=` (listar +
  baixar). Link "Recuperar relatórios" no footer de todas as telas.
- **E-mail:** `notifier/templates/recovery.html` + `KlarimMailer.send_recovery_link`.

---

## 15. Discovery Worker (KL-11 + KL-15) — `discovery/`

Motor de aquisição (modelo contínuo desde o KL-15): um **poller de CT logs** lê os
CT logs públicos em tempo real, filtra domínios `.com.br` e os acumula num buffer;
a cada `DISCOVERY_INTERVAL_MINUTES` (padrão 30) o worker drena o buffer, filtra por
presença de e-mail de contato, registra como alvo e enfileira para scan.

- **`ct_poller.py` (KL-15, fonte primária)** — `CTLogPoller`: descobre os CT logs
  "usable" da lista oficial do Google (`CT_LOG_LIST_URL`, auto-adapta à rotação de
  shards por ano), amostra o **topo** de cada log via `get-sth`/`get-entries`,
  parseia o `MerkleTreeLeaf` (RFC 6962) → cert DER → extrai os domínios do SAN com
  **`cryptography`** (já é dependência), filtra `.com.br` (`normalize_domain`) e
  enche um buffer (set, dedup). Roda numa thread daemon (`start_listener`), expõe
  `flush_buffer`/`get_stats`. Motivo do KL-15: **o Certstream público (calidog)
  está morto** (conecta e não envia nada — confirmado da VM) e o crt.sh é instável.
- **`ct_client.py` (KL-11, fallback)** — crt.sh. Se o poller não coletar nada num
  ciclo, o worker tenta o crt.sh (Postgres público `crt.sh:5432` + fallback JSON).
  `normalize_domain` (wildcards, infra `mail./api./cdn.`, não-`.com.br`, domínio
  registrável) é **compartilhado** pelo poller e pelo crt.sh. ⚠️ crt.sh é instável.
- **`fingerprint.py`** — plataforma (duda, wordpress, cra, wix, squarespace, shopify).
- **`contact.py`** — melhor e-mail (mailto > texto > meta; fallback `/contato`);
  descarta genéricos (noreply, webmaster) e de terceiros (duda.co, wixpress…);
  prefere o mesmo domínio do site. **`_is_valid_email` (KL-19)** rejeita nomes de
  arquivo (`.css/.js/.png…`), placeholders (`seuemail@`, `email@email.com.br`) e
  domínios de exemplo — evita bounce/reputação. **Sem e-mail válido ⇒
  `status='sem_contato'`.**
- **`classifier.py`** — setor + `price_tier` + **confiança** por **cascata de 3
  camadas** (refino do KL-11), da pista mais forte para a mais fraca: **(1)
  domínio** (o dono batizou o site — `hotelverdegreen`→hotel, conf 0.9; 2 padrões
  do mesmo setor → 0.95); **(2) cabeçalho** `<title>/<h1>/meta` (peso 5×; conf
  0.7–0.8); **(3) conteúdo limpo** do body — `extract_visible_text` remove
  `nav/footer/header/script/style` antes de contar keywords (peso 1×; conf ≥0.5).
  Sem pista ⇒ `('outro', 0.0)`. Keywords **ambíguas** ("reserva", "produto",
  "entrega") só contam com **co-ocorrência** de uma âncora do mesmo setor (evita
  que "direitos reservados" vire hotel). Keywords casam **sem acento** (`_fold`).
  `classify_sector(html, url)` retorna `(setor, tier, confiança)`; é síncrono (CPU
  puro). A confiança é gravada em `targets.classification_confidence` (REAL). 11
  setores + `outro`. **Reclassificação:** `POST /admin/reclassify-domains`
  (instantâneo, só domínio, nunca rebaixa para `outro`) e `POST
  /admin/reclassify-all` (background, refaz fetch, 1/s; `GET
  /admin/reclassify-status`). No painel **Alvos**: badge com indicador de confiança
  (≥0.8 normal · 0.5–0.79 pontilhado · <0.5 cinza com "?") + filtro "Classificação
  incerta" + botão "Reclassificar domínios".
  **Classificação manual (operador):** coluna `targets.classification_source`
  (`auto|domain|manual`). `PATCH /targets/{id}/classify {sector, price_tier?}` (tier
  derivado do setor se omitido) e `POST /admin/classify-batch {target_ids, sector}`
  gravam `source='manual'`, `confidence=1.0`. **Manual nunca é sobrescrito** pelo
  automático: o `register_target` (UPSERT) preserva setor/tier de alvos manuais, e
  reclassify-domains/all pulam `source='manual'` (log `[reclassify] pulando target
  N`). No painel: edição inline do setor (dropdown ✏️ na lista e no detalhe, com
  🔒 quando manual) + seleção múltipla → "Classificar selecionados"
  (`components/admin/SectorEditor.jsx`).
- **`store.py`** — `TargetStore` (Postgres): tabelas **`targets`** e **`scans`**
  (criadas no `ensure_schema`, mesmo padrão de `payments`). Conecta por
  `POSTGRES_*` (imune a `/` na senha).
- **`worker.py`** — `DiscoveryWorker`: inicia o poller (thread) + heartbeat de
  status; `run_cycle()` a cada `DISCOVERY_INTERVAL_MINUTES` (30): drena o buffer
  (ou fallback crt.sh) → por domínio: fetch + fingerprint + e-mail + setor →
  registra → enfileira. Publica o status no Redis (`discovery:status`) pra API.
  Serviço `discovery` no compose (roda os 3 loops: descoberta + alertas + re-scan).
  **Blindagem (KL-19):** cada domínio roda sob `asyncio.wait_for
  (DISCOVERY_DOMAIN_TIMEOUT=30s)` — um site travado é pulado (`timeouts` no stat),
  não congela o loop; e um **watchdog em thread** (`DISCOVERY_WATCHDOG_SECONDS=600`)
  faz `os._exit(1)` se o event loop não progride, deixando o `restart:unless-stopped`
  subir de novo. `docker-compose` tem `HEALTHCHECK` (`discovery/healthcheck.py`,
  checa heartbeat no Redis) para visibilidade. Motivo: o incidente de 08/07 03:20,
  em que um domínio travado congelou discovery+alert+rescan por 7,5h.

**Scan worker** (`scanner/main.py --worker`) agora é async: consome a fila
`{target_id, url}`, escaneia, **cacheia (KL-9)**, salva em `scans` + atualiza
`targets`, com rate limit `WORKER_MAX_SCANS_PER_HOUR` (50 → 72s entre scans).

**API de gestão:** `GET /targets` (filtros), `GET /targets/stats`, `POST /targets/add`,
`POST /targets/{id}/scan`, `GET /scans`, `GET /scans/{id}`,
**`GET /discovery/status`** (KL-15: estado do poller — connected/total_seen/
total_matched/buffer_size + ciclos + alvos descobertos hoje; via Redis, JWT).

**Regra de negócio inviolável:** só escanear sites com e-mail de contato. Sem
e-mail = sem conversão = não vale o custo do scan.

## 16. Alert Worker + calibração do semáforo (KL-12) — `discovery/alert_worker.py`

**Calibração do semáforo (`scanner/scoring.py`):** 🟢 **Verde** exige score **≥ 90
E zero FALHAS de severidade Alta/Crítica**; 🟡 **Amarelo** = score ≥ 50 (ou ≥ 90
mas com FALHA Alta/Crítica); 🔴 **Vermelho** = score < 50. `_semaphore(score,
has_high_fail)` recebe o flag de falha alta calculado no `compute_score`. Motivo:
um site com nota alta mas com falha grave não deve exibir "tudo certo" (verde).

**Alert Worker** dispara o alerta gratuito (o anzol do funil) para alvos já
escaneados que têm falhas:

- **`alert_worker.py`** — `AlertWorker.run_cycle()`: busca elegíveis
  (`status='scanned'`, com e-mail, `fail_count>0`, sem alerta nos últimos 30d, não
  `unsubscribed`), respeita throttle (`MAX_ALERTS_PER_HOUR=10`,
  `MAX_ALERTS_PER_DAY=50`, contados no `alert_log`), envia via `KlarimMailer.send_alert`
  com **pausa de 5s** entre e-mails, marca `status='alerted'` + `last_alert_at` e
  registra em `alert_log`. Loop a cada `ALERT_INTERVAL_HOURS` (1h).
- **Mesmo container do Discovery Worker:** `discovery/worker.py` `main()` roda
  `asyncio.gather(DiscoveryWorker().start(), AlertWorker().start())`.
- **`store.py`** — tabela **`alert_log`** (histórico/throttle) + métodos
  `get_eligible_targets_for_alert`, `mark_target_alerted`, `log_alert`,
  `count_alerts_last_hours`, `list_alerts`, `alert_stats`, `mark_unsubscribed`.
- **Descadastro (unsubscribe):** token **HMAC-SHA256** do e-mail (`UNSUBSCRIBE_SECRET`,
  gerado na VM com `openssl rand -hex 32`). Link no rodapé do alerta →
  `GET /api/unsubscribe?email&token` valida (constant-time) e marca
  `status='unsubscribed'`. `notifier`: `unsubscribe_token` / `build_unsubscribe_link`.
- **API:** `GET /alerts` (histórico), `GET /alerts/stats`, `POST /targets/{id}/alert`
  (dispara manual, ignora throttle/janela), `GET /unsubscribe`.

**Regra inviolável:** o throttle protege a reputação do domínio no Resend — nunca
remover os tetos nem a pausa entre envios.

## 17. Re-scan Worker + e-mail de evolução (KL-13) — `discovery/rescan_worker.py`

Fecha o ciclo de vida do alvo: a cada 30 dias reescaneia sites já engajados,
compara o score com o anterior e envia um e-mail de evolução (re-engajamento sem
descobrir alvos novos).

- **`rescan_worker.py`** — `RescanWorker.run_cycle()` (loop 24h,
  `RESCAN_INTERVAL_HOURS`): (1) reenvia e-mails de evolução pendentes do ciclo
  anterior (`_flush_pending`); (2) `get_targets_for_rescan` (status `scanned`/
  `alerted`, com e-mail, `last_scan_at` > `RESCAN_AGE_DAYS`=30); (3) por alvo, com
  pausa igual ao scan worker (`WORKER_MAX_SCANS_PER_HOUR`): reescaneia, salva em
  `scans`, atualiza `targets`, **cacheia (KL-9)**, classifica a evolução e envia o
  e-mail. `classify_evolution` → `improved` / `worsened` / `unchanged` /
  `first_rescan`. A função `rescan_target()` é compartilhada com a API.
- **Throttle GLOBAL compartilhado com o Alert Worker:**
  `count_proactive_emails_last_hours` soma `alert_log` + `rescan_log`. No teto, o
  re-scan acontece (dados atualizados) e o e-mail fica **pendente**
  (`rescan_log.email_id IS NULL`) para reenvio no próximo ciclo. Após enviar uma
  evolução, `mark_target_contacted` seta `last_alert_at` (evita alerta duplicado).
- **`notifier`** — 3 templates (`evolution_improved/worsened/unchanged.html`) +
  `KlarimMailer.send_evolution` (escolhe o template pelo tipo). Preço do CTA vem de
  `payments.PRICING` pelo `price_tier` do alvo.
- **`store.py`** — tabela **`rescan_log`** + métodos `get_targets_for_rescan`,
  `log_rescan`, `update_rescan_email`, `get_pending_evolution_emails`,
  `list_rescans`, `rescan_stats`, `count_proactive_emails_last_hours`,
  `mark_target_contacted`.
- **Mesmo container:** `discovery/worker.py` `main()` roda três loops —
  `asyncio.gather(DiscoveryWorker, AlertWorker, RescanWorker)`.
- **API:** `GET /rescans` (filtros target_id/evolution), `GET /rescans/stats`,
  `POST /targets/{id}/rescan` (força re-scan + e-mail, ignora janela/throttle).

**Nota de design:** o re-scan roda **inline** no worker (como o Alert Worker),
não via re-enfileiramento na `klarim:scan_queue`, porque a comparação de score + o
e-mail + o throttle compartilhado + a fila de pendentes vivem melhor num só lugar;
o `rescan_log` já distingue os re-scans dos scans normais.

## 18. Dashboard admin (KL-14) — `klarim.net/painel`

Painel do operador (login único) para operar e monitorar o Klarim. Faz parte do
**mesmo app React** (`frontend/`) — rotas `/painel/*` protegidas por JWT. Sem novo
domínio, container ou certificado; o Nginx (`try_files … /index.html`) já cobre o
SPA.

**Autenticação (`api/main.py`):**
- `POST /auth/login {username, password}` → `{token, expires_in: 86400}`.
  Credenciais em `ADMIN_USER`/`ADMIN_PASSWORD`; JWT (PyJWT, HS256, 24h) assinado
  com `JWT_SECRET`. Sem tabela de usuários (é um operador só).
- **Middleware** (`_admin_auth_mw`) protege os prefixos `/targets`, `/scans`,
  `/alerts`, `/rescans`, `/email`, `/payments`, `/config` — exigem
  `Authorization: Bearer <token>` (401 se ausente/inválido/expirado). Rotas
  públicas ficam livres: `/health`, `/scan/summary`, `/payment/*`, `/report/*`,
  `/webhooks/*`, `/recovery/*`, `/unsubscribe`, `/auth/login`.
- Segredos gerados **na VM** (`openssl rand -hex 32`), nunca no repo.

**Endpoints novos de gestão** (protegidos): `GET /targets/{id}`,
`POST /targets/{id}/discard`, `GET /scans/stats`, `/scans/daily`, `/alerts/daily`,
`GET /scans/{id}/report/{executive|technical}` (PDF sem gating de pagamento),
`GET /payments/list`, `/payments/stats`, `GET /config` (params operacionais, sem
segredos). `list_targets` passou a trazer `last_semaphore` (JOIN scans);
`list_alerts`/`list_rescans` trazem a `url` do alvo.

**Frontend (`frontend/src/`):**
- `lib/auth.js` (token no localStorage + checagem de exp), `lib/adminApi.js`
  (Bearer + redirect em 401 + `adminDownload` para PDFs), `lib/useAsync.js`.
- `components/admin/` — `AdminLayout` (sidebar responsiva + logout),
  `ProtectedRoute`, `ui.jsx` (Card/StatCard/Badge/SemaphoreDot/Pagination…).
- `pages/admin/` — `Login`, `Overview` (KPIs + **Recharts**: donut status, bar
  plataforma, 2 line charts diários, atividade recente), `Alvos` (lista + filtros
  + ações + modal), `AlvoDetalhe` (ficha + históricos + ações), `Scans`,
  `ScanDetalhe` (checks + PDF), `Alertas`, `Pagamentos`, `Rescans`, `Config`
  (read-only).
- **Code-split:** o painel é `lazy()` — o site público não baixa o bundle do
  dashboard (Recharts fica num chunk separado).

**Variáveis (`.env` da VM):** `ADMIN_USER`, `ADMIN_PASSWORD`, `JWT_SECRET`.

**Acesso:** `https://painel.klarim.net` (subdomínio dedicado, redireciona ao login)
ou `https://klarim.net/painel/login`. Ver o subdomínio na seção 10 (HTTPS).

## 19. Integração completa (KL-17) — scans públicos + fluxo admin + rastreabilidade

Fecha os gaps de integração entre o site público, o scanner e o painel.

- **Scans públicos gravam no banco.** `GET /scan/summary` agora, além de cachear
  (KL-9), **ingere em background** (`_spawn`, source='public') via
  `discovery/ingest.py::ingest_scan`: registra/atualiza o `target` (fingerprint +
  setor + e-mail, como o Discovery) e salva o `scan`. Só na cache **miss** (scan de
  verdade) — o response volta imediato do cache; o visitante não espera o banco.
- **Origem do scan.** Coluna `scans.source` (`public|discovery|admin|manual|
  rescan`). A fila de scan (`{target_id,url,source}`) carrega a origem: Discovery →
  `discovery`, `POST /targets/add` → `manual`, `POST /targets/{id}/scan` → `admin`,
  rescan worker → `rescan`. `list_scans`/`list_targets` filtram por `source`.
- **Fluxo admin num request:** `POST /admin/scan-and-report {url, send_email?,
  email_to?, email_type}` — escaneia (cache/fresh) → `ingest_scan(source='admin')`
  → devolve checks + plataforma + setor + e-mail + ids → opcionalmente envia alerta
  ou relatório. Tela **`/painel/escanear`** (input → resultado inline → modal de
  e-mail).
- **Reenvio (JWT, ignora throttle):** `POST /admin/resend-alert {target_id}`,
  `POST /admin/send-report {target_id, email_to?}` (2 PDFs),
  `POST /admin/resend-payment {charge_id}` (reusa o caminho pós-pagamento).
- **Vínculo pagamentos ↔ alvos:** `payments` não muda; a API casa por URL —
  `GET /payments/list` traz `target_id` (via `map_urls_to_target_ids`) e
  `GET /targets/{id}/payments` lista as cobranças do alvo. No painel: Pagamentos
  linka o site → alvo; AlvoDetalhe tem seção "Pagamentos" + "Reenviar relatório".
- **Idempotência:** `register_target` faz UPSERT por URL (não duplica alvo);
  scan repetido atualiza `last_scan_*` e grava um novo `scan` (histórico).

**Sidebar:** Visão geral · **Escanear** · Alvos · Scans · Alertas · Pagamentos ·
Re-scans · **Sistema** · Configurações.

## 20. Dashboard operacional (KL-16) — `/painel/sistema`

Visão de operação em tempo real (auto-refresh a cada 30s).

- **Heartbeat dos workers** (`discovery/heartbeat.py`): cada worker publica
  `worker:<name>:status` no Redis com **TTL 600s** (10min). Se o worker morre, a
  chave expira e o painel mostra 🔴. Alert/Re-scan/Scan têm um loop de heartbeat a
  cada 60s (independente do ciclo, que é de horas); o Discovery reusa o
  `discovery:status` do KL-15 (TTL baixado para 600s). O Scan Worker faz `blpop`
  com timeout de 30s para bater o heartbeat mesmo com a fila vazia.
- **`GET /api/system/status`** (JWT): estado dos 4 workers (alive + últimos
  ciclos + stats), health das dependências e métricas de e-mail.
- **`api/health_checks.py`**: `postgres` (SELECT 1), `redis` (ping), `ct_logs`
  (lê `discovery:status`), `resend` (GET /domains), `abacatepay` (GET /billing/list)
  — cada um `{status, latency_ms, detail}`, nunca levanta; rodam em paralelo.
- **`GET /api/system/activity?limit=`** (JWT): timeline intercalada das últimas
  ações (scans, alertas, re-scans, pagamentos), ordenada por data.
- **Métricas de e-mail:** `store.email_metrics()` soma `alert_log` + `rescan_log`
  (hoje/semana/mês); `throttle_used = enviados_hoje/MAX_ALERTS_PER_DAY`.
- **Frontend `/painel/sistema`:** cards 🟢/🔴 por worker, health das dependências,
  métricas de e-mail e log de atividade — polling a cada 30s (`setInterval`).

**Limpeza (KL-16):** as 4 cobranças simuladas do sandbox (`simulate_payment`,
`paid_at` instantâneo) + cobranças de teste (URLs example/klarim/igoove) foram
removidas; ficou só o pagamento **real** (pousadacostera, R$ 29, 36s para pagar).
`payments/stats` → **R$ 29,00, 1 pago**. `alert_log` preservado (alertas reais do
funil).

## 21. Mensagens de risco dinâmicas (KL-20) — `reporter/risk_messages.py`

O bloco fixo de LGPD ("sanções de até R$ 50 milhões") foi trocado por **riscos
concretos** por falha — o dono de PME reage a "seu site pode ser usado para golpes",
não a artigos de lei.

- **`reporter/risk_messages.py`** (módulo leve, sem WeasyPrint): `RISK_MESSAGES`
  (headline + risco + ícone para os **15 checks**, indexado por `check_id`);
  `get_risk_messages(report)` — filtra os FAILs, ordena por severidade, limita a 4;
  `get_risk_summary(risks)` — frase-resumo por categoria (vazamento de dados /
  golpes / invasão / código de terceiros). Aceita ScanReport, dict ou lista.
- **`reporter/__init__.py`** virou **lazy** (PEP 562 `__getattr__`) para que
  importar `reporter.risk_messages` não puxe o WeasyPrint nos containers do worker.
- **Onde aparece** (mesmos riscos em todas as superfícies, consistente):
  PDF executivo (`executive.html`), e-mail de **alerta** (`alert.html`, máx 3) e de
  **evolução** (`evolution_worsened/unchanged.html`), tela pública `/result`, e a
  tela admin **Escanear**. A LGPD virou **nota de rodapé** discreta.
- **API:** `/scan/summary` e `/admin/scan-and-report` retornam `risk_messages` +
  `risk_summary`. Os workers (alert/rescan) e os helpers de e-mail computam os
  riscos do `checks_json` e passam para `send_alert`/`send_evolution`.
- **Sem FAILs ⇒ sem seção de risco** (ex.: PDF de site 100/100, e-mail de melhoria).

## 22. Tracking da jornada do lead (KL-21) — `site_events` + UTM + Analytics

Tracking **100% interno** (sem GA4/terceiros) do funil pós-alerta: e-mail enviado →
link clicado → resultado visto → CTA → PIX gerado → pago → PDF baixado.

- **Tabela `site_events`** (`event_type, session_id, target_url, target_id,
  page_url, referrer, utm_*, metadata, created_at`) + índices. Tipos:
  `page_view, scan_started, scan_completed, result_viewed, cta_clicked,
  payment_created, payment_completed, report_downloaded, email_link_clicked`.
- **`POST /api/events`** (público, sem JWT): fire-and-forget — valida o tipo,
  **rate limit 100/min por sessão** (in-memory), resolve `target_id` de
  `utm_content` (`target_<id>`), grava em **background** (`_spawn`) e responde
  `{ok:true}` na hora. Nunca bloqueia.
- **Frontend `lib/tracker.js`:** `session_id` (sessionStorage + `crypto.randomUUID`),
  captura os **UTM na 1ª página** e persiste (some da URL ao navegar), `trackEvent`
  fire-and-forget (`keepalive`, `.catch(()=>{})`). `page_view` em cada rota pública
  (App.jsx, ignora `/painel`); os outros 7 eventos disparados nas páginas do funil.
- **UTM nos e-mails** (`notifier`): `utm_result_link(url, campaign, target_id)` —
  alerta `utm_campaign=alerta`, evolução `evolucao_<tipo>`, recuperação
  `recuperacao`; `utm_content=target_<id>` (ou o domínio).
- **Analytics (JWT):** `GET /api/analytics/{funnel|abandoned|campaigns|pages|events}`
  (`?period=today|7d|30d|total`). Tela **`/painel/analytics`**: funil de conversão
  (barras + %), carrinho abandonado (PIX gerado sem pagar + tempo no site),
  atribuição por campanha, páginas mais visitadas, timeline de eventos. Sidebar:
  item **Analytics** (entre Re-scans e Sistema).
- **Contagem do funil:** `COUNT(DISTINCT session_id)` por etapa em `site_events`;
  o topo (e-mails enviados) vem do `alert_log`.
