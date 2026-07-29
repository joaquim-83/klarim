# KL-127 — Solução DEFINITIVA do pipeline de verificação de e-mail

**Data:** 2026-07-29 · **Status:** ✅ implementado, **1881 pytest passed**, zero código morto.
**Deploy:** pendente de push + CI. Consolida no repositório os patches manuais que já estavam
nos containers (sem `if False`/comentários).

---

## Problema (CRÍTICO)

O pipeline de alertas travou **4×** desde o KL-110. A regra do **KL-125** (`unknown` NUNCA
envia) matou **100% dos alertas**: no mercado BR, `unknown` = "o servidor não respondeu ao
SMTP check" (Locaweb, Hostinger, UOL, Titan) — **incerto, não ruim**. Foram aplicados patches
manuais via `docker exec` DENTRO dos containers (`if False:` / `# DESABILITADO`), que:
- não estavam no repositório (o repo ainda bloqueava `unknown`);
- **divergiram** `klarim-api-1` de `klarim-discovery-1` (patches aplicados separadamente).

**Dados (do card):** Power safe/role **0%** bounce · catch_all **2,9%** · unknown **~5-8%** ·
sem verificação (bug) 3,9%. Conclusão: **o gate de score é a regra correta** — bloquear todo
`unknown` zera os alertas; o gate filtra os de menor qualidade.

## Solução (5 mudanças, ZERO código morto)

### 1. `is_safe_to_send` — regra ÚNICA (`notifier/email_verifier.py`)
```
safe/valid/role                         → sempre envia
invalid/disabled/disposable/spamtrap    → nunca (blocklist)
unknown/catch_all/inbox_full            → envia se lead_score > ALERT_UNSAFE_SCORE_GATE (20)
```
Removida a docstring/branch do KL-125 ("unknown NUNCA envia"). A obrigatoriedade de verificação
é garantida no worker, não aqui (fallback final = enviar).

### 2. `_verify_and_filter` reescrito e SIMPLIFICADO (`discovery/alert_worker.py`)
Removidos os artefatos do KL-125: **Regra 2** (unknown/power skip), a **reverificação de
`unknown` via Power** (Regra 1) e o branch **"unknown 2× → drop"**. Decisão por alvo:
1. fresco no DB → usa o status cacheado; senão → verifica via Power (semáforo de 5).
2. `fallback` (Reoon fora) → **não persiste** (não condena) e **não envia** (sem verificação).
3. block-statuses → `block_email` + `descartado` (não repete se já era cache).
4. senão → `is_safe_to_send` (gate). `unknown` de QUALQUER source é tratado só pelo gate.

`rest_kept` (além do teto `email_verify_max`) = já-verificados (`email_verified` + status
não-vazio); **`unknown` verificado é permitido** (o gate decide). **Sem verificação → não envia.**
**Modo degradado sem `REOON_API_KEY`** (dev/fallback): já-verificados seguem o gate, não
verificados passam (o MX da Camada 0 já foi validado na extração — bloquear tudo zeraria alertas).

### 3. Log estruturado por e-mail (`logging`, mascarado — LGPD)
`logger.info("[alert] %s → status=%s source=%s score=%d gate=%d → %s", ...)` com a decisão
`SENT | BLOCKED | SKIPPED_GATE | SKIPPED_UNVERIFIED`. Visível em `docker logs klarim-discovery-1`.

### 4. Teste de integração (`tests/test_kl127_pipeline_integration.py`, +7)
Mix realista de 200 → **170 enviados** (nunca zera); tudo-unknown score>20 → **200**;
tudo-unknown score<20 → **0** (gate); 100 safe + 100 disabled → **exatamente 100**; boundary do
gate (`>20`, não `>=`); sem-verificação/`fallback` → não envia; **guard anti código morto**
(`if False`/`DESABILITADO` ausentes de `email_verifier`+`alert_worker` via `inspect.getsource`).
Testes do KL-110/pipeline atualizados p/ o gate; `test_kl125_unknown_reverify.py` **removido**.

### 5. Propagação Docker (`docs/ARCHITECTURE.md`)
`api`/`worker`/`discovery` usam a MESMA imagem (`build: .`) → o deploy com `--force-recreate`
(KL-124) garante código idêntico nos três. **Nunca** editar arquivo via `docker exec` (foi a
causa do incidente). Documentado com o passo de validação por `diff`.

## Segurança / LGPD
- Log sempre com e-mail **mascarado** (`_mask`); nenhum e-mail em claro.
- `REOON_API_KEY` só do `.env`; cache por SHA-256; semáforo de 5; fail-open.
- Nenhum novo endpoint/superfície; SQL inalterado (parametrizado).

## Testes
- `pytest`: **1881 passed, 1 skipped**. `is_safe_to_send`: unknown+21→True, +20→False, +100→True;
  catch_all+21→True; safe+0→True; disabled+100→False; inbox_full+25→True.
- Zero código morto confirmado por `grep -rn "if False\|DESABILITADO"` (prod) + guard de teste.

## Validação pós-deploy
1. `diff <(docker exec klarim-api-1 cat /app/notifier/email_verifier.py) <(docker exec klarim-discovery-1 cat /app/notifier/email_verifier.py)` → **vazio**.
2. Idem `discovery/alert_worker.py` → **vazio**.
3. `docker exec klarim-{api,discovery}-1 grep -c "if False" /app/notifier/email_verifier.py /app/discovery/alert_worker.py` → **0**.
4. Alert worker envia no próximo ciclo (backlog>0 → `sent_today` cresce); `get_system_status` alive.
5. Log: `[alert] c***@dominio.com.br → status=unknown source=power score=35 gate=20 → SENT`.
6. Fechar KL-127 no Jira.

## Arquivos
- Alterados: `notifier/email_verifier.py`, `discovery/alert_worker.py`, `docs/ARCHITECTURE.md`,
  `docs/DEPLOY.md`, `CLAUDE.md`, `tests/test_kl110_email_verifier.py`, `tests/test_email_pipeline.py`.
- Novos: `tests/test_kl127_pipeline_integration.py`, `claude/reports/KL-127_pipeline_definitivo.md`.
- Removido: `tests/test_kl125_unknown_reverify.py` (premissa invalidada).
