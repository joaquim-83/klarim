# KL-152 (Prompt 3/3) — Enterprise workflow: due diligence, comparativo, PDF, monitoramento

## Contexto

Fecha o KL-152. Implementa a avaliação de fornecedores (Enterprise): CRUD + scan de terceiro
**redigido**, status vs thresholds, PDF comparativo, notificação opt-in ao fornecedor, monitoramento
contínuo e export PDF de run. Pré-requisito: plano com `scan_third_party=true` (KL-151 P4).

## Schema (2 tabelas)

- **`gate_vendors`** — fornecedor de um Enterprise (name/url/domain, `status` pending|approved|
  attention|rejected, `approval_threshold`/`critical_threshold`, `last_scan_*`, `notify_vendor`,
  `monitor_enabled`/`monitor_interval_days`/`next_monitor_at`, notes). Índice parcial de monitoramento.
- **`gate_vendor_scans`** — histórico (score/passed/critical/high/medium/status/`results` REDIGIDOS/
  `summary` de contagens). Validado contra Postgres 16 real (dev).

## Lógica pura — `security_gate/vendor.py`

`calculate_vendor_status(score, critical, threshold, max_critical)` (críticos acima do máx → reprovado;
score≥threshold → aprovado; score≥threshold-20 → atenção; senão reprovado). `build_vendor_scan_payload`
serializa + **redige** (credenciais/exposição/api → `[redacted]` + detalhe genérico) + `vendor_summary`
(contagens: exposed_files/credentials/unauth_endpoints) + `vendor_categories` (categoria→status, sem paths).

## Endpoints — `api/gate.py` (todos `_require_enterprise` → 403 sem `scan_third_party`)

`POST /gate/vendors` (cria + 1º scan), `GET /gate/vendors`, `GET/PUT/DELETE /gate/vendors/{id}`,
`POST /gate/vendors/{id}/scan`, `POST /gate/vendors/report` (PDF comparativo → link 1h),
`GET /gate/vendors/report/{report_id}`, `GET /gate/runs/{id}/pdf` (export de run próprio).
Núcleo compartilhado `run_vendor_scan(account_id, vendor)` — engine no servidor → redige → status →
persiste scan + atualiza vendor (+ reprograma `next_monitor_at`) → audit `vendor_scan` → notifica (opt-in).

## Redação (o que o Enterprise vê × não vê)

Score, categorias com status, nomes dos checks e **contagens** ("2 arquivos expostos", "0 credenciais
detectadas") — SIM. Paths exatos, valores de credencial, endpoints — NÃO (redigidos no servidor, nunca
chegam ao cliente). Confirmado no browser: o detalhe do vendor mostra as contagens + badges de
categoria, e um teste garante que nenhum path/segredo vaza no JSON.

## Notificação opt-in ao fornecedor

`notify_vendor=true` → `_notify_vendor` (fire-and-forget): busca o `contact_email` do domínio na base
(uso interno, NUNCA exposto); sem contato → pula. E-mail **transacional** `klarim@klarim.net`
(`send_vendor_assessment`, texto puro, "não é reclamação", CTA `/site/{domain}`). **Dedup 1/scan** via
Redis (`gate:vendor_notify:{vendor}:{scan}`, NX). Funil B2B invertido.

## Monitoramento — `discovery/vendor_monitor_worker.py`

`VendorMonitorWorker` (registrado no `_run_all`, 1x/dia): `get_vendors_due_for_monitoring` → re-scan
via `run_vendor_scan` (reprograma o próximo) → se `score < approval_threshold` → alerta o Enterprise
(`send_vendor_score_drop`, transacional). Deps injetáveis (scan_fn/mailer_fn) → testável sem rede.

## PDF — `reporter/gate_report.py` (WeasyPrint)

`build_vendor_report_html` (PURO/testável) + `generate_vendor_report_pdf` (render em thread). Template
com cabeçalho Klarim + **CNPJ do Enterprise** + resumo executivo + tabela comparativa (Score/Críticos/
Altos/Status) + recomendação (nomeia os reprovados) + detalhe por fornecedor (categorias + contagens) +
**disclaimer** ("não constitui pentest"). PDF guardado em **base64** no Redis (TTL 1h; o cliente usa
`decode_responses=True` → bytes crus não fariam round-trip) com fallback in-memory. Validado ao vivo:
PDF de 12,9KB, `%PDF` válido.

## Frontend — `web/src/components/dashboard-v2/GateVendors.jsx`

Seção "🏢 Avaliação de Fornecedores" no `GatePortal` — **self-hide** para quem não é Enterprise (o
`GET /gate/vendors` responde 403). Tabela (nome/URL/score+semáforo/status/ações 📋🔄📧) + modal "Avaliar
novo fornecedor" (nome/URL/thresholds/notify/monitor) + detalhe expansível (contagens redigidas +
badges de categoria) + "Gerar relatório comparativo (PDF)". Tokens theme-aware (KL-87).

## Testes

- **`tests/test_kl152_vendors.py`** (+20): status (paramétrico), redação do payload, CRUD, gate
  Enterprise (403), re-scan, detalhe sem paths, notificação (envia/dedup/sem-contato), monitoramento
  (scan+alerta no drop / sem alerta ok / reprograma next_monitor), report endpoint (HTML com CNPJ +
  disclaimer, round-trip do PDF).
- **2216 pytest passed, 1 skipped**; `test:unit` 166; `npm run build` OK.
- **Validado no browser** (`docker-compose.dev.yml`, conta tornada Enterprise): seção só aparece p/
  Enterprise, plano 18/18, modal, scan real de klarim.net → Score 70 🟡 Atenção, detalhe redigido
  (contagens, não paths), PDF comparativo válido (WeasyPrint, 200/application/pdf), zero erro no console.

## Docs

`claude.md` §9 (**KL-152 COMPLETO**), `docs/API.md` (endpoints de vendor + report + run PDF).

## KL-152 — COMPLETO

P1 (fix visual + onboarding) · P2 (docs de integração) · P3 (Enterprise workflow). Fechado no Jira
após o deploy verde (transition 41).
