# Klarim — Deploy, CI/CD e Variáveis de Ambiente

> Extraído de `claude.md` + `docker-compose.yml`, `deploy/*.sh`, `.env.example` e
> `.github/workflows/deploy.yml`. Histórico completo em `docs/HISTORY.md`.

## 1. Infraestrutura de produção

| Campo | Valor |
|-------|-------|
| Instância | `klarim-prod` (migração KL-77 Fase 1, 2026-07-19) |
| Zona | `us-central1-a` |
| Projeto | `project-b08050df-fa4e-49ac-919` |
| Diretório | `/opt/klarim` |
| Máquina | GCP Compute Engine `e2-standard-4` (4 vCPU, 16GB) |
| Disco | 200GB `pd-ssd` |
| IP | estático `34.135.194.208` (reserva `klarim-static-ip`) |
| VM antiga | `instance-20260706-112125` (e2-medium, IP efêmero 35.238.72.10) — **standby de fallback** |

```bash
gcloud compute ssh --zone "us-central1-a" "klarim-prod" \
  --project "project-b08050df-fa4e-49ac-919"
```

O `.env` de produção vive **apenas na VM** (`/opt/klarim/.env`) — nunca no git.

> **Migração de VM (KL-77 Fase 1):** IP estático → criar VM (`e2-standard-4`, 200GB
> pd-ssd, **sem** `enable-oslogin` — o SSH do CI usa injeção de chave por metadata) →
> Docker + clone → `.env` copiado byte-idêntico → `pg_dump -Fc | pg_restore` por stream
> SSH → comparar contagens → subir serviços → **copiar `/etc/letsencrypt` da VM antiga**
> (cert LE portável, o nginx só sobe HTTPS com o cert presente + `DOMAIN` no .env) →
> validar → **trocar DNS no Cloudflare** (registros A → novo IP, proxy laranja on) +
> atualizar secret `GCP_INSTANCE_NAME` → **handoff dos workers** (parar `discovery` na
> antiga, iniciar na nova → só a produção emaila) → VM antiga 24h em standby. Reverter =
> DNS de volta para 35.238.72.10 + reiniciar workers da antiga.

## 2. Deploy manual

```bash
# na VM (operação root — mesmo caminho do CI):
sudo bash /opt/klarim/deploy/deploy.sh
```

`deploy.sh`: marca `/opt/klarim` como `safe.directory` → `git pull` → `docker compose
build` (site **no ar** durante o build) → `docker compose up -d --remove-orphans`
(recria só o que mudou) → prune → `docker compose ps` → health checks
(`localhost:8000/health` + `:4321/` Astro). Downtime ~10–30s (só o recreate;
Postgres/Redis nem são tocados). ⚠️ O script se auto-atualiza no `git pull`, mas a
mudança só vale **no deploy seguinte** (o bash já leu o arquivo no início).

> **Nota:** o build de `api`/`web` na `e2-standard-4` (4 vCPU) leva **~5–15 min**
> (era 10–50 min na e2-small/medium antiga). Lento ≠ travado.

## 3. CI/CD automático (`.github/workflows/deploy.yml`)

A todo push para `main`, com `deploy` dependente de `needs: [test, build-web, nginx-check]`:

1. **`test`** — Python 3.12, `pip install -r requirements.txt`, `pytest`. Falhou → bloqueia.
2. **`build-web`** — `npm ci` + `npm run build` do Astro (quebra de build não vai a prod).
3. **`nginx-check`** — `nginx -t` no `http.conf` e no `https.conf.template` renderizado
   (cert dummy). Config inválida bloqueia o deploy (**não** derruba o site).
4. **`deploy`** — autentica no GCP via **Workload Identity Federation** (OIDC, keyless,
   sem chave de SA), conecta via `gcloud compute ssh` e roda `deploy/deploy.sh`.

**GitHub Secrets** (configurados manualmente): `GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`,
`GCP_PROJECT_ID`, `GCP_INSTANCE`, `GCP_ZONE`. Nunca commitar chave SSH / SA key / `.env`.

## 4. HTTPS / Let's Encrypt

- **Emitir (uma vez, após o DNS apontar):** `sudo bash /opt/klarim/deploy/setup-https.sh
  <dominio>` (webroot, sem downtime; grava `DOMAIN=` no `.env`; recria o `web` em HTTPS).
