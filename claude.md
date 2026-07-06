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
├── api/                    # API HTTP (FastAPI)
│   └── main.py             # semáforo grátis + relatório técnico
└── tests/                  # pytest
    └── test_checks.py      # unit tests + teste online opt-in
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
# na VM:
bash /opt/klarim/deploy/deploy.sh
```

`deploy/deploy.sh` faz `git pull` → `docker compose down` → `up -d --build` →
`docker compose ps` → health check em `http://localhost:8000/health`.

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
  (papel `roles/compute.instanceAdmin.v1`).
- Pool `github-pool` + provider `github-provider` (issuer GitHub), com condição
  travada no repo `joaquim-83/klarim`.
- Binding `roles/iam.workloadIdentityUser` da SA só para esse repo.

**Secrets no GitHub** (configurar manualmente — o repo nunca guarda credenciais):
`GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`, `GCP_PROJECT_ID`, `GCP_INSTANCE`, `GCP_ZONE`.

**Regra de segurança:** nunca commitar chaves SSH, service account keys ou o
`.env` de produção. Tudo sensível vive em GitHub Secrets ou na VM.
