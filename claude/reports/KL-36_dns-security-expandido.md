# KL-36 — DNS security expandido (DNSSEC, CAA, MTA-STS, BIMI)

**Card:** KL-36 · **Prioridade:** Média.
**Objetivo:** completar a camada DNS/e-mail do scanner (que já tinha SPF/DKIM/DMARC nos
checks 21–23) com 4 verificações **100% passivas**: DNSSEC, CAA, MTA-STS e BIMI. Todas
tier **pago** (ORDER>15).

---

## dns_util.py

Dois helpers novos, no mesmo padrão mockável de `resolve_mx`/`resolve_ns`/`resolve_txt`
(síncrono, chamado via `asyncio.to_thread`; `[]` = ausência definitiva → FAIL, `None` =
erro de DNS → INCONCLUSO):
- `resolve_ds(name)` — registros DS (DNSSEC).
- `resolve_caa(name)` — registros CAA como `[{flags, tag, value}]`.

`resolve_txt` (já existente) é reusado pelo MTA-STS e pelo BIMI.

## Os 4 checks

| # | Check | Sev. | OWASP / CWE | Regra |
|---|-------|------|-------------|-------|
| 37 | DNSSEC | Média | A02 / CWE-350 | DS presente → PASS; ausente → FAIL (cache poisoning) |
| 38 | CAA | Média | A02 / CWE-295 | `issue`/`issuewild` → PASS (lista CAs); ausente → FAIL |
| 39 | MTA-STS | Baixa | A02 / CWE-319 | TXT `_mta-sts` + GET policy: `enforce`→PASS, `testing`→PASS c/ nota, sem policy→FAIL, ausente→FAIL |
| 40 | BIMI | Baixa | A07 / CWE-290 (LGPD None) | `v=BIMI1` → PASS (checa DMARC enforce, anota se falta); ausente → FAIL (maturidade) |

**MTA-STS** é o único que faz rede além do DNS: um **GET** em
`https://mta-sts.<domínio>/.well-known/mta-sts.txt` — URL pública definida pela RFC 8461,
via o `fetch` do base (rate limit + timeout + User-Agent honesto). Continua 100% passivo.

**BIMI** consulta também o `_dmarc.<domínio>` para verificar o pré-requisito (DMARC em
`p=quarantine`/`reject`) e anota na evidência quando falta — mas o BIMI presente já é PASS.

## Classificação (KL-34/35) e relatórios

`classifications.py`: 4 entradas (A02/CWE-350, A02/CWE-295, A02/CWE-319, A07/CWE-290 com
**LGPD None** no BIMI — informativo). `RISK_MESSAGES` (executivo: "redirecionar visitantes
para uma cópia falsa", "qualquer empresa pode emitir um certificado", "carta sem lacrar o
envelope", "logo da marca nos e-mails"), `ACCESSIBLE` e `TECHNICAL` (com os registros DNS e
o fix) para os 4. Categorias de risco atualizadas.

## Testes (`tests/test_kl36_dns.py`, 14)

DNSSEC (presente/ausente/erro), CAA (presente com CA listada/ausente), MTA-STS (enforce/
testing/DNS-sem-policy/ausente), BIMI (presente/ausente/sem-DMARC-enforce), erro DNS →
INCONCLUSO, classificações dos 4. Mocks de `dns_util.resolve_ds/resolve_caa/resolve_txt` e
do `fetch` do MTA-STS. Contagens ajustadas **36→40 / pagos 21→25** em `test_kl27_funnel.py`,
`test_classifications.py`, `test_checks_16_29.py`.

## Deploy

**Flush `scan:*` no Redis após deploy** (novos checks mudam scores). Docs: `claude.md` §34,
`README.md`.

## Arquivos

**Novos:** `check_37_dnssec.py`, `check_38_caa.py`, `check_39_mta_sts.py`,
`check_40_bimi.py`, `tests/test_kl36_dns.py`, este relatório. **Alterados:**
`scanner/checks/dns_util.py`, `scanner/checks/classifications.py`, `reporter/generator.py`,
`reporter/risk_messages.py`, `tests/test_checks_16_29.py`, `tests/test_kl27_funnel.py`,
`tests/test_classifications.py`, `claude.md`, `README.md`.
