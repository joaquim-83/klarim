# KL-77 — Fases 2 e 3: GCS para responses brutos + aceleração de scan

**Card:** KL-77 · **Prioridade:** Highest · **Data:** 2026-07-19
**Escopo desta entrega:** Fase 2 (bucket GCS para responses brutos) + Fase 3
(aceleração do scan rate). Fase 1 (migração da VM) já estava entregue.

---

## 1. Resumo

Cada scan gerava e **descartava** o response bruto (HTML da homepage no momento do
scan, headers crus, snapshot DNS/SSL) — o Postgres só guarda o veredito dos checks +
score. Esse dado é irrecuperável e o **KL-75** (enriquecimento expandido) vai precisar
dele para reprocessar sem re-escanear. A Fase 2 passa a **arquivar o response bruto de
cada scan** num bucket GCS Nearline privado. A Fase 3 sobe a vazão do scan worker de
**50 → 200 scans/hora** (o rate limit ético de 1 req/s por-domínio permanece intacto).

**Decisão de arquitetura central:** o response bruto **não** estava consolidado em
memória no worker (cada check faz o próprio `fetch`). Mas o `enrich_profile` — que roda
logo após `save_scan` — **já busca** a homepage (headers/html/status) e o DNS (MX/NS).
Reusamos esse fetch (**sem request extra**): `enrich_profile(capture_raw=True)` devolve o
response ao worker. O **SSL vem do cache do `tls_analyzer`** (warm logo após os checks
TLS do próprio scan). O caminho público/anônimo (`/scan/summary`) passa
`capture_raw=False` — nada muda lá, nem a leitura TLS.

---

## 2. O que foi implementado (código)

### Fase 2 — arquivamento no GCS

- **`scanner/gcs_archive.py` (novo)** — módulo de upload, puro e testável:
  - `archive_object_path(scan_id, now)` → `YYYY/MM/DD/{scan_id}.json.gz` (particiona por dia).
  - `build_archive_payload(...)` → dict com `target_id, scan_id, domain, url, timestamp,
    http_status, response_time_ms, headers, html, html_size_bytes, dns, ssl`
    (`html_size_bytes` = bytes UTF-8, não caracteres).
  - `serialize_payload(...)` → JSON UTF-8 + gzip (`default=str` cobre os datetimes do
    certificado sem quebrar).
  - `archive_scan_response(...)` → **fire-and-forget**: bypass total se `GCS_ENABLED=false`;
    client GCS **lazy** (import de `google.cloud.storage` só no 1º upload); upload em
    **thread** (`asyncio.to_thread`, não prende o event loop); **qualquer exceção é logada
    e engolida** (o scan já está no Postgres).
  - `get_archive_stats(...)` → saúde do dia (habilitado, bucket, arquivos, bytes, média,
    último upload, erros). Contadores por dia no **Redis** (`klarim:gcs:*`, TTL 48h) — para
    serem visíveis à API/MCP, que rodam em **outro processo** (container `api`, não `worker`)
    — com espelho em memória para o caminho sem Redis.

- **`scanner/enrichment.py`** — `enrich_profile` ganhou `capture_raw: bool = False`.
  Quando `True`, monta o `response_data` (headers/html/dns/ssl/status/tempo) **antes** do
  profiler/IA (sobrevive a falha dessas etapas) e o retorna. O SSL usa
  `tls_analyzer.get_tls_info` (cache-hit). Mudança **aditiva**: o retorno antes era `None`,
  os chamadores existentes ignoram-no.

- **`scanner/main.py` (worker loop)** — após `save_scan` + `update_scan_result`, chama
  `enrich_profile(capture_raw=True)` e, com `scan_id`+`raw`, `archive_scan_response(..., redis=client)`.

- **`api/main.py`** — `GET /admin/gcs-archive/stats` (admin-only, prefixo `/admin`) +
  bloco `gcs_archive` no `GET /system/status` (best-effort, erro nunca derruba o status).

- **`mcp_server/tools/system.py`** — nova MCP tool **`get_gcs_archive_stats`**.

- **`requirements.txt`** — `google-cloud-storage>=2.14.0,<3.0`.

- **`docker-compose.yml`** — comentário no serviço `worker` + mount opcional (comentado)
  da key JSON. As vars `GCS_*`/`GOOGLE_APPLICATION_CREDENTIALS` vêm do `env_file: .env`.

- **`.env.example`** — bloco GCS (`GCS_ENABLED`, `GCS_BUCKET`, `GOOGLE_APPLICATION_CREDENTIALS`).

### Fase 3 — aceleração do scan rate

- `WORKER_MAX_SCANS_PER_HOUR` **50 → 200** (default do `.env.example`; editável ao vivo no
  painel/MCP `set_scan_config` — `admin_settings` > `.env`). O worker relê o throttle a cada
  ciclo (`worker_control` > `admin_settings` > env). **Rate limit por-domínio (1 req/s)
  inalterado** — a paralelização só sobrepõe domínios distintos.

---

## 3. Testes

- **`tests/test_kl77_gcs_archive.py` (novo, 18 testes)** — caminho do objeto; payload com
  todas as chaves; `html_size_bytes` em bytes UTF-8; payload tolera response vazio;
  serialize gzip round-trip; serialize com datetime; parsing de `GCS_ENABLED`; **bypass com
  `GCS_ENABLED=false`** (não toca no client); upload feliz (comprime + `content_type
  application/gzip` + caminho correto + conteúdo recuperável); **exceção no upload engolida**;
  contadores no Redis (sucesso e erro); `get_archive_stats` do Redis e fallback em memória;
  captura no `enrich_profile` (`capture_raw=True` retorna o response; `False` retorna `None`;
  sobrevive a falha de TLS com `ssl={}`).
