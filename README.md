# Klarim

**"O alarme que toca antes do ataque."**

Scanner passivo de superfície de ataque para PMEs e desenvolvedores. O Klarim
executa **verificações passivas de segurança** comprováveis — sem invasão —
contra um site público, calcula um **score de 0 a 100** e gera um relatório
acionável. O conjunto de checks é **dinâmico e cresce continuamente** (hoje 15).

> ⚖️ **Passivo e legal por design.** O Klarim faz apenas requisições HTTP
> `GET`/`HEAD` a URLs públicas e lê certificados TLS públicos. Ele **nunca**
> envia payloads de ataque (SQLi/XSS), não faz brute-force, não acessa áreas
> autenticadas e não explora vulnerabilidades. Ver [Framework legal](#framework-legal).

---

## As verificações

O número de checks **não é fixo** — novos módulos `check_*.py` são descobertos
automaticamente (ver [Como adicionar um check](#como-adicionar-um-check)).
Conjunto atual (15):

| # | Check | Módulo | Severidade |
|---|-------|--------|-----------|
| 01 | HTTPS ativo (porta 80 redireciona p/ HTTPS) | `check_https.py` | 🔴 Crítica |
| 02 | HSTS presente (`Strict-Transport-Security`) | `check_hsts.py` | 🟠 Alta |
| 03 | Certificado SSL válido (expiração, CA, host) | `check_ssl.py` | 🔴 Crítica |
| 04 | TLS 1.2+ only (rejeita TLS 1.0/1.1) | `check_tls.py` | 🟠 Alta |
| 05 | Content-Security-Policy presente | `check_csp.py` | 🟠 Alta |
| 06 | X-Frame-Options (anti-clickjacking) | `check_xfo.py` | 🟡 Média |
| 07 | X-Content-Type-Options: `nosniff` | `check_xcto.py` | 🟡 Média |
| 08 | Server header não expõe versão | `check_server.py` | 🟡 Média |
| 09 | Source maps não expostos (`.js.map`, manifest) | `check_sourcemaps.py` | 🔴 Crítica |
| 10 | Arquivos sensíveis (`.env`, `.git/config`, …) | `check_sensitive.py` | 🔴 Crítica |
| 11 | Directory listing desativado | `check_dirlist.py` | 🟠 Alta |
| 12 | Meta tags sem fingerprint de framework | `check_metatags.py` | 🔵 Baixa |
| 13 | SRI ausente em scripts externos (>50%) | `check_sri.py` | 🟠 Alta |
| 14 | Scripts de fontes arriscadas (GitHub Pages, S3, paste) | `check_risky_sources.py` | 🟠 Alta |
| 15 | Domínios externos em excesso carregando scripts | `check_external_domains.py` | 🟡 Média / 🟠 Alta |

Os checks 13–15 cobrem **supply chain / third-party risk** (KL-2). Eles fazem um
parse **passivo do HTML servido** (via `html.parser` da stdlib) — scripts
injetados dinamicamente por JavaScript em runtime não são vistos por uma
requisição HTTP simples.

Cada check implementa a mesma interface:

```python
async def check(url: str) -> CheckResult
```

onde `CheckResult` carrega `name`, `status` (`PASS`/`FAIL`/`INCONCLUSO`),
`severity` (`CRITICA`/`ALTA`/`MEDIA`/`BAIXA`) e `evidence` (string com o detalhe
concreto observado). Timeout de **10s por request** e **rate limit de 1 req/s por
domínio** são aplicados de forma centralizada em `checks/base.py`.

### Como adicionar um check

Não há lista hardcoded — o runner descobre os checks dinamicamente. Para
adicionar um:

1. Crie `scanner/checks/check_<slug>.py`.
2. Defina três constantes de módulo: `ORDER` (int, posição na suíte),
   `CHECK_ID` (str, ex.: `"check_16_cookies"`) e `NAME`.
3. Implemente `async def check(url: str) -> CheckResult`.
4. Pronto — `scanner.checks.discover_checks()` já o inclui, ordenado por `ORDER`.
   O score em `scoring.py` funciona com qualquer número de checks.

---

## Estrutura

```
klarim/
├── claude.md               # guia de onboarding para agentes Claude
├── claude/                 # governança: session summaries + task reports
│   ├── README.md
│   ├── sessions/           # resumos de sessão do chat planejador
│   └── reports/            # relatórios por tarefa (KL-xxx)
├── .github/workflows/
│   └── deploy.yml          # CI/CD: push main → test → deploy (GCP)
├── deploy/
│   └── deploy.sh           # script de deploy executado na VM
├── docker-compose.yml      # PostgreSQL + Redis + API + Worker
├── Dockerfile              # imagem compartilhada (API/Worker)
├── .env.example            # variáveis de ambiente (sem segredos)
├── requirements.txt
├── scanner/
│   ├── main.py             # entry point do worker + CLI
│   ├── runner.py           # orquestra todos os checks registrados + score
│   ├── scoring.py          # cálculo do score 0-100 + semáforo
│   └── checks/
│       ├── base.py         # CheckResult, rate limit, HTTP helper, HTML parse
│       └── check_*.py      # os checks (descobertos dinamicamente)
├── reporter/               # geração de PDF (WeasyPrint + Jinja2)
│   ├── generator.py        # generate_executive_pdf / generate_technical_pdf
│   ├── templates/          # executive.html + technical.html
│   └── assets/logo.svg
├── frontend/               # interface web (React + Vite + Tailwind v4 + Nginx)
│   ├── src/                # pages/ + components/ + lib/
│   ├── nginx.conf          # estático + proxy /api → api:8000
│   └── Dockerfile
├── api/
│   └── main.py             # FastAPI (semáforo + relatório + PDFs)
└── tests/
    ├── test_checks.py      # unit tests dos checks + teste online opt-in
    └── test_reporter.py    # geração de PDF (offline)
```

---

## Uso

### 1. Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Scan pela CLI

```bash
# Relatório legível
python -m scanner.main https://www.example.com

# JSON (para pipelines)
python -m scanner.main https://www.example.com --json

# Gera os PDFs executivo + técnico no diretório atual
python -m scanner.main https://www.example.com --pdf
```

Exit code `0` se o score ≥ 50, `1` caso contrário (útil em CI/cron).

### 3. Uso programático

```python
import asyncio
from scanner import run_scan, format_report

report = asyncio.run(run_scan("https://www.example.com"))
print(format_report(report))
print(report.score.score, report.score.semaphore)   # ex.: 55 amarelo
```

### 4. API

```bash
uvicorn api.main:app --reload --port 8000
```

- `GET /scan?url=…` — relatório técnico completo (JSON).
- `GET /scan/summary?url=…` — semáforo executivo gratuito (score + contagens).
- `GET /report/executive?url=…` — relatório executivo em **PDF**.
- `GET /report/technical?url=…` — relatório técnico em **PDF**.

### 5. Stack completa (Docker)

```bash
cp .env.example .env      # edite as variáveis
docker compose up --build
```

Sobe **PostgreSQL**, **Redis**, a **API** (`:8000`) e o **Worker**. Para
enfileirar um alvo:

```bash
redis-cli LPUSH klarim:scan_queue "https://www.example.com"
redis-cli GET  "klarim:report:https://www.example.com"
```

### 6. Testes

```bash
pytest                                   # unit tests offline
KLARIM_ONLINE=1 pytest tests/test_checks.py   # inclui scan real
```

---

## Deploy e infraestrutura

Produção roda em uma **VM GCP Compute Engine** (`e2-small`, Debian) com Docker
Compose, em `/opt/klarim`. O `.env` de produção vive **apenas na VM** (nunca no
git).

**Provisionamento (uma vez):** instalar Docker + plugin Compose, criar
`/opt/klarim`, clonar o repo e criar o `.env`. Passo a passo em
[`claude/reports/KL-3_gcp-deploy-cicd.md`](./claude/reports/KL-3_gcp-deploy-cicd.md).

**Deploy manual:**

```bash
gcloud compute ssh --zone "us-central1-a" "instance-20260706-112125" \
  --project "project-b08050df-fa4e-49ac-919"
# na VM (deploys rodam como root, igual ao CI):
sudo bash /opt/klarim/deploy/deploy.sh
```

**CI/CD (`.github/workflows/deploy.yml`)** — a cada push para `main`:

1. **`test`** — Python 3.12, `pip install -r requirements.txt`, `pytest`. Falhou,
   não faz deploy.
2. **`deploy`** (`needs: test`) — autentica no GCP, faz SSH na VM e roda
   `deploy/deploy.sh` (`git pull` → `docker compose up -d --build` → health check).

Autenticação **keyless via Workload Identity Federation** (OIDC) — o projeto
proíbe chaves de service account. Secrets necessários no GitHub (configurados
**manualmente**, nunca no repo): `GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`,
`GCP_PROJECT_ID`, `GCP_INSTANCE`, `GCP_ZONE`. O provider é travado no repo
`joaquim-83/klarim` e a SA `klarim-deploy` só pode ser impersonada por ele.

---

## Score

O score é uma proporção ponderada dos checks que passaram. Cada check tem peso
por severidade (Crítica 5, Alta 3, Média 2, Baixa 1). `PASS` soma o peso, `FAIL`
soma zero e `INCONCLUSO` é **excluído do denominador** (neutro):

```
score = round(100 * Σ peso(PASS) / Σ peso(PASS + FAIL))
```

Semáforo (calibração KL-12): **🟢 verde** score ≥ 90 **e** zero FALHA
Alta/Crítica · **🟡 amarelo** score ≥ 50 (ou ≥ 90 com FALHA Alta/Crítica) ·
**🔴 vermelho** < 50. Verde não convive com falha séria — verde = "está tudo bem".

---

## Relatórios PDF

O módulo [`reporter/`](./reporter/) transforma um `ScanReport` em dois PDFs
(**Jinja2 → WeasyPrint**), na identidade visual do Klarim (dark + laranja/verde):

- **Executivo** (1-2 páginas) — para o dono do negócio: semáforo, linguagem
  acessível, seção **"O que pode acontecer com o seu site"** com riscos concretos
  por falha (KL-20 — "seu site pode ser usado para golpes", não artigos de lei; a
  LGPD vira nota de rodapé), lista de problemas em linguagem humana.
- **Técnico** (3-5 páginas) — para dev/agência: tabela de todos os checks,
  detalhamento de cada falha (evidência + impacto + correção com exemplo) e
  inventário (domínios externos, scripts sem SRI, fontes arriscadas, headers).

```python
import asyncio
from scanner import run_scan
from reporter import generate_executive_pdf, generate_technical_pdf

report = asyncio.run(run_scan("https://www.example.com"))
pdf = asyncio.run(generate_executive_pdf(report, "https://www.example.com"))  # -> bytes
```

Ou via CLI (`--pdf`) e API (`/report/executive`, `/report/technical`). Exemplos
reais gerados para os 3 hotéis Duda estão em
[`claude/reports/`](./claude/reports/) (`klarim_*_*.pdf`).

> **WeasyPrint** precisa de bibliotecas nativas (pango/cairo) — já incluídas no
> `Dockerfile` e no job de teste do CI. Em macOS local: `brew install pango`.

---

## Interface web

Frontend **React + Vite + Tailwind v4** em [`frontend/`](./frontend/), servido
como build estático pelo **Nginx** (que também faz proxy de `/api` → API). Telas:

- **Landing** (`/`) — scan self-service com **verificação de e-mail** (KL-25):
  URL + e-mail → código de 6 dígitos → scan. **1 scan gratuito por e-mail**; o 2º
  (outra URL) pede o relatório pago. Captura o lead e corta bot/curioso.
- **Scan** (`/scan?url=`) — loading com feedback enquanto a varredura roda (~30s).
- **Result** (`/result?url=`) — semáforo, contagem por severidade, LGPD e CTA.
- **Report** (`/report?url=`) — download dos relatórios executivo e técnico (PDF).

```bash
cd frontend
npm install
npm run dev      # dev (proxy /api → localhost:8000)
npm run build    # build de produção → dist/
```

Em produção, o serviço **`web`** do `docker-compose.yml` (portas **80/443**)
constrói o frontend e serve tudo via Nginx; a API fica em `127.0.0.1:8000` (só o
Nginx é público). Suba a stack completa com `docker compose up --build` e acesse
`http://localhost`.

**HTTPS (Let's Encrypt):** o Nginx serve HTTP até existir um certificado e passa
a HTTPS automaticamente depois (entrypoint self-healing — o deploy nunca quebra
por falta de cert). Com o DNS apontando para o IP da VM, emita o certificado uma
vez: `sudo bash /opt/klarim/deploy/setup-https.sh <dominio>`. O HTTPS inclui os
security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options,
Referrer-Policy) — o Klarim pratica o que prega. Renovação automática via
`certbot renew` no `deploy.sh`.

---

## Pagamento (PIX via AbacatePay)

O relatório completo é liberado após pagamento **PIX** (módulo
[`payments/`](./payments/), integração AbacatePay). Fluxo: semáforo grátis →
"Ver relatório completo — R$ 29" → **QR code PIX inline** → polling do status →
pago → download dos PDFs. Um **webhook** confirma o pagamento server-side.

- `POST /api/payment/create` → cria a cobrança e retorna `br_code` + `qr_code_base64`.
- `GET /api/payment/status?charge_id=` → polling (`{status, paid}`).
- `POST /api/webhooks/abacatepay?webhookSecret=…` → confirmação server-side.
- `GET /api/report/{executive,technical}?url=…&charge_id=…` → **402** sem cobrança paga.

**Modo livre:** com `KLARIM_DEV_MODE=true` **ou** sem `ABACATEPAY_API_KEY`
configurada, os PDFs ficam liberados (o site funciona antes de configurar o
pagamento). Variáveis (no `.env` da VM, **nunca commitadas**):
`ABACATEPAY_API_KEY`, `ABACATEPAY_WEBHOOK_SECRET`, `KLARIM_DEV_MODE`. Chave
`abc_dev_…` = sandbox (permite simular pagamento).

---

## E-mail (Resend)

Módulo [`notifier/`](./notifier/): **alerta gratuito** (semáforo — o anzol do
funil) e **entrega do relatório** pago (2 PDFs anexados). Templates HTML
table-based (Gmail/Outlook), paleta dark.

- `POST /api/email/test` — e-mail de teste.
- `POST /api/email/send-alert` — escaneia e envia o alerta com semáforo.
- `POST /api/email/send-report` — envia os 2 PDFs (exige cobrança paga).

**Controle de bounce (KL-24).** Para proteger a reputação do domínio (bounce rate
precisa ficar < 4%): (1) a captação de e-mail (`discovery/contact.py`) valida
**MX** do domínio antes de aceitar (dnspython + cache); (2) o Alert Worker filtra
**blocklist** + domínios sem MX antes de enviar e **pausa** se o bounce rate passar
de 8%; (3) o webhook `POST /api/webhooks/resend` (assinatura Svix) marca bounces
permanentes como `descartado` + blocklist e complaints como `unsubscribed`; (4)
`POST /api/admin/process-bounces` faz o backfill dos bounces já ocorridos. O painel
**Sistema** mostra o bounce rate com semáforo de risco. Variáveis: `RESEND_WEBHOOK_SECRET`,
`ALERT_VALIDATE_MX`, `ALERT_MAX_BOUNCE_RATE`.

Na compra, a tela `/pay` pede o e-mail; após o pagamento confirmado (webhook ou
polling), o relatório é **enviado automaticamente** em background (idempotente;
se falhar, o cliente ainda baixa no site). A tela `/report` mostra o status do
envio (enviando → enviado/falhou). Variáveis (`.env` da VM, **nunca
commitadas**): `RESEND_API_KEY`, `RESEND_FROM`. Sem domínio verificado, use
`Klarim <onboarding@resend.dev>` (só envia ao dono da conta Resend); para enviar
a qualquer um, verifique `klarim.net` no Resend (SPF/DKIM/DMARC).

> **Cache de scan (KL-9):** o `ScanReport` é cacheado no **Redis** (TTL 1h,
> `scanner/cache.py`), então baixar o PDF após o pagamento é **instantâneo**
> (< 3s) em vez de re-escanear ~30s.

**Recuperação (KL-10):** quem pagou e não recebeu recupera em
[`klarim.net/recuperar`](https://klarim.net/recuperar) — informa o e-mail do
pagamento e recebe um **link temporário** (token 24h) que lista e permite
re-baixar os relatórios pagos. Endpoints `/recovery/request|validate|download`;
resposta genérica (anti-enumeração), rate limit 3/e-mail/hora, e-mail mascarado,
validação cruzada charge↔e-mail.

---

## Discovery Worker (aquisição)

O [`discovery/`](./discovery/) é o motor de aquisição. Um **poller de CT logs**
(KL-15) lê os **Certificate Transparency logs públicos direto**, em tempo real
(descobre os logs "usable" da lista oficial do Google, amostra o topo via
`get-entries` e extrai os domínios do SAN com `cryptography`), acumula os
`.com.br` num buffer e, a cada 30 min, processa: detecta a plataforma (Duda,
WordPress, Wix…), extrai o **e-mail de contato**, classifica o setor/preço,
registra em `targets` e enfileira para scan. **Regra de negócio:** site sem
e-mail extraível é marcado `sem_contato` e **não** é escaneado. Gestão via API:
`GET /api/targets`, `/api/targets/stats`, `POST /api/targets/add`, `/api/scans`,
e **`GET /api/discovery/status`** (estado do poller em tempo real).

> **Por que não crt.sh nem Certstream?** O Postgres público do crt.sh rejeita
> conexões e a JSON API dá timeout em consultas amplas; o Certstream público
> (calidog) está morto (conecta e não envia nada). Ler os CT logs direto é
> confiável e sem dependência de agregador. O crt.sh fica só como **fallback**.

**Blindagem (KL-19):** cada domínio é processado sob timeout total de 30s (um site
travado é pulado, não congela o worker), e um watchdog reinicia o processo se o
event loop parar de progredir — resposta ao incidente de 08/07 em que um domínio
travado congelou os três workers por 7,5h. O `contact.py` também filtra "e-mails"
inválidos (nomes de arquivo, placeholders) para não desperdiçar cota nem gerar
bounces no Resend.

## Dashboard admin (`klarim.net/painel`)

Painel do operador (login único) para operar e monitorar tudo: KPIs em tempo real
(alvos, alertas, receita, score médio) com gráficos **Recharts**, gestão de alvos
(lista, filtros, scan/alerta/re-scan manual, detalhe com históricos), scans (com
detalhe dos checks e geração de PDF), alertas, pagamentos (receita + conversão) e
re-scans (evolução de score), além de uma tela de configurações (read-only).

Faz parte do **mesmo app React** — as rotas `/painel/*` são protegidas por **JWT**
(`POST /api/auth/login` com `ADMIN_USER`/`ADMIN_PASSWORD`; middleware trava
`/api/targets`, `/scans`, `/alerts`, `/rescans`, `/email`, `/payments`, `/config`).
As rotas públicas (scan, pagamento, relatório, webhooks, recuperação) seguem
livres. O bundle do painel é carregado sob demanda (code-split) para não pesar no
site público.

Acessível em **`https://painel.klarim.net`** (subdomínio dedicado que redireciona à
tela de login) ou em `https://klarim.net/painel`. O subdomínio usa o mesmo
certificado Let's Encrypt (SAN `painel.klarim.net`) e um server block Nginx próprio
com os mesmos security headers — sem novo container nem regra de firewall.

**Analytics da jornada (KL-21):** tracking 100% interno (sem GA4) do funil
pós-alerta. Os links dos e-mails levam UTM; o `tracker.js` dispara eventos
(`page_view`, `scan_started/completed`, `result_viewed`, `cta_clicked`,
`payment_created/completed`, `report_downloaded`) para `POST /api/events` (público,
rate-limited, gravação em background na tabela `site_events`). A tela **Analytics**
(`/painel/analytics`) mostra o funil de conversão, carrinho abandonado, atribuição
por campanha, páginas mais visitadas e a timeline de eventos, com período
selecionável.

**Dashboard operacional (KL-16):** a tela **Sistema** (`/painel/sistema`) mostra em
tempo real (auto-refresh 30s) o status 🟢/🔴 dos 4 workers (via heartbeat no Redis,
TTL 10min), o health das dependências (PostgreSQL, Redis, CT logs, Resend,
AbacatePay), as métricas de e-mail (hoje/semana + **cota mensal** e backlog de
alertas) e um log de atividade (scans, alertas, re-scans, pagamentos). Endpoints
`GET /api/system/status` e `/api/system/activity`.

**Integração completa (KL-17):** os scans feitos no site público passam a gravar em
`targets`/`scans` (em background, com `source='public'`), então aparecem no painel;
a tela **Escanear** deixa o operador rodar o ciclo inteiro (URL → scan → resultado
inline → enviar alerta/relatório por e-mail) num só lugar; cada scan carrega a
**origem** (público/discovery/admin/manual/rescan, com badge e filtro); e os
pagamentos ficam vinculados aos alvos (link nos dois sentidos + reenvio de
relatório). Endpoints: `POST /api/admin/scan-and-report`, `/resend-alert`,
`/send-report`, `/resend-payment`.

### Alert Worker (disparo automático — envio em lote, KL-23)

No mesmo container do Discovery Worker (via `asyncio.gather`), o **Alert Worker**
(`discovery/alert_worker.py`) dispara o alerta gratuito por e-mail para alvos
escaneados **com falhas**: filtra elegíveis (com e-mail, não alertados nos últimos
30 dias, não descadastrados). Com o **Resend Pro**, o envio é em **lote**
(`KlarimMailer.send_alert_batch` → Resend Batch API, até 100 e-mails por request,
com **idempotency key** para não duplicar em retry): cada ciclo manda
`ALERT_BATCH_SIZE`×`ALERT_BATCHES_PER_CYCLE` alertas (padrão 50×4 = 200/ciclo,
pausa `ALERT_BATCH_PAUSE` entre batches). O único teto é a **cota mensal**
(`ALERT_MONTHLY_LIMIT`, padrão 45k — reserva 5k dos 50k/mês do Pro para
transacionais), compartilhada com os e-mails de evolução. Tudo é registrado em
`alert_log`. Cada alerta traz um link de **descadastro** com token HMAC
(`GET /api/unsubscribe`). Gestão via API: `GET /api/alerts`, `/api/alerts/stats`,
`POST /api/targets/{id}/alert` (disparo manual, ignora a cota).

### Re-scan Worker (evolução de score — e-mail em lote, KL-23)

Terceiro loop no mesmo container (ciclo de 24h). O **Re-scan Worker**
(`discovery/rescan_worker.py`) reescaneia alvos já engajados a cada **30 dias**
(cada site é varrido individualmente), compara o score novo com o anterior e envia
um e-mail de **evolução**: 🎉 melhorou, ⚠️ piorou ou 📊 permaneceu igual. Isso
reativa a conversão sem descobrir alvos novos. Os e-mails de evolução saem em
**lote** (`send_evolution_batch`) ao fim do ciclo e dividem a **mesma cota mensal**
(`ALERT_MONTHLY_LIMIT`) dos alertas; no teto, o re-scan atualiza os dados e o
e-mail fica pendente (`rescan_log.email_id IS NULL`) para o próximo ciclo.
Histórico em `rescan_log`. Gestão via API: `GET /api/rescans`, `/api/rescans/stats`,
`POST /api/targets/{id}/rescan` (força re-scan + e-mail).

## Servidor MCP (operar via Claude — KL-18)

O módulo [`mcp_server/`](./mcp_server/) expõe um **servidor MCP** montado no mesmo
FastAPI (endpoint SSE em **`https://klarim.net/mcp/sse`**), permitindo operar o
Klarim por linguagem natural no Claude: **25 tools** (17 de leitura — sistema,
alvos, scans, alertas, pagamentos, analytics, saúde de e-mail; 8 de escrita —
scan, adicionar alvo, editar e-mail/status/setor, disparar alerta, enviar
relatório, classificar em lote). Cada tool é um wrapper fino sobre a API/`store`
existente. Transporte **SSE** em `/mcp/sse` (modelo Traka), com autenticação por
`MCPAuthMiddleware` (`MCP_API_KEY`, fail-closed, constant-time, `Authorization:
Bearer` ou `?token=`). O endpoint SSE **propaga o token** para os POSTs de mensagens,
o que faz a conexão funcionar no Claude.ai web.

**Conectar** (URL única com a chave no `?token=`):
- **Claude.ai web:** Configurações → Conectores → Add → `https://klarim.net/mcp/sse?token=<MCP_API_KEY>`
- **Claude Desktop:** `{"mcpServers":{"klarim":{"url":"https://klarim.net/mcp/sse","headers":{"Authorization":"Bearer <MCP_API_KEY>"}}}}`
- **Claude Code:** `claude mcp add klarim --transport sse https://klarim.net/mcp/sse --header "Authorization: Bearer <MCP_API_KEY>"`

---

## Framework legal

O Klarim se enquadra como serviço de *Security Rating* / *Monitoramento de
Superfície de Ataque* — **não** é pentest e não requer autorização do alvo para
varredura passiva. Ainda assim:

- **Faz:** requisições `GET`/`HEAD` a URLs públicas, leitura de headers, leitura
  de certificados SSL públicos, acesso a arquivos servidos sem autenticação.
- **Não faz:** injeção de payloads, brute-force, acesso autenticado, exploração
  de falhas, extração de dados.

Consulte um advogado de direito digital antes de qualquer uso comercial e inclua
disclaimer claro em todos os relatórios.

---

## Governança e documentação

- **[`claude.md`](./claude.md)** — guia do projeto e onboarding obrigatório para
  qualquer agente Claude (regras, stack, convenções, fluxo de trabalho). **Leia
  antes de tocar no código.**
- **[`claude/`](./claude/)** — rastro de trabalho gerado pelo Claude:
  - `claude/sessions/` — resumos das sessões de planejamento (Claude chat).
  - `claude/reports/` — um relatório por tarefa executada (card `KL-xxx`).
- **[`klarim_mvp_spec.md`](./klarim_mvp_spec.md)** — especificação de produto
  (fonte da verdade).

---

## Roadmap (MVP)

- [x] Scanner engine com checks passivos + score (conjunto em expansão)
- [x] CLI de scan manual
- [x] API com semáforo + relatório
- [x] Geração de PDF (executivo + técnico) — WeasyPrint
- [x] Discovery Worker (Certificate Transparency → alvos com e-mail)
- [x] Alert Worker (disparo automático do alerta + throttle + descadastro)
- [x] Re-scan Worker (re-scan de 30 dias + e-mail de evolução de score)
- [x] Dashboard admin (`/painel`) — auth JWT, KPIs, gestão e monitoramento
- [x] Dashboard operacional (`/painel/sistema`) — status dos workers, health, atividade
- [x] Interface web (React + Vite + Tailwind + Nginx) — scan self-service
- [x] Pagamento PIX (AbacatePay) para liberar o relatório completo
- [ ] Pagamento por cartão (Stripe)

Ver `klarim_mvp_spec.md` para a especificação completa do produto.
