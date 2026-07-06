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

Semáforo: **🟢 verde** ≥ 80 · **🟡 amarelo** 50–79 · **🔴 vermelho** < 50.

---

## Relatórios PDF

O módulo [`reporter/`](./reporter/) transforma um `ScanReport` em dois PDFs
(**Jinja2 → WeasyPrint**), na identidade visual do Klarim (dark + laranja/verde):

- **Executivo** (1-2 páginas) — para o dono do negócio: semáforo, linguagem
  acessível, bloco de risco **LGPD**, lista de problemas em linguagem humana.
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

- **Landing** (`/`) — input de scan self-service + seções informativas.
- **Scan** (`/scan?url=`) — loading com feedback enquanto a varredura roda (~30s).
- **Result** (`/result?url=`) — semáforo, contagem por severidade, LGPD e CTA.
- **Report** (`/report?url=`) — download dos relatórios executivo e técnico (PDF).

```bash
cd frontend
npm install
npm run dev      # dev (proxy /api → localhost:8000)
npm run build    # build de produção → dist/
```

Em produção, o serviço **`web`** do `docker-compose.yml` (porta **80**) constrói o
frontend e serve tudo via Nginx; a API fica em `127.0.0.1:8000` (só o Nginx é
público). Suba a stack completa com `docker compose up --build` e acesse
`http://localhost`.

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
- [ ] Discovery Worker (Google Dorks por plataforma)
- [x] Interface web (React + Vite + Tailwind + Nginx) — scan self-service
- [ ] Pagamento (Pix + Stripe) para liberar o relatório completo

Ver `klarim_mvp_spec.md` para a especificação completa do produto.
