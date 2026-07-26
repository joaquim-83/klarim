# KL-108 — Circuit breaker: separar hard bounce de soft bounce no cálculo de pausa de sender

**Data:** 2026-07-26 · **Prioridade:** Highest · **Status:** ✅ código pronto + testado (deploy pendente)

## Problema

O circuit breaker por remetente (`notifier/cold_alert.flag_high_bounce`, KL-91) contava
`bounced` + `soft_bounced` juntos no bounce rate. Soft bounces são **transitórios** (caixa
cheia, servidor temporário fora, `delivery_delayed`) e não deveriam pausar um remetente.

**Impacto real (26/07/2026):** os 3 senders cold passaram do threshold de 5% pelo bounce rate
**combinado** e foram pausados simultaneamente → **zero cold alerts no dia**, backlog acumulou
para **2.683**. Fix emergencial aplicado na VM: `ALERT_SENDER_MAX_BOUNCE_RATE=12` no `.env`.

Distorção comprovada (janela 7 dias):

| Sender | Hard % | Soft % | Combinado % |
|---|---|---|---|
| alertas.klarim.net | 6,21% | 0,85% | 7,06% |
| aviso.klarim.net | 8,66% | 3,15% | 11,81% |
| perfil.klarim.net | **1,33%** | 5,61% | **6,94%** |

O `perfil.klarim.net` tem 1,33% de hard bounce (saudável) mas 38 soft bounces o inflam para
6,94% — quase pausado injustamente.

## Solução

Separar hard de soft em toda a cadeia e pausar **só por hard**.

### 1. `discovery/store.py::email_health_by_domain()`

A query somava hard+soft num único `FILTER (WHERE status IN ('bounced','soft_bounced'))`.
Agora conta em **FILTERs separados**:

```sql
COUNT(*) FILTER (WHERE status = 'bounced')       AS hard_bounced
COUNT(*) FILTER (WHERE status = 'soft_bounced')  AS soft_bounced
```

Cada entrada de `by_domain` passou a devolver:

```python
{
    "total": 353,
    "hard_bounced": 22,
    "soft_bounced": 3,
    "bounced": 25,              # compat (hard+soft)
    "bounce_rate": 6.23,        # HARD only — é isso que pausa
    "soft_bounce_rate": 0.85,   # informativo, não pausa
    "sent": 353, "delivered": 328, "complained": 0,
}
```

### 2. `notifier/cold_alert.py::flag_high_bounce()`

- Lê `hard_bounced`/`soft_bounced` (antes lia o campo combinado `bounced`).
- Pausa `if total >= min_sample and hard_rate > max_rate` — **soft nunca pausa**.
- Devolve `[(from_domain, hard_rate)]`.
- **Log por remetente** para diagnóstico (hard e soft separados):

```
[alert] sender perfil.klarim.net: hard=1.3% soft=5.6% (threshold=5.0%, sample=677/100) → ok
[alert] sender aviso.klarim.net:  hard=8.7% soft=3.2% (threshold=5.0%, sample=... ) → PAUSED
```

### 3. `api/main.py::api_system_email_health()` (endpoint MCP `get_email_health`)

Nenhuma mudança de lógica: o endpoint já propaga `by_domain` inteiro (a tool MCP
`get_email_health` só delega para ele), então `hard_bounced`/`soft_bounced`/`soft_bounce_rate`
fluem automaticamente. O `bounce_status` (ok<2% · warning 2–4% · critical>4%) agora deriva do
`bounce_rate` **hard-only**. Docstring atualizado.

## O que NÃO mudou (rule #4 — safety net global do KL-24)

O safety net GLOBAL (`discovery/alert_worker._check_bounce_health`, all-time, 8%) usa uma query
**própria e separada** — `store.email_health()` — que já contava só `status = 'bounced'` (hard) e
já excluía `soft_bounced` do total. Portanto está correto e ficou **intacto**.

## Resultado esperado com o default 5% (hard-only)

- `alertas.klarim.net`: 6,21% hard → **pausa** (correto — a lista precisa de limpeza)
- `aviso.klarim.net`: 8,66% hard → **pausa** (correto)
- `perfil.klarim.net`: 1,33% hard → **ATIVO** (correto — antes pausava injustamente com 6,94%)

## Testes

+10 testes offline, todos passando (suíte total: **1707 passed, 1 skipped**):

- `tests/test_kl108_hard_soft_bounce.py` (+6): mapeamento SQL→dict de `email_health_by_domain`
  (hard/soft separados, `bounce_rate` hard-only, `soft_bounce_rate`, `total=0` seguro, FILTERs
  separados no SQL) + circuit breaker soft-only ativo / hard pausa.
- `tests/test_kl91_cold_alert.py` (+4): `flag_high_bounce` com 4%hard+10%soft (não pausa),
  6%hard+0%soft (pausa), 6%hard+5%soft (pausa por hard), e o caso real perfil.klarim.net
  (1,33% hard + 5,61% soft → ativo). Fakes existentes migrados para `hard_bounced`/`soft_bounced`.
- Ajustados: `tests/test_alert_worker.py::test_run_cycle_pauses_high_bounce_sender` e o fake do
  endpoint em `test_kl91_cold_alert.py` para a shape KL-108.

**Nota sobre validação em Postgres:** o Docker Desktop não estava rodando nesta máquina, então a
stack `docker-compose.dev.yml` não pôde ser subida. Mitigação: os dois novos `FILTER (WHERE
status = '...')` usam **exatamente** o padrão por igualdade já em produção no método irmão
`email_health()` (mesma tabela `email_log`, mesma coluna `status`), e um teste offline
(`test_email_health_by_domain_sql_uses_separate_filters`) valida o shape do SQL gerado.

## Validação pós-deploy (manual)

1. MCP `get_email_health` → `hard_bounced`/`soft_bounced` separados, `bounce_rate` = hard only.
2. MCP `get_system_status` → `perfil.klarim.net` sai da pausa (1,33% hard < 5%).
3. Próximo ciclo do alert worker → cold alerts voltam a sair; backlog começa a drenar.
4. **Na VM:** remover `ALERT_SENDER_MAX_BOUNCE_RATE=12` do `/opt/klarim/.env` e recriar containers
   (`docker compose up -d`). O default 5% (hard-only) mantém perfil ativo e pausa alertas/aviso.

## Arquivos alterados

- `discovery/store.py` — `email_health_by_domain` (hard/soft separados).
- `notifier/cold_alert.py` — `flag_high_bounce` (hard-only + log diagnóstico).
- `api/main.py` — docstring do `/system/email-health` (KL-108).
- `tests/test_kl108_hard_soft_bounce.py` (novo), `tests/test_kl91_cold_alert.py`,
  `tests/test_alert_worker.py`.
- Docs: `CLAUDE.md` (§ e-mail + §9 card + estado), `docs/DEPLOY.md` (env var).
