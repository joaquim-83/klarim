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

**Modelo de negócio (bottom-up, funil KL-27):** o scan **gratuito** roda os 15
primeiros checks e mostra só score + semáforo + contagem + a lista de verificações
(✅/❌, sem detalhes) — os outros 14 aparecem **bloqueados** (🔒). O **relatório
completo** (todos os checks, com evidências e correções) custa **R$ 19** (preço único,
todos os setores — decisão de impulso) e inclui **1 re-verificação gratuita**
("retorno médico"). O dono encaminha o relatório para a **agência** que fez o site.
Quando várias agências recebem relatórios de vários clientes, elas procuram o
Klarim organicamente — a venda B2B acontece **sem prospecção**. Detalhes do funil
na seção **26**.

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
│       ├── dns_util.py     # helpers DNS mockáveis (SPF/DKIM/DMARC/CNAME — KL-22)
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
├── mcp_server/             # servidor MCP (KL-18) — operar o Klarim via Claude (SSE)
│   ├── _base.py            # instância FastMCP + helpers (_guard/_api/_store)
│   ├── server.py           # app SSE (mcp_app) + propagação de token
│   ├── auth.py             # MCPAuthMiddleware (ASGI, fail-closed, Bearer/?token=)
│   └── tools/              # as 25 tools por domínio (system/targets/scans/…)
└── tests/                  # pytest
    ├── test_checks.py      # unit tests dos checks + teste online opt-in
    ├── test_checks_16_29.py # unit tests dos checks 16–29 (KL-22, rede mockada)
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
específico como identidade do produto. Conjunto atual (**48**):

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
| 16 | Documentação de API exposta | `check_16_api_docs.py` | Alta |
| 17 | Cookies sem flags de segurança | `check_17_cookies.py` | Média |
| 18 | CORS permissivo | `check_18_cors.py` | Alta |
| 19 | Redirect para domínio diferente | `check_19_redirect_domain.py` | Média |
| 20 | Diferenciação 403/404 em paths sensíveis | `check_20_info_disclosure.py` | Baixa |
| 21 | SPF ausente/fraco | `check_21_spf.py` | Alta |
| 22 | DKIM ausente | `check_22_dkim.py` | Média |
| 23 | DMARC ausente/duplicado/permissivo | `check_23_dmarc.py` | Alta |
| 24 | Mixed content | `check_24_mixed_content.py` | Média |
| 25 | Formulários inseguros | `check_25_form_security.py` | Alta |
| 26 | Subdomínios expostos (CT logs) | `check_26_subdomains.py` | Média |
| 27 | Dangling CNAME (subdomain takeover) | `check_27_dangling_cname.py` | Crítica |
| 28 | Vazamentos de dados (HIBP) | `check_28_hibp.py` | Média |
| 29 | Google Safe Browsing | `check_29_safe_browsing.py` | Crítica |
| 30 | Componentes com vulnerabilidades conhecidas (CVE) | `check_30_vulnerable_components.py` | Dinâmica |
| 31 | Permissions-Policy | `check_31_permissions_policy.py` | Média |
| 32 | Cross-Origin-Opener-Policy (COOP) | `check_32_coop.py` | Baixa |
| 33 | Cross-Origin-Embedder-Policy (COEP) | `check_33_coep.py` | Baixa |
| 34 | Cross-Origin-Resource-Policy (CORP) | `check_34_corp.py` | Baixa |
| 35 | Referrer-Policy (qualidade) | `check_35_referrer_policy.py` | Baixa/Média |
| 36 | Cache-Control em páginas sensíveis | `check_36_cache_control_forms.py` | Média |
| 37 | DNSSEC (registro DS no parent zone) | `check_37_dnssec.py` | Média |
| 38 | CAA (Certificate Authority Authorization) | `check_38_caa.py` | Média |
| 39 | MTA-STS (TLS obrigatório em e-mail) | `check_39_mta_sts.py` | Baixa |
| 40 | BIMI (logo da marca em e-mail) | `check_40_bimi.py` | Baixa |
| 41 | Cipher suites (cipher negociado fraco) | `check_41_cipher_suites.py` | Alta |
| 42 | Certificate chain (cadeia/self-signed/expiração) | `check_42_cert_chain.py` | Média |
| 43 | OCSP stapling (URI de revogação) | `check_43_ocsp_stapling.py` | Baixa |
| 44 | Força da chave criptográfica | `check_44_key_strength.py` | Alta/Crítica |
| 45 | Info sensível em comentários HTML | `check_45_html_comments.py` | Média/Alta |
| 46 | Indicadores de modo debug em produção | `check_46_debug_mode.py` | Alta/Média |
| 47 | Padrões de open redirect | `check_47_open_redirect.py` | Baixa/Média |
| 48 | Campos de senha sem proteções | `check_48_password_fields.py` | Baixa |

Checks 13–15 (supply chain, KL-2) fazem parse **passivo do HTML servido**;
scripts injetados por JavaScript em runtime não são vistos por um GET simples.

Checks 16–29 (KL-22) são organizados em blocos: **web** (16–20: docs de API,
cookies, CORS, redirect cross-domain, 403/404), **DNS/e-mail** (21–23: SPF, DKIM,
DMARC — via `dns_util.py`/dnspython, síncrono em `asyncio.to_thread`, mockável),
**conteúdo** (24–25: mixed content, formulários), **infra passiva** (26–27:
subdomínios via crt.sh, dangling CNAME) e **OSINT** (28–29: HIBP e Google Safe
Browsing — APIs públicas gratuitas). Os checks que dependem de API externa
degradam para **`INCONCLUSO`** (nunca erro) quando a API está fora, com rate limit,
ou sem chave. **`GOOGLE_SAFE_BROWSING_KEY`** é opcional (sem ela, o check 29 é
`INCONCLUSO`). Nenhum check 16–29 envia payload de ataque, faz brute-force ou
acessa área autenticada — só GET/HEAD, DNS público e APIs públicas de leitura.

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
`docker compose build` (site **no ar** durante o build) → `docker compose up -d
--remove-orphans` (recria só os containers que mudaram) → prune → `docker compose ps` →
health check em `http://localhost:8000/health` + `:4321/` (Astro). **⚠️ Downtime (fix):**
o antigo `docker compose down` derrubava tudo antes do build (site fora ~2-5 min); trocado
por `build` + `up -d` → downtime ~10-30s (só o recreate); Postgres/Redis (sem build) nem
são tocados. **Nota:** o script se auto-atualiza no `git pull` — como o bash já leu o
arquivo inteiro no início, a mudança só vale **no deploy seguinte** ao que a instalou.

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
  CTA), `Report` (`/report?url=`, download dos dois PDFs), `Sobre` (`/sobre`) e
  `Parceiros` (`/parceiros`) — páginas de conteúdo institucional (o e-mail no texto
  abre o `ContactModal` via `ContactEmail`, sem `mailto`). Roteamento client-side
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