- **`tests/test_mcp_server.py`** — `get_gcs_archive_stats` no registro + teste focado do shape.

**Resultado local:** `pytest` → **1181 passed, 1 skipped** (o skip é o teste online). Suíte
KL-77 + MCP + enrichment + system + admin_config: **72 passed**.

---

## 4. Revisão de segurança (regra de 2026-07-15)

- **Service account com `objectCreator` APENAS** (nunca admin, nunca leitura pública) —
  documentado no runbook; ADC via SA da VM é o caminho preferível (sem key file no disco).
- **Bucket privado** (uniform bucket-level access, sem ACL pública).
- **Key JSON (se usada) montada read-only** (`:ro`) no container.
- **Endpoint `/admin/gcs-archive/stats` sob o prefixo `/admin`** → JWT admin (middleware);
  a MCP tool passa pela auth própria fail-closed. **Nenhum dado sensível exposto** (só
  contadores agregados; sem `contact_email`/PII).
- **Rate limit por-domínio 1 req/s inalterado**; User-Agent honesto do Klarim mantido.
- O arquivo bruto contém HTML/headers **públicos** já buscados no scan passivo — nenhuma
  área autenticada, nenhum payload de ataque.

---

## 5. Regra de dados (KL-57)

Contadores para calibrar/monitorar: `files_today`, `bytes_today`, `avg_bytes` (tamanho
médio comprimido por scan), `errors_today` + `last_error`, `last_upload_at` — via Redis
(`klarim:gcs:*`, TTL 48h), expostos no `get_gcs_archive_stats` e no status do sistema. A
taxa de scan efetiva (scans/hora real) sai do `get_system_status` (`scan.completed_today`).

---

## 6. Runbook na VM (pós-deploy — ações do operador)

> Estas ações rodam **na VM `klarim-prod`** (não dá para executá-las daqui). Detalhe
> completo em `docs/DEPLOY.md §7`.

```bash
PROJECT=project-b08050df-fa4e-49ac-919

# 1. Bucket Nearline privado, mesma região da VM.
gcloud storage buckets create gs://klarim-raw \
  --location=us-central1 --default-storage-class=NEARLINE \
  --uniform-bucket-level-access --project=$PROJECT

# 2. Auth PREFERÍVEL — ADC via SA da VM (sem key file). Descubra a SA:
curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
gcloud storage buckets add-iam-policy-binding gs://klarim-raw \
  --member="serviceAccount:<SA-DA-VM>" --role="roles/storage.objectCreator" --project=$PROJECT
#    (deixe GOOGLE_APPLICATION_CREDENTIALS vazio no .env)

# 3. .env da VM: GCS_ENABLED=true, GCS_BUCKET=klarim-raw, WORKER_MAX_SCANS_PER_HOUR=200
docker compose up -d worker     # recria (relê env_file); NÃO usar restart

# 4. Confirmar o 1º upload (após alguns scans):
docker compose exec worker python -c "from google.cloud import storage; c=storage.Client(); \
b=c.bucket('klarim-raw'); print('exists:', b.exists()); \
[print(' ', x.name, x.size) for x in list(b.list_blobs(max_results=5))]"

# 5. Aceleração — via MCP set_scan_config max_per_hour=200, ou pelo .env acima.

# 6. Monitorar carga após 1h a 200/h:
docker stats --no-stream ; free -h ; df -h / ; redis-cli LLEN klarim:scan_queue
#   CPU<70% e RAM<80% → considerar 300/h. CPU>80% ou RAM>85% → manter 200/h.
```

---

## 7. Validação de sucesso (status)

| # | Critério | Status |
|---|---|---|
| 1 | Bucket `klarim-raw` Nearline | ⏳ operador (runbook §6) |
| 2 | SA com `objectCreator` (e nada mais) | ⏳ operador |
| 3 | Worker faz upload após cada scan | ✅ código (worker → `archive_scan_response`) |
| 4 | Path `YYYY/MM/DD/{scan_id}.json.gz` | ✅ código + teste |
| 5 | Conteúdo: headers, html, dns, ssl, target_id, domain | ✅ código + teste |
| 6 | Upload falho não trava o scan | ✅ fire-and-forget + teste (bucket inexistente) |
| 7 | `GCS_ENABLED=false` desativa tudo | ✅ código + teste |
| 8 | Scan rate 200/h | ✅ default `.env.example`; ⏳ aplicar no `.env` da VM |
| 9 | Fila Redis estável | ⏳ observar pós-aceleração |
| 10 | CPU<70% / RAM<80% após 1h a 200/h | ⏳ observar pós-aceleração |
| 11 | ≥10 testes novos | ✅ **18** (+1 MCP) |
| 12 | CI verde | ⏳ após push |

**Legenda:** ✅ entregue no código/testes locais · ⏳ ação de operador/observação na VM.

---

## 8. Arquivos alterados

```
scanner/gcs_archive.py            (novo)
scanner/enrichment.py             (capture_raw + retorno do response)
scanner/main.py                   (worker → arquiva pós-enrich)
api/main.py                       (/admin/gcs-archive/stats + bloco no status)
mcp_server/tools/system.py        (tool get_gcs_archive_stats)
requirements.txt                  (google-cloud-storage)
docker-compose.yml                (worker: comentário + mount opcional da key)
.env.example                      (GCS_* + WORKER_MAX_SCANS_PER_HOUR=200)
tests/test_kl77_gcs_archive.py    (novo, 18 testes)
tests/test_mcp_server.py          (tool no registro + teste focado)
CLAUDE.md · docs/DEPLOY.md · docs/ARCHITECTURE.md · docs/API.md   (documentação)
claude/reports/KL-77_fases2_3_gcs_aceleracao.md                   (este relatório)
```
