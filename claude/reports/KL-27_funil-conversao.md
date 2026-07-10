# KL-27 — Reestruturação do funil de conversão

**Card:** KL-27
**Objetivo:** corrigir o funil que converteu **zero** dos 1.418 alertas enviados.
Três causas atacadas: o resultado gratuito entregava detalhe demais (nada a
comprar), o e-mail parecia spam (preço + alarme), e não havia gancho de retorno.

---

## 1. Novo modelo de negócio

| Camada | Checks | Preço | Entrega |
|--------|--------|-------|---------|
| **Gratuita** | 15 (checks 1–15) | R$ 0 | Score + semáforo + contagem + lista dos 15 (só ✅/❌) + os 14 pagos **bloqueados** (🔒). Sem detalhes. |
| **Completa** | 29 (checks 1–29) | **R$ 19** (único, todos os setores) | Relatório executivo + técnico com evidências e correções. |
| **Re-verificação** | 29 | Inclusa | 1 re-scan gratuito pós-compra ("retorno médico"). |

---

## 2. Tiering do scanner (free 15 / full 29)

- **`scanner/checks/__init__.py`** — `FREE_CHECK_MAX_ORDER = 15`; `discover_checks(full)`
  filtra por `ORDER`; expõe `ALL_CHECKS` (29), `FREE_CHECKS` (15) e `CHECK_META`
  (`{check_id, name, order, paid}`) — metadados leves para o frontend listar nomes
  e separar tiers **sem executar** os checks pagos.
- **`scanner/runner.py`** — `run_scan(url, full=True)` escolhe `ALL_CHECKS`/`FREE_CHECKS`.
- **`scanner/cache.py`** — chave **namespaced por tier**: `scan:free:<hash>` e
  `scan:full:<hash>` (ambas casam `scan:*` no flush da VM). Um scan de 15 nunca é
  servido como relatório de 29 e vice-versa.
- **`api/main.py::get_or_scan(url, full=…)`** — `_tier_ok(report, full)` exige ≥29
  (full) ou ≥15 (free); um scan do banco no tier errado força re-scan. Default
  `full=True`; só o `/scan/summary` público e o `get_recent_only` usam `full=False`.
- **Onde cada scan roda:** discovery/público = **free**; pós-pagamento, admin,
  `/report/*`, recuperação, re-verificação KL-27 = **full**. O re-scan de
  re-engajamento (KL-13) roda **free** — o score de evolução tem que ser comparável
  ao do alerta (também 15).

## 3. Resultado gratuito sem detalhes (`GET /scan/summary`)

`_summary_payload(report, full=False)` devolve:

```
score, semaphore, grade_icon, risk_summary (genérico), fail_count,
free_checks:[{check_id, name, status}]  (15, PASS/FAIL/INCONCLUSO),
paid_checks:[{check_id, name, status:"locked"}]  (14),
price:1900, price_display:"R$ 19", is_full:false
```

**Removido do gratuito:** `risk_messages` (headlines/ícones/descrições),
`severity_counts`, evidências, impacto e correção. O visitante vê **o que** foi
verificado e **quantas** falhas — nunca **quais** nem **como**. Com token de
re-verificação (`full`) ou JWT admin, o payload traz o status real dos 14.

## 4. E-mail sem preço nem alarme

- **Assunto:** `dominio.com.br — resultado da avaliação de segurança` (sem emoji,
  sem "problema(s)"). Evolução: `dominio — atualização da avaliação de segurança`.
- **Corpo (`alert.html` + `evolution_*.html`):** só score + semáforo + contagem +
  CTA **"Veja o relatório"**. Removidos preço, cards de risco e contagem por
  severidade. `email_client._alert_params`/`_evolution_params` deixam de renderizar
  risco/preço; `alert_worker.build_alert_payload` e `send_alert_for_target` param de
  computar risco/severidade para o e-mail.

## 5. Preço único R$ 19

`payments/models.py`: `PRICE_AMOUNT = 1900`, `PRICE_DISPLAY = "R$ 19"`.
`POST /payment/create` cobra sempre 1900. `PRICING`/`PRICE_TIERS` por setor ficam
**só para analytics** de classificação (não definem o preço). `price_tier` na tabela
`targets` é mantido.

## 6. Scan completo + crédito de re-scan pós-pagamento

Ao confirmar o pagamento (webhook **ou** polling), `_maybe_send_report_email`
(idempotente via `report_email_sent`): (1) concede **1 crédito de re-verificação**
ao `buyer_email` (`store.grant_rescan_credit`), (2) roda o scan **completo (29)** e
envia os 2 PDFs (`_send_report_email_task` → `get_or_scan(full=True,
ingest_source='paid')`).

