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
| Deploy | Docker (`Dockerfile` compartilhado por API e Worker) |

**Links do projeto:**

- **Repositório:** https://github.com/joaquim-83/klarim.git
- **Jira (board KL):** https://igoove.atlassian.net/jira/software/c/projects/KL/boards/265/backlog

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
│       ├── base.py         # CheckResult, rate limit, helper HTTP
│       └── check_*.py      # os checks (hoje: 12)
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
- Registre o novo check em `scanner/checks/__init__.py` (`ALL_CHECKS`), em ordem.
- Um check que não conseguiu avaliar retorna **`INCONCLUSO`** — nunca finge um
  `PASS`. `INCONCLUSO` é neutro no score.

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
