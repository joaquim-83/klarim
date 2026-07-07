# KL-11 — Banco de alvos + Discovery Worker (CT logs + fingerprint + filtro por e-mail)

- **Card Jira:** KL-11
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-9 (cache), KL-8 (e-mail)
- **Commit:** `feat(KL-11): add Discovery Worker with CT logs, fingerprinting, and email filter`

---

## Objetivo

Sair do modelo passivo (esperar o cliente) para o ativo: descobrir sites `.com.br`
recém-certificados, filtrar por presença de e-mail, registrar como alvo e
escanear. **Regra inviolável:** sem e-mail extraível, não escaneia.

## Banco de dados (Parte 1)

Tabelas `targets` e `scans` (schema do card) criadas no `TargetStore.ensure_schema`
(mesmo padrão de `payments`/`recovery_tokens`), com índices. Conexão por
`POSTGRES_*` (imune ao `/` na senha — dívida do KL-3).

## Módulo `discovery/` (Parte 2)

- **`ct_client.py`** — crt.sh. Primário: **Postgres público** (`crt.sh:5432`),
  padrão reverso `rb.moc.%` (usa índice reverso, evita `LIKE '%...'`), 3 tentativas
  + `wait_for` 45s; fallback **JSON API**. `_filter`: descarta wildcards,
  subdomínios de infra (`mail./api./cdn./staging.`…), não-`.com.br`, e reduz ao
  **domínio registrável** com dedup.
- **`fingerprint.py`** — duda, wix, squarespace, shopify, wordpress, cra (ou unknown).
- **`contact.py`** — `extract_email`: mailto → texto → meta → fallback `/contato`.
  Descarta genéricos (noreply, webmaster, postmaster) e domínios de terceiros
  (duda.co, wixpress, shopify…); **prefere e-mail do mesmo domínio** do site.
- **`classifier.py`** — `classify_sector` (contagem de keywords) → `(setor, price_tier)`.
- **`store.py`** — `TargetStore` com todos os métodos do card (register/list/stats/
  get_targets_for_scan/save_scan/…).
- **`worker.py`** — `DiscoveryWorker.run_cycle()`: CT → dedup vs registrados → por
  domínio (pausa 2s): fetch → fingerprint → e-mail → setor → registra → enfileira.
  Sem e-mail → `sem_contato`, não enfileira. Loga estatísticas. `start()` = loop 6h.

## Integração com o scanner (Parte 3)

`scanner/main.py --worker` reescrito **async**: consome `{target_id, url}` (ou URL
simples, retrocompat), escaneia, **cacheia no formato do KL-9**, salva em `scans`
+ atualiza `targets` (score, data, status→`scanned`), rate limit
`WORKER_MAX_SCANS_PER_HOUR` (50/h → 72s entre scans).

## Container (Parte 4)

Serviço **`discovery`** no `docker-compose.yml` (`python -m discovery.worker`,
`DISCOVERY_BATCH_SIZE`, `DISCOVERY_INTERVAL_HOURS`).

## API (Parte 5)

`GET /targets` (status/platform/sector/limit/offset), `GET /targets/stats`,
`POST /targets/add {url}`, `POST /targets/{id}/scan`, `GET /scans`, `GET /scans/{id}`.

## Validação

- `tests/test_discovery.py` — fingerprint (6 plataformas), classifier (setores +
  tiers), contact (junk, best same-domain, mailto/texto), filtro do CT (wildcard,
  infra, dedup registrável). Suíte total: **49 passed, 1 skipped**.
- **Offline ao vivo:** fingerprint real do Verdegreen → `duda`; extração de e-mail
  com priorização de mesmo domínio.
- **Produção (VM) — pipeline validado ponta-a-ponta:**
  - `POST /api/targets/add` (verdegreen) → `target_id=1`, enfileirado.
  - O **scan worker** (async, nova versão) consumiu `{target_id,url}`, escaneou,
    salvou em `scans` e atualizou `targets`: `GET /api/scans` → `verdegreen 86
    verde, 2 fails`; `GET /api/targets/stats` → `{by_status:{scanned:1}}`.
  - Containers `discovery` e `worker` no ar; tabelas `targets`/`scans` criadas.
- **crt.sh indisponível no momento (externo, não é o código):** diagnóstico na VM
  e local — o **Postgres público** (`crt.sh:5432`) **rejeita a conexão na hora**
  (SSL fechado em ~0,9s, até para uma query trivial); a **JSON API responde a
  consultas específicas** (verdegreen → 178 certs em 2,3s) mas **dá timeout na
  consulta ampla `%.com.br`** necessária para descoberta. O ciclo rodou e
  **degradou com elegância** (`ct_domains: 0`, sem crash). Quando o Postgres do
  crt.sh voltar, a descoberta ampla funciona; enquanto isso, alvos entram via
  `POST /api/targets/add`.

## Critérios de aceite

- [x] Tabelas `targets` e `scans`.
- [x] `ct_client.py` (crt.sh, Postgres + JSON).
- [x] `fingerprint.py` (6 plataformas).
- [x] `contact.py` (mailto, regex, meta, filtro de junk).
- [x] `classifier.py` (setor + price_tier).
- [x] `worker.py` (ciclo completo).
- [x] Sem e-mail → `sem_contato`, não enfileira.
- [x] Scan worker salva em `scans` + atualiza `targets`.
- [x] Container `discovery` no compose.
- [x] API `/targets`, `/targets/stats`, `/targets/add`.
- [x] Ciclo validado na VM — pipeline OK (manual add → scan → persist); crt.sh
  amplo indisponível no momento (Postgres deles rejeitando conexões), degradou ok.
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- **crt.sh confiável:** avaliar fonte alternativa de CT (certstream, certspotter)
  ou uma janela/consulta mais estreita; hoje o volume por ciclo depende da sorte
  com o crt.sh.
- Deduplicar e-mails de contato entre alvos (mesmo dono, vários sites).
- Alert Worker (KL-12) consumirá os `targets` com `status='scanned'` e FAILs.
