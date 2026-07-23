# Hotfix — alert worker cold envia 0 com 1.375 elegíveis (livelock de fila)

**Prioridade:** ALTA. Os alertas cold pararam de sair. Deploy imediato.

---

## Diagnóstico (dados reais, não as hipóteses do card)

O `last_cycle_stats` já dizia a causa: `eligible: 32, sent: 0, skipped_low_quality: 32,
avg_alert_score: 0`. **Todos os 32 buscados foram filtrados pelo lead scoring (KL-85)** —
não é daily-limit (Hip 1), circuit breaker (Hip 2), blocklist (Hip 3), sem-email (Hip 4:
a query exige `contact_email IS NOT NULL`) nem já-alertado (Hip 5: a query exige
`status='scanned'`).

**Distribuição do `alert_quality_score` dos elegíveis:** 134 em **15**, 36 em 5, … e apenas
**1** ≥ 20 (o threshold). Ninguém passa.

**Causa raiz — livelock de ordenação:** a query buscava `ORDER BY last_scan_at ASC` (mais
antigo primeiro). A frente da fila era **e-mail genérico que NÃO casa o domínio do site**
(ex.: site `assinearqplay.com.br`, e-mail `contato@arqplay.com.br` → sem o sinal +30
"email_matches_domain"). Score deles = 15 (`corporate +10` + `score_band +20` − `role −15`) <
20 → pulados. Como um lead pulado **não é marcado nem removido**, ele volta à frente no ciclo
seguinte → o worker relê os mesmos 32 e manda **0 para sempre**.

**Os leads bons estavam no fundo da fila:** dos 1.382 elegíveis, **733 têm e-mail no domínio
do site** (+30) e **732 desses têm score ≥ 40** → pontuam **45–60**, muito acima do threshold.
Mas, com `last_scan_at ASC`, esses (enriquecidos mais recentemente) ficavam atrás e nunca
eram buscados (o ciclo só busca ~32 da frente).

Confirmado na VM: com `ORDER BY (e-mail casa domínio) DESC`, os **40 primeiros buscados são
todos domain-match** → todos passariam.

## Correção

1. **Ordenar os melhores leads primeiro** (`get_eligible_targets_for_alert`):
   `ORDER BY (lower(t.domain) = split_part(lower(t.contact_email),'@',2)) DESC, t.last_scan_at ASC`.
   Traz os 733 leads de e-mail-no-domínio (score 45–60) para a frente → passam o threshold.

2. **Desacoplar FETCH do SEND cap** (`run_cycle`): o `send_cap` (throttle + cooldown + cotas)
   limita quantos **enviar**; o `fetch_cap` (`ALERT_FETCH_CAP=200`, editável) define quantos
   **avaliar**. Buscar só `send_cap` era metade do livelock (poucos candidatos → todos da frente
   ruim → 0). Agora avalia 200, filtra, e envia os melhores até o `send_cap`. Guard novo
   `if stats["sent"] >= send_cap: break`. Os aprovados são **ordenados por score DESC** (melhores
   primeiro).

3. **Logging detalhado e PERMANENTE** (pedido do card): cada lead pulado por baixa qualidade
   loga `[alert] skip lead t={id} {email_mascarado} score=X<threshold [sinais]` (amostra de
   `ALERT_SKIP_LOG_SAMPLE=20`/ciclo p/ não floodar) + resumo `[alert] lead scoring: N aprovados,
   M pulados`. O resumo do ciclo agora mostra `avaliados/inválidos/baixa_qualidade/adiados/erros`.
   E-mail **mascarado** no log (`c***o@x.com.br`) por privacidade.

**Não mexi na calibração do scoring** (o `−15` de prefixo role-based). Não é preciso: os 733
domain-match já pontuam 45–60 e passam. O `−15` só afeta e-mail genérico não-matching (leads de
menor qualidade), que é o comportamento pretendido do KL-85. Se no futuro quisermos alcançá-los,
reduzir o `−15` de "contato@" seria o passo (mesma lógica do fix do gmail `−20→0` de 2026-07-20).

## Não quebrei o caminho antigo
A mudança é só na SELEÇÃO/ordenação/logging do ciclo cold do KL-91. Os 816 envios antigos
(profile_view via `alerta@klarim.net` etc.) não são tocados.

## Testes
`test_alert_worker.py`: +2 (`sends_best_leads_first` — filtra e envia por score DESC;
`apply_scoring_logs_skip_with_reason` — log com sinais + e-mail mascarado). `test_alert_sender_
migration.py`: `test_fetch_decoupled_from_daily_limit` (o fetch não é mais limitado pelo diário).
**Suite: 1605 passed, 1 skipped.** SQL do `ORDER BY` validado contra o Postgres 16 da VM
(top-40 = 40 domain-match).

## Validação pós-deploy
`sudo docker logs klarim-discovery-1 --since 35m | grep 'alert.*ciclo'` → `X enviados` com **X > 0**;
`email_log` da última hora com `alertas.klarim.net`/`aviso.klarim.net` > 0.
