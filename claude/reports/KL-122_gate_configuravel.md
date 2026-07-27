# KL-122 — Commitar gate unknown/catch_all (score>20) + torná-lo configurável + documentar configs operacionais

**Data:** 2026-07-27 · **Status:** ✅ código pronto + testado.

## Contexto

Em 27/07/2026 foi aplicado um patch **direto no container de produção**: o gate de envio para e-mails
de deliverability INCERTA (`unknown`/`catch_all`/`inbox_full`) em `is_safe_to_send` caiu de
`lead_score > 50` (KL-110 original) → `> 20`. O 50 bloqueava **~3.895 e-mails elegíveis** (2.757
`unknown` + 1.138 `catch_all`), muitos de provedores BR legítimos (Locaweb/Hostinger/UOL) que
simplesmente não respondem ao SMTP check da Reoon → caíam em `unknown`/`catch_all` sem serem ruins.

O patch precisava ser **commitado** (senão o próximo deploy o reverteria) e as **configs operacionais**
ajustadas na VM precisavam ser documentadas.

## Mudanças

### 1 + 2. Gate configurável (`notifier/email_verifier.py`)

- Nova constante de módulo (default 20, documentada):
  ```python
  _UNSAFE_SCORE_GATE = int(os.environ.get("ALERT_UNSAFE_SCORE_GATE", "20"))
  ```
- `is_safe_to_send` passou a usar `_unsafe_score_gate()`, que **lê o env a cada chamada** (com fail-safe
  p/ valor inválido) → o gate é ajustável **sem deploy**:
  ```python
  if status in ("catch_all", "unknown", "inbox_full"):
      return (lead_score or 0) > _unsafe_score_gate()
  ```
- **Sem `ALERT_UNSAFE_SCORE_GATE` no env → default 20** = comportamento IDÊNTICO ao patch de produção
  (`> 20`). O deploy substitui o patch ad-hoc pela versão commitada e configurável, sem mudança de
  comportamento (a menos que o operador sobrescreva o env).

**Inalterado (regra 4):** `safe`/`valid`/`role` → envia; `invalid`/`disabled`/`disposable`/`spamtrap` →
nunca. Só o branch incerto mudou.

### 3. Documentação

- **`docs/DEPLOY.md`:** nova linha `ALERT_UNSAFE_SCORE_GATE` + a seção **"Valores operacionais atuais"**
  (o que faz / onde é lido / default no código / quando ajustar):

  | Var | Valor atual | Default código |
  |---|---|---|
  | `ALERT_DAILY_LIMIT` | 500 | 500 |
  | `ALERT_SENDER_DAILY_LIMIT` | 500 | 100 |
  | `ALERT_SENDER_MAX_BOUNCE_RATE` | 10 | 5.0 |
  | `ALERT_UNSAFE_SCORE_GATE` | 20 | 20 |
  | `PROFILE_VIEW_DAILY_LIMIT` | 500 | 200 |

- **`claude.md`:** registra `ALERT_UNSAFE_SCORE_GATE` (seção e-mail + card §9), atualiza a menção de
  `ALERT_SENDER_MAX_BOUNCE_RATE` (valor operacional 10).

## Testes

`tests/test_kl110_email_verifier.py` atualizado (o threshold antigo era 50 — mudança **intencional**, não
falha) + novos casos:
- `unknown` + 21 → True · + 20 → False (gate é `>`, não `>=`) · + 0 → False
- `catch_all` + 25 → True · + 20 → False · `inbox_full` + 15 → False · + 51 → True
- `test_unsafe_gate_default_is_20` (env ausente → 20)
- `test_unsafe_gate_reads_env_var` (`ALERT_UNSAFE_SCORE_GATE=30` → score 25 False, 31 True)
- `test_unsafe_gate_invalid_env_falls_back_to_default` (valor inválido → não crasha)

Também ajustados os testes do KL-26 que dependiam do gate 50 (`test_email_pipeline.py` score 30→15;
`test_kl110::test_verify_and_filter_blocks_and_gates` id-3 score 30→10). Os usos `safe`/`invalid` em
`test_e2e_flows.py` não são gateados → intactos.

**Suíte:** `1853 passed, 1 skipped` (+5 líquidos).

## Nota de ambiente

O disco da máquina de dev estava em 100% (243 MiB livres) — uma escrita de arquivo falhou com `ENOSPC`.
Liberei **só caches regeneráveis do repo** (`__pycache__`, `.pytest_cache`, `web/.astro`, `web/dist`,
`node_modules/.cache`) — nada do usuário/produção — e a edição concluiu. Vale monitorar o disco.

## Validação pós-deploy

1. `pytest tests/test_kl110_email_verifier.py -v` → passa com o novo threshold.
2. Comportamento == patch de produção: sem `ALERT_UNSAFE_SCORE_GATE` no `.env`, o default 20 reproduz o
   `> 20` do patch. (Opcional: setar a env na VM p/ ajustar sem novo deploy.)
3. `docs/DEPLOY.md` lista as 5 env vars operacionais com descrição.
