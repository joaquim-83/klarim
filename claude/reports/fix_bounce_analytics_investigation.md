# Fix operacional — lead scoring (bounce) + investigação completa do analytics

**Data:** 2026-07-20 · Sem card Jira (fix operacional). Dois problemas independentes.

---

## Problema 1 — Lead scoring: bounce por-domínio penaliza provedores genéricos

### Fix aplicado
Um bounce em `joao@gmail.com` marcava `gmail.com` como bounced → **todos** os alvos `@gmail.com`
levavam **-40**. Isso não faz sentido: são endereços independentes. Corrigido em DUAS camadas:
- `discovery/alert_scoring.py::calculate_alert_score`: `if domain_bounced and edomain not in
  FREE_EMAIL_DOMAINS: add(-40, "bounce_domain")` — a penalidade só vale para domínio próprio/corporativo.
- `discovery/alert_worker.py::_domain_bounced`: curto-circuita provedores genéricos → `False` (evita
  a query e não polui o cache Redis).
- Cache Redis `bounce_domain:<provedor>` limpo (7 chaves de provedores genéricos deletadas).
- 4 testes novos (`test_kl85_scoring.py`): free não penaliza / corp penaliza / `_domain_bounced`
  curto-circuita / E2E gmail+bounce = 0.

### ⚠️ DESCOBERTA CRÍTICA — o fix do bounce, SOZINHO, NÃO atinge a meta (60-70% enviados)

Dados reais (targets alertáveis, `status IN ('discovered','scanned')` com `contact_email` + score):

```
 email_tipo |  n  | passa_agora(>=20) | mn  | avg | mx
------------+-----+-------------------+-----+-----+----
 corp       | 678 |        466 (69%)  | -30 |  22 | 60
 free       | 258 |          0 (0%)   | -60 | -48 |  0
```

Threshold em produção: **20** (sem override em `admin_settings` → default).

**Análise:** um alvo com e-mail genérico (gmail) tem sempre `-20` (`email_mismatch_free`, pois um
gmail nunca casa o domínio do site) + no máximo `+20` (zona de ação) = **teto de 0**. O bounce
adiciona `-40` → -40. O fix remove esse -40, levando o alvo de ~-40 para **0** — **ainda abaixo do
threshold 20**. Ou seja: **o fix do bounce move ZERO alvos free para cima do threshold**; ele só
corrige a super-penalização (score fica menos errado), mas não desbloqueia os leads genéricos.

O gargalo REAL dos leads de e-mail genérico é o `-20 email_mismatch_free` + threshold 20 — não o
bounce. A premissa "gmail = terceiro (não é o dono)" é frequentemente FALSA para PMEs brasileiras,
que usam gmail como e-mail comercial. Para enviar ~60-70% desse batch é preciso um 2º ajuste
(reduzir o `email_mismatch_free` OU baixar o threshold) — **decisão sensível de reputação**, ainda
mais porque os alertas ACABARAM de migrar para `klarim.net`. **Levado ao dono para decidir** (não
apliquei unilateralmente).

---

## Problema 2 — Analytics: investigação completa (output exato das queries)

### Q1 — is_human dist HOJE
```
 is_human | count
----------+-------
 f        |  8271
 t        |  1095
```
**Zero eventos NULL hoje** — o backfill (NULL→false) já foi feito (como o card previa). Os eventos
`f` (8271) são os bots (page_views antigos backfillados + eventos de ação sem interação).

### Q2 — page_view/profile_view x is_human HOJE
```
  event_type  | is_human | count
--------------+----------+-------
 page_view    | f        |  4449
 page_view    | t        |   471
 profile_view | f        |  3813
 profile_view | t        |   431
```

### Q3 — emails HOJE por tipo x status
```
 profile_view | sent    | 7116     alert | sent    |  333
 alert        | bounced |   33     profile_view | bounced |  18
 welcome_confirmation | sent | 7    profile_view | blocked | 7  ...
```
Os 7116 profile_view são o CUMULATIVO do dia — dominado pela madrugada (pré-fix).

### Q4 — profile_view emails por HORA (o fix do KL-64 cortou?)
```
 02:00=807  03:00=1002  04:00=970  05:00=1095  06:00=1050  07:00=1001  08:00=788
 09:00=244  10:00=34   11:00=5    12:00=11  ...  17:00=6   18:00=11   19:00=7
```
**SIM — cortou.** De ~1000/hora (02-08h) para 5-20/hora (após ~10h, quando o KL-64 deployou). Os
7116 do dia são o legado da madrugada; a taxa atual é residual (visitas humanas reais).

### Q6 — últimos 10 eventos (novos vêm com is_human=true?)
```
 16342 | page_view    | t | 19:46:51
 16343 | profile_view | t | 19:46:51    ... (todos is_human=t, detection=interaction)
```
**SIM** — todo evento novo é `is_human=true`. Tracker funcionando.

### Q10 — page_view is_human desde 18:00 (pós cache-bust ?v=64)
```
 is_human | count
----------+-------
 t        |   456
```
456 page_views pós-deploy, **100% is_human=true.**

### Visitantes únicos HOJE — com filtro vs sem
```
 todos_incl_null | filtro_atual(is_human OR NULL) | so_humano_true
-----------------+-------------------------------+----------------
      4893       |             519               |      519
```
E via endpoint (MCP `get_analytics_metrics?period=today`):
- **default (só humanos): `unique_visitors=521`**
- **`include_bots=true`: `unique_visitors=4895`**

### Cenário identificado e fix
- **Cenário C (filtro nas queries): OK** — o toggle muda 521↔4895 → o filtro `(is_human=TRUE OR IS
  NULL)` ESTÁ em todas as queries `aa_*` (KL-64), funcionando.
- **Cenário D (SSR ainda chama /notify): OK** — a queda de profile_view (Q4) confirma que o SSR não
  dispara mais; os residuais vêm do evento humano.
- **Cenário B (NULL dominando): resolvido** — backfill já feito (Q1: 0 NULL hoje). Tracker novo
  garante true/false daqui p/ frente.
- **Cenário E (cache Redis stale): checado** — `KEYS analytics:*` estava **VAZIO** (não havia cache
  velho). Flush executado mesmo assim (idempotente).

**Conclusão Problema 2: o analytics JÁ está correto** — mostra 521 visitantes humanos hoje (não
4.000+). O "4895" só aparece com `include_bots=true`. A percepção de "ainda inflado" era: (a) o
CUMULATIVO do dia inclui a madrugada pré-fix (emails/eventos), que sai amanhã; (b) possivelmente o
toggle "incluir bots". Nenhuma regressão; nenhuma mudança de código necessária além do KL-64 já
deployado.

Nota lateral: `pageviews_per_session` humano = 0,91 (< 1) — consequência do gating (page_view só
dispara após interação; sessões só-ação contam como visitante sem page_view). Métrica mais honesta
(menor), não um bug. A expectativa ">1,5" do KL-64 não se sustenta sob o gating.

---

## Testes / validação
- Bounce fix: **4 testes novos** (27 em `test_kl85_scoring.py`). **1311 backend passed.**
- Analytics: confirmado por query + MCP (521 humano vs 4895 com bots).
- Cache flush: `analytics:*` (vazio) + `bounce_domain:<provedores>` (7 chaves).

## Pendente de decisão do dono (Problema 1)
Para o alert worker enviar mais e drenar o backlog, é preciso um 2º ajuste além do bounce (reduzir
`email_mismatch_free` ou baixar o threshold) — trade-off de reputação no `klarim.net` recém-migrado.
