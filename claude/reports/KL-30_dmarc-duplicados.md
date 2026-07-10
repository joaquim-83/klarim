# KL-30 — Endurecer check_23 DMARC: detectar registros duplicados (RFC 7489)

**Card:** KL-30 · **Tipo:** fix de robustez de check.

## Problema

O `check_23_dmarc` avaliava apenas o **primeiro** registro `_dmarc` TXT que o DNS
retornava (`next(...)`). Se o domínio tem **múltiplos** registros DMARC, a **RFC 7489
§6.6.3** determina que os receptores **ignoram todos** — o DMARC fica **sem efeito**.
O check podia dar PASS num domínio com DMARC efetivamente quebrado, e o resultado
**oscilava** conforme a ordem aleatória do DNS.

**Descoberto em produção:** a `klarim.net` tinha `p=none` **e** `p=quarantine`
simultaneamente; o score do self-scan oscilava **96↔100** dependendo de qual registro
o DNS devolvia primeiro.

## Correção

**`scanner/checks/check_23_dmarc.py`:** em vez de pegar o primeiro DMARC, coleta
**todos** (`dmarc_records = [r for r in records if r.strip().lower().startswith("v=dmarc1")]`)
e decide:

| Caso | Resultado |
|------|-----------|
| **> 1** registro DMARC | **FAIL** — "Múltiplos registros DMARC (N). Pela RFC 7489, receptores ignoram todos — o DMARC está sem efeito." + lista os registros. |
| **0** registros | FAIL (ausente) — inalterado |
| **1** registro, `p=none` | FAIL (permissivo) — inalterado |
| **1** registro, `p=quarantine`/`p=reject` | PASS — inalterado |
| DNS indisponível | INCONCLUSO — inalterado |

TXT não-DMARC (ex.: registros de verificação, SPF publicado por engano no `_dmarc`)
são ignorados — só contam os que começam com `v=DMARC1`.

**`dns_util.resolve_txt`:** confirmado que **já retorna todos** os registros TXT da
query (itera sobre `answers`, sem `[0]`/`next()`) — nenhuma mudança necessária.

**`reporter/risk_messages.py`:** o entry de `check_23_dmarc` ("Seu domínio não tem
proteção contra phishing" / "Golpistas podem enviar e-mails como se fossem da sua
empresa…") já cobre com precisão o caso de duplicatas (DMARC sem efeito = sem proteção)
— mantido.

## Testes (`tests/test_checks_16_29.py`)

- `test_check23_multiple_dmarc_records_fail` — 2 registros (`p=none` + `p=quarantine`,
  o caso real da klarim.net) → **FAIL** com "Múltiplos" na evidência + `count=2`.
- `test_check23_single_dmarc_quarantine_pass` — registro único `p=quarantine` → PASS.
- `test_check23_ignores_non_dmarc_txt` — DMARC único entre outros TXT → avalia só o DMARC.
- Mantidos: ausente → FAIL, `p=none` → FAIL, `p=reject` → PASS, DNS erro → INCONCLUSO.

`pytest -k check23` → **7 passed**.

## Validação

1. 2 registros DMARC → FAIL com "Múltiplos registros DMARC" ✅
2. 1 registro `p=quarantine` → PASS ✅
3. 1 registro `p=none` → FAIL ✅
4. Sem DMARC → FAIL ✅
5. `klarim.net`: **enquanto tiver os 2 registros, dará FAIL** (correto — o DMARC está
   sem efeito). Para voltar a PASS/100, remover o `p=none` duplicado na Hostinger,
   mantendo só `v=DMARC1; p=quarantine; rua=…`. (A correção do DNS é uma ação de
   operação, fora do escopo de código deste card.)
6. Testes passando ✅

## Nota importante

Depois deste deploy + flush do cache `scan:*`, a `klarim.net` deve dar **FAIL** no
check 23 (score volta a cair) enquanto os **dois** registros `_dmarc` existirem — isso
é o check funcionando **corretamente** (a RFC diz que o DMARC dela está sem efeito). O
resultado deixa de oscilar: agora é FAIL determinístico até o DNS ser corrigido.

## Arquivos

`scanner/checks/check_23_dmarc.py`, `tests/test_checks_16_29.py`, `claude.md`,
`README.md`, este relatório.