**Subdomínio `mta-sts.klarim.net` (policy MTA-STS, RFC 8461, check_39).** Dois server
blocks dedicados (80 **e** 443) em `https.conf.template` servem
`/.well-known/mta-sts.txt` (arquivo estático `frontend/nginx/mta-sts.txt` →
`/etc/nginx/klarim_mta-sts.txt` via `alias`), com o conteúdo `version/mode:
enforce/mx: mx1|mx2.hostinger.com/max_age`. O **Cloudflare** (proxy) faz o TLS
voltado ao cliente; a origem responde em **80** (CF Flexible) **e 443** (CF Full, com
o cert do klarim.net — que **não** cobre `mta-sts.`, então CF Full **strict** exigiria
`certbot --expand -d mta-sts.klarim.net`). O bloco 80 **não** redireciona p/ HTTPS
(senão o Flexible quebra). O server principal 443 ganhou `default_server` explícito
(os blocos mta-sts não podem virar o default de SNI não-casado). Falta o dono criar o
**DNS** (`mta-sts.klarim.net` proxied no CF + TXT `_mta-sts.klarim.net` com o `id` da
policy) — só então `https://mta-sts.klarim.net/.well-known/mta-sts.txt` responde e o
check_39 passa. A **MX da policy tem que casar** a MX real do domínio (mx1/mx2.hostinger.com).

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
  .ini|.conf|.config`), paths de outros frameworks (`phpinfo`, `wp-admin`,
  `administrator`…) e **diretórios "suspeitos"** que scanners sondam
  (`backup|uploads|admin|internal|debug|test|staging|tmp|temp|logs|private|secret|dump`,
  regex `^/(…)(/|$)`) — em vez de 200 com a SPA. A âncora `^/` **não** casa
  `/api/admin/*` (é `/api/…`) nem `/painel/`. O ACME usa `location ^~
  /.well-known/acme-challenge/` para ter prioridade sobre os regex (não quebra a
  renovação). Valide a sintaxe com `nginx -t` antes de deployar (config ruim
  derruba o `web`).
- **Security headers e a herança do Nginx (auditoria pós-MCP).** Os headers
  (`Strict-Transport-Security`, CSP, `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`) ficam no `server` block **com `always`** (aparecem até em
  4xx/5xx). ⚠️ **Gotcha:** um `add_header` **próprio** num `location` **quebra a
  herança** de todos os do `server`. Como `location /assets/` tem
  `add_header Cache-Control`, ele **repete os 5 headers de segurança** — senão os
  JS/CSS sairiam sem HSTS/CSP (a regressão que a auditoria pegou). Ao adicionar um
  `add_header` a qualquer `location` novo, **repita os de segurança lá**.
- **Resolver dinâmico no proxy (`/api/` e `/mcp/`).** O Nginx resolve o hostname do
  `proxy_pass` **uma vez no boot** e cacheia o IP. Quando o container `api` é
  recriado (novo IP no bridge do Docker) e o `web` não, o Nginx fica com o IP velho
  e responde **502** (bug do login do painel: `localhost:8000` respondia 200 mas
  `painel.klarim.net/api/*` dava 502). Fix: `resolver 127.0.0.11 valid=10s;` (DNS
  embutido do Docker) + upstream em variável (`set $klarim_api api:8000;
  proxy_pass http://$klarim_api;`) → o Nginx **re-resolve** o IP por request. Como o
  `proxy_pass` com variável não faz o strip do prefixo, o `/api/` usa `rewrite
  ^/api/(.*)$ /$1 break;`; o `/mcp/` é identidade (sem rewrite). Remédio manual
  imediato se recorrer: `sudo docker compose restart web`.

---

## 11. Pagamento — AbacatePay PIX (`payments/`)

Fluxo: semáforo grátis → CTA → cria cobrança PIX → QR code inline → polling do
status → PAID → libera o download dos PDFs. Webhook confirma server-side (redundância).

- **`payments/abacatepay.py`** — client httpx da AbacatePay v2 (`create_pix_charge`,
  `check_payment`, `create_webhook`, `simulate_payment` [dev], `verify_webhook_signature`).
  Timeout 15s, retry/backoff em 5xx. Valores em **centavos**.
- **`payments/store.py`** — persistência de cobranças em **PostgreSQL** (tabela
  `payments`, psycopg2 em thread) com **fallback em memória** se não houver DB.
- **`payments/models.py`** — `Charge`, `PaymentStatus`, **`PRICE_AMOUNT = 1900`**
  / **`PRICE_DISPLAY = "R$ 19"`** (preço ÚNICO cobrado no `/payment/create`, KL-27).
  `PRICING`/`DEFAULT_TIER` por setor ficam **só para analytics** de classificação —
  não definem mais o preço.

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
  `send_test`, `send_contact`, `send_evolution`. SDK `resend` (síncrono) encapsulado
  em `asyncio.to_thread`. **Envio em lote (KL-23):** `send_alert_batch` /
  `send_evolution_batch` mandam até **100 e-mails em 1 request** via Resend Batch API
  (`_send_batch_raw` fala com `POST /emails/batch` por httpx, com header
  **`Idempotency-Key`** — `batch_idempotency_key`, determinístico por e-mails+data,
  anti-duplicata em retry). **Bounce (KL-24):** `get_email_event(email_id)` (GET
  `/emails/{id}` → `last_event`) para o backfill, e `verify_resend_signature`
  (esquema **Svix**) para validar o webhook. Templates Jinja2 **table-based**
  (compatível com Gmail/Outlook), paleta dark.
- **Endpoints** (`api/main.py`): `POST /email/test`, `POST /email/send-alert`
  (scan + alerta), `POST /email/send-report` (exige cobrança paga; anexa PDFs).
- **Formulário de contato (público):** `POST /contact {name?, email, message}` —
  encaminha para `scan@klarim.net` via `send_contact` (com `reply_to` do
  remetente). Sanitiza os campos, valida e-mail, rate limit **3/h por IP**. No
  frontend, o link "Contato" do footer abre o **`ContactModal`** (e-mail com botão
  copiar + formulário inline) — **sem `mailto:`**, o visitante não sai do site.
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
  domínios de exemplo. **`_clean_email` (fix):** `_collect_emails` **URL-decoda**
  (`%20`→espaço, `%40`→@) e tira espaços/tabs/quebras/nbsp + lowercase antes de
  validar — corta o lixo tipo `%20contato@x.com.br` (o `%` passa no regex do
  local-part) que **envenenava o batch do Resend** (1 e-mail inválido faz o Batch
  API rejeitar os 50). **Validação de MX (KL-24):** `extract_email` só aceita e-mail
  cujo domínio tem **registro MX** (`email_has_mx`/`_mx_status` via dnspython, cache
  `lru_cache`, DNS fora do event loop com `to_thread`); tri-estado `ok|no_mx|unknown`
  — só rejeita `no_mx` (fail-open no timeout/sem lib). Corta a maior fonte de bounce.
  **Sem e-mail válido ⇒ `status='sem_contato'`.**
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
  **Edição de status e e-mail (operador):** `PATCH /targets/{id}/status {status}`
  (valida contra `_VALID_STATUSES`) e `PATCH /targets/{id}/email {contact_email}`
  (valida formato; alvo `sem_contato` que ganha e-mail volta a `discovered`). No
  painel: editores inline ✏️ na lista e no detalhe
  (`components/admin/TargetEditors.jsx` → `StatusEditor`/`EmailEditor`).
  **Busca de alvos:** `GET /targets?search=` filtra **server-side** (case-insensitive,
  parcial) em `url`, `domain` **e** `contact_email` — combina com os filtros de
  status/plataforma/setor. No frontend, o input usa `useDebounce` (300ms).
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

**Regra de negócio (KL-60 — scan DESACOPLADO do e-mail):** ⚠️ a regra antiga ("só
escanear sites com e-mail") foi **revogada**. No modelo freemium, o scan gera
perfil/landing/ranking/dados **mesmo sem e-mail** — então o discovery enfileira **TODO
site acessível**, tenha e-mail ou não (`_process_domain` sempre chama `_enqueue` quando
`html != None`). O `status` ainda reflete o e-mail (`sem_contato` = não achamos e-mail),
mas o `update_scan_result` promove o alvo a `scanned` quando o scan completa (o e-mail,
se houver, fica salvo p/ notificações). Só o site **inacessível** vira `descartado` (sem
enqueue). **Backlog:** os ~7,8k `sem_contato` sem scan (`last_scan_id IS NULL`) são
drenados por `scripts/enqueue_unscanned.py --limit 500` (batches, `store.list_unscanned_
targets`/`count_unscanned_targets`) — **nunca** enfileirar tudo de uma vez. Rate:
`WORKER_MAX_SCANS_PER_HOUR` (env, default 50; subir p/ 100 na VM se aguentar; a vazão real
é limitada pela duração do scan, worker único).

## 16. Alert Worker + calibração do semáforo (KL-12) — `discovery/alert_worker.py`

**Calibração do semáforo (`scanner/scoring.py`):** 🟢 **Verde** exige score **≥ 90
E zero FALHAS de severidade Alta/Crítica**; 🟡 **Amarelo** = score ≥ 50 (ou ≥ 90
mas com FALHA Alta/Crítica); 🔴 **Vermelho** = score < 50. `_semaphore(score,
has_high_fail)` recebe o flag de falha alta calculado no `compute_score`. Motivo:
um site com nota alta mas com falha grave não deve exibir "tudo certo" (verde).

**Alert Worker** dispara o alerta gratuito (o anzol do funil) para alvos já
escaneados que têm falhas:

- **`alert_worker.py` (envio em lote, KL-23)** — `AlertWorker.run_cycle()`: busca
  TODOS os elegíveis (`status='scanned'`, com e-mail, `fail_count>0`, sem alerta nos
  últimos 30d, não `unsubscribed`), agrupa em batches de `ALERT_BATCH_SIZE` (50) e
  envia cada batch em **1 request** via `KlarimMailer.send_alert_batch` (Resend
  Batch API, até 100/request, com **idempotency key** anti-duplicata). Por ciclo:
  `ALERT_BATCH_SIZE`×`ALERT_BATCHES_PER_CYCLE` (50×4 = 200), pausa `ALERT_BATCH_PAUSE`
  (10s) entre batches. Marca `status='alerted'` + `last_alert_at`, registra em
  `alert_log`. Loop a cada `ALERT_INTERVAL_MINUTES` (30min). `build_alert_payload`
  monta o dict do alerta do JOIN de `get_eligible_targets_for_alert` (sem N+1);
  `send_alert_for_target` (envio único) segue para os disparos manuais da API.
  **Único teto:** a **cota mensal** `ALERT_MONTHLY_LIMIT` (45k dos 50k/mês do Resend
  Pro; 5k reservados p/ transacionais), via `store.count_proactive_emails_this_month`
  — os antigos `MAX_ALERTS_PER_HOUR/DAY/CYCLE` e a pausa de 5s foram removidos.
- **Kill-switch `STOP_ALERTS` (KL-27):** `alerts_stopped()` checa o arquivo em
  `ALERTS_STOP_FILE`; se existir, o `run_cycle` (alerta **e** evolução) é pulado
  (`stats["paused_by_flag"]`). O compose monta o dir de deploy em `/klarim-control`
  (ro), então `touch`/`rm /opt/klarim/STOP_ALERTS` no host pausa/resume **sem
  redeploy** (bind mount ao vivo, vale no próximo ciclo). ⚠️ O flag antes **não era
  lido pelo código** — a "pausa" por arquivo não existia até este card.
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

**Validação pré-envio + safety net de bounce (KL-24).** Antes de montar cada batch,
`_validate_batch`: **(1)** limpa o e-mail (`_clean_email`: URL-decode + tira lixo) e,
se mudou, **conserta no banco** (self-healing) e usa o limpo; **(2)** rejeita
**formato inválido** (`_EMAIL_RE`) — evita o **422** que derruba o batch inteiro do
Resend; **(3)** remove **blocklist** (`is_email_blocked`) e **(4)** domínio sem MX
(`email_mx_status=='no_mx'`). **Rede de segurança (`_send_with_split`):** se o batch
ainda der 422, divide ao meio e retenta recursivamente para **isolar** o e-mail ruim
(envia os 49 bons, descarta o 1); erro de infra (não-422) propaga e loga tudo como
`failed` sem descartar. No início de cada ciclo, `_check_bounce_health` **pausa** os
envios se o bounce rate passar de `ALERT_MAX_BOUNCE_RATE` (8%) — com amostra ≥
`ALERT_BOUNCE_MIN_SAMPLE`. Limpeza de e-mails sujos já no banco:
`POST /api/admin/clean-emails`. Ver seção **23**.

**Regra inviolável:** a **cota mensal** (`ALERT_MONTHLY_LIMIT`) protege a reputação
do domínio e o custo do plano Resend Pro — nunca remover o teto mensal nem estourar
os 50k/mês do Pro. Manter sempre a reserva para e-mails transacionais. **Nunca
enviar para e-mail na blocklist nem para domínio sem MX** (KL-24).

## 17. Re-scan Worker + e-mail de evolução (KL-13) — `discovery/rescan_worker.py`

Fecha o ciclo de vida do alvo: a cada 30 dias reescaneia sites já engajados,
compara o score com o anterior e envia um e-mail de evolução (re-engajamento sem
descobrir alvos novos).

- **`rescan_worker.py` (e-mail em lote, KL-23)** — `RescanWorker.run_cycle()` (loop
  24h, `RESCAN_INTERVAL_HOURS`): (1) `get_targets_for_rescan` (status `scanned`/
  `alerted`, com e-mail, `last_scan_at` > `RESCAN_AGE_DAYS`=30) → por alvo, com pausa
  igual ao scan worker (`WORKER_MAX_SCANS_PER_HOUR`): reescaneia, salva em `scans`,
  atualiza `targets`, **cacheia (KL-9)**, classifica a evolução e **loga a evolução
  com e-mail pendente** (`send_email=False`); (2) `_flush_pending_batch` despacha
  TODOS os e-mails de evolução pendentes (deste ciclo + de ciclos anteriores) em
  **lote** via `send_evolution_batch`. `classify_evolution` → `improved` /
  `worsened` / `unchanged` / `first_rescan`. A função `rescan_target()` é
  compartilhada com a API (disparo manual continua envio único).
- **Cota mensal GLOBAL compartilhada com o Alert Worker:**
  `count_proactive_emails_this_month` soma `alert_log` + `rescan_log` no mês
  corrente (calendário). Se a cota estourar, o re-scan acontece (dados atualizados)
  e o e-mail fica **pendente** (`rescan_log.email_id IS NULL`) para o próximo ciclo.
  Após enviar uma evolução, `mark_target_contacted` seta `last_alert_at` (evita
  alerta duplicado).
- **`notifier`** — 3 templates (`evolution_improved/worsened/unchanged.html`) +
  `KlarimMailer.send_evolution` (escolhe o template pelo tipo). Preço do CTA vem de
  `payments.PRICING` pelo `price_tier` do alvo.
- **`store.py`** — tabela **`rescan_log`** + métodos `get_targets_for_rescan`,
  `log_rescan`, `update_rescan_email`, `get_pending_evolution_emails`,
  `list_rescans`, `rescan_stats`, `count_proactive_emails_this_month` (KL-23),
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

## 23. Controle de bounce (KL-24) — MX + webhook Resend + blocklist + auto-pause

Emergência: bounce rate em **10,67%** (limite seguro < 4%; acima de 10% o Resend
suspende a conta e provedores blacklistam `klarim.net`). Complaint 0% — o problema
são **endereços inválidos**, não o conteúdo. Quatro camadas de defesa:

- **Validação de MX na captação (`discovery/contact.py`):** `extract_email` só
  aceita e-mail cujo domínio tem registro **MX** (`_mx_status` via **dnspython**,
  cache `lru_cache`, DNS via `to_thread`). Tri-estado `ok|no_mx|unknown` — rejeita
  só `no_mx` (fail-open no timeout/sem lib). `dnspython` no `requirements.txt`.
- **Validação pré-envio (`discovery/alert_worker.py`):** `_validate_batch` remove +
  marca `descartado` os alvos na **blocklist** e com domínio **sem MX** antes de
  montar o batch. `_check_bounce_health` **pausa** o worker se o bounce rate passar
  de `ALERT_MAX_BOUNCE_RATE` (8%) com amostra ≥ `ALERT_BOUNCE_MIN_SAMPLE`.
- **Webhook do Resend (`POST /api/webhooks/resend`, público):** valida a assinatura
  **Svix** (`verify_resend_signature` + `RESEND_WEBHOOK_SECRET`; 401 se inválida).
  `email.bounced` **permanente** → `discard_target_by_email` + `block_email` +
  `alert_log.status='bounced'`; **transitório** (soft/temporary) é ignorado.
  `email.complained` → `mark_unsubscribed` + `block_email` (spam é mais grave).
- **Backfill (`POST /api/admin/process-bounces`, JWT):** checa no Resend
  (`get_email_event`, concorrência limitada a 8) o status de cada alerta enviado e
  descarta/bloqueia os que bouncaram. Idempotente. **Rodar uma vez na VM após o
  deploy** para marcar os 37 bounces existentes.
- **Blocklist (`email_blocklist`):** bloqueio **por e-mail** (guarda o domínio para
  análise, mas não descarta endereços irmãos do mesmo domínio). `is_email_blocked`,
  `block_email`, `blocklist_size`.
- **Dashboard (`GET /api/system/email-health` + `/painel/sistema`):** card com
  **bounce rate** (🟢 <2% · 🟡 2–4% · 🔴 >4%), bounces permanentes, complaints e
  tamanho da blocklist. `_bounce_status` classifica; métricas de `store.email_health`.
- **Store:** `discard_target_by_email`, `block_email`, `is_email_blocked`,
  `blocklist_size`, `mark_alert_status_by_email_id`, `get_sent_alerts_for_bounce_check`,
  `email_health`. **Vars** (`.env`): `RESEND_WEBHOOK_SECRET`, `ALERT_VALIDATE_MX`,
  `ALERT_MAX_BOUNCE_RATE`, `ALERT_BOUNCE_MIN_SAMPLE`.

**Regra inviolável:** nunca reenviar para e-mail na blocklist nem para domínio sem
MX; nunca remover a pausa automática por bounce rate — a reputação do domínio no
Resend/Gmail é ativo crítico do funil.

## 24. Servidor MCP (KL-18) — operar o Klarim via Claude — `mcp_server/`

Wrapper **fino** sobre a API (nenhuma lógica duplicada): permite operar o Klarim
por linguagem natural no Claude — reaproveitar os ~1.900 alvos `sem_contato`,
monitorar o sistema, disparar scans/alertas, tudo por tools.

**Modelo Traka (SSE puro + auth middleware ASGI).** Estrutura em módulos:
`mcp_server/_base.py` (instância `FastMCP` + helpers), `server.py` (app SSE +
propagação de token), `auth.py` (`MCPAuthMiddleware`), `tools/` (as 25 tools por
domínio). Montado no FastAPI em **3 linhas**: `app.mount("/mcp",
MCPAuthMiddleware(mcp_app))`.

- **`_base.py`** — `mcp = FastMCP(name="klarim")` + `_guard`/`_api`/`_store`. As tools
  chamam funções de endpoint do `api.main` (import **lazy** via `_api()`, evita ciclo)
  ou métodos do `store`; `_guard()` converte exceções (incl. `HTTPException`) num dict
  `{"error", "status_code"}` — a tool nunca derruba a sessão. `transport_security`
  com DNS-rebinding **OFF** (senão o Host `klarim.net` atrás do Nginx seria rejeitado).
- **`tools/`** — 25 tools organizadas por domínio (`system.py`, `targets.py`,
  `scans.py`, `alerts.py`, `payments.py`, `analytics.py`), cada uma
  `from mcp_server._base import mcp, _guard, _api, _store` + `@mcp.tool()`. Importar
  `mcp_server.tools` registra todas. **17 leitura** (`get_system_status`,
  `get_email_health`, `get_discovery_status`, `get_config`, `list_targets`,
  `get_target`, `get_target_stats`, `search_targets`, `list_scans`, `get_scan`,
  `get_scan_stats`, `list_alerts`, `get_alert_stats`, `list_payments`,
  `get_payment_stats`, `get_funnel`, `get_rescan_stats`) + **8 escrita** (`scan_url`,
  `add_target`, `update_target_email`, `update_target_status`, `update_target_sector`,
  `send_alert_to_target`, `send_report_to_email`, `classify_targets_batch`).
- **`server.py` — SSE + propagação de token.** `mcp_app` (Starlette) com o
  `SseServerTransport` em `/sse` (+ `/messages/`). **O fix que faz o Claude.ai
  conectar:** o transporte anuncia `data: /mcp/messages/?session_id=<hex>` **sem** a
  auth; sem o token, os POSTs do Claude bateriam na middleware e levariam 401 na 2ª
  fase. `_token_propagating_send` reescreve o evento `endpoint` para incluir
  `&token=<token>` (o mesmo com que o cliente abriu o SSE) → os POSTs chegam
  autenticados.
- **`auth.py` — `MCPAuthMiddleware`** (ASGI, envolve o `mcp_app` inteiro):
  **fail-closed** (sem `MCP_API_KEY` ⇒ tudo 401), **constant-time**
  (`hmac.compare_digest`), aceita `Authorization: Bearer <chave>` **ou** `?token=<chave>`,
  header `WWW-Authenticate: Bearer realm="klarim-mcp"` em toda 401. `/mcp/*` **não**
  está nos prefixos protegidos por JWT (`_admin_auth_mw`) — tem auth própria.
- **Nginx:** `location /mcp/` inalterado — `proxy_buffering off` + `proxy_cache off` +
  `Connection ''` + `proxy_http_version 1.1` (sem isso o SSE não flui) + `access_log
  off` + resolver dinâmico (seção 10). A auth vem no `?token=`, por isso `access_log
  off` importa.
- **SDK mantido:** `mcp>=1.27,<2` (o v1.x já tem SSE) — **sem** trocar para
  `fastmcp` 3.x (que forçaria Starlette 1.x + bump FastAPI 0.115→0.139, alto risco). Os
  pins `starlette>=0.40,<0.42` e `sse-starlette>=1.6.1,<2.2` continuam.
- **Conectar (URL única, com a chave no `?token=`):**
  - **Claude.ai web:** Configurações → Conectores → Add → `https://klarim.net/mcp/sse?token=<MCP_API_KEY>`.
  - **Claude Desktop:** `{"mcpServers": {"klarim": {"url": "https://klarim.net/mcp/sse",
    "headers": {"Authorization": "Bearer <MCP_API_KEY>"}}}}`.
  - **Claude Code:** `claude mcp add klarim --transport sse https://klarim.net/mcp/sse
    --header "Authorization: Bearer <MCP_API_KEY>"`.

## 25. Scan público verificado por e-mail (KL-25) — código 6 dígitos + 1 grátis/e-mail

O scan público passou a **exigir e-mail confirmado** (código de 6 dígitos) e dar **1
scan gratuito por e-mail** — captura o lead e corta bot/curioso. Fluxo: URL + e-mail
→ código no e-mail → digita o código → scan roda → resultado. 2º scan (outra URL) →
"limite atingido" + CTA de pagamento; mesma URL → resultado anterior (sem gastar o
crédito).

- **Tabelas (`store.py`):** `scan_verifications` (código, url, `verified`, `expires_at`
  10min, ip) e `scan_credits` (`email` unique, `free_scans_used`, `first_scan_url`).
  Coluna `scans.scanned_by_email` liga o scan ao lead.
- **Endpoints (`api/main.py`, públicos):**
  - `POST /scan/request-code {email, url}` — checa o crédito (→ `already_scanned` /
    `limit_reached`), gera código **CSPRNG** (`secrets.randbelow`), grava (TTL 10min),
    envia via Resend. **Rate limit** 3/e-mail/h + 5/IP/h (in-memory).
  - `POST /scan/verify-code {email, code, url}` — valida (não usado, não expirado),
    consome o gratuito (`record_free_scan`), devolve um **scan token** HMAC (email+url+
    exp, 1h). Rate limit 5/e-mail/10min.
  - `POST /scan/check-credit` — estado do crédito sem enviar código.
  - `GET /scan/summary` — exige o **`X-Scan-Token`** (ou JWT de admin) para **disparar**
    um scan novo (`_verify_scan_token`, url tem que casar); sem token, só devolve
    resultado **já existente** (`get_recent_only`: cache/banco, nunca reescaneia) ou
    `{"status":"auth_required"}`. O e-mail do token vira `scanned_by_email` no scan.
- **Token:** HMAC-SHA256 assinado com `JWT_SECRET`, payload base64 (email/url/exp).
- **E-mail:** `KlarimMailer.send_verification_code` + `verification_code.html` (dark,
  código grande). `send_verification_code` só roda se `RESEND_API_KEY` configurada.
- **Frontend (`Landing.jsx`):** 3 estados — **form** (URL+e-mail) → **code** (código +
  reenviar 45s) → **limit** (CTA pagamento). Ao verificar, guarda o token
  (`sessionStorage`, `api.setScanToken`) e vai para `/scan` (que escaneia com o token
  via `fetchSummary` → `X-Scan-Token`). `useSummary`/`Scan.jsx` redirecionam à home em
  `auth_required`.
- **Tracking (KL-21):** eventos `code_requested`, `code_verified`, `code_failed`,
  `scan_limit_reached`.
- **Dashboard:** `GET /analytics/public-scans` (card em `/painel/analytics`) e
  `scanned_by_email` no detalhe do scan.
- **Limpeza:** `create_scan_verification` apaga os códigos expirados a cada gravação
  (sem cron).

## 26. Funil de conversão — free 15 / pago 29, R$ 19, re-verificação (KL-27)

Reestruturação do funil (o antigo entregava detalhe demais no grátis, e-mail com
preço/alarme, e sem gancho de retorno — 0 conversões em 1.418 alertas).

**Tiering do scanner.** `scanner/checks/__init__.py`: `FREE_CHECK_MAX_ORDER=15`,
`discover_checks(full)`, `ALL_CHECKS` (29) / `FREE_CHECKS` (15) / `CHECK_META`
(`{check_id,name,order,paid}` — metadados leves, **sem** rodar os checks pagos).
`run_scan(url, full=True)` escolhe o conjunto. **Cache por tier** (`scanner/cache.py`):
`scan:free:<hash>` e `scan:full:<hash>` (ambas casam `scan:*` no flush). Em
`api.get_or_scan(url, full=…)`, `_tier_ok` exige ≥29 (full) / ≥15 (free) — scan do
tier errado força re-scan. **Default `full=True`**; só `/scan/summary` público e
`get_recent_only` usam `full=False`. **Onde roda:** discovery/público = free;
pós-pagamento, admin, `/report/*`, recuperação e re-verificação = full; o re-scan de
re-engajamento (KL-13) roda **free** (score de evolução comparável ao do alerta).

**Resultado gratuito sem detalhes.** `_summary_payload(report, full=False)` →
`score`, `semaphore`, `risk_summary` (genérico), `fail_count`,
`free_checks:[{check_id,name,status}]` (15), `paid_checks:[{…,status:"locked"}]`
(14), `price:1900`, `price_display:"R$ 19"`, `is_full`. **Removidos do grátis:**
`risk_messages`, `severity_counts`, evidências, impacto, correção. Token de
re-verificação (`full`) ou JWT admin revela o status real dos 14.

**E-mail sem preço/alarme.** Assunto `dominio — resultado da avaliação de segurança`
(evolução: `… — atualização da avaliação de segurança`); corpo só com score +
semáforo + contagem + CTA **"Veja o relatório"**. `alert.html`/`evolution_*.html`,
`email_client`, `alert_worker`/`rescan_worker` não computam/renderizam risco/preço.

**Preço único R$ 19.** `PRICE_AMOUNT=1900`/`PRICE_DISPLAY` em `payments/models.py`;
`/payment/create` cobra 1900. `PRICING`/`PRICE_TIERS` só para analytics.

**Pós-pagamento.** `_maybe_send_report_email` (idempotente via `report_email_sent`):
concede **1 crédito de re-scan** ao `buyer_email` (`grant_rescan_credit`) e roda o
scan **completo (29)** + 2 PDFs. `scan_credits.rescan_credits` (coluna nova).

**Re-verificação ("retorno médico").** `check-credit` → `{…, rescan_credits,
can_rescan}`; `request-code` libera código a quem tem crédito mesmo já tendo
escaneado; **`POST /scan/rescan {email,code,url}`** valida o código, **consome 1
crédito**, roda o scan completo e devolve o resultado + **comparação antes/depois**
+ um **scan token `full`**. Esse token (claim `full:true`, HMAC) autoriza os PDFs
sem cobrança (`/report/*` aceita `scan_token`, `_has_full_scan_token`).

**Frontend.** `Landing` (hero "29 pontos… R$ 19"; `check-credit` decide re-verificação
→ `/scan/rescan` → `/result` com a comparação), `Result` (15 ✅/❌ + 14 🔒 + CTA
"Fazer scan completo — R$ 19"; com `is_full` mostra os 29 reais + comparação + PDFs),
`Payment`/`Report` (R$ 19 + nota da re-verificação), `lib/api.js` (`rescanScan`,
`reportUrl` anexa o scan token).

**Regra inviolável:** o grátis **nunca** vaza detalhe dos checks (headline, evidência,
impacto, correção) nem o resultado dos 14 pagos; o e-mail **nunca** menciona preço.
Ao adicionar um check, ele entra no tier certo pelo `ORDER` (≤15 grátis) e ganha
entrada em `RISK_MESSAGES`/`ACCESSIBLE`/`TECHNICAL` (seção 4.2 / KL-22).

### Ajustes pós-KL-27 (teste real de pagamento)

- **Resultado completo na tela (não só PDFs).** `_summary_payload(report, full=True)`
  enriquece cada FAIL com `evidence` (do `CheckResult`) + `impact`/`fix` (do
  `reporter.generator.TECHNICAL`, import **lazy**); PASS/INCONCLUSO ficam só com
  status. `/scan/summary` aceita **`charge_id` pago** (ou scan token `full`) como
  autorização → devolve os 29 com detalhe + `report_urls` + `rescan_credits`
  (`_full_extras`). O `Payment` passou a navegar para **`/result?...&charge_id=`**
  (não `/report`); o `Result` completo mostra os 29 (FAILs expandem), os PDFs e o
  bloco de re-verificação. O gratuito **continua** bloqueando os 14 e sem detalhe —
  `_entry` força `locked` nos pagos quando `not full`, mesmo que o report tenha 29.
- **Anti-duplicação de scan.** `/scan/summary` só ingere no caminho **público
  gratuito**; admin/pago/re-verificação já ingerem no próprio fluxo (evita 2ª linha
  em `scans`). A "atividade recente" do painel usa `GET /scans?distinct_url=true`
  (`list_scans` com `DISTINCT ON (url)`) → **1 linha por site**, com badge do tipo
  (Básico/Completo/Re-verificação/Admin/Demo).
- **Modo demo** (testar o fluxo com pagamento **sem** cobrar). `_is_demo(email, url)`
  casa `DEMO_EMAIL`/`DEMO_URL` (ambos vazios = desligado). Efeitos: `request-code`
  não envia e-mail (código fixo **`000000`**); `verify-code` aceita `000000` **sem
  consumir crédito**; `payment/create` cria cobrança **PAID instantânea**
  (`charge_id` `demo_…`, sem AbacatePay); scans marcados **`source='demo'`**; o Alert
  Worker pula alvos demo (`is_demo_target`); **cobranças demo não entram em
  `payments/stats`** (filtro `charge_id NOT LIKE 'demo\_%'`). ⚠️ **NÃO** apontar
  `DEMO_URL` para `klarim.net` (liberaria relatório grátis do site real) — usar
  domínio de teste. Vars no `.env` da VM: `DEMO_EMAIL`, `DEMO_URL`.

## 27. Sites Monitorados (KL-29) — selo de segurança para score 100

Credibilidade real + retenção + viralidade: sites com **score 100/100** (scan
completo, 29 checks) ganham monitoramento gratuito e aparecem numa seção pública
(`/monitorados`). Score cai → alerta + selo suspenso; volta a 100 → restaura.

**Tabela `monitored_sites`** (`discovery/store.py`): `domain` UNIQUE, `contact_email`,
`approval_token` (uso único), `status` (`pending`→`active`→`suspended`→`active`/
`removed`), `last_check_score/at`, `logo_url`, `display_name`. Métodos:
`upsert_monitoring_offer` (idempotente por domínio, não rebaixa active/suspended),
`approve_monitored_site`, `get_active_monitored_sites`, `get_monitored_for_rescan`,
`suspend`/`restore_monitored_site`, `monitored_stats`, etc.

**Endpoints (`api/main.py`):** públicos — `POST /monitoring/offer {url,email}`
(**confere score 100 no servidor** via `get_recent_only(full=True)`, 409 senão; rate
limit 10/h/IP; cria `pending` + `approval_token`), `GET /monitoring/status`,
`POST /monitoring/approve {token,display_name?}` (uso único + favicon como logo),
`GET /monitoring/remove?domain=&token=` (HMAC por domínio), `GET /monitoring/sites`
(**sem** e-mail/target_id/token — `_public_monitored`). Admin (prefixo
**`/monitoring/admin`** protegido por JWT): `list`, `stats`, `POST /{id}/status`.

**Re-scan semanal (`rescan_worker.py`):** `RescanWorker._monitor_cycle` (loop
`_monitor_loop`, `MONITOR_INTERVAL_DAYS=7`) roda o scan **completo (29)** de cada site
active/suspended: <100 → `suspend` + `send_monitor_alert`; suspended que volta a 100 →
`restore` + `send_monitor_restored`. **Auto-oferta:** `_maybe_offer_monitoring` — quando
o re-scan de re-engajamento (free 15) bate 100, confirma no completo e oferta.

**E-mails:** `monitor_offer/alert/restored.html` + `KlarimMailer.send_monitor_*`.
**Frontend:** `/monitorados` (grid), `/monitorados/aprovar?token=` (aprovação),
oferta no `Result` (score 100), prévia na landing, link no footer, painel
`/painel/monitorados`. **MCP:** `list_monitored_sites`, `offer_monitoring(target_id)`.

**Regra inviolável:** a listagem pública **nunca** expõe `contact_email`/`target_id`/
`approval_token`; a oferta só vale para score 100 **comprovado no servidor**; o token
de aprovação é **uso único**. Vars: `MONITOR_INTERVAL_DAYS`, `SITE_BASE`.

## 28. Scan completo gratuito para score 100 (KL-31)

**REGRA INVIOLÁVEL:** zero cobrança no fluxo de score 100 — o scan completo e o
monitoramento são **gratuitos**. R$ 19 só existe se o site **não** passou nos 29 e
quer re-verificar após correções.

Fluxo: discovery (15) → score 100 verde → Alert Worker envia e-mail de **parabéns**
(convite, não alerta) + concede crédito → cliente clica → `/result?bonus=full&t=<token>`
→ 15 ✅ + botão **"Fazer análise completa gratuita"** (sem R$ 19) → 29 checks sem
cobrança → 100/29 oferta de monitoramento; <100/29 FAILs + "Re-verificar após correções
— R$ 19".

**Crédito (`scan_credits`):** colunas `full_scan_credits` + `full_scan_url` (vinculado a
email+URL, uso único, não acumula). `grant_full_scan_credit`/`consume_full_scan_credit`.
**Elegibilidade** (`get_eligible_targets_for_alert`) inclui `fail_count>0 OR (score=100
AND semaphore='verde')`. **E-mail** (`_alert_params`): score 100 verde → `alert_score100
.html` + assunto "parabéns" + link `?bonus=full&t=<token>` (token `bonus_scan_token`,
HMAC, `full=false,bonus=true`, **TTL 30d**, formato idêntico ao `_make_scan_token`).

**Autorização (`/scan/summary`):** prioridade **admin → charge pago → bônus** (`use_bonus`
+ crédito no banco, consumido aqui) **→ re-verificação (`full`) → básico (15)**. O token
de bônus sozinho **não basta** (o backend consome o crédito no banco); a visão inicial de
15 checks **não** consome (só o clique no botão, `use_bonus=true`). `/scan/check-credit`
retorna `full_scan_credits`+`can_full_scan_free`.

**Frontend (`Result.jsx`):** guarda o token do link, mostra o botão verde gratuito no
lugar do R$ 19, roda o completo com `use_bonus`, e no <100 oferece "Re-verificar — R$ 19".
**Monitor a cada 30 dias** (`MONITOR_INTERVAL_DAYS=30`). **Tracking:** `score100_full_scan_
started/completed`, `score100_monitoring_offered/accepted`.

**Regra inviolável:** o bônus é por (e-mail, URL), **uso único**, consumido ao rodar o
scan; `bonus=full` na URL nunca autoriza sozinho — sempre confere o crédito no banco.

## 29. Controle dos workers via MCP (KL-32)

Pausa/retoma **cada worker independentemente** (discovery, alert, rescan, scan) e ajusta
throttle, sem redeploy, com persistência entre restarts.

**Estado (`discovery/worker_control.py`):** um JSON em `WORKER_CONTROL_FILE` (padrão
`/klarim-control/worker_control.json` = host `/opt/klarim/worker_control.json`) com
`{worker: {enabled, paused_at, paused_by, <config>}}`. **Fail-open** (ausente/corrompido/
chave faltando ⇒ `enabled: true` — nunca trava). Escrita **atômica** (tmp + `os.replace`).
API: `load`/`is_enabled`/`worker_config`/`pause`/`resume`(incl. `"all"`)/`set_config`.

**Mounts (compose):** `api` monta `./:/klarim-control` **rw** (o MCP grava); `discovery`
e `worker` montam `:ro` (leem). O arquivo vive no host → persiste. Ambos no `.gitignore`.

**Integração:** cada worker checa `is_enabled` **no início de cada ciclo** e pula se
desabilitado. Overrides lidos por ciclo: alert `max_per_hour`/`batch_size`, discovery
`cycle_minutes`/`max_targets_per_cycle`, scan `max_per_hour`. O scan pausado **não
consome a fila** (itens ficam enfileirados) mas mantém heartbeat. **Aditivo ao
`STOP_ALERTS`** (KL-27): o alert só envia se `STOP_ALERTS` ausente **E** `alert.enabled`.

**MCP (6 tools, `mcp_server/tools/workers.py`):** `pause_worker`, `resume_worker`,
`get_worker_control` (controle + alive/dead do heartbeat), `set_alert_throttle`,
`set_discovery_config`, `set_scan_config`. **REST (JWT):** `POST /admin/workers/pause|
resume`, `GET /admin/workers/control`. `get_system_status` inclui `enabled/paused_at/
paused_by` por worker.

**Regra inviolável:** o controle é **fail-open** (um erro de leitura nunca pausa um
worker por engano); pausar `alert`/`rescan` protege a reputação do domínio — o kill-switch
`STOP_ALERTS` continua válido em paralelo.

## 30. Perfil comercial: multi-page crawl + parser (KL-50)

Extrai **dados de negócio** (não afeta o score de segurança) para desbloquear perfis
públicos, notificações e aquisição orgânica. Reduz `sem_contato` e `unknown`.

**Camada 1 — multi-page.** `discovery/contact.py` busca e-mail em 8 páginas internas
(`_CONTACT_PATHS`: contato/contact/sobre/about/quem-somos/sobre-nos/fale-conosco/
atendimento) — tira alvos de `sem_contato` já na descoberta. `scanner/profiler.
crawl_contact_pages(url)` faz homepage + internas (200, 1 redirect, rate limit 1 req/s).

**Camada 2 — `scanner/profiler.py` (parsers puros, testáveis, sem deps externas):**
`extract_contacts` (e-mail hardened + `tel:` + `wa.me`/`data-phone` + endereço +
**CNPJ com dígitos verificadores**), `extract_structured_data` (JSON-LD/@graph →
name/phone/email/address/hours/sameAs/logo + **setor pelo @type**), `extract_social_
links` (handles IG/FB/LI/YT/TT + maps + has_blog/has_app, ignora paths reservados),
`extract_technologies` (~30 fingerprints case-insensitive por categoria, JSONB),
`extract_infrastructure` (MX→e-mail provider, NS→dns provider, headers→CDN),
`calculate_maturity_score` (0–10). `build_profile(...)` orquestra tudo (nunca levanta).
`dns_util.resolve_mx`/`resolve_ns` (novos, mockáveis).

**Tabela `site_profile`** (SERIAL/INTEGER — o schema não usa UUID; adaptado da spec),
1 por target (UNIQUE, ON DELETE CASCADE). `upsert_site_profile`/`get_site_profile`.

**Integração:** o **scan worker** (`_enrich_profile`) grava o perfil após o scan
(best-effort). `GET /targets/{id}` anexa `profile`; `GET /targets/{id}/profile`; MCP
`get_site_profile`. **Reprocessamento:** `scripts/enrich_batch.py --limit 500`
(sem_contato → crawl + e-mail + perfil; achou e-mail → `discovered` + enfileira scan).

**Reprocessamento completo — `scripts/enrich_all.py` (extensão do KL-50 + KL-47A).**
Onde o `enrich_batch` só cobre `sem_contato`, este cobre **todos** os alvos acessíveis
(não `descartado`) que ainda faltam perfil/IA — inclusive os escaneados **antes** do
profiler/IA e os classificados como `outro`. Seleção por prioridade em **3 grupos
disjuntos** (via `store.list_enrichment_candidates`/`count_enrichment_groups`, LEFT
JOIN `site_profile`): **G1** sem perfil (`alerted` > `scanned` > `sem_contato` >
`discovered`); **G2** com perfil e classificação por **regex** — não-IA e não-manual
(**KL-54:** com 48 setores, TODA classificação por regex é revista pela IA,
independentemente de setor/confiança; ex.: `agencianextweb` que o regex deu
`imobiliaria` 0.5 vira `agencia`); **G3** com perfil + setor por IA mas sem descrição
(a IA gera a descrição). Por alvo: crawl multi-page + `build_profile` + IA
(`ai_enrich`/`merge_ai_into_profile`; setor só via `ai_update_classification`, que
**preserva `manual` e `ai`**); e-mail achado (com MX) em `sem_contato` → `discovered`
+ enfileira. Helpers puros `enrichment_group`/`needs_crawl`/`needs_ai`/
`should_update_sector` (espelham o SQL, testáveis offline). **Idempotente** (a seleção
nunca traz alvo já completo), **fail-open** (IA opcional; erro por alvo é logado e
não aborta o batch), **controla custo** (`--ai-delay`, ~US$0,001/site). Flags:
`--limit N`/`--no-limit`, `--only-ai` (pula o crawl), `--only-sem-contato` (modo
antigo), `--dry-run`. Log em `enrichment_all.log`. Cron sugerido: 2×/dia × 500 →
~6 dias para drenar o backlog.

**Regra inviolável:** o perfil é dado **comercial** (passivo, GET público) — **não
altera o score de segurança**; a extração é sempre **best-effort** (erro só loga, nunca
derruba scan/worker). A IA **complementa** o regex (só preenche campo vazio; setor só
em alvo fraco, nunca `manual`) — vale também no reprocessamento em massa.

## 31. Classificação OWASP/CWE/LGPD (KL-34/35)

Cada finding ganha classificação em frameworks reconhecidos — **OWASP Top 10 2025**,
**CWE** e **LGPD** — o que transforma o relatório de "lista de problemas" em documento
que um auditor, advogado ou seguradora aceita. É **metadata** sobre os checks
existentes: **não muda a lógica de scan nem o score**.

**Identidade dual (inviolável):** o relatório **técnico** (PDF + resultado web
completo) e a **API** expõem OWASP/CWE/LGPD; o **executivo NUNCA** os menciona por
finding — mantém linguagem informal e leva só uma nota institucional genérica ("baseado
em padrões internacionais de segurança (OWASP) e considera a LGPD").

- **`scanner/checks/classifications.py` — fonte da verdade única.** `CLASSIFICATIONS`
  mapeia os **29** `check_id` → `(owasp, cwe, lgpd)`. `classify(check_id)`,
  `compliance_summary(results)` (conta as FALHAS por categoria OWASP e por artigo
  LGPD), `owasp_parts`/`lgpd_articles`/`LGPD_LABELS` e o `COMPLIANCE_DISCLAIMER`
  obrigatório ("não constitui auditoria…"). LGPD pode ser múltiplo ("Art. 46, Art. 48");
  checks 12/20/26 têm LGPD `None`. A tabela cobre **todos** os checks da suíte (o teste
  `test_every_check_is_mapped` falha se algum ficar de fora).
- **`CheckResult` (base.py)** ganhou `owasp`/`cwe`/`lgpd` **opcionais** (`None` default,
  retrocompatível — `from_dict` de scan antigo não quebra). O **`runner`** os **carimba**
  pelo `check_id` (onde já seta o `check_id`), então **não** foi preciso editar as ~100
  `return CheckResult(...)` dos 29 checks, e até o resultado de fallback (check que
  levanta exceção) fica classificado. Serializam sozinhos no `to_dict` → fluem para
  cache/banco (`checks_json`) e para o `get_scan` **sem** mudança na API.
- **Relatório técnico** (`reporter/generator.py` + `technical.html`): cada FALHA mostra
  uma linha **Classificação** (OWASP/CWE/LGPD) abaixo da evidência, e há um **Sumário de
  conformidade** no fim (contagem por OWASP e por artigo LGPD + disclaimer). O
  `generator` usa o carimbo do `CheckResult` com **fallback** a `classify(check_id)`
  (robusto para reports antigos). `executive.html` só recebe a nota genérica.
- **Resultado web** (`/scan/summary` completo, `_summary_payload(full=True)`): as
  entradas de **FALHA** trazem `owasp`/`cwe`/`lgpd`; o **gratuito** (`full=False`) **não**
  — mantém o gate do funil (KL-27). Frontend `Result.jsx` renderiza a classificação nos
  FAILs expandidos do modo completo.

**Ao adicionar um check novo:** acrescente a entrada em `CLASSIFICATIONS` (o teste
`test_every_check_is_mapped` falha se faltar) além de `RISK_MESSAGES`/`ACCESSIBLE`/
`TECHNICAL` (seções 4.2/21). **Flush `scan:*` no Redis** após deploy para os scans
cacheados reganharem os campos (metadata não muda o score, então o flush é recomendado,
não obrigatório).

## 32. Componentes vulneráveis + CVE matching (KL-33) — `check_30`

O achado mais acionável do scanner: detecta **versões** de bibliotecas JS e CMS (100%
passivo) e cruza com CVEs conhecidos. "jQuery 2.1.4 com 12 vulnerabilidades conhecidas"
é concreto e assustador — diferente de "falta um header". É o **check 30** (tier pago,
ORDER 30) e entra no score com **severidade dinâmica** (pelo maior CVSS/severidade).

- **`scanner/cve_db.py` — base de CVEs.** `CVEDatabase` (singleton via `get_cve_db()`):
  baixa a base **Retire.js** (`jsrepository.json`, ~500KB) em **runtime**, cacheia em
  `KLARIM_CVE_CACHE` (padrão `/tmp/klarim_retirejs_cache.json`, **TTL 24h**, escrita
  atômica) e é **fail-open** — download/parse falho → base vazia → o check vira
  INCONCLUSO, **nunca** derruba o scan. `lookup_js(lib, version)` casa a versão contra
  `below`/`atOrAbove` (via `packaging.version`), `recommended_upgrade` (menor versão
  segura), `covers`, `severity_from_cves`/`max_cvss`. **NVD/NIST** para CMS/PHP/servidor
  fica atrás de `NVD_ENABLED` (**default `false`**) — pronto mas inerte até ter rede/chave.
- **`scanner/checks/check_30_vulnerable_components.py`.** `detect_versions(html, headers,
  script_urls)` (puro, testável): JS via `<script src>` + inline (50KB) usando
  `VERSION_PATTERNS`; CMS via `<meta generator>`/`?ver=` (`CMS_VERSION_PATTERNS`,
  WordPress é o de maior impacto — 18,6% dos alvos); PHP/servidor via headers. Cruza com
  o `cve_db`. **FAIL** se algum componente tem CVE; **PASS** se detectou componente(s)
  cobertos pela base e nenhum é vulnerável; **INCONCLUSO** se nada foi detectado ou só há
  componentes que a base não cobre (ex.: WordPress com NVD off). `details.components`
  carrega `{library, version, source, cves:[{id,severity,cvss,summary}], recommendation}`.
- **Classificação (KL-34/35):** `check_30` → **A06:2025 Vulnerable and Outdated
  Components** / **CWE-1104** / **Art. 46** (em `classifications.py`, carimbado pelo runner).
- **Relatórios:** `RISK_MESSAGES` (executivo — "carro com vários recalls que você nunca
  levou na oficina"), `ACCESSIBLE`/`TECHNICAL` (técnico com CVE-IDs + recomendação de
  atualização). Identidade dual preservada.

**Regra inviolável:** 100% passivo — o check só lê o HTML/headers que o site já entrega
(nenhuma versão é sondada ativamente). A base de CVE é **best-effort/fail-open**: nunca
bloquear o scan por falha de download. **Flush `scan:*` no Redis após deploy** (novo check
altera scores). `packaging` está no `requirements.txt`.

## 33. Análise de qualidade de headers + headers modernos (KL-32)

Transforma "header checker" em "header analyser": aprofunda 4 checks de presença para
**análise de eficácia** e adiciona 6 checks de headers modernos. Tudo passivo (só lê
headers/HTML já servidos).

> ⚠️ **Colisão de numeração:** o card Jira **KL-32** é o **de headers** (esta seção). O
> "worker control via MCP" (seção 29) também recebeu KL-32 no Jira por engano — são
> coisas diferentes.

**Aprofundados (mesmo arquivo, tier/severidade inalterados):**
- **check_05 CSP** (`analyze_csp`): faz parse da policy. `'unsafe-inline'`/`'unsafe-eval'`/
  `*`/`data:`/`blob:` em `script-src`/`default-src` → **FAIL** (CSP cosmético agora reprova);
  diretivas essenciais ausentes → nota (PASS).
- **check_02 HSTS**: avalia `max-age` (mín. 6 meses, ideal 1 ano), `includeSubDomains`,
  `preload`. `max-age` ausente/0/curto → FAIL; aceitável → PASS com notas.
- **check_17 cookies** (`analyze_cookie`, por cookie): `SameSite=None` sem `Secure`, `Domain`
  amplo (public suffix), prefixo `__Secure-`/`__Host-` sem a flag, sessão sem HttpOnly/
  Secure/SameSite → FAIL.
- **check_18 CORS**: `*`/origem-refletida **+ `Allow-Credentials: true`** → **FAIL ALTA**
  (exfiltração cross-origin); `*` sozinho → **FAIL MÉDIA**.

**Novos (checks 31–36, tier pago ORDER>15, todos A05:2025):** 31 Permissions-Policy
(MÉDIA, CWE-693), 32 COOP (BAIXA, CWE-346), 33 COEP (BAIXA, CWE-346), 34 CORP (BAIXA,
CWE-346), 35 Referrer-Policy (qualidade; `unsafe-url`→MÉDIA, ausente→BAIXA, CWE-200), 36
Cache-Control em páginas com `<form>`/`<input type=password>` (MÉDIA, CWE-524). Cada um é
um `check_NN_*.py` no padrão; classificados em `classifications.py`; com `RISK_MESSAGES`/
`ACCESSIBLE`/`TECHNICAL`.

**Regra inviolável:** 100% passivo (só lê headers/HTML já entregues). Os checks 31–36 são
**pagos** (headers modernos com adoção baixa — se fossem gratuitos, todo site ficaria
vermelho). **Flush `scan:*` no Redis após deploy** — a análise de qualidade faz um CSP/HSTS
antes PASS virar FAIL, mudando scores.

## 34. DNS security expandido (KL-36) — checks 37–40

Completa a camada DNS/e-mail (que já tinha SPF/DKIM/DMARC nos checks 21–23) com 4
verificações **100% passivas** (consulta DNS pública + 1 GET público no MTA-STS). Todas
tier **pago** (ORDER>15), via `dns_util.py`.

- **`dns_util.py`:** novos `resolve_ds` (DNSSEC) e `resolve_caa` (retorna
  `[{flags,tag,value}]`), no mesmo padrão mockável de `resolve_mx`/`resolve_ns`/
  `resolve_txt` (`[]` = ausência definitiva → FAIL; `None` = erro → INCONCLUSO).
- **check_37 DNSSEC** (MÉDIA, A02/CWE-350): presença de **DS** no parent zone → PASS;
  ausente → FAIL (respostas DNS adulteráveis / cache poisoning).
- **check_38 CAA** (MÉDIA, A02/CWE-295): registros CAA `issue`/`issuewild` → PASS (lista as
  CAs; nota se tem `iodef`); ausente → FAIL (qualquer CA pode emitir certificado).
- **check_39 MTA-STS** (BAIXA, A02/CWE-319): TXT `_mta-sts.<domínio>` + GET público na
  policy `mta-sts.<domínio>/.well-known/mta-sts.txt` (RFC 8461). `mode: enforce` → PASS;
  `testing` → PASS com nota; declarado sem policy → FAIL; ausente → FAIL.
- **check_40 BIMI** (BAIXA, A07/CWE-290, **LGPD None**): TXT `default._bimi.<domínio>`
  com `v=BIMI1` → PASS (checa o pré-requisito DMARC enforce e anota se falta); ausente →
  FAIL (indicador de maturidade, não falha grave).

**Regra inviolável:** 100% passivo (consulta DNS pública + o GET do MTA-STS é URL pública
definida pela RFC). Os 4 são pagos. **Flush `scan:*` no Redis após deploy** (novos checks
mudam scores).

## 35. TLS profundo (KL-37) — checks 41–44

Vai além de "certificado válido?" (check_03) e "TLS 1.2+?" (check_04) para "TLS bem
configurado?", competindo com o SSL Labs. 4 checks pagos (ORDER>15), todos
**A02:2025 Cryptographic Failures**, via um **único handshake TLS compartilhado**.

- **`scanner/tls_analyzer.py`:** `get_tls_info(host, port)` faz **um** handshake (em
  `asyncio.to_thread`), cacheado por (host,porta) ~2min — os 4 checks **compartilham** o
  mesmo handshake (o runner os roda em sequência), sem reconectar 4×. Parseia o DER com
  `cryptography` (cipher/protocolo/cert/SAN/OCSP URI/chave). Tenta handshake **verificado**;
  em erro de verificação, cai para **não-verificado** para ainda extrair o cert (self-signed/
  expirado). Helpers puros testáveis: `weak_cipher_reason`, `has_forward_secrecy`,
  `classify_key`. **Não enumera** todas as suites (seria N conexões, mais intrusivo) —
  avalia a **negociada** (abordagem pragmática do card).
- **check_41 Cipher suites** (ALTA, CWE-327): cipher fraco (RC4/DES/3DES/NULL/EXPORT/anon) ou
  protocolo obsoleto → FAIL ALTA; sem forward secrecy no TLS 1.2 → FAIL MÉDIA; TLS 1.3/ECDHE
  forte → PASS.
- **check_42 Certificate chain** (MÉDIA, CWE-295): self-signed ou cadeia que não valida →
  FAIL; válido → PASS (nota se expira em <30 dias; mostra emissor/SAN).
- **check_43 OCSP stapling** (BAIXA, CWE-299): **limitação** — a `ssl` stdlib não expõe
  stapling; reporta a presença do **OCSP URI** (AIA) do cert (PASS com nota) ou a ausência
  (FAIL BAIXA).
- **check_44 Força da chave** (ALTA/CRÍTICA, CWE-326): RSA 2048+/ECDSA P-256+ → PASS; RSA
  1024 → FAIL ALTA; RSA <1024 → FAIL CRÍTICA.

**Regra inviolável:** 100% passivo (um handshake TLS público, como qualquer navegador); os 4
compartilham o handshake via `tls_analyzer` (não faz 4 handshakes). **Flush `scan:*` no Redis
após deploy** (novos checks mudam scores). Usa só `ssl`/`socket` (stdlib) + `cryptography`
(já no requirements) — **sem** pyOpenSSL.

## 36. Content analysis passivo (KL-38) — checks 45–48

Último card da Fase 0 (scanner profissional). Analisa o **HTML servido** em busca de padrões
de risco — sem requisição nova (exceto o GET de erro do check_46). 4 checks pagos (ORDER>15).
**Nova categoria OWASP** `_A04 = "A04:2025 Insecure Design"`.

- **check_45 Comentários HTML** (MÉDIA/ALTA, A01/CWE-615): extrai `<!-- ... -->` e procura
  credenciais/chaves/token (ALTA), IP/servidor/banco/TODO-de-segurança/paths (MÉDIA). Uma
  **whitelist** (copyright, meta, tracking, markers de template, condicionais de IE) é
  aplicada **antes** para evitar falso positivo.
- **check_46 Debug mode** (ALTA/MÉDIA, A05/CWE-489): stack traces / erros de framework
  (Python/PHP/Java/Django/Laravel/WP) no HTML **e** numa página de erro (GET numa URL
  inexistente `/klarim-nonexistent-debug-check-404` — passivo, o que um navegador faria);
  strip de `<script>/<style>` antes; headers de debug (Symfony) → FAIL MÉDIA.
- **check_47 Open redirect** (BAIXA/MÉDIA, A01/CWE-601): detecta a **presença** de params de
  redirect (`?redirect=`, `?next=`, `?url=`…) em href/action/src. **Não testa** se é
  explorável (depende do servidor) → BAIXA; >5 ocorrências → MÉDIA.
- **check_48 Password fields** (BAIXA, A04/CWE-522, **LGPD Art. 46+11**): `<input
  type=password>` sem `autocomplete=off/new-password` (navegador salva a senha) ou sem
  `name`/`id` (campo anônimo → phishing). Sem campo de senha = não aplicável (PASS).

**Regra inviolável:** 100% passivo (analisa o HTML já servido; o único request extra é o GET
de erro do check_46, inofensivo). Os 4 são pagos. **Flush `scan:*` no Redis após deploy**.
A whitelist de comentários (check_45) roda **antes** dos padrões sensíveis.

## 37. Enriquecimento por IA — setor + contato + perfil (KL-47A / KL-50 L5)

O classificador por regex deixa ~57% dos alvos em `outro` e a extração por regex ~39% em
`sem_contato`. Uma **única** chamada ao **GPT-4o mini** resolve os dois: classifica o setor
(inclui cauda longa), extrai contatos em texto corrido e gera a descrição do negócio. Custo
~US$0,001/site (~US$3,5 para os ~4,7k `sem_contato`).

- **`scanner/ai_enrichment.py`** (httpx direto, **sem** o SDK `openai`): `call_openai`
  (gpt-4o-mini, `response_format=json_object`, temp 0.1, chave de `OPENAI_API_KEY`),
  `SYSTEM_PROMPT`/`build_user_prompt` (trunca 3000 chars), `extract_clean_text` (strip
  script/style/tags), `ai_enrich` (normaliza o setor para o enum), `merge_ai_into_profile`.
  **Opt-in/fail-open:** sem `OPENAI_API_KEY`, `AI_ENRICHMENT_ENABLED=False` e toda a IA é
  silenciosamente desligada (regex-only, zero impacto); qualquer erro de rede/parse → `None`.
- **Regra de ouro (inviolável):** a IA **complementa** o regex e **preserva** o que é
  humano/IA. `merge_ai_into_profile` só preenche campo **vazio** do perfil. O setor só é
  atualizado por `store.ai_update_classification` (source `ai`) e só quando a IA volta com
  setor ≠ `outro` e confiança > 0.7. **KL-54:** com a expansão para 48 setores, o SQL passou
  a rever **toda** classificação por **regex** (auto/domain), independentemente de
  setor/confiança — o guard agora só **preserva** `classification_source='manual'` **e**
  `='ai'` (`IS DISTINCT FROM 'manual' AND IS DISTINCT FROM 'ai'`). Antes só tocava alvos
  fracos (`sector='outro' OR confidence<0.5`), o que deixava passar erros de regex confiante
  (ex.: `agencianextweb` classificado `imobiliaria` 0.5).
- **Contato via IA** (só quando o regex não achou): passa pela **mesma validação de MX**
  (KL-24) antes de tirar o alvo de `sem_contato` — nunca alimenta o funil com e-mail sem MX.
- **5 setores novos** (a IA classifica, o regex não): `saude`, `tecnologia`, `industria`,
  `agencia`, `consultoria` (em `SECTORS` e `PRICE_TIERS`; tier só p/ analytics — preço único).
- **Integração:** scan worker (`scanner/main.py::_ai_enrich_profile`, após o `_enrich_profile`
  do KL-50, inline) e `scripts/enrich_batch.py` (a IA tenta quando o regex não achou e-mail;
  `await asyncio.sleep(1)` entre chamadas p/ rate limit da OpenAI).

**Config (nunca no git):** `OPENAI_API_KEY` vive **só** no `/opt/klarim/.env` da VM. Os
serviços `api`/`worker`/`discovery` já usam `env_file: .env`, então a chave é propagada
**sem** mudar o `docker-compose.yml`. `os.environ.get("OPENAI_API_KEY")` — ausente ⇒ regex-only.
Opcional `OPENAI_MODEL` (padrão `gpt-4o-mini`). **Não** adicionar o SDK `openai` (httpx basta).

## 38. Taxonomia de setores — 48 setores + 13 macro-setores (KL-54)

Antes o Klarim tinha ~15 setores + `outro` e **57% dos alvos caíam em `outro`** (a
taxonomia não cobria o mercado de PME brasileiro). O KL-54 expande para **48 setores
finos** organizados em **13 macro-setores** (+ `outro`). Sem impacto no score de
segurança, sem flush de Redis, sem migration (a coluna `targets.sector` é TEXT).

- **`discovery/sector_taxonomy.py` — FONTE DA VERDADE ÚNICA** (módulo puro, zero
  imports internos → sem risco de ciclo). `SECTOR_TAXONOMY` (`setor → {macro, label}`),
  `VALID_SECTORS`, `MACRO_SECTORS`, `MACRO_LABELS`, `SECTOR_ALIASES` e os helpers
  `get_macro`/`get_label`/`normalize_sector`. O **macro-setor é derivável** por lookup
  (`get_macro`) — **não** há coluna nova no banco. **Ao mexer em setores, edite só
  aqui**; todos os outros módulos importam desta taxonomia.
- **IA (`scanner/ai_enrichment.py`):** o `SYSTEM_PROMPT` lista os 48 setores
  **dinamicamente** (`", ".join(sorted(VALID_SECTORS - {"outro"}))`, nunca à mão);
  `SECTORS = VALID_SECTORS`; a validação virou `normalize_sector(...)` (limpa +
  resolve aliases + inválido ⇒ `outro`).
- **Classificador regex (`discovery/classifier.py`):** `PRICE_TIERS = {s: "standard"
  for s in VALID_SECTORS}` — **preço único** (R$ 19); o tier só existe para analytics.
  `DOMAIN_PATTERNS`/`SECTOR_KEYWORDS` foram **desmembrados** (os finos vêm ANTES dos
  genéricos no dict, para o específico vencer o empate: `odontologia`>`clinica`,
  `padaria_confeitaria`>`restaurante`, `faculdade`>`escola`) e ganharam padrões dos
  setores novos **óbvios/precisos**. O regex **não** cobre os 48 — a IA cobre a cauda
  longa e refina os `outro`/fracos. ⚠️ `classify_sector` indexa `PRICE_TIERS[setor]`
  **direto**, então todo setor em `DOMAIN_PATTERNS`/`SECTOR_KEYWORDS` **tem** que estar
  em `VALID_SECTORS`.
- **Profiler (`scanner/profiler.py`):** `_SCHEMA_SECTOR` (Schema.org `@type` → setor)
  mapeia os tipos finos (`Dentist`→`odontologia`, `Bakery`→`padaria_confeitaria`,
  `Pharmacy`→`farmacia`, `VeterinaryCare`→`veterinaria`, `TravelAgency`→
  `turismo_viagens`, …). Todos os valores são setores válidos.
- **API:** `_VALID_SECTORS = set(PRICE_TIERS)` já cobre os 48 automaticamente; endpoint
  público **`GET /sectors`** (via Nginx `GET /api/sectors`) devolve `sectors`
  (48 `{id,label,macro}`) + `macro_sectors` (13 `{id,label}`) para dropdowns/filtros —
  **sem** expor nada sensível. `targets/stats` já agrupa por `sector` dinamicamente.
- **Frontend:** `SECTOR_OPTIONS` (em `components/admin/SectorEditor.jsx`) espelha a
  taxonomia (48 + outro, ordenada por macro); o filtro de setor em `Alvos.jsx` deriva
  dela (`SECTOR_OPTS = SECTOR_OPTIONS.map(o => o.value)`).
- **Retrocompatibilidade:** os 15 setores antigos continuam válidos; o genérico
  antigo `saude` (KL-47A, ~3 alvos) foi **desmembrado** e vira `clinica` via
  `SECTOR_ALIASES` (a IA refina no batch). Nenhum valor legado quebra.

**Regra inviolável:** `discovery/sector_taxonomy.py` é a única fonte da verdade — ao
adicionar/renomear setor, edite **só** lá; o prompt da IA, o `PRICE_TIERS` e a
validação da API derivam dela. Todo setor classificável pelo regex **tem** que estar
em `VALID_SECTORS` (senão `PRICE_TIERS[setor]` levanta `KeyError`). A taxonomia é
**dado comercial** — **não** altera o score de segurança.

## 39. Plataforma pública em Astro — landing + páginas legais (KL-51, fase 1)

Fase 1 da migração do site público de **React SPA (Vite)** para **Astro** (SSR
standalone), para ganhar SEO, performance e credibilidade. Arquitetura de experiência
completa em `claude/reports/klarim_arquitetura_experiencia_plataforma.md`.

**Decisão de arquitetura (menor risco, painel intacto):** em vez de substituir o
`frontend/`, **adicionamos** um serviço `astro` e **mantivemos** o Nginx (serviço `web`)
como front de TLS/segurança. O Nginx passou a fazer **proxy das rotas públicas novas →
Astro** e continua servindo o **build Vite** em `/painel*` + o **fluxo de scan existente**
(`/scan`, `/result`, `/pay`, …). Nada do painel/admin mudou.

- **`web/` (novo) — projeto Astro 7** (`output: 'server'` + `@astrojs/node`
  standalone → `dist/server/entry.mjs`; páginas desta fase com `export const prerender =
  true` = SSG). Tailwind **v4** via `@tailwindcss/vite` (CSS-first, igual ao `frontend/`;
  **não** o `@astrojs/tailwind`, que é v3). `@astrojs/react` já incluso para as *islands*
  das próximas fases. Estrutura: `src/layouts` (`Base.astro` com SEO/OG/dark, `Page.astro`
  para conteúdo), `src/components` (Header/Footer/Logo/ScanInput + seções da landing),
  `src/pages` (`index`, `termos`, `privacidade`, `sobre`), `src/styles/global.css`,
  `public/` (favicon.svg, robots.txt). Dark-mode default, mobile-first, PT-BR, **sem**
  Google Fonts/JS externo. O `ScanInput` é um form progressivo `GET /scan` (o fluxo
  completo de scan chega na fase 2).
- **`docker-compose.yml`:** serviço **`astro`** (`build: ./web`, Node em `:4321`,
  publicado só em `127.0.0.1:4321` para debug). O `web` (Nginx) ganhou `depends_on:
  astro`.
- **Nginx (`frontend/nginx/http.conf` + `https.conf.template`, só no server block
  principal — NÃO no subdomínio painel):** rotas do Astro com **resolver dinâmico** +
  upstream em variável (`set $klarim_astro astro:4321`, mesmo padrão do `/api/`):
  `location = /` (landing), `~ ^/(termos|privacidade|sobre|favicon\.svg|robots\.txt)`,
  `^~ /_astro/` (assets, cache 1a + **repete os security headers** — add_header próprio
  quebra a herança). **Tudo o mais é preservado:** `location /` (SPA Vite), `/assets/`
  (assets do painel), `/api/`, `/mcp/`, os bloqueios de paths sensíveis, o subdomínio
  `painel.` e os security headers do server. ⚠️ Ao mexer no Nginx, rode o `nginx -t`
  (há um job de CI para isso — veja abaixo); config inválida **derruba o site**.
- **`.dockerignore` (novo, raiz):** exclui `frontend/`, `web/`, `node_modules`, `dist`
  etc. do contexto da imagem Python (`api`/`worker`/`discovery`, `build: .`) — imagem
  enxuta e **não recriada** quando só o Astro muda.
- **`web/Dockerfile`:** multi-stage `node:20-slim` → `npm ci` + `npm run build` →
  runtime roda `node ./dist/server/entry.mjs` (`HOST=0.0.0.0 PORT=4321`).
- **CI (`.github/workflows/deploy.yml`):** dois jobs novos **antes** do deploy
  (`deploy` tem `needs: [test, build-web, nginx-check]`): **`build-web`** (`npm ci` +
  `npm run build` do Astro — quebra de build não vai a produção) e **`nginx-check`**
  (`nginx -t` no `http.conf` e no `https.conf.template` renderizado, com cert dummy —
  config inválida bloqueia o deploy, **não** derruba o site). `deploy.sh` ganhou um
  health check do Astro (`curl localhost:4321/`).

**Regra inviolável:** o Nginx é o front único de TLS/segurança — ao adicionar rota,
**preserve** todos os security headers, o subdomínio painel, `/api`, `/mcp` e os
bloqueios; valide com `nginx -t` (job de CI). O painel admin continua no build Vite
(`frontend/`, servido em `/painel`); o Astro (`web/`) serve **só** as rotas públicas
listadas. Fases seguintes migram o fluxo de scan, contas, dashboard e o painel para o
Astro (ver o doc de arquitetura).

### Fase 2 — fluxo de scan + resultado + correções (KL-51 f2)

- **Paywall aberto por flag (`PAYWALL_ENABLED`, default `false`).** Pivot freemium:
  todo scan **autorizado** (e-mail verificado, KL-25) vê os **48 checks** com detalhe e
  **não há** limite de 1 scan/e-mail. `_paywall_enabled()` gateia dois pontos em
  `api/main.py`: `/scan/request-code` (pula o `limit_reached`/`already_scanned` quando
  aberto) e `/scan/summary` (força `full=True`, preservando o ingest público KL-17 via
  `is_public_free`). Com `PAYWALL_ENABLED=true` volta o gate KL-27 (15 grátis + 33 🔒, 1
  scan/e-mail). O **PDF é sempre gratuito**. ⚠️ São **48 checks (15 free + 33 pago)** — a
  contagem "29" em docstrings antigas é do KL-27; `_tier_ok` já usa `len(ALL_CHECKS)`.
- **Benchmark:** `GET /benchmark` (média global) e `GET /benchmark/{sector}` (cai para a
  global se amostra < 5), via `store.global_avg_score`/`sector_avg_score`
  (`targets.last_scan_score`). Públicos.
- **Fluxo de scan (React island):** `web/src/components/scan/ScanFlow.jsx` (+
  `checks.js`, agrupa os 48 em 6 categorias) em `web/src/pages/scan.astro` (SSR,
  `prerender=false`, lê `?url=`). Etapas no client: e-mail → `POST /api/scan/request-code`
  → código → `POST /api/scan/verify-code` (scan token) → **progresso simulado** durante o
  `GET /api/scan/summary` (bloqueante ~30s) → **resultado inline** (score animado +
  semáforo + frase + benchmark + 48 checks por categoria, FAILs expansíveis com
  evidência/impacto/correção/OWASP-CWE-LGPD, CTA de PDF). O resultado renderiza no island
  (não há `/resultado/{scan_id}` — o backend faz scan bloqueante sem id pollável; página
  SSR de resultado com SEO fica para a fase de perfis públicos).
- **Correções f1:** logo real (`Logo.astro` = beacon laranja + `KLA`**`R`**`IM`, réplica
  do `Logo.jsx`) + favicon beacon; **contato** é a página `/contato` (Astro) que **reusa** o
  endpoint existente `POST /contact` — o footer aponta pra `/contato` (sem `mailto`). A API
  interna para os fetches SSR do Astro é `KLARIM_API_URL` (`http://api:8000`, no serviço `astro`).
  **⚠️ KL-58:** o form virou **ilha React** (`ContactForm.jsx`) que faz **POST client-side
  para `/api/contact`** — o `<form method=POST>` SSR batia na proteção CSRF do Astro
  (`checkOrigin`) atrás do Cloudflare ("Cross-site POST form submissions are forbidden", no
  mobile). O `/api/contact` (FastAPI) não tem CSRF do Astro; o rate limit segue por
  `X-Real-IP` do Nginx. Nenhuma outra página Astro processa POST (não mexemos no `checkOrigin`).
- **Nginx:** `/scan` e `/contato` entram na regex das rotas Astro (`~ ^/(termos|
  privacidade|sobre|contato|scan|…)`). `/api/scan/*` **não** conflita (casa o prefixo
  `/api/`, não a âncora `^/scan`). O fluxo Vite de scan (`/scan` antigo) fica sombreado.

### Fase 3 — contas de usuário + dashboard (KL-51 f3)

Transforma o visitante em usuário retido: conta (e-mail já verificado no scan) +
dashboard com evolução/benchmark + monitoramento mensal. **Duas superfícies de auth
distintas:** o operador/admin (`/auth/login`, `{username,password}` do `.env`, token
Bearer 24h — inalterado) e as **contas de usuário** (namespace **`/account/*`**, senha
bcrypt, JWT 30d no **cookie** HttpOnly). Como os dois JWT são assinados com o mesmo
`JWT_SECRET`, cada token carrega `typ` (`admin`|`user`) e cada camada só aceita o seu
(`_verify_token` exige `typ=admin`; `auth_users.verify_user_token` exige `typ=user`) —
sem isso um cookie de usuário passaria no middleware admin.

- **Backend (`api/auth_users.py` + `api/main.py`):** `bcrypt` (`hash_password`/
  `verify_password`), JWT de usuário (`create_user_token`/`verify_user_token`), e a
  dependency **`require_user`** (aceita `Authorization: Bearer` **ou** o cookie
  `klarim_session`). Endpoints `/account/*`: `signup` (e-mail verificado no scan +
  senha ≥8; vincula o site recém-escaneado; rate limit 5/IP/h; 409 se duplicado),
  `login`, `logout`, `forgot` (código 6 dígitos por e-mail, resposta **genérica**
  anti-enumeração, 3/e-mail/h), `reset`, `me`, e `sites` (`GET` lista, `GET /{id}`
  detalhe — alvo+histórico+checks+perfil+CNAE, `POST` adiciona respeitando
  `max_sites` do plano → **403 upgrade**, `DELETE`, `POST /{id}/claim` — dono só se o
  e-mail da conta bate o `contact_email` do alvo). Cookie `Secure/HttpOnly/SameSite=Lax`.
- **Tabelas (`discovery/store.py`):** `users` (email UNIQUE, `password_hash`, `plan`,
  `max_sites` 1 no free), `user_sites` (vínculo N-N, `is_owner`), `password_resets`
  (código TTL 1h, `used`). Métodos de user/site/reset + `get_user_sites_for_monitoring`
  e `latest_scan_meta` (monitoramento). `bcrypt` no `requirements.txt`.
- **Frontend Astro (`web/`):** páginas SSR (`prerender=false`) `/cadastrar`, `/entrar`,
  `/recuperar-senha`, `/dashboard`, `/dashboard/site/[id]` + ilhas React
  (`components/account/*`: SignupForm/LoginForm/ForgotForm/Dashboard/SiteDetail) que
  falam com `/api/account/*` via `lib/api.js` (`credentials: 'include'`).
  **`src/middleware.js`** protege `/dashboard/*`: lê o cookie, valida no backend
  (`GET /account/me`), injeta `Astro.locals.user`, senão redireciona a `/entrar?redirect=`.
  **`Header.astro`** é dinâmico: o cookie é HttpOnly (JS não lê), então um script
  consulta `/api/account/me` e alterna Entrar/Cadastrar ↔ Dashboard/Sair. O **CTA de
  cadastro** entra no resultado do scan (`ScanFlow.jsx`), passando e-mail+url à
  `/cadastrar`. Gráfico de evolução é **SVG inline** (sem Recharts no bundle Astro).
- **Monitoramento mensal (`scripts/monitor_rescan.py`, cron diário):** re-scan
  **completo (48)** de todo site de conta ativa com último scan >30d, salva, e envia o
  e-mail de evolução novo (`send_account_evolution`, `account_evolution.html`). É
  **independente** do rescan worker antigo (pausado; não mexe em alert_log/rescan_log).
  Deduplica o scan por site (1 scan, e-mail a cada dono).
- **Nginx:** `cadastrar|entrar|recuperar-senha|dashboard` entram na regex das rotas
  Astro do server block **principal** (não no subdomínio painel). `/api/account/*` casa
  o prefixo `/api/`, não a âncora `^/`.

**Regra inviolável:** admin e usuário são superfícies separadas — o `typ` do JWT nunca
é ignorado (um cookie de usuário jamais acessa `/targets` etc.). O e-mail já verificado
no scan (KL-25) é reaproveitado no signup (sem re-verificar). O limite de sites do plano
é servidor-autoritativo (403 no `POST /account/sites`, nunca só no frontend).

**Ajustes de UX pós-teste (KL-51 f3 fix):**
- **Escanear ≠ monitorar.** Escanear (consulta) é **ilimitado** para conta logada —
  **sem código de e-mail**. O `scan/summary` autoriza um scan novo também via sessão
  (`auth_users.optional_user`, cookie ou Bearer): `scanned_by` vira o e-mail da conta.
  O limite do plano vale **só para monitorar** (o 403 fica no `POST /account/sites`,
  nunca bloqueia o scan). No `scan.astro` (SSR) o cookie é validado e o `user` é passado
  ao `ScanFlow`; logado ⇒ pula e-mail/código e escaneia direto (`fetchSummary` com
  `credentials:'include'`).
- **Histórico no signup.** `store.get_targets_scanned_by_email` (via `scans.scanned_by_email`
  do KL-25, ou `targets.contact_email`) vincula os scans anteriores do e-mail à conta
  recém-criada, **respeitando `max_sites`** (o site do signup ocupa a vaga primeiro).
- **CTAs de conta em 2 posições** no resultado (topo, após o benchmark + reforço no fim):
  deslogado → "Criar conta"; logado → "Adicionar ao monitoramento" (trata 403 = limite).
- **PDF com dropdown** (Executivo/Técnico) + **"Enviar por e-mail"**: `POST /scan/send-report
  {url, email?}` gera os 2 PDFs e envia via Resend em **background** (rate limit 3/e-mail/h),
  resposta imediata com o e-mail **mascarado** (`_mask_email`); logado usa o e-mail da
  conta (sem pedir), deslogado usa o e-mail já verificado.
- **Contato → `scan@klarim.net`** (era `seguranca@`) em todas as superfícies públicas
  (páginas, templates de e-mail, HTML de descadastro). `send_contact` já mandava para
  `scan@`; só o texto exibido mudou. **Nenhuma mudança de sender no Resend** — `scan@` é
  só destinatário/mailto; os envios saem sempre do `RESEND_FROM` verificado.

**Hotfix do 504 no `scan/summary`.** Sintoma pós-deploy: 504 no scan + `AssertionError`
nos logs. **Causa:** o scan roda **inline** e **sequencial** (`runner`: `for check: await`)
— um site grande leva ~80s (gov.br medido), perto do `proxy_read_timeout` de 120s do
`/api/`. Sites lentos (ou a janela de cache frio logo após o deploy — Redis + CVE/CNAE
recém-limpos) passam de 120s ⇒ o Nginx **desconecta** ⇒ o handler atrás do
`_admin_auth_mw` (BaseHTTPMiddleware) tenta responder a um cliente já desconectado ⇒
`AssertionError` (ruído; o worker se recupera). **A auth do `scan/summary` já era
opcional** (anônimo → `auth_required` em ~0,5s; logado → escaneia). Fixes: (1)
**paralelizar o `runner`** — só bumpar o timeout não bastou (um scan frio de
`correios.com.br` deu 504 aos 180,6s). Fixes: (1) **`scanner/runner.py` roda os checks em
paralelo** (`asyncio.gather` + `Semaphore(SCAN_MAX_CONCURRENCY=12)`); é **seguro** porque o
rate limiter de `base.fetch` é **por-domínio** (`asyncio.Lock` segurado durante o request
inteiro) — requests ao MESMO domínio seguem serializados em **1 req/s** (regra do scanner
passivo preservada), só checks de domínios distintos (crt.sh/HIBP/DNS/TLS/CVE) se sobrepõem;
`gather` preserva a **ordem** dos checks; (2) `auth_users.optional_user` captura **qualquer**
exceção → `None` (auth opcional nunca derruba o scan); (3) `proxy_read_timeout`/`send_timeout`
do `/api/` **120s → 180s** (folga extra); (4) o fetch SSR de `/account/me` no `scan.astro`
ganhou timeout (4s, `AbortSignal.timeout`) p/ não travar o render; (5) `runScan` re-tenta 1×
após pausa (o scan lento **cacheia** no servidor mesmo com 504 no cliente → a re-tentativa
pega o cache quente). ⚠️ Paralelizar faz os checks 41-44 poderem abrir alguns handshakes TLS
a mais (cache por host, ainda passivo) — sem impacto no score. Teto por
`SCAN_MAX_CONCURRENCY` p/ não estourar o event loop / thread pool do worker único.

**2ª rodada de UX (KL-51 f3 fix).** (1) **Headline** da landing: "Seu site é seguro?
Descubra em 30 segundos." (+ meta/og description). (2) **Linguagem consistente**:
**Verificar/Consultar** = scan (ilimitado) · **Monitorar** = adicionar ao dashboard
(limitado pelo plano). No dashboard: form "🔍 Verificar um site" (GET `/scan`, consulta
livre) + o antigo "+ Novo site" virou **"+ Monitorar outro site"**. (3) **Histórico de
consultas** no dashboard: `GET /account/scan-history` (JWT de usuário) lê
`scans.scanned_by_email` (KL-25), 1 linha por URL, mais recente 1º; cada item abre o
resultado (`/scan?url=`); dedup dos sites já monitorados. O signup já vincula o histórico
a `user_sites` (KL-51 f3, `get_targets_scanned_by_email`). (4) **Painel admin**: a página
"Sites Monitorados" (KL-29) virou **"Gestão de Clientes"** (`/painel/clientes`,
`Clientes.jsx`) — lista as **contas de usuário** (`users` + `user_sites`) com plano,
sites (score/último scan), criação, último login e status, via `GET /admin/clients`
(`store.list_users_with_sites`); a rota antiga `/painel/monitorados` redireciona. O item
**"Escanear"** saiu da navegação do painel (redundante com a página Alvos).

**3ª rodada de correções (KL-51 f3 fix).** (1) **PDF grátis com o paywall desligado.**
`_require_paid` devolvia 402 mesmo com `PAYWALL_ENABLED=false` (só liberava em dev/sem
chave AbacatePay). Fix: `if not _paywall_enabled() or _free_access(): return` — com o
paywall off (default, freemium) o PDF (`/report/*`) é gratuito. (2) **Tracking na
plataforma Astro.** O Astro (`web/`) não tinha o tracker do KL-21 → `site_events` parava
de receber atividade nova → "Eventos recentes" do painel vazio. Fix: `web/public/track.js`
(asset **externo** → passa na CSP `script-src 'self'`) dispara `page_view` e expõe
`window.klarimTrack`; `Base.astro` o inclui; o `ScanFlow` emite o funil
(`scan_started/code_requested/code_verified/scan_completed/result_viewed`). (3) **Headers
do Nginx p/ score 100.** Snippet compartilhado `frontend/nginx/security_headers.conf`
(incluído via `include` no server + em cada location com `add_header` próprio; o
`nginx-check` do CI monta o arquivo): **CSP** sem `'unsafe-inline'` no `script-src` — os
**3 scripts inline** do Astro (toggle de auth do Header + 2 do runtime de island) entram
por **hash SHA-256** (estáveis; todo script novo é externo), `style-src` mantém
`'unsafe-inline'` (só script-src/default-src reprovam); **Permissions-Policy** (nega
câmera/mic/geo/…); **COOP** `same-origin`; **COEP** `require-corp` (seguro — site 100%
same-origin, todo recurso leva CORP); **CORP** `same-origin`; **Cache-Control** `no-store`
na landing + páginas com formulário (check_36). **OCSP (check_43):** `ssl_stapling` **não**
resolve (o check lê o **OCSP URI do certificado**, que a Let's Encrypt **removeu** em 2025);
o check foi ajustado — ausência de OCSP URI é o novo normal ⇒ **INCONCLUSO** (neutro), não
FAIL. Os 4 checks de **DNS** (DNSSEC/CAA/MTA-STS/BIMI) são configuração manual do dono.
⚠️ Ao mexer nos scripts inline do Astro (Header) ou subir a versão do Astro, **recalcular
os 3 hashes** da CSP (curl das páginas + sha256) — senão os scripts são bloqueados.

### Fase 4 — perfis públicos SEO + og:image + sitemap + notificação (KL-51 f4)

Expõe os ~18k sites como landing pages indexáveis (tráfego orgânico + viralidade). **Regra
de linguagem:** o Klarim avalia a segurança do **SITE**, não do negócio.

- **Página `/site/{dominio}` (Astro SSR, `web/src/pages/site/[domain].astro`).** Uma
  chamada ao backend **`GET /public/profile/{domain}`** (agregado: alvo + perfil + CNAEs
  + benchmark). Estados: `ok` (perfil completo), `not_found`/`not_scanned` ("ainda não
  analisado" + CTA `/scan?url=`), `discarded` ("não disponível"). **Privacidade
  inviolável:** o perfil público **nunca** expõe `contact_email`, `cnpj` nem `whatsapp`
  (`_PUBLIC_PROFILE_FIELDS` filtra) — nem os detalhes PASS/FAIL dos checks. `/score/{dominio}`
  → 301 para `/site/`. Rotas em `/public`, `/og`, `/notify` (NÃO nos prefixos protegidos
  por JWT admin; `/targets` é admin, por isso o endpoint é `/public/profile/`, não
  `/targets/by-domain`).
- **og:image dinâmico** (`GET /og/{dominio}.png`, 1200×630): SVG template (`_og_svg`) →
  PNG via **cairosvg** (reusa o cairo do WeasyPrint; import **lazy** → o CI/suite não
  precisa do libcairo). Cache em processo 24h + `Cache-Control: public, max-age=86400`.
  **Fail-open:** alvo sem score / render falho → 302 para o favicon. Servido via `/api/og/`
  (location `/api/` existente).
- **Sitemap** (`web/src/pages/sitemap.xml.js`, SSR): páginas estáticas + 1 URL por perfil,
  domínios de **`GET /public/sitemap-domains`** (`store.list_public_profile_domains`: só
  `scanned`/`alerted` com `site_profile`; exclui descartado/sem_contato). `robots.txt` já
  aponta o sitemap.
- **Notificação ao dono** (`POST /notify/profile-view {domain}`): fire-and-forget, envia
  o aviso "alguém consultou seu site" via Resend. **Rate limit 1/domínio/24h** (Redis SET
  NX EX). Pula alvos sem e-mail, `descartado`, `unsubscribed`, ou cujo e-mail já é de
  **usuário registrado** (o dono já acompanha). Opt-out reusa o `/api/unsubscribe` (KL-12).
  O `[domain].astro` chama `/notify` no SSR (best-effort, com timeout).
- **Base.astro** ganhou props `ogImage`/`ogType`/`twitterCard`/`fullTitleOverride`/`jsonLd`
  (structured data WebPage). O `jsonLd` é `<script type="application/ld+json">` — **dado**,
  não script executável, então **não** é governado pela CSP `script-src` (não precisa de
  hash). **Tracking** (`track.js`): dispara `profile_view` (com o domínio) nas páginas
  `/site/`.
- **Nginx:** location `~ ^/(site|score|sitemap\.xml)(/|$)` → Astro, com os security headers
  (include) + `Cache-Control: public, max-age=300` (perfis são cacheáveis, sem formulário).

**Regra inviolável:** o perfil público é só **dado que o próprio site já publica** (GET
passivo) + o score — **nunca** e-mail/CNPJ/WhatsApp/detalhe de check. og:image e sitemap
são best-effort/fail-open. A notificação respeita 1/domínio/24h e o opt-out.

### Fase 5 — enriquecimento de perfil em TODO scan (KL-51 f5)

Bug: só o **scan worker** gerava `site_profile`, e nem ele gravava os CNAEs (só o
`enrich_all.py` fazia). Scan manual do site (`/scan/summary`) e o fluxo admin não
enriqueciam nada — o perfil público `/site/{dominio}` (KL-51 f4) saía vazio. Fix: **todo**
caminho de scan gera o perfil **completo**.

- **`scanner/enrichment.py` (novo, módulo compartilhado):** `enrich_profile(store,
  target_id, url, security_score)` — crawl multi-page + `profiler.build_profile` (KL-50) +
  IA (`_ai_enrich`: setor + descrição + tags + **CNAEs**, KL-47A/55) → grava `site_profile`
  **e** `target_classifications`. **Best-effort** (erro só loga, nunca derruba
  scan/worker/request); imports lazy (evita ciclo e não pesa no boot). O `_ai_enrich` grava
  os CNAEs da IA com `source='ai'` (a Receita, `source='receita'`, nunca é sobrescrita —
  KL-55) e só refina o setor de alvo fraco preservando `manual`/`ai` (KL-54).
- **Um único ponto de verdade, três chamadores:** (1) **scan worker**
  (`scanner/main.py`) chama inline após salvar o scan — antes tinha `_enrich_profile`/
  `_ai_enrich_profile` locais (removidos), e o AI local **não** gravava CNAE; (2)
  **`/scan/summary`** (público + logado) via `_ingest_scan_bg` — que já roda em
  **background** (`_spawn`, depois da resposta do scan), então o profiler+IA (~10-20s) **não**
  entram no tempo de resposta nem no timeout de 180s; (3) **`/admin/scan-and-report`** via
  `_spawn(enrich_profile(...))`. O caminho pago/re-verificação ingere pelo mesmo
  `_ingest_scan_bg`.

**Regra inviolável:** o enriquecimento é **best-effort** e roda **fora do caminho síncrono**
do `/scan/summary` (background) — nunca atrasa a resposta do scan nem estoura o timeout. O
perfil comercial **não altera o score de segurança** (KL-50). CNAE da IA nunca sobrescreve a
Receita (KL-55); a classificação de setor preserva `manual`/`ai` (KL-54).

**Perfis esparsos + logging (fix pós-KL-60).** Sintoma: alguns sites (ex.: `igoove.com`)
geram landing esparsa (maturidade 1, plataforma `unknown`, sem descrição/tags/CNAE). **Causa
diagnosticada:** o site devolve **HTTP 403 ao User-Agent honesto do Klarim** (WAF/anti-bot).
Como o §4.3 proíbe **nos passarmos por navegador**, um site que bloqueia o UA **não pode ser
crawleado** — o perfil fica esparso e a notificação é pulada (`contact_email=None`). Isso é
**limitação**, não bug. O `enrich_profile` agora **loga o bloqueio** (`homepage HTTP 403 …`,
nº de páginas crawleadas) — antes a falha era silenciosa (`except: pass`). **Re-enrich
forçado:** `scripts/enrich_all.py --domain <texto>` (ou `--force`) roda o `enrich_profile`
compartilhado em cada alvo casado, **ignorando os grupos** (reprocessa mesmo com perfil
existente) — via `store.list_targets_matching`. Útil para perfis incompletos de sites que
**não** bloqueiam (o crawl volta com conteúdo). **Consultas de perfil no painel:** a página
**Alertas** ganhou a aba **"Consultas de perfil"** (eventos `profile_view` do `site_events`,
KL-51 f4) via `GET /analytics/events?event_type=profile_view` — domínio + data (o `site_events`
não guarda IP).

## 40. Classificação CNAE multi-setor + descrição natural + tags (KL-55)

A taxonomia fixa de 48 setores (KL-54) é insuficiente: ~54% dos sites caem em `outro`
porque negócios reais são **multi-setor** e muitos não têm equivalente. O KL-55 troca o
"1 setor por alvo" por **N classificações CNAE 2.0 (IBGE)** por alvo + uma **descrição em
linguagem natural** + **tags** de busca. É **dado comercial** — **não altera o score de
segurança**. **Retrocompatível:** `targets.sector` continua existindo (a IA devolve
`sector_legacy`, espelhado em `sector`), então todo o funil/painel/preço segue funcionando.

- **`discovery/cnae.py` — referência estrutural CNAE 2.0.** `derive_division` (2 dígitos)
  e `derive_section` (A–U) são **puros e offline** (mapa `_SECTION_RANGES` embutido: 21
  seções, faixas de divisão) — a classificação **nunca** depende de rede. `format_cnae`
  normaliza p/ `NN.NN-N`. `CNAETable` baixa `/classes` do IBGE em **runtime**, cacheia em
  `KLARIM_CACHE_DIR/cnae_table.json` (**TTL 30d**, escrita atômica) e é **fail-open**
  (sem rede/cache ⇒ tabela vazia; `validate_code` aceita 5+ dígitos, só a descrição é
  pulada). `get_cnae_table()` singleton; `sections()` (21) / `divisions()` (87) p/ a API.
- **Tabela `target_classifications`** (`discovery/store.py`): `target_id` (FK ON DELETE
  CASCADE), `cnae_code`, `cnae_description`, `cnae_section`, `cnae_division`, `confidence`,
  `source` (`receita|ai|manual|schema_org`), `rank`, `UNIQUE(target_id, cnae_code)` + 4
  índices. `site_profile` ganhou `tags TEXT[]` e `business_type`. Métodos:
  `upsert_target_classifications` (idempotente por (target,cnae)), `get_target_classifications`
  (ORDER BY rank), `has_receita_cnae`, `count_targets_without_cnae`, `cnae_division_avg_score`.
  **Regra inviolável (no WHERE do ON CONFLICT):** `source='receita'` (oficial) **nunca** é
  sobrescrito por `ai`/`schema_org` — só por `receita` nova ou `manual`.
- **IA (`scanner/ai_enrichment.py`) — prompt novo, 1 chamada.** O GPT-4o mini agora
  devolve `description` (1-3 frases), `business_type`, `company_name`, `tags` (5-10),
  `cnaes` (2-5, cada `code`/`description`/`confidence`), `sector_legacy` (taxonomia antiga)
  + `sector_confidence` + `contacts_found`. `_normalize_cnaes` formata o código e **deriva
  seção/divisão offline** (≤5). `ai_enrich` mapeia `sector_legacy → sector` (normalizado;
  alias `saude→clinica`; inválido⇒`outro`) p/ retrocompat, `max_tokens=900`.
  `merge_ai_into_profile`: `business_type` só preenche vazio (regra de ouro), `tags` a IA
  é a fonte (sobrescreve).
- **CNPJ → Receita Federal (`discovery/cnpj.py`).** Quando o profiler (KL-50) extrai um
  CNPJ, `fetch_cnpj` consulta **BrasilAPI → ReceitaWS** (cache 90d, fail-open) e
  `build_receita_classifications` monta os CNAEs **oficiais** (principal rank 1,
  secundários 2..N; `source='receita'`, `confidence=1.0`). `enrich_from_cnpj(cnpj, store,
  target_id)` grava — best-effort, nunca levanta. **Runtime-only** (as APIs podem faltar
  no CI; os testes mockam).
- **enrich_all G4 (`scripts/enrich_all.py`).** Novo **Grupo 4**: alvo "completo" pelo
  KL-54 (perfil + classificação IA/manual + descrição) mas **sem** CNAE — reclassificação
  CNAE de todo o banco. `_HAS_CNAE` (EXISTS, não JOIN — evita multiplicar linhas). Por
  alvo: a IA grava os CNAEs (`source='ai'`); se há CNPJ e ainda não tem Receita,
  `enrich_from_cnpj` grava os oficiais (`--cnpj-delay`, default 20s). `has_receita_cnae`
  evita reconsulta (idempotente). Stats: `cnae_ai`/`cnae_receita`/`group4`.
- **API + MCP.** `GET /targets/{id}` anexa `classifications`; `GET
  /targets/{id}/classifications` (JWT); públicos `GET /cnaes/sections`, `/cnaes/divisions`,
  `/benchmark/cnae/{division}` (nome `cnae/` p/ não colidir com `/benchmark/{sector}` do
  KL-51). MCP: `get_target` traz classifications + a tool nova `get_target_classifications`
  (**36 tools** no total: as 25 do KL-18 + monitoramento 2 + worker control 6 + perfil 1 +
  esta 2 — incl. `get_site_profile`).

**Regra inviolável:** o CNAE é **referência estrutural** + dado comercial — **não muda o
score de segurança**. `derive_section`/`derive_division` são offline (a classificação nunca
depende do IBGE estar no ar). `source='receita'` nunca é sobrescrito pela IA. Toda a
extração (CNAE/CNPJ/tags) é **best-effort/fail-open**: erro só loga, nunca derruba
scan/worker/batch. `targets.sector` permanece (retrocompat via `sector_legacy`).

## 41. Gestão de landing + paginação de scans + inbox de e-mail (KL-56)

Três frentes no painel admin + a caixa `scan@klarim.net` integrada.

**1. Gestão da landing pública na página Alvos.** Cada alvo com `site_profile` e status
`scanned`/`alerted` ganha o botão **"Landing"** (`components/admin/ProfileEditor.jsx`,
modal) com: **Ver landing** (abre `/site/{dominio}` em nova aba), **editar** os campos
(`description`/`business_type`/`company_name`/`tags`) e o **toggle** da landing pública.
- **`site_profile` ganhou 3 colunas** (`ALTER … ADD COLUMN IF NOT EXISTS`): `public_visible`
  (BOOLEAN default TRUE), `edited_by_admin` (BOOLEAN default FALSE), `edited_by_admin_at`.
- **`PUT /targets/{id}/profile`** (`ProfileEditBody`, JWT admin) → `store.update_site_profile_fields`
  atualiza só os campos editáveis e marca **`edited_by_admin=TRUE`**. A partir daí o enrich
  automático **preserva** esses campos: o guard está no `ON CONFLICT` do `upsert_site_profile`
  (`CASE WHEN site_profile.edited_by_admin THEN <valor antigo> ELSE EXCLUDED.<col> END` para
  description/business_type/company_name/tags). `public_visible`/`edited_by_admin` **nunca**
  entram no upsert (o enrich não os toca).
- **`PATCH /targets/{id}/profile/visibility`** (`VisibilityBody`) → `store.set_profile_visibility`.
  `public_visible=FALSE` faz `GET /public/profile/{domain}` retornar **`not_found`** (some do
  site, mesmo comportamento de descartado) e **exclui do sitemap** (`list_public_profile_domains`
  ganhou `AND COALESCE(sp.public_visible, TRUE) = TRUE`). `list_targets` traz `has_profile` +
  `public_visible` (LEFT JOIN) para a linha saber o estado sem N+1.
- **MCP:** `toggle_profile_visibility(target_id, visible)` + `update_site_profile(target_id,
  description?, business_type?, company_name?, tags?)` (`mcp_server/tools/targets.py`).

**2. Página Scans — paginação real + filtro por data.** O bug: o frontend tinha `page` na
dependência mas **não mandava `offset`** — toda página repetia a primeira. Fix:
`list_scans` ganhou **`offset`** + **`from_date`/`to_date`** (`YYYY-MM-DD`, `to_date` inclusivo
via `< to_date + 1 day`); `GET /scans` expõe os 3; `Scans.jsx` manda `offset=page*PAGE_SIZE`
e um **seletor de período** (Hoje / Últimos 7 dias / Últimos 30 dias / Personalizado /
Todos) com **default 7 dias** (não "tudo desde o início"). O período custom tem 2 date
pickers. Só a página Scans usa datas por default; os outros chamadores de `list_scans`
(atividade recente, público) seguem sem filtro.

**3. Inbox `scan@klarim.net` (Hostinger Agentic Mail).** Webhook recebe os e-mails e grava
em `inbox_messages`; o painel lê/gere.
- **Tabela `inbox_messages`** (independente, sem FK): `message_id` UNIQUE (dedup),
  `from_address/from_name/to_address/subject/body_preview/body_html/received_at`,
  `is_read/is_starred/is_archived`, **`source`** (KL-60: `webhook`|`contact_form`) + índices.
- **Formulário de contato → inbox (KL-60).** `POST /contact` grava a mensagem direto no
  `inbox_messages` (`source='contact_form'`, `message_id=contact-<uuid>`) **antes** de tentar
  o e-mail — a mensagem **nunca se perde** mesmo se o Resend falhar/entrar em loop (mesmo
  domínio sender/dest). O e-mail via Resend virou **best-effort** (try/except, só loga). No
  painel, o inbox tem tabs de origem **[Todos] [Emails] [Contato]** (`?source=`).
- **`POST /email/webhook` (público, auth PRÓPRIA).** `/email` é prefixo admin, então o webhook
  está no **`_PUBLIC_UNDER_PROTECTED`** (`_is_protected` retorna False) — não passa pelo JWT
  admin, tem **token próprio**. `_hostinger_token_ok` valida `HOSTINGER_WEBHOOK_TOKEN`
  (constant-time, **fail-closed** sem a env) aceito em `Authorization: Bearer`, vários headers
  custom **ou** `?token=`. `parse_inbox_payload` (função pura, testável) suporta o formato
  **AgentMail** (`event_type=message.received` + objeto `message` com `from/to/subject/text/
  html/message_id/timestamp`) **e** formas achatadas; sintetiza `message_id` (hash) se faltar;
  usa `email.utils.parseaddr` no `from`. Payload não reconhecido → **loga o raw** (para
  adaptar) e responde 200 (Hostinger não re-tenta). Grava via `insert_inbox_message`
  (ON CONFLICT DO NOTHING → dedup). ⚠️ A AgentMail nativa usa **Svix**, mas a Hostinger hPanel
  usa o token plano configurado — por isso a validação é por token + log do raw na 1ª recepção.
- **API admin (JWT):** `GET /admin/inbox` (filtros `box=all|unread|starred|archived` +
  **`source=webhook|contact_form`** KL-60, paginado),
  `GET /admin/inbox/unread-count` (**declarado ANTES de `/{msg_id}`** senão vira id inválido),
  `GET /admin/inbox/{id}` (corpo completo, marca lida ao abrir), `POST …/{id}/read|star|archive`.
- **Frontend:** `pages/admin/Inbox.jsx` (rota `/painel/inbox`, `lazy` no `App.jsx`) — lista com
  ●/○ (não-lida/lida), estrela, arquivar; **badge de não-lidas** no `AdminLayout` (poll 60s via
  `admin.inboxUnread`). ⚠️ **Segurança:** o corpo HTML vem de remetente externo (não confiável)
  → renderizado em **`<iframe sandbox="">`** (sem scripts, origem opaca) — **nunca**
  `dangerouslySetInnerHTML` (evita stored-XSS roubando o JWT do operador). Responder = link
  `mailto:`/webmail (envio via API Hostinger fica para fase opcional, `HOSTINGER_API_TOKEN`).

**Config (nunca no git):** `HOSTINGER_WEBHOOK_TOKEN` + `HOSTINGER_API_TOKEN` vivem **só** no
`/opt/klarim/.env` da VM (os serviços usam `env_file: .env`). O webhook precisa ser configurado
no hPanel da Hostinger para `https://klarim.net/api/email/webhook` (POST, header `Authorization:
Bearer <HOSTINGER_WEBHOOK_TOKEN>` **ou** `?token=<TOKEN>`; cai no `location /api/`). **⚠️ KL-58:**
diagnóstico em produção provou o pipeline ponta a ponta (POST público → 200 → `stored:true`,
inbox 0→1); se as mensagens não chegam, é porque a **Hostinger não está enviando** — conferir a
config do webhook no hPanel. `_hostinger_token_ok` aceita o token em vários headers/queries e
loga os **nomes** dos headers (nunca valores) num 401 para diagnosticar; `parse_inbox_payload`
desembrulha wrappers `data`/`payload`/`body`/`email` e aceita lista.

**Regra inviolável:** o webhook é **fail-closed** (sem `HOSTINGER_WEBHOOK_TOKEN`, tudo 401) e
tem auth própria (não JWT admin); o corpo de e-mail externo **nunca** é injetado no DOM do
painel (só `<iframe sandbox>`); `edited_by_admin` protege a edição manual do perfil contra o
enrich; a landing desligada (`public_visible=FALSE`) some do site **e** do sitemap.

## 42. Perfil no resultado + gestão de conta + dashboard admin (KL-57)

Três frentes de maturidade: o resultado do scan liga ao perfil público, o usuário
gerencia a própria conta, e o painel admin ganha totalizadores + saúde do sistema.

**1. Perfil público no resultado do scan.** `/scan/summary` anexa `has_profile` +
`profile_domain` (via `_profile_info(url)`, mesmo critério de visibilidade do KL-51 f4:
existe `site_profile`, `public_visible` não desligado e alvo não descartado). No
`ScanFlow.jsx` (`web/`), o `ResultView` mostra **"🔗 Ver perfil público"** (link para
`/site/{dominio}`, nova aba) ao lado dos botões de PDF **e** uma seção **"Compartilhar"**
no fim (URL `klarim.net/site/{dominio}` + **Copiar link** + **Abrir perfil**). Sem perfil
(gerado em background após o scan, KL-51 f5) → aviso discreto "disponível em instantes".
Sem pop-up/modal/redirect. Evento `profile_link_clicked` (KL-21).

**2. Gestão de conta (`/dashboard/conta`).** Página Astro SSR (protegida pelo
middleware) + ilha `AccountSettings.jsx`: **dados pessoais** (nome editável;
`PUT /account/me` → `update_user_name`, sanitiza HTML; e-mail não editável),
**segurança** (`POST /account/change-password` confere a atual via bcrypt, exige nova ≥ 8,
**não** invalida a sessão; rate limit 5/e-mail/10min), **plano** (read-only) e **zona de
perigo** (`DELETE /account/me` confirma por senha → `delete_user` [**CASCADE** apaga
`user_sites`; `targets`/`scans`/`site_profile` **permanecem** — o perfil público segue no
ar], limpa o cookie, envia e-mail `account_deleted.html` em background). Link "Minha conta"
no `Header.astro` (logado) + "Gerenciar conta" no card de plano do `Dashboard.jsx`.
`_user_public` passou a expor `created_at`. Eventos `password_changed`/`account_deleted`.

**3. Dashboard admin (`GET /admin/dashboard-stats`, JWT).** `store.dashboard_summary()`
agrega em poucas queries (sem N+1): **alvos** (total, por status, `score_100`), **scans**
(total, média, semáforo, **manual** [`scanned_by_email IS NOT NULL` = site público] vs
**automatizado** [worker], hoje, 7 dias), **perfis/landings** (total, públicas, ocultas,
com IA [descrição preenchida], com CNAE), **contas** (total, ativas, sites monitorados) e
**alertas** (total, hoje); o endpoint mescla `inbox.unread`. A home do painel
(`Overview.jsx`) ganhou a grade de totalizadores + a grade de enriquecimento de perfis + um
card **Saúde do sistema** (workers ▶️/⏸️/🔴 + postgres/redis/ct_logs, via `/system/status`,
best-effort). `adminApi.dashboardStats()`.

**Regra inviolável:** a exclusão de conta **nunca** remove `targets`/`scans`/`site_profile`
(dados do sistema — o perfil público permanece); os totalizadores são queries agregadas
(sem N+1, sem full scan caro); o resultado gratuito **continua** sem detalhe dos checks
pagos (KL-27) — `has_profile` é só o sinal do link, não vaza dado do perfil.

## 43. Score social: widget + card + ranking + selo (KL-42)

Cinco mecânicas de viralidade (fase 8 da arquitetura) que transformam cada usuário num
canal de aquisição. Tudo **público** (não está sob prefixo protegido) e derivado do score
que o site já tem — sem campo novo no banco.

**Selo/badge (`_score_badge`, `web/src/lib/badge.js`):** ≥90 **Klarim Verified** ⭐, ≥80
**Klarim Approved** ✅, <80 sem selo. Derivado do score — o backend e o front espelham a
mesma regra. Aparece no widget, no card, no perfil, no ranking e no dashboard.

**1. Widget embeddable "Verificado por Klarim".** `GET /widget/{dominio}.js`
(`application/javascript`, cache 1h) devolve um JS leve, self-contained, CSS inline, com o
domínio embutido; o estilo (`inline`/`card`/`minimal`) é lido em runtime do `?style=` do
próprio `<script>`. O JS busca o score em **`GET /score/{dominio}`** (JSON, cache 24h, **CORS
`*`** — é dado público sem cookie) e injeta o selo antes da própria tag. **Beacons** de
impressão/clique via **pixel GET** `GET /widget/event?e=&d=&s=` (204, sem CORS — o widget roda
em site externo). O link do selo aponta para `/site/{dominio}?utm_source=widget`. Página
**`/dashboard/widget`** (`WidgetGenerator.jsx`): seleção de site + estilo + preview + snippet
`<script async …>` + copiar. `"Powered by Klarim"` é inerente (todos free hoje).

**2. Card compartilhável.** `GET /card/{dominio}.png?format=square|landscape` (reusa a infra
do og:image: `_card_svg` → cairosvg, cache 24h, fail-open → favicon). **square** 1080×1080
(Instagram), **landscape** 1200×630 (LinkedIn/Twitter), com o CTA "Nosso site tem score X…
E o seu?". `ShareScore.jsx` (usado no `SiteDetail` e no resultado do `ScanFlow`): selo +
posição no ranking + preview + download (square/landscape) + copiar link + WhatsApp/
LinkedIn/Twitter (share URLs nativas).

**3. Rankings por setor (SEO).** `GET /ranking` (setores com ≥5 sites: contagem, média, top
site) e `GET /ranking/{setor}` (top 20 por score). Só sites com scan público (`scanned`/
`alerted`) **e landing ligada** (`public_visible`, KL-56) entram — usa `targets.sector`
(taxonomia 48, KL-54). Páginas Astro SSR `web/src/pages/ranking/index.astro` +
`ranking/[sector].astro` (indexável só com ≥5 sites; JSON-LD ItemList). Adicionadas ao
`sitemap.xml` (1 URL por setor ≥5). `track.js` dispara `ranking_viewed`.

**4. Posição no ranking (dashboard).** `GET /account/sites/{id}` ganhou `badge` + `ranking`
(`store.get_sector_position`: `ROW_NUMBER()` no setor — ranqueia entre TODOS os sites com
score do setor, não exige perfil, pois a posição é do dono). `SiteDetail.jsx` mostra
"#N de M sites de {setor} · acima de X%" + selo + `ShareScore`. `Dashboard.jsx` mostra o selo
no `SiteCard` + links Compartilhar/Widget.

**5. Notificação de mudança de posição:** o e-mail de evolução mensal (`monitor_rescan.py`)
fica preparado, mas a linha de ranking no e-mail é **futuro** (a posição já aparece no
dashboard).

**Store (KL-42):** `list_sector_ranking`, `ranking_sectors_summary` (≥N, com top domínio),
`get_sector_position`. **Nginx:** `ranking` entrou no bloco cacheável do Astro
(`^/(site|score|ranking|sitemap\.xml)`, 300s) no `https.conf.template`; `/api/widget|score|
card|ranking` caem no `/api/` existente; `/dashboard/widget` já cai no `dashboard` da regex
Astro. **Eventos:** `widget_loaded`, `widget_clicked`, `widget_copied`, `card_downloaded`,
`share_clicked`, `ranking_viewed`.

**Regra inviolável:** widget/card/score/ranking são **100% dados públicos** (score + domínio,
nunca e-mail/CNPJ/WhatsApp) e respeitam a visibilidade (`public_visible`/descartado) — o
`/score` de site oculto devolve `score: null`. O widget é **leve, async, CSS inline** e não
pode impactar a performance do site externo. O card é **best-effort/fail-open** (render falho
→ favicon). O Klarim avalia a segurança do **SITE**, não do negócio.