Tabela `scan_credits` ganhou **`rescan_credits`** (+ `ALTER` idempotente).
Métodos: `grant_rescan_credit`, `consume_rescan_credit`, `get_last_scan_score`.

## 7. Re-verificação ("retorno médico")

- `POST /scan/check-credit` → `{has_free_scan, same_url_scanned, free_scans_used,
  rescan_credits, can_rescan}`.
- `POST /scan/request-code` — com `rescan_credits>0`, libera o código mesmo já
  tendo escaneado (é a re-verificação paga, não o gratuito).
- `POST /scan/rescan {email, code, url}` — valida o código, **consome 1 crédito**,
  roda o scan completo, devolve o resultado completo + **comparação antes/depois**
  (`{old_score, new_score, delta, evolution}`) + um **scan token `full`**.
- O token `full` (HMAC, claim `full:true`) autoriza os PDFs sem cobrança:
  `/report/*` aceita `scan_token` (`_has_full_scan_token`).

## 8. Frontend

- **`Landing.jsx`** — hero "Analisamos 29 pontos… Scan básico gratuito + relatório
  completo por R$ 19". `onSubmit` chama `check-credit`: com `can_rescan`, entra no
  fluxo de **re-verificação** (código → `/scan/rescan` → `/result` com a comparação).
- **`Result.jsx`** — score + semáforo + `problemLine` (15 pontos, N vulnerabilidades)
  + `risk_summary` genérico + lista dos 15 (✅/❌, 🔒 nas falhas) + lista dos 14
  bloqueados (🔒) + CTA **"Fazer scan completo — R$ 19"**. Sem risco detalhado, sem
  `SeverityChips`. Quando `is_full` (re-verificação/admin): mostra o status real dos
  29, o banner de comparação e o download dos PDFs (via scan token). LGPD como rodapé.
- **`Payment.jsx`** — "Relatório completo — R$ 19" + nota da re-verificação inclusa.
- **`Report.jsx`** — bloco informando a re-verificação gratuita incluída.
- **`lib/api.js`** — `rescanScan()`, `checkCredit` com `can_rescan`, `reportUrl`
  anexa o scan token guardado.

## 9. Testes

- **`tests/test_kl27_funnel.py`** (novo, 9): split 15/29, `discover_checks(full)`,
  `CHECK_META.paid`, chave de cache por tier, preço único, payload gratuito
  (locked + sem risco), payload full, token `full` round-trip, `_evolution_label`.
- **Atualizados** ao novo contrato: `test_get_or_scan` (tiering + assinaturas com
  `full`), `test_scan_verification` (mocks com `full`), `test_alert_worker`
  (payload sem severidade/risco), `test_notifier` (CTA "Veja o relatório", assunto
  neutro, sem preço), `test_rescan_worker` (assunto de evolução, `run_scan(full)`).
- **Suíte:** `292 passed, 1 skipped` (offline). Build do frontend (Vite) OK.

## 10. Deploy e retomada dos alertas

⚠️ **Ordem obrigatória.** Só depois do deploy e da validação em produção:

```bash
# na VM
rm -f /opt/klarim/STOP_ALERTS
sudo docker compose restart discovery
# flush do cache de scan (tiering mudou os resultados cacheados) — KL-27/KL-22
sudo docker compose exec -T redis sh -c "redis-cli --scan --pattern 'scan:*' | xargs -r redis-cli del"
```

Os alertas retomam com o **novo formato** (assunto neutro, sem preço, CTA "Veja o
relatório"). **Não retomar antes da validação.**

## 11. Arquivos

**Novos:** `tests/test_kl27_funnel.py`, este relatório.
**Alterados (backend):** `scanner/checks/__init__.py`, `scanner/__init__.py`,
`scanner/runner.py`, `scanner/cache.py`, `scanner/main.py`, `api/main.py`,
`payments/models.py`, `payments/__init__.py`, `discovery/store.py`,
`discovery/alert_worker.py`, `discovery/rescan_worker.py`,
`notifier/email_client.py`, `notifier/templates/{alert,evolution_improved,
evolution_worsened,evolution_unchanged}.html`.
**Alterados (frontend):** `lib/api.js`, `lib/useSummary.js`, `pages/Landing.jsx`,
`pages/Result.jsx`, `pages/Payment.jsx`, `pages/Report.jsx`.
**Alterados (testes):** `test_get_or_scan.py`, `test_scan_verification.py`,
`test_alert_worker.py`, `test_notifier.py`, `test_rescan_worker.py`.
**Docs:** `claude.md`, `README.md`.