- **Renovação:** automática — `deploy.sh` roda `certbot renew` a cada deploy + timer do certbot.
- **Subdomínios cobertos** pelo mesmo cert (SAN): `klarim.net`, `www.klarim.net`,
  `painel.klarim.net`. `mta-sts.klarim.net` é servido via Cloudflare.
- **Firewall GCP:** `klarim-allow-http` (80) + `klarim-allow-https` (443), tag `http-server`.
- O Nginx é **self-healing**: sem `DOMAIN`/cert ⇒ HTTP; com cert ⇒ HTTPS (redirect 80→443).

## 5. Variáveis de ambiente

Todas vivem em `/opt/klarim/.env` na VM (serviços usam `env_file: .env`). ⚠️ **Use as
`POSTGRES_*` individuais, não `DATABASE_URL`** (a senha base64 contém `/`).

### Banco / fila
| Var | Uso |
|---|---|
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | Postgres (individuais, imune a `/` na senha) |
| `REDIS_URL` | Redis (fila `klarim:scan_queue`, cache, heartbeat) |
| `KLARIM_SCAN_QUEUE` | nome da fila (default `klarim:scan_queue`) |

### API / scanner
| Var | Uso |
|---|---|
| `API_HOST` / `API_PORT` | bind da API (interno; público é o Nginx) |
| `CORS_ORIGINS` | origens permitidas |
| `SCAN_TIMEOUT` / `SCAN_RATE_LIMIT` | 10s / 1 req/s por domínio |
| `SCAN_MAX_CONCURRENCY` | paralelismo do runner (default 12) |
| `PAYWALL_ENABLED` | **default `false`** (freemium: 48 checks grátis) |
| `KLARIM_DEV_MODE` | liga `/docs` + modo livre de PDF |
| `KLARIM_CACHE_DIR` / `KLARIM_CVE_CACHE` / `KLARIM_CVE_CACHE_TTL` | caches de CVE/CNAE |
| `NVD_ENABLED` / `NVD_API_KEY` | NVD/NIST (default off) |
| `GOOGLE_SAFE_BROWSING_KEY` | check 29 (opcional; sem ela → INCONCLUSO) |
| `KLARIM_API_URL` | API interna p/ os fetches SSR do Astro (`http://api:8000`) |

### Pagamento (AbacatePay) — compra de relatório (KL-27) + assinatura (KL-44 P6)
| Var | Uso |
|---|---|
| `ABACATEPAY_API_KEY` | chave (`abc_dev_` = sandbox); vazia = modo livre |
| `ABACATEPAY_WEBHOOK_SECRET` | query-secret do webhook (registrar `.../webhooks/abacatepay?webhookSecret=<secret>`) |
| `ABACATEPAY_HMAC_STRICT` | opcional (HMAC defense-in-depth) |

**Webhook único** para os dois fluxos: `POST /webhooks/abacatepay` trata compra de relatório
e **assinatura** (KL-44 P6) — no evento `.paid`/`.completed` ativa o plano se o `charge_id`
casar um `subscription_payments`; idempotente (só transiciona de `pending`). O selo/QR de
upgrade usa PIX **transparente** (sem checkout hospedado). Preços: Pro R$19 (1900), Agency
R$49 (4900). **Nenhum dado de cartão/PIX é armazenado.**

### Trial (KL-44 P6) — config ao vivo no painel (`admin_settings` > .env)
| Var | Uso |
|---|---|
| `TRIAL_EXPIRATION_ENABLED` | liga/desliga o downgrade automático de trials (default `true`) |
| `TRIAL_HOUR_UTC` | hora UTC em que o worker `trial` age (default `6`) |

### E-mail (Resend) — **2 domínios, nunca misturar**
| Var | Uso |
|---|---|
| `RESEND_API_KEY` | chave send-only |
| `RESEND_FROM` | remetente transacional (`seguranca@klarim.net`) |
| `ALERT_FROM_EMAIL` / `ALERT_FROM_NAME` | remetente proativo (`alerta@klarimscan.com`) |
| `RESEND_WEBHOOK_SECRET` | webhook Resend (Svix, bounce/complaint) |
| `UNSUBSCRIBE_SECRET` | HMAC do link de descadastro (`openssl rand -hex 32`) |

