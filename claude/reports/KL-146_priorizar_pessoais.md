# KL-146 — Priorizar e-mails pessoais sobre genéricos no lead scoring

## Contexto e problema

Dados de produção: `contato@` gera **66% dos bounces** (taxa de **8,7%**) contra e-mails pessoais
(`joao@`, `maria.silva@`) com **3,6%**. A solução **NÃO é filtrar** — a regra de envio do KL-145
(sintaxe + MX + blocklist) permanece **inalterada**. A solução é **REORDENAR** a fila de alerta:
**pessoais primeiro, genéricos depois**. Assim a blocklist aprendente (alimentada pelos bounces via
webhook) aprende com os bounces dos genéricos ANTES de enviar muitos deles.

## A mudança — 1 arquivo (`discovery/alert_scoring.py`)

### Novo `_email_type_factor(email)`

Fator de priorização por tipo de e-mail (só ORDENA a fila, nunca bloqueia envio):

| Tipo | Prefixos | Fator |
|---|---|---|
| Genérico high-bounce | `contato` | **-10** |
| Genérico medium-bounce | `atendimento`, `sac` | **-5** |
| Genérico neutro | `comercial`, `vendas`, `suporte`, `info`, `faleconosco`, `falecom`, `email`, `adm`, `admin`, `cadastro`, `contact`, `hello`, `oi` **+ união com `ROLE_BASED_PREFIXES`** | **0** |
| Pessoal | tudo que não é genérico conhecido (`joao@`, `maria.silva@`…) | **+15** |

**Decisão de engenharia (união com `ROLE_BASED_PREFIXES`):** o card lista um `GENERIC_NEUTRAL`
curto. Se o "pessoal = qualquer prefixo fora das 3 listas" fosse literal, `noreply@`, `naoresponda@`,
`financeiro@`, `rh@` (que estão em `ROLE_BASED_PREFIXES`, não nas 3 listas) receberiam **+15** e
seriam priorizados como se fossem pessoas — uma regressão. A união trata esses como **genérico
neutro (0)**. O resultado passa **todos** os testes explícitos do card e evita a regressão.

### Integração em `calculate_alert_score`

Substitui a penalidade role-based do KL-136 (`_role_penalty`/`ALERT_ROLE_PENALTY`, ambos
**removidos** — não acumula). Sinais no breakdown admin: `email_type_personal` (+15),
`email_type_generic` (0), `email_type_generic_medium_bounce` (-5), `email_type_generic_high_bounce`
(-10). Um prefixo que **parece pessoal** mas a Reoon confirmou `role` é rebaixado a **0**
(`email_type_role_verified`) — não premia uma caixa de função como pessoa.

### Efeito na fila (mesmo domínio + score na action_zone)

| E-mail | Fatores | Score | Posição |
|---|---|---|---|
| `joao@empresa.com.br` | +10 corp +20 action **+15 pessoal** | **45** | Primeiro |
| `comercial@empresa.com.br` | +10 corp +20 action **0 neutro** | **30** | Meio |
| `contato@empresa.com.br` | +10 corp +20 action **-10 high-bounce** | **20** | Fim |

O `_apply_alert_scoring` grava o `alert_quality_score`; o `run_cycle` ordena por score DESC
(`targets.sort(..., reverse=True)`). **Volume total inalterado** — só a ORDEM muda.

## O que NÃO mudou

- Regra de envio (KL-145): sintaxe + MX + blocklist — **inalterada**.
- `is_safe_to_send` — inalterada. **Nenhum e-mail é bloqueado por tipo de prefixo** (`contato@`
  continua sendo enviado).
- Circuit breaker (KL-108), blocklist + webhook de bounce — inalterados.

## Testes

- **Novo `test_email_type_factor`** (parametrizado, `tests/test_kl85_scoring.py`) — cobre a lista
  do card: pessoal/high/medium/neutral, `noreply`/`financeiro` → 0 (não +15), case-insensitive,
  vazio/None/sem-@/sem-prefixo → 0.
- **`test_generic_high_bounce_*` / `test_generic_neutral_zero` / `test_generic_medium_bounce_minus_5`
  / `test_personal_ranks_above_generic_action_zone`** — score final por tipo + ordenação pessoal >
  neutro > contato.
- **`test_run_cycle_personal_before_generic_real_scoring`** (`tests/test_alert_worker.py`) — com
  scoring REAL, a fila envia pessoal → neutro → contato e envia **todos** (volume inalterado).
- **`test_email_type_no_double_with_role_status` / `test_role_verified_downgrades_personal_looking`**
  (`tests/test_kl136_…` + `tests/test_kl110_…`) — sem duplicação com o status `role` da Reoon.
- Atualizados os testes de KL-85/KL-110/KL-136 (todo e-mail pessoal ganha +15; removidos os testes
  de `_role_penalty`/`ALERT_ROLE_PENALTY`).

**Resultado:** `2043 passed, 1 skipped` (backend pytest).

> ⚠️ Docker não estava disponível no ambiente local para subir o `docker-compose.dev.yml`; como o
> `alert_scoring.py` é uma função **pura** (sem DB/rede/Redis), a suíte offline a cobre integralmente.
> A CI roda pytest no push (gate verde antes do deploy).

## Documentação atualizada

- `CLAUDE.md` — fator de tipo de e-mail na descrição do lead scoring (KL-85) + entrada do card em §9.
- `docs/DEPLOY.md` — `ALERT_ROLE_PENALTY` marcada como superada pelo fator de tipo (KL-146).

## Pós-deploy

Fechar o **KL-146 no Jira** após validação: confirmar no detalhe de alvos (painel) que e-mails
pessoais aparecem no topo da fila (badge de score maior) e os `contato@` no fim, e que o volume
enviado por ciclo não caiu (o tipo só reordena, não filtra).
