# KL-17 — Integração completa: scans públicos no painel + fluxo admin scan→enviar + rastreabilidade

- **Card Jira:** KL-17
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-14 (dashboard/JWT), KL-11 (targets/scans), KL-9 (cache), KL-7/8 (pagamento/e-mail)
- **Commit:** `feat(KL-17): full integration — public scans in dashboard, admin scan+send, traceability`

---

## Objetivo

Fechar os gaps que impediam a operação ponta-a-ponta: scans públicos invisíveis ao
painel, admin sem fluxo scan→enviar, ausência de origem/rastreabilidade, falta de
reenvio e de vínculo pagamento↔alvo.

## Parte 1 — Scans públicos gravam no banco

`GET /scan/summary` continua devolvendo o semáforo **na hora** (do cache, KL-9),
mas agora, em cache **miss**, dispara em **background** (`_spawn`) a ingestão no
Postgres — o visitante não espera. `discovery/ingest.py::ingest_scan(store, url,
report, source)`: faz um GET do HTML, roda fingerprint + setor + e-mail (mesmas
funções do Discovery), registra/atualiza o `target` (UPSERT por URL, não duplica) e
salva o `scan`. Reutilizado pelo fluxo admin (síncrono).

## Parte 2 — Origem do scan

Coluna nova **`scans.source`** (`public|discovery|admin|manual|rescan`,
`ALTER … ADD COLUMN IF NOT EXISTS`). A fila de scan passou a carregar a origem
(`{target_id, url, source}`): Discovery → `discovery`; `POST /targets/add` →
`manual`; `POST /targets/{id}/scan` → `admin`; rescan worker → `rescan`; ingestão
pública/admin gravam direto com a origem. `save_scan`, `list_scans` e `list_targets`
ganharam `source` (projeção + filtro).

## Parte 3 — Fluxo admin (`/painel/escanear` + `POST /admin/scan-and-report`)

Endpoint único (JWT): escaneia (cache/fresh) → `ingest_scan(source='admin')` →
devolve `{target_id, scan_id, score, semaphore, checks, platform, sector,
contact_email, …}` → se `send_email`, envia **alerta** (sem PDF) ou **relatório**
(2 PDFs) para `email_to` ou o `contact_email` extraído. Tela **Escanear**: input →
resultado inline (semáforo + checks + plataforma/setor/e-mail + baixar PDFs) → modal
de envio (e-mail pré-preenchido, alerta vs relatório, confirmação "✅ Enviado").

## Parte 4 — Reenvio (JWT, ignora throttle)

`POST /admin/resend-alert {target_id}` (reusa `send_alert_for_target`),
`POST /admin/send-report {target_id, email_to?}` (gera + envia os 2 PDFs),
`POST /admin/resend-payment {charge_id}` (reusa `_send_report_email_task` do
pós-pagamento). Botões no **AlvoDetalhe** (Reenviar alerta / Enviar relatório
completo) e na seção de Pagamentos (Reenviar relatório quando `report_email_sent=
false` ou `email_status='failed'`).

## Parte 5 — Rastreabilidade (frontend)

Badge de **Origem** (`SourceBadge`, cor por tipo) + dropdown de filtro nas telas de
**Scans** e **Alvos**.

## Parte 6 — Vínculo pagamentos ↔ alvos

`payments` não muda; casamos por URL. `GET /payments/list` traz `target_id`
(`map_urls_to_target_ids`, 1 query) → a tela Pagamentos linka o site → `/painel/
alvos/:id`. `GET /targets/{id}/payments` (via `list_charges_by_url`) alimenta a
seção "Pagamentos" do AlvoDetalhe.

## Parte 7 — Sidebar

`Visão geral · Escanear (novo) · Alvos · Scans · Alertas · Pagamentos · Re-scans ·
Configurações · Sair`. (De quebra, o Config passou a mostrar
`DISCOVERY_INTERVAL_MINUTES` — corrigido desde o KL-15.)

## Validação

- **Testes** (`tests/test_ingest.py`, 2 casos): `ingest_scan` registra o alvo
  (UPSERT), enriquece (setor `hotel`, e-mail do mailto), salva o scan **com a
  origem certa** e atualiza `last_scan_*`; caminho sem HTML degrada com elegância.
  Ajustado o FakeStore do rescan para o novo `source`. **Suíte total: 91 passed,
  1 skipped.** Build do frontend OK (tela Escanear code-split).
- **Produção (VM):** _validação pós-deploy — ver seção abaixo._

## Validação em produção (pós-deploy)

- [ ] Scan público em `klarim.net` → aparece em Scans com badge **público**; o
      alvo aparece em Alvos com plataforma/setor/e-mail.
- [ ] `POST /admin/scan-and-report` (via painel Escanear) → resultado inline;
      `send_email` entrega o alerta/relatório.
- [ ] Reenvio (`/admin/resend-alert`, `/admin/send-report`) entrega.
- [ ] Origem correta nos scans (público/admin/discovery/manual/rescan).
- [ ] Pagamentos linkam para o alvo; AlvoDetalhe mostra os pagamentos.
- [ ] Scan duplicado atualiza o alvo (não cria duplicata).

## Critérios de aceite

- [x] Scans públicos gravam em `targets`+`scans` (assíncrono, não bloqueia).
- [x] Coluna `scans.source`.
- [x] Tela "Escanear" (input → scan → inline → enviar e-mail).
- [x] `POST /admin/scan-and-report`.
- [x] Reenvio de alertas e relatórios pelo painel.
- [x] Badge de origem + filtro (Scans e Alvos).
- [x] Vínculo pagamentos ↔ alvos.
- [x] Sidebar com "Escanear".
- [x] Scan duplicado faz UPSERT (não duplica alvo).
- [x] Documentação (`claude.md` §19, `README.md`).
- [x] Relatório em PT-BR.
- [ ] Deploy + validação em produção + commit/push.

## Follow-ups

- Vínculo pagamento↔alvo é por URL exata; normalizações diferentes (com/sem `www`)
  podem não casar — hoje a URL escaneada e a paga são a mesma string.
- Dívida do KL-3 (stores por `POSTGRES_*`) segue de pé.