### Admin / JWT / MCP
| Var | Uso |
|---|---|
| `ADMIN_USER` / `ADMIN_PASSWORD` | credenciais do operador (fallback; senha real vira hash no banco) |
| `JWT_SECRET` | assina JWT admin + usuário (`typ` distingue) |
| `MCP_API_KEY` | token estático MCP (fallback; rotacionável no painel) |
| `MCP_JWT_SECRET` | OAuth MCP (default `JWT_SECRET`) |
| `MCP_ISSUER` | issuer OAuth (default `https://klarim.net`) |

### IA
| Var | Uso |
|---|---|
| `OPENAI_API_KEY` | GPT-4o mini (ausente ⇒ regex-only, fail-open) |
| `OPENAI_MODEL` | default `gpt-4o-mini` |

### Workers — Discovery
| Var | Uso |
|---|---|
| `DISCOVERY_INTERVAL_MINUTES` | ciclo (30) |
| `DISCOVERY_BATCH_SIZE` / `DISCOVERY_DOMAIN_TIMEOUT` / `DISCOVERY_WATCHDOG_SECONDS` | blindagem (KL-19) |
| `DISCOVERY_WARMUP_SECONDS` / `DISCOVERY_PAUSE_SECONDS` | ritmo |
| `CT_LOG_LIST_URL` / `CT_MAX_LOGS` / `CT_POLL_BATCH` / `CT_POLL_INTERVAL_SECONDS` / `CT_MAX_BUFFER` / `CT_SUFFIX` | CT log poller |

### Workers — Scan / Alert / Rescan / Vigília / Monitor
| Var | Uso |
|---|---|
| `WORKER_MAX_SCANS_PER_HOUR` | vazão do scan worker (**KL-77: 200 na VM `klarim-prod`**; editável ao vivo no painel/MCP `set_scan_config`) |
| `WORKER_HEARTBEAT_TTL` / `WORKER_CONTROL_FILE` | heartbeat / pausa por worker (KL-32) |
| `ALERT_DAILY_LIMIT` | teto diário de alertas proativos (warmup=30) |
| `ALERT_MONTHLY_LIMIT` | cota mensal (45k dos 50k Resend Pro) |
| `ALERT_BATCH_SIZE` / `ALERT_BATCHES_PER_CYCLE` / `ALERT_BATCH_PAUSE` / `ALERT_INTERVAL_MINUTES` | batch de alertas |
| `ALERT_VALIDATE_MX` / `ALERT_MAX_BOUNCE_RATE` / `ALERT_BOUNCE_MIN_SAMPLE` | anti-bounce (KL-24) |
| `ALERTS_STOP_FILE` | kill-switch `STOP_ALERTS` |
| `RESCAN_INTERVAL_HOURS` / `RESCAN_AGE_DAYS` / `RESCAN_BATCH_SIZE` | re-scan |
| `MONITOR_INTERVAL_DAYS` | monitoramento (sites 100/contas) |
| `VIGILIA_CYCLE_HOURS` / `VIGILIA_MAX_PER_CYCLE` / `VIGILIA_CHECK_TIMEOUT` / `VIGILIA_RDAP_PAUSE` / `VIGILIA_WARMUP_SECONDS` | vigílias (KL-44 P2) |

