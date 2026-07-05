# `/claude/` — Documentação de governança gerada pelo Claude

Esta pasta guarda o **rastro de trabalho** dos agentes Claude no Klarim. Ela
existe para que qualquer pessoa (ou agente) consiga reconstruir *o que foi feito,
por quê e quando* sem depender do histórico de chat.

Para as **regras** do projeto e o onboarding de agentes, veja
[`../claude.md`](../claude.md) na raiz.

## Estrutura

```
claude/
├── README.md     # este arquivo
├── sessions/     # resumos de sessão do chat planejador (Claude chat)
└── reports/      # relatórios de cada tarefa executada pelo Claude CLI
```

### `sessions/`

Resumos das sessões de **planejamento/estratégia** conduzidas no Claude chat
(não no CLI). Capturam decisões de produto, insights de validação de mercado e
o racional por trás das tarefas que viram cards no Jira.

- **Nome do arquivo:** `AAAA-MM-DD_<slug-curto>.md`
- **Exemplo:** `2026-07-05_scaffold-inicial.md`

### `reports/`

Um relatório por **tarefa executada pelo Claude CLI**. Cada tarefa corresponde a
um card `KL-xxx` no Jira (ver regra em `claude.md` §4.4). Documenta o que foi
criado/alterado, arquivos afetados e decisões tomadas.

- **Nome do arquivo:** `KL-xxx_<slug-curto>.md`
- **Exemplo:** `KL-1_governanca-setup.md`

## Convenção

Datas no formato **ISO `AAAA-MM-DD`**. Um arquivo por sessão/tarefa — não
edite relatórios antigos para refletir trabalho novo; crie um novo.
