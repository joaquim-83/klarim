# Reestruturação do `claude.md` + documentação oficial (`docs/`)

**Data:** 2026-07-16
**Tipo:** 100% documentação (nenhum código Python, frontend ou config alterado)
**Motivo:** o `claude.md` chegou a **164.437 caracteres** (50 seções, 2.425 linhas),
acima do limite de 150k do Claude Code — carregar o arquivo passou a ser **bloqueante**.

---

## Diagnóstico do `claude.md` original

| Métrica | Valor |
|---|---|
| Tamanho | 164.437 chars (2.425 linhas) |
| Seções `##` | 50 |
| Natureza | ~7 seções de **regras/instruções** + ~43 seções de **histórico de entregas** (uma por card KL-xxx), com notas técnicas e "Regra inviolável" repetidas ao fim de quase toda seção |

O arquivo virou um **changelog** disfarçado de guia: cada tarefa (KL-1 … KL-65)
acrescentou a documentação detalhada da entrega, duplicando decisões e diluindo as
instruções realmente acionáveis para o agente.

**Classificação das seções:**
- **Regras/instruções (ficam no guia):** §1 visão geral, §4 regras invioláveis, §5
  convenções, §6 como rodar, §7 fluxo de tarefa + regras espalhadas de segurança,
  dados, e-mail e frontend.
- **Histórico de entregas (→ `docs/HISTORY.md`):** §8–§50 (uma por card).
- **Documentação técnica (→ `docs/`):** arquitetura, endpoints, deploy/env, segurança
  — extraídas e reorganizadas a partir do texto + do código.

---

## O que foi feito

### 1. Novo `claude.md` enxuto — **15.473 chars** (limite 30k; meta 20–25k)

Guia puramente instrutivo, com 9 seções: identidade + ponteiros para `docs/`; links e
acesso; stack; **regras invioláveis** (processo, scanner passivo, segurança 15/07,
dados, frontend Astro, e-mail); arquitetura em resumo; estrutura de diretórios;
convenções + **como adicionar um check** + como rodar; **estado atual** (bloco a
atualizar a cada tarefa); **gotchas**; referência rápida de cards.

> Ficou **abaixo** da meta de 20–25k (15,5k) porque todo o detalhe migrou limpo para
> `docs/`. Nenhuma instrução acionável foi perdida — o que saiu foi histórico e
> aprofundamento técnico, agora endereçável por ponteiro. Consolidei num único bloco as
> regras invioláveis que estavam pulverizadas em ~43 rodapés de seção.

### 2. Documentação oficial em `docs/` (novo diretório)

| Arquivo | Chars | Conteúdo |
|---|---|---|
| `docs/HISTORY.md` | 167.943 | **Cópia byte-a-byte** do `claude.md` original (as 50 seções, intactas) + **índice** de seções no topo + ponteiros para os novos docs. Zero perda. |
| `docs/ARCHITECTURE.md` | 9.117 | Containers, Nginx, Astro+Vite, scanner, workers, dados, integrações, MCP, fluxo end-to-end. |
| `docs/API.md` | 11.186 | **~140 endpoints reais** (extraídos dos decorators de `api/main.py`) + **49 tools MCP** (de `mcp_server/tools/`), agrupados, com auth/rate-limit. |
| `docs/DEPLOY.md` | 8.030 | Infra GCP, deploy manual, CI/CD (WIF keyless), HTTPS, **tabela completa de env vars** (cruzada com `.env.example` + uso no código), comandos pós-deploy. |
| `docs/SECURITY.md` | 8.133 | Postura passiva, regra 15/07, hardening, auth (admin×usuário×MCP), privacidade, anti-bounce, segredos, webhooks + **checklist de revisão de segurança**. |

### 3. Fidelidade e método

- `HISTORY.md` foi gerado por `cp` do arquivo original **antes** de sobrescrever o
  `claude.md` → preservação garantida (167.943 = 164.437 originais + ~3,5k do índice).
- `API.md` e `DEPLOY.md` foram **ancorados no código** (grep dos decorators de rota, das
  tools MCP, dos `_PROTECTED_PREFIXES` e das `os.environ.get(...)`), não só no texto —
  então refletem o estado real, não a memória do `claude.md`.

---

## Validação

```
wc -c claude.md            → 15473   (< 30000 ✅)
wc -c docs/*.md            → API 11186 · ARCH 9117 · DEPLOY 8030 · SEC 8133 · HISTORY 167943
grep -c "inviolá|NUNCA|regra" claude.md → 6  (regras presentes ✅)
grep -c "^## " docs/HISTORY.md          → 52 (50 seções originais + 2 do índice ✅)
wc -l docs/HISTORY.md                   → 2500 linhas (histórico preservado ✅)
```

Nenhuma informação essencial foi perdida — apenas reorganizada: instruções acionáveis
no `claude.md`, aprofundamento em `docs/`, histórico íntegro em `docs/HISTORY.md`.

---

## Observação sobre o commit

O comando de deploy sugerido na tarefa (`git add … claude/reports/`) varreria **todos**
os arquivos não rastreados de `claude/reports/`, incluindo binários e um CSV com
**endereços de e-mail** (`emails-sent-*.csv`) e PDFs de marketing que **não** foram
produzidos por esta tarefa. Para não commitar dado pessoal/binário por engano, **staguei
apenas o que esta tarefa produziu**: `claude.md`, `docs/` e este relatório. Os demais
arquivos não rastreados de `claude/reports/` foram deixados como estavam (o dono decide
se/como versioná-los).

## Arquivos alterados

- `claude.md` (reescrito: 164k → 15k)
- `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/DEPLOY.md`, `docs/SECURITY.md`,
  `docs/HISTORY.md` (novos)
- `claude/reports/reestruturacao_docs.md` (este relatório)
