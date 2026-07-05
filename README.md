# Klarim

**"O alarme que toca antes do ataque."**

Scanner passivo de superfície de ataque para PMEs e desenvolvedores. O Klarim
executa **12 verificações de segurança** comprováveis — sem invasão — contra um
site público, calcula um **score de 0 a 100** e gera um relatório acionável.

> ⚖️ **Passivo e legal por design.** O Klarim faz apenas requisições HTTP
> `GET`/`HEAD` a URLs públicas e lê certificados TLS públicos. Ele **nunca**
> envia payloads de ataque (SQLi/XSS), não faz brute-force, não acessa áreas
> autenticadas e não explora vulnerabilidades. Ver [Framework legal](#framework-legal).

---

## As 12 verificações

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

Cada check implementa a mesma interface:

```python
async def check(url: str) -> CheckResult
```

onde `CheckResult` carrega `name`, `status` (`PASS`/`FAIL`/`INCONCLUSO`),
`severity` (`CRITICA`/`ALTA`/`MEDIA`/`BAIXA`) e `evidence` (string com o detalhe
concreto observado). Timeout de **10s por request** e **rate limit de 1 req/s por
domínio** são aplicados de forma centralizada em `checks/base.py`.

---

## Estrutura

```
klarim/
├── claude.md               # guia de onboarding para agentes Claude
├── claude/                 # governança: session summaries + task reports
│   ├── README.md
│   ├── sessions/           # resumos de sessão do chat planejador
│   └── reports/            # relatórios por tarefa (KL-xxx)
├── docker-compose.yml      # PostgreSQL + Redis + API + Worker
├── Dockerfile              # imagem compartilhada (API/Worker)
├── .env.example            # variáveis de ambiente (sem segredos)
├── requirements.txt
├── scanner/
│   ├── main.py             # entry point do worker + CLI
│   ├── runner.py           # orquestra os 12 checks + score
│   ├── scoring.py          # cálculo do score 0-100 + semáforo
│   └── checks/
│       ├── base.py         # CheckResult, rate limit, HTTP helper
│       └── check_*.py      # os 12 checks
├── api/
│   └── main.py             # FastAPI (semáforo grátis + relatório)
└── tests/
    └── test_checks.py      # unit tests + teste online opt-in
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

## Score

O score é uma proporção ponderada dos checks que passaram. Cada check tem peso
por severidade (Crítica 5, Alta 3, Média 2, Baixa 1). `PASS` soma o peso, `FAIL`
soma zero e `INCONCLUSO` é **excluído do denominador** (neutro):

```
score = round(100 * Σ peso(PASS) / Σ peso(PASS + FAIL))
```

Semáforo: **🟢 verde** ≥ 80 · **🟡 amarelo** 50–79 · **🔴 vermelho** < 50.

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

- [x] Scanner engine com os 12 checks + score
- [x] CLI de scan manual
- [x] API com semáforo + relatório
- [ ] Geração de PDF (executivo + técnico) — WeasyPrint
- [ ] Discovery Worker (Google Dorks por plataforma)
- [ ] Dashboard React + pagamento (Pix + Stripe)

Ver `klarim_mvp_spec.md` para a especificação completa do produto.
