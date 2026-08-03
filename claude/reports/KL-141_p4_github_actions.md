# KL-141 Prompt 4/4 — Integração GitHub Actions + notificação (fecha KL-139/KL-141)

**Data:** 2026-08-03

Último prompt: integra o Security Gate no CI/CD como job PÓS-deploy e adiciona notificação em falha.

## Entregue
- **Job `security-gate` no `.github/workflows/deploy.yml`:** `needs: [deploy]`, `if: success()` —
  roda **DEPOIS** do deploy contra o site LIVE. **NÃO bloqueia o deploy** (o site já está no ar); se
  reprovar (finding ≥ `--fail-on critical`), o exit code do CLI (1/2) falha o STEP via `pipefail` → job
  vermelho + e-mail; o operador decide o rollback (o Gate nunca reverte). Pipeline:
  `test → build → nginx-check → deploy → security-gate`. UMA execução (`--json | tee gate-report.json`):
  o JSON vira artifact (`upload-artifact`, `if: always()`) e é mostrado no log. `pip install -r
  requirements.txt` (o Gate importa de `scanner/` → puxa dnspython/google-cloud-storage/cryptography;
  weasyprint instala sem libs de sistema pois o Gate não o importa).
- **`scripts/security_gate_notify.py`:** e-mail (Resend) + webhook. Chamado **só em falha**
  (`if: failure()`). Corpo/assunto puros (`email_subject`/`email_body`/`webhook_payload`), lista só os
  FAIL, inclui `error` se houver. **Fail-safe:** sem `RESEND_API_KEY`/URL → apenas avisa (não quebra o
  job, que já está vermelho pelo step do Gate). **Nunca vaza o VALOR** de credencial — o report já traz
  só tipo+localização+severidade (o check de credenciais nunca gravou o valor).
- **Badge** no `README.md` (workflow deploy.yml).

## ⚠️ Ação PENDENTE do dono (documentada, não bloqueante)
`RESEND_API_KEY` **NÃO é secret do repo** (o deploy usa a key só via o `.env` da VM, nunca no CI —
`gh secret list` mostra só os `GCP_*`). Então o step de notificação por e-mail **loga o aviso e não
envia** até o dono adicionar `RESEND_API_KEY` em *Settings → Secrets → Actions*. Isso **não** afeta o
pipeline verde (o notify só roda em falha); quando o Gate reprovar sem a key, o job já fica vermelho
(visível no badge/PR) — o e-mail é o extra. Optei por degradar com elegância em vez de adicionar um
secret cujo valor eu não tenho (e adicionar secret é ação do dono).

## Dogfooding final (este próprio push)
Este commit aciona o CI/CD; o job `security-gate` roda o Gate contra `klarim.net` LIVE. Esperado:
**score 100/100 🟢 → job verde** (validado localmente: `python scripts/security_gate.py https://
klarim.net` = 100/100, 0 findings, ~16s). Report disponível como artifact. Se um dia reprovar, o job
fica vermelho + (com a key) e-mail p/ `seguranca@klarim.net`.

## Testes — `tests/test_kl141_notify.py` (11)
`email_subject`/`email_body` (score+url, lista só FAIL, PASS fora), `webhook_payload`; `send_email`
(com key → POST ao Resend com to/subject corretos; sem key → False sem POST; sem destinatário → False;
Resend 4xx → False); `send_webhook` (com URL → POST; sem URL → False sem crash); `main` (lê report e
despacha; report ausente → 0 sem crash). **2065 pytest passed** (suite completa).

## KL-141 — COMPLETO (4/4 prompts)
`security_gate/` (portável, separado do `scanner/`): engine + models + config + 5 checks (headers,
ssl, exposure, credentials, api) + CLI + formatters + notificação + job de CI pós-deploy. Score
100/100 contra klarim.net em ~16s. **Fecha o KL-141.**

## KL-139 (catálogo de checks) — coberto → fecha junto
- Checks 1-3 (env/git/cms config), 5-12 (admin/api-docs/debug/backup/sourcemaps/dir-listing/htaccess/
  server-info): `exposure.py` ✅
- Check 4 (credenciais): `credentials.py` ✅
- Headers + SSL + API security: `headers.py`/`ssl.py`/`api_security.py` ✅

**Transiciona KL-139 e KL-141 → Feito** após o CI verde.
