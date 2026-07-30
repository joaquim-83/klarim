# KL-129 — Priorizar verificação de novos no subset + filtrar unknowns + canário por domínio

**Data:** 2026-07-30 · **Status:** ✅ código + testes + docs; **1899 pytest passed**, SQL validado no
Postgres 16 da VM. **Deploy:** via commit/push (CI/CD).

---

## Problema (alertas parados 3+ horas)

O cap de verificação (120/ciclo) era consumido pelos e-mails **já verificados** do cache
(`unknown`/`catch_all` que o gate do KL-128 barra). Zero vaga sobrava para os e-mails **novos**
(`email_verified=false`). O pipeline girava em falso:
```
eligible 200, from_cache 120, skipped_gate 120, sent 0, verified 0  → repete a cada 30 min
```

## Solução (`discovery/alert_worker.py::_verify_and_filter` reescrito)

### 1-3. Partição ANTES do subset (prioriza os NOVOS)
Em vez de `subset = targets[:cap]` (que pegava os primeiros, geralmente cacheados), particiona:
- **sendable** — já-verificado **aprovado** (safe/role/valid, catch_all+gate) → **envia direto**, sem
  re-tocar a API.
- **blocked_known** — já-verificado **barrado** (`unknown`, block-status, catch_all/inbox_full com
  score ≤ gate) → descartado **sem consumir vaga**.
- **unverified** — `email_verified=false` / status vazio / TTL expirado → **prioridade**:
  `subset = unverified[:cap]` → verificados via Power **NESTE ciclo**; o excedente = `deferred`
  (próximo ciclo). Removido o conceito de `rest`.

Resultado: os novos são verificados já, e os safe/role/catch_all resultantes **enviam no mesmo ciclo**
→ `sent > 0` e `verified > 0`. Quando tudo é `unknown` cacheado, o subset fica vazio → **0 chamadas à
API** (não desperdiça crédito, não gira em falso).

### 4. Domínio confiável (canário — parte estática)
`store.trusted_recipient_domains(domains, hours=48)` — dos domínios de **destinatário** passados,
quais tiveram envio `sent`/`delivered` e **zero** bounce/complaint nas últimas 48h (domínio extraído
de `to_email` via `split_part`, pois `email_log.domain` guarda o site-alvo). Um `unknown` **fresco**
de domínio confiável é rebaixado a `catch_all` (passa a valer o gate de score) — recupera volume dos
mega-hosts BR (Locaweb/Hostinger retornam `unknown` no SMTP-check). **Kill-switch**
`ALERT_TRUST_DOMAIN_DOWNGRADE=false` (lido a cada ciclo, sem deploy). **Fail-open** (erro na query →
conjunto vazio → nada rebaixado). O **canário ATIVO** (envio 1 + recheck 24h + blocklist por domínio +
coluna `email_log.is_canary`) foi **deferido** para um card futuro (o card permite explicitamente).

### 5. Cap 120 → 200
`EMAIL_VERIFY_MAX_PER_CYCLE` default **200** (era 120) + **editável ao vivo** no painel
(`_reload_settings`, `admin_settings` > env). ~9.600 verificações/dia em ciclos de 30 min.

## Regra de e-mail (inalterada, KL-128)
safe/valid/role → envia · disabled/invalid/disposable/spamtrap → blocklist · **`unknown` → NÃO envia**
· catch_all/inbox_full → gate `> ALERT_UNSAFE_SCORE_GATE` (20). O trust-downgrade só transforma
`unknown`→`catch_all` (continua passando pelo gate — não é um bypass).

## Segurança / risco
- Trust-downgrade é **conservador** (domínio com histórico limpo **E** score > gate) e tem kill-switch
  instantâneo — mitiga o risco de reabrir bounce. **Recomendação:** monitorar o bounce nas primeiras
  24h; se subir, `ALERT_TRUST_DOMAIN_DOWNGRADE=false` no painel/`.env`.
- SQL parametrizado (`= ANY(%s)`, `make_interval(hours => %s)`); log de e-mail mascarado (LGPD).
- `trusted_recipient_domains` é 1 query/ciclo (30 min), agrupada e filtrada — barata.

## Testes (1899 pytest passed)
- **Novos** (`tests/test_kl129_subset_priority.py`, +10): mix novos+cache (só os novos tocam a API; os
  safe cacheados enviam direto; unknown não consome vaga); unknown cacheado não ocupa slot; novo
  verificado e enviado no mesmo ciclo; tudo-unknown → **0 API** (sem girar em falso); deferimento além
  do cap; trust-downgrade (rebaixa; respeita o gate; domínio não-confiável fica barrado); cap 200/env.
- **Ajustados:** KL-127/128 (novas stats `blocked_known`/`deferred`; partição exige `email_verified_at`).
- SQL validado no Postgres 16 da VM (retornou `gmail.com|190|2` — gmail tem 2 bounces/48h → NÃO trusted,
  confirmando a regra conservadora).

## Validação pós-deploy
1. Próximo ciclo do alert worker: `[alert] verify KL-129: sendable N (cache) + M verif (API), … ` com
   **`verified > 0`** (API dos novos) e **`sent > 0`** no `run_cycle`.
2. `get_system_status` → alert worker alive, `verification.verified`/`deferred` > 0.
3. Bounce ≤ 5% em 24h (métrica final; se subir, kill-switch do trust-downgrade).
4. Fechar KL-129 no Jira após confirmar `verified > 0` e `sent > 0`.

## Arquivos
- Alterados: `discovery/alert_worker.py` (partição + cap + reload), `discovery/store.py`
  (`trusted_recipient_domains`), `CLAUDE.md`, `docs/DEPLOY.md`,
  `tests/test_kl127_pipeline_integration.py`, `tests/test_kl110_email_verifier.py`.
- Novos: `tests/test_kl129_subset_priority.py`, `claude/reports/KL-129_prioriza_novos_subset.md`.
