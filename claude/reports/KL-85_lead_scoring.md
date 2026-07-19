# KL-85 — Lead Scoring para Qualidade de Alertas (Parte 1)

**Card:** KL-85 (Parte 1) · **Prioridade:** High · **Data:** 2026-07-19
**Partes 2 (rate limit signup 3/h+5/d) e 3 (blocklist de descartáveis) já entregues no KL-82
Slice 2.** Esta é a **Parte 1** — lead scoring para filtrar alertas de baixa qualidade antes do
envio (economiza cota Resend, protege reputação, sobe a taxa de clique).

---

## Função `calculate_alert_score` (pura)

`discovery/alert_scoring.py::calculate_alert_score(target, contact_email, domain_bounced=False)`
→ `{"score": int, "signals": [...]}`. **Pura** (sem SQL) — o worker faz o lookup de bounce (com
cache Redis) e passa o booleano. Sinais/pesos exatamente como o card:

| Sinal | Pts |
|---|---|
| e-mail no domínio do site (`email_matches_domain`) | +30 |
| e-mail corporativo (não free) | +10 |
| score 50–85 (zona de ação) / 40–49 / >85 | +20 / +10 / +5 |
| setor de alto clique (`HIGH_CLICK_SECTORS`, **vazio** por ora) | +15 |
| e-mail free de terceiro (`email_mismatch_free`) | −20 |
| prefixo role-based (sac@, contato@…) | −15 |
| descartado ou score < 40 | −10 |
| domínio com bounce anterior | −40 |

Exemplo do card confirmado: corporativo no domínio com score 70 = **60** (30+10+20). Edge cases
tratados: e-mail sem `@` / domínio vazio / score None → sem sinais espúrios.

## Banco

`ALTER TABLE targets ADD COLUMN alert_quality_score INTEGER` + índice parcial, na
`ensure_schema`. Gravado para **TODOS** os alvos avaliados (mesmo os filtrados) — permite
analytics/calibração. **Lead scoring NUNCA impede scan** — só a decisão de alertar.

## Integração no alert worker (fail-safe)

`_apply_alert_scoring(targets)` roda logo após `_validate_batch`: para cada alvo, calcula o
bounce por domínio (`_domain_bounced` com cache em-memória por-ciclo + **Redis 24h**
`bounce_domain:{domain}`, fail-open), calcula o score, **grava** (`update_target_alert_score`) e
**filtra** os abaixo de `ALERT_SCORE_THRESHOLD` (default **20**, editável no painel via
`admin_settings`). **Fail-safe:** um bug de scoring num alvo → o alvo é **mantido** (nunca perde um
lead bom por erro de scoring); falha ao gravar → não bloqueia o envio. Stats do ciclo ganham
`skipped_low_quality` e `avg_alert_score` (aparecem no MCP `get_system_status`).

## Backfill

`scripts/backfill_alert_scores.py` — calcula o score de todos os alvos com `contact_email` (batch
500, sem re-scan), imprime o histograma por faixas para **calibrar o threshold antes** do worker
usar. (Distribuição real: preenchida na execução pós-deploy — ver seção "Execução".)

## Visibilidade admin

- **Lista de alvos** (`/painel/alvos`): coluna "Alert" com badge colorido (`AlertScoreBadge`:
  ≥40 verde · 20-39 amarelo · 0-19 laranja · <0 vermelho). O campo já vem no row (`t.*`).
- **Detalhe do alvo**: card "Lead score de alerta" com o score + **breakdown dos sinais**
  (recalculado ao abrir; o valor oficial é o gravado pelo worker).

## Endpoint + MCP

`GET /admin/analytics/alert-quality?period=` (admin-only, cache 5min): total avaliado, filtrado,
médias, distribuição, alertas enviados. **Honestidade:** `click_rate` por faixa e
`top_disqualify_reasons` exigem log **por-envio** (score no momento do envio + motivo), que não
está no modelo da Parte 1 → retornados **nulos/omitidos** (candidato à Parte 2). MCP:
`get_lead_scoring_stats`.

## Testes (24 + integração + endpoint)

`tests/test_kl85_scoring.py`: 16 da função pura (cada sinal + combinações + edge cases), 5 de
integração no worker (filtra <threshold · grava score de TODOS mesmo filtrados · bounce penaliza ·
**fail-safe mantém o alvo**), 3 do endpoint (auth 401 · shape/cálculos · período inválido 422).
`test_mcp_server`: +1 tool. `test_alert_worker`: ajustado (scoring desligado no fluxo de batch +
métodos no FakeStore) — **27/27 ok**. **Suite: 1126 passed** (1103 → 1126). Build Astro verde.

## Segurança / regras

`calculate_alert_score` não faz SQL (recebe o bounce pronto). Bounce com cache Redis (não query
por alvo). Threshold via env/admin_settings (não hardcoded). Endpoint admin-only. Lead scoring só
afeta o alerta proativo — nunca o scan. `HIGH_CLICK_SECTORS` vazio (não inventa dados).

## Execução (pós-deploy)

O backfill deve rodar **uma vez** na VM após o deploy:
`docker compose exec api python -m scripts.backfill_alert_scores`. O histograma resultante calibra
o threshold. Até lá, alvos sem score não são filtrados (o worker grava o score no 1º ciclo que os
avalia). Threshold 20 é **conservador** — melhor filtrar de menos do que perder leads bons.