### Arquivamento de responses brutos no GCS (KL-77 Fase 2)
| Var | Uso |
|---|---|
| `GCS_ENABLED` | liga/desliga o arquivamento (default `true`; `false` = bypass total, sem tocar no client GCS) |
| `GCS_BUCKET` | nome do bucket (default `klarim-raw`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | **vazio = ADC** (SA da VM, preferível). Só preencher se usar key JSON montada read-only (ver `docker-compose.yml`) |

O scan worker comprime o response bruto de **cada scan** (headers, html, dns, ssl,
status, tempo — tudo já em memória do enrich, sem request extra) e faz upload para
`gs://klarim-raw/YYYY/MM/DD/{scan_id}.json.gz`. **Fire-and-forget:** falha de upload é
logada e engolida, o scan (já persistido no Postgres) nunca trava. Saúde via MCP
`get_gcs_archive_stats` / `GET /admin/gcs-archive/stats` (arquivos/bytes hoje, último
upload, erros) — contadores no Redis (chaves `klarim:gcs:*`, TTL 48h).

### Inbox (Hostinger) / Demo / Site
| Var | Uso |
|---|---|
| `HOSTINGER_WEBHOOK_TOKEN` | webhook de inbox (fail-closed) |
| `HOSTINGER_API_TOKEN` | envio via Hostinger (fase opcional) |
| `DEMO_EMAIL` / `DEMO_URL` | modo demo (teste sem cobrar; nunca apontar p/ klarim.net) |
| `DOMAIN` | domínio TLS (self-healing Nginx) |
| `SITE_BASE` | base pública p/ links (default `https://klarim.net`) |
| `APP_VERSION` / `GIT_COMMIT` | info na página de config |

## 6. Comandos pós-deploy (na VM, quando aplicável)

```bash
# Flush do cache de scan após mudança em scoring.py ou em qualquer check:
docker compose exec redis redis-cli --scan --pattern 'scan:*' | xargs -r docker compose exec -T redis redis-cli del

# Backfills / migrações idempotentes:
docker compose exec -T api python scripts/backfill_email_log.py     # KL-62 (migrate_email_log)
docker compose exec -T api python scripts/backfill_leads.py         # KL-61
docker compose exec -T api python scripts/seed_vigilias.py          # KL-44 P2
# Marcar bounces existentes: POST /api/admin/process-bounces (JWT)

# Drenar backlog de scans (nunca tudo de uma vez):
docker compose exec -T worker python scripts/enqueue_unscanned.py --limit 500
docker compose exec -T worker python scripts/enrich_all.py --limit 500   # perfil + IA + CNAE
```

## 7. GCS — bucket de responses brutos (KL-77 Fase 2, one-time)

Setup único, na VM (conta `klarimscan@gmail.com`, Owner). Segurança: SA com
`objectCreator` **apenas** (nunca admin, nunca leitura pública); bucket privado
(uniform bucket-level access).

```bash
PROJECT=project-b08050df-fa4e-49ac-919

# 1. Bucket Nearline na mesma região da VM (us-central1), privado.
gcloud storage buckets create gs://klarim-raw \
  --location=us-central1 --default-storage-class=NEARLINE \
  --uniform-bucket-level-access --project=$PROJECT

# 2. Auth — PREFERÍVEL: ADC via SA da VM (sem key file). Descubra a SA da VM:
curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
# Dê objectCreator a ESSA SA e pule os passos 3–4 (deixe GOOGLE_APPLICATION_CREDENTIALS vazio):
gcloud storage buckets add-iam-policy-binding gs://klarim-raw \
  --member="serviceAccount:<SA-DA-VM>" --role="roles/storage.objectCreator" --project=$PROJECT

# 3. ALTERNATIVA (só se a VM não tiver SA utilizável): SA dedicada + key JSON.
gcloud iam service-accounts create klarim-scan-archive \
  --display-name="Klarim Scan Archive Writer" --project=$PROJECT
gcloud storage buckets add-iam-policy-binding gs://klarim-raw \
  --member="serviceAccount:klarim-scan-archive@$PROJECT.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator" --project=$PROJECT
# 4. Key JSON → /opt/klarim/gcs-key.json; no .env: GOOGLE_APPLICATION_CREDENTIALS=/app/gcs-key.json
#    e descomente o volume `./gcs-key.json:/app/gcs-key.json:ro` no docker-compose.yml.
gcloud iam service-accounts keys create /opt/klarim/gcs-key.json \
  --iam-account=klarim-scan-archive@$PROJECT.iam.gserviceaccount.com

# 5. .env da VM: GCS_ENABLED=true, GCS_BUCKET=klarim-raw. Suba o worker (recria, relê env):
docker compose up -d worker

# 6. Verifique (após alguns scans):
docker compose exec worker python -c "from google.cloud import storage; c=storage.Client(); \
b=c.bucket('klarim-raw'); print('exists:', b.exists()); \
[print(' ', x.name, x.size) for x in list(b.list_blobs(max_results=5))]"
```
