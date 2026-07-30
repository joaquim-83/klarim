# KL-128 — Fix deploys falhados + regra DEFINITIVA de validação de e-mail

**Data:** 2026-07-30 · **Status:** ✅ código + testes + docs prontos; **1889 pytest passed**, zero
código morto. **Deploy:** pendente de commit/push (o que destrava a propagação à VM).

---

## Causa do deploy que não propagou (investigação)

Os commits **`49e5286`** (`is_safe_to_send` bloqueia `unknown`) e **`e1a8626`** (`parse_reoon_
response` rebaixa safe+catch_all) **estão em `origin/main`** (HEAD == origin/main, 0/0 divergência).
Então o push ocorreu — o que falhou foi o **CI**: o job **`test`** quebrou porque o código passou a
`unknown`=blocked mas os testes do KL-127 ainda esperavam `unknown`=gate (7 testes vermelhos). Como o
job `deploy` depende de `needs: [test, build-web, nginx-check]`, ele **nunca rodou** → a VM ficou no
KL-127 (`94aec55`), 2 commits atrás. **Não foi o `--force-recreate` (KL-124)** — o deploy nem chegou a
executar. **Correção:** alinhar docstring + testes ao código correto → CI verde → deploy propaga.

## Regra DEFINITIVA (KL-128)

### `is_safe_to_send` (`notifier/email_verifier.py`)
- `safe`/`valid`/`role` → **sempre envia**.
- `disabled`/`invalid`/`disposable`/`spamtrap` → **nunca** (blocklist).
- **`unknown` → NUNCA envia.** O gate de score NÃO filtra `unknown` (no BR = "servidor não respondeu
  ao SMTP check": Locaweb/Hostinger/UOL/Titan) — bounce ~5-8%, que fez a taxa voltar a **>10%** quando
  o KL-127 mandou `unknown`→gate. Reverificações futuras podem promovê-lo a safe/catch_all.
- `catch_all`/`inbox_full` → gate `> ALERT_UNSAFE_SCORE_GATE` (default 20).

### `parse_reoon_response` — catch-all disfarçado de safe
Num servidor **catch-all** (`is_catch_all=true`) o Reoon devolve `safe`/`valid` porque o servidor
aceita QUALQUER caixa no SMTP-check. Rebaixa **`safe`/`valid` + `is_catch_all` → `catch_all`** (passa
a valer o gate de score) — ataca o bounce na origem (o commit `e1a8626` já tinha isso; documentado agora).

### `_verify_and_filter` (`discovery/alert_worker.py`)
- Decisão única `is_safe_to_send` no subset **e no `rest`** — o `rest` (além do teto de verificação)
  agora aplica o MESMO gate pelo status cacheado, então **`unknown` não escapa** por ele (antes o rest
  deixava passar qualquer verificado). Custo zero (sem API).
- `_is_fresh` passou a exigir **status não-vazio** — um alvo `email_verified=true` com status vazio não
  vira mais `safe` fantasma; é reverificado via Power.
- Docstrings/comentários migrados de KL-127 → KL-128.

## Zero código morto
`grep -rn "if False\|DESABILITADO\|DISABLED"` em `discovery/`+`notifier/`+`api/` → **nada**. Guard de
teste (`test_no_dead_code_if_false_in_pipeline`) falha o CI se algum patch morto voltar.

## Testes (1889 pytest passed)
- **Alinhados** (`unknown`=blocked): `test_is_safe_to_send` (param), `test_unsafe_gate_default_is_20`,
  `test_send_decision_unknown_never_sends`, e o `test_kl127_pipeline_integration.py`
  (mix 200 → **100 enviados** / 85 unknown barrados / 15 block; tudo-unknown → **0**; boundary do gate).
- **Novos:** `parse_reoon_response` demote (safe+catch_all → catch_all; valid+catch_all → catch_all;
  safe sem catch_all → inalterado); `_verify_and_filter` **6 casos** (não-verificado→não; verificado
  sem-status→não; safe→sim; unknown→não; catch_all score25→sim; catch_all score15→não).

## Validação pós-deploy (VM)
1. `git log --oneline -1` na VM → o commit KL-128.
2. `docker inspect --format='{{.Created}}' klarim-api-1` → recriado agora.
3. `diff <(docker exec klarim-api-1 md5sum /app/notifier/email_verifier.py /app/discovery/alert_worker.py) <(docker exec klarim-discovery-1 md5sum /app/notifier/email_verifier.py /app/discovery/alert_worker.py)` → **idêntico**.
4. `docker exec klarim-api-1 grep -c 'if False' /app/notifier/email_verifier.py /app/discovery/alert_worker.py` → **0**.
5. `curl http://localhost:8000/health` → OK.
6. `docker exec klarim-api-1 python3 -c "from notifier.email_verifier import is_safe_to_send, VerifyResult; print(is_safe_to_send(VerifyResult('unknown','x',False,False), 100))"` → **False**.
7. Bounce rate < 5% em 24h (métrica final; fechar o card quando o deploy estiver validado).

## Arquivos
- Alterados: `notifier/email_verifier.py` (docstrings/comentários), `discovery/alert_worker.py`
  (rest gate + `_is_fresh` + docstrings), `CLAUDE.md`, `docs/DEPLOY.md`,
  `tests/test_kl110_email_verifier.py`, `tests/test_email_pipeline.py`,
  `tests/test_kl127_pipeline_integration.py`.
- Novo: `claude/reports/KL-128_regra_definitiva_email.md`.
