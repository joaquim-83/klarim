# Fix — Alertas em lotes menores e mais frequentes

**Tipo:** Ajuste operacional (sem card Jira)
**Data:** 2026-07-08

## Objetivo

Esvaziar o backlog de alvos escaneados aguardando alerta sem estourar o limite do
Resend (100 e-mails/dia). Trocar o padrão "5/hora em ciclo de 1h" por **lotes
pequenos e frequentes que parecem tráfego orgânico**: 4 e-mails a cada 30min,
espaçados de 5s → ~90 alertas/dia (10 reservados para transacionais).

| Parâmetro | Antes (VM) | Depois |
|---|---|---|
| Ciclo do Alert Worker | 1h | **30min** |
| Alertas por ciclo | 5 (implícito) | **4** |
| Máximo por hora | 5 | **8** |
| Máximo por dia | 9999 | **90** |
| Pausa entre e-mails | 5s | 5s (mantido) |

## Alterações de código

### `discovery/alert_worker.py`
- **Intervalo em minutos:** novo `ALERT_INTERVAL_MINUTES` com **precedência** sobre
  `ALERT_INTERVAL_HOURS` (fallback). O loop de `start()` agora dorme
  `interval_minutes * 60`.
- **Cap por ciclo:** novo `MAX_ALERTS_PER_CYCLE` (padrão 4). O loop de envio para
  quando atinge o cap do ciclo **ou** o throttle global (hora/dia), o que vier
  primeiro. A busca de elegíveis usa `limit = min(max_cycle, cota_diária_restante)`
  — nunca busca mais do que vai enviar.
- **Throttle:** a checagem no topo do ciclo separa "limite diário" de "limite
  horário" com logs distintos; ao bater o teto, faz `break` (em vez de marcar cada
  restante como throttled).
- **Logs:**
  - startup: `[alert] iniciado (ciclo 30min, 4/ciclo, teto 8/h 90/dia, pausa 5s)`
  - fim de ciclo: `[alert] ciclo: 4 enviados, 273 restantes, cota dia 12/90`
  - teto diário: `[alert] limite diário atingido (90/90)`

### `discovery/store.py`
- Novo `count_eligible_targets_for_alert()` — backlog total elegível (mesma regra do
  `get_eligible_targets_for_alert`, sem `limit`), usado no log "restantes".

### `api/main.py`
- `GET /config` expõe `alert_interval_minutes`, `max_alerts_per_cycle` e atualiza os
  defaults (8/h, 90/dia); `GET /system/status` usa default 90 no `throttle_used`.

### `.env.example`
- Documenta `ALERT_INTERVAL_MINUTES=30`, `MAX_ALERTS_PER_CYCLE=4`,
  `MAX_ALERTS_PER_HOUR=8`, `MAX_ALERTS_PER_DAY=90` com a estratégia de lotes.

## Ponto crítico — transacionais fora do throttle (item 5)

Confirmado por inspeção de código: `log_alert` só é chamado no `alert_worker.py` e
`log_rescan` só no `rescan_worker.py`. O throttle
(`count_proactive_emails_last_hours`) soma **apenas** `alert_log` + `rescan_log`
(proativos). Os transacionais — `send_report` (relatório pós-pagamento),
`send_recovery_link`, `send_test` — passam direto por `_mailer()` em `api/main.py`
e **não** gravam nesses logs. Logo, **sempre entregam** mesmo com a cota de alerta
cheia. O e-mail de evolução (`send_evolution`/rescan) é proativo por design (KL-13)
e **continua** somando no teto compartilhado.

## Testes

- `tests/test_alert_worker.py`: `FakeStore` ganhou `count_eligible_targets_for_alert`
  e passou a respeitar o `limit`; novo `test_run_cycle_caps_per_cycle` (6 elegíveis,
  cap 4 → envia exatamente 4); `test_run_cycle_stops_at_hourly_throttle` ajustado ao
  novo `break`.
- Suíte completa: **115 passed, 1 skipped**.

## Deploy e validação em produção

1. `.env` da VM atualizado (backup em `/opt/klarim/.env.bak-alerttune`): removidas
   as 3 linhas antigas (`MAX_ALERTS_PER_HOUR=5`, `MAX_ALERTS_PER_DAY=9999`,
   `ALERT_INTERVAL_HOURS=1`) e adicionadas as 4 novas. `grep` confirmou sem
   duplicatas.
2. Push para `main` → CI/CD (test 24s + deploy 1m) recriou o container `discovery`
   lendo o `.env` novo + código novo.
3. **Log de startup ao vivo:** `[alert] iniciado (ciclo 30min, 4/ciclo, teto 8/h
   90/dia, pausa 5s)` ✅
4. **Teto diário ao vivo:** `[alert] limite diário atingido (90/90)` — a janela
   móvel de 24h já tinha exatamente **90** alertas (herança do worker antigo a 5/h);
   o worker corretamente aguarda. Conforme os envios antigos saem da janela de 24h,
   ele retoma 4/ciclo. Confirmado no banco: 90 alertas/24h, 0 evolução, 0 na última
   hora.
5. Backlog elegível atual: **349** alvos. Container `discovery`: **Up (healthy)**.

## Observação operacional

Como já havia 90 proativos nas últimas 24h, o worker só volta a enviar quando a
contagem móvel cair abaixo de 90 (à medida que os envios antigos completam 24h) —
então estabiliza em ~90/dia. O backlog de 349 deve drenar em ~4 dias nesse ritmo.
