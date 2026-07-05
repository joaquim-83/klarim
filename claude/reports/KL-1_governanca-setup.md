# KL-1 — Criar claude.md, pasta /claude e setup de governança

- **Card Jira:** KL-1
- **Data:** 2026-07-05
- **Executor:** Claude CLI (Opus 4.8)
- **Commit anterior:** `325e6d0` (scaffold inicial com 12 checks)
- **Commit desta tarefa:** `docs(KL-1): add claude.md, /claude dir, and governance setup`

---

## Objetivo

Estabelecer a **governança de documentação** do projeto: um guia de onboarding
para agentes Claude (`claude.md`) e uma pasta (`/claude/`) que registra o rastro
de trabalho — resumos de sessão de planejamento e relatórios por tarefa.

## O que foi criado

### 1. `claude.md` (raiz)

Guia obrigatório de onboarding para qualquer agente Claude. Seções:

- **Visão geral** do Klarim (scanner passivo, fingerprinting, semáforo, modelo
  bottom-up R$ 19–49).
- **Stack e infra** (Python 3.12 / FastAPI / httpx / Redis / PostgreSQL /
  React+Tailwind / WeasyPrint; GCP `e2-small`; Docker Compose) + links de repo e
  Jira.
- **Estrutura de diretórios** (documenta `scanner/`, `api/`, `tests/`, `claude/`).
- **Regras invioláveis:** só varredura passiva; interface
  `async def check(url) -> CheckResult`; timeout 10s + rate limit 1 req/s;
  User-Agent honesto; commits/código em inglês, comentários PT-BR; todo prompt
  precisa de card `KL-xxx`; cada tarefa gera relatório em `/claude/` e atualiza a
  documentação.
- **Convenções de código** (async/await, type hints, docstrings, pytest).
- **Como rodar** e **checklist de fluxo de trabalho**.

### 2. Pasta `/claude/`

```
claude/
├── README.md                              # explica a finalidade da pasta
├── sessions/
│   └── 2026-07-05_scaffold-inicial.md     # resumo da sessão de scaffold
└── reports/
    └── KL-1_governanca-setup.md           # este relatório
```

- **`README.md`** — descreve `sessions/` (resumos do chat planejador) e
  `reports/` (relatórios por tarefa do CLI) e a convenção de nomes.
- **`sessions/2026-07-05_scaffold-inicial.md`** — resumo da sessão de scaffold:
  12 checks, scan real 100/100 no Verdegreen, gap dos checks 13–15 (SRI, supply
  chain, domínios externos), modelo de negócio e casos validados.

### 3. `README.md` (atualizado)

Adicionada a seção **"Governança e documentação"** apontando para `claude.md`
(guia do projeto) e `/claude/` (sessões e relatórios).

## Arquivos afetados

| Arquivo | Ação |
|---------|------|
| `claude.md` | criado |
| `claude/README.md` | criado |
| `claude/sessions/2026-07-05_scaffold-inicial.md` | criado |
| `claude/reports/KL-1_governanca-setup.md` | criado |
| `README.md` | atualizado (nova seção de governança) |

## Decisões tomadas

- **Nome `claude.md` em minúsculas**, conforme o card. Como o repositório ainda
  não tinha um `CLAUDE.md`, não há colisão; em filesystem case-insensitive
  (macOS) manter um único arquivo evita ambiguidade.
- **Separação `sessions/` vs `reports/`:** sessões = planejamento (Claude chat);
  relatórios = execução (Claude CLI, 1:1 com cards `KL-xxx`). Isso mantém o
  rastro de *decisão* separado do rastro de *implementação*.
- **`claude.md` referencia `klarim_mvp_spec.md`** como fonte da verdade de
  produto, evitando duplicar a spec dentro do guia.
- **Governança documentada como regra em `claude.md` §4.4**, para que futuras
  tarefas produzam relatório e atualizem docs automaticamente.

## Critérios de aceite

- [x] `claude.md` existe na raiz e serve como onboarding para um novo agente.
- [x] `/claude/` existe com `README.md`, `sessions/` e `reports/`.
- [x] Session summary e task report criados.
- [x] `README.md` atualizado.
- [x] Commit e push com a mensagem `docs(KL-1): add claude.md, /claude dir, and governance setup`.

## Próximos passos sugeridos

- Abrir cards para os **checks 13–15** (SRI, supply chain, domínios externos)
  identificados como gap na sessão de scaffold.
