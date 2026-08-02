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

`deploy.sh` (fluxo, **KL-124**):
1. marca `/opt/klarim` como `safe.directory`;
2. guarda o commit atual (`PREV_COMMIT`, alvo do rollback) → `git pull --ff-only origin main`;
3. `docker compose build` (site **no ar** durante o build);
4. `docker compose up -d --remove-orphans` (garante db/redis no ar, remove órfãos) +
   `docker compose up -d --force-recreate --no-deps api astro web worker discovery`
   (**`--force-recreate` escopado aos 5 apps** — recria SEMPRE mesmo quando o layer cache
   não detecta mudança em `.py`; **db/redis NÃO reiniciam**, zero downtime na camada de dados);
5. `docker builder prune -af` + `image prune -f` (limpa disco) → `docker compose ps`;
6. health checks (`localhost:8000/health` + `:4321/` Astro). **Se falhar → rollback
   automático:** `git checkout $PREV_COMMIT` + rebuild + recreate dos apps, e `exit 1`;
7. `certbot renew` (se aplicável) → log `Deploy OK: commit <sha>`.

Downtime ~10–30s (só o recreate dos apps). ⚠️ O `--force-recreate` foi adicionado no KL-124:
o `up -d` sem ele só recria containers cuja **imagem** mudou — mas o layer cache do Docker
podia manter o container antigo rodando código velho (incidente do KL-123). ⚠️ O script se
auto-atualiza no `git pull`, mas a mudança só vale **no deploy seguinte** (o bash já leu o
arquivo no início). ⚠️ Após um rollback o repositório fica em **HEAD destacado** no
`PREV_COMMIT`; o próximo deploy de CI reavança (`git pull --ff-only`) quando o fix chegar em
`origin/main` — investigue os logs antes.

> **Nota:** o build de `api`/`web` na `e2-standard-4` (4 vCPU) leva **~5–15 min**
> (era 10–50 min na e2-small/medium antiga). Lento ≠ travado.

## 3. CI/CD automático (`.github/workflows/deploy.yml`)

A todo push para `main`, com `deploy` dependente de `needs: [test, build-web, nginx-check]`:

1. **`test`** — Python 3.12, `pip install -r requirements.txt`, `pytest`. Falhou → bloqueia.
2. **`build-web`** — `npm ci` + `npm run build` do Astro (quebra de build não vai a prod).
3. **`nginx-check`** — `nginx -t` no `http.conf` e no `https.conf.template` renderizado
   (cert dummy). Config inválida bloqueia o deploy (**não** derruba o site).
4. **`deploy`** — autentica no GCP via **Workload Identity Federation** (OIDC, keyless,
   sem chave de SA), conecta via `gcloud compute ssh` e roda `deploy/deploy.sh` (fluxo
   detalhado na §2: `--force-recreate` escopado aos apps + **rollback automático** se o
   health check falhar). Nos logs do Actions dá pra ver `up -d --force-recreate … api astro
   web worker discovery` e o `Deploy OK: commit <sha>` final.

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
| `RESEND_FROM` | remetente transacional (`Klarim <klarim@klarim.net>` — migrado de `seguranca@` em 2026-07-21; a palavra "seguranca" elevava o spam score, confirmação caía no spam) |
| `ALERT_FROM_EMAIL` / `ALERT_FROM_NAME` | remetente proativo legado (`alerta@klarim.net`) — hoje só **profile_view/bulletin** (o alerta cold migrou para a rotação KL-91) |
| `ALERT_SENDER_EMAILS` | **KL-91** — CSV dos remetentes cold rotacionados (default `scan@alertas.klarim.net,scan@aviso.klarim.net`; verificados no Resend). `klarim.net` cru é ignorado (isolamento do transacional) |
| `ALERT_SENDER_DAILY_LIMIT` | **KL-91** — limite diário POR remetente cold (warmup: 100→250→500→750; editável no painel) |
| `ALERT_SEND_INTERVAL_MIN` / `ALERT_SEND_INTERVAL_MAX` | **KL-91** — cooldown randômico entre envios individuais (default 30/60s; 0/0 em dev) |
| `ALERT_SENDER_MAX_BOUNCE_RATE` | **KL-91 · KL-108 · KL-122** — circuit breaker: pausa o remetente cujo **HARD bounce rate** passa disto (default **5.0%** no código; **valor operacional atual = 10** no `.env` da VM, amostra ≥`ALERT_SENDER_BOUNCE_MIN_SAMPLE`). **KL-108:** opera sobre **hard-only** — soft bounces (transitórios) não contam. |
| `ALERT_SENDER_BOUNCE_MIN_SAMPLE` | **KL-91** — amostra mínima antes de o circuit breaker julgar um remetente (default 100; evita pausar em warmup por poucos bounces) |
| `REOON_API_KEY` | **KL-110** — chave da API de verificação de e-mail (emailverifier.reoon.com). **Só no `.env` da VM, nunca no git/log.** Sem ela, o alert worker NÃO faz verificação Power (o MX da Camada 0 já roda na extração); ao configurar, a verificação de inbox ativa no próximo ciclo |
| `EMAIL_VERIFY_ENABLED` | **KL-110** — liga/desliga a verificação Power no alert worker (default `true`; só tem efeito com `REOON_API_KEY`) |
| `EMAIL_VERIFY_MAX_PER_CYCLE` | **KL-110 → KL-129** — máx de alvos **NÃO-verificados** verificados via Power por ciclo (default **200**, era 120/60; editável ao vivo no painel). KL-129 prioriza os `email_verified=false` no subset; os já-verificados (sendable/barrados) NÃO consomem vaga. Com 200/ciclo e ciclos de 30 min ≈ 9.600/dia |
| `EMAIL_VERIFY_TTL_DAYS` | **KL-110** — validade de uma verificação antes de re-verificar (default 60 dias) |
| `REOON_MAX_CONCURRENCY` | **KL-110** — máx de chamadas simultâneas à Reoon (default 5; restrição da API) |
| `ALERT_ROLE_PENALTY` | **KL-136** — penalidade de `lead_score` para e-mail de prefixo role-based (`contato@`/`vendas@`/`sac@`…) no `alert_scoring`. **Default -5** (era -15). **KL-137:** o score deixou de FILTRAR (só ORDENA a fila) — esta penalidade agora só afeta a ORDEM de envio, nunca barra um lead. Vale para os dois sinais de caixa de função (prefixo + status `role` da Reoon; nunca dobram). Lido do env a cada chamada. |
| `PROFILE_VIEW_FROM_EMAIL` / `PROFILE_VIEW_FROM_NAME` | **KL-101** — remetente dedicado do aviso "perfil consultado" (default `notifica@perfil.klarim.net`). ⚠️ o subdomínio precisa estar **verificado no Resend** antes do deploy |
| `PROFILE_VIEW_DAILY_LIMIT` | **KL-101** — teto diário de warmup do `perfil.klarim.net` (default 200; editável no painel) |
| `DRY_RUN_EMAIL` | dev — `true` faz o `KlarimMailer._send_sync` simular (não fala com o Resend), mas grava `email_log` |
| `RESEND_WEBHOOK_SECRET` | webhook Resend (Svix, bounce/complaint) |
| `UNSUBSCRIBE_SECRET` | HMAC do link de descadastro (`openssl rand -hex 32`) |

#### Valores operacionais atuais (produção, `.env` da VM — KL-122)
Os knobs de outreach ajustados na VM (podem divergir do default do código). **Onde é lido:** env
(`os.environ`) e/ou `admin_settings` (editável no painel, precedência sobre o env).

| Var | Valor atual | Default no código | O que faz / quando ajustar |
|---|---|---|---|
| `ALERT_DAILY_LIMIT` | **500** | 500 (`ALERT_MONTHLY_LIMIT` separado) | Teto GLOBAL de cold alerts por dia (todos os senders somados). Subir quando o warmup avançar e o bounce estiver saudável. |
| `ALERT_SENDER_DAILY_LIMIT` | **500** | 100 | Teto POR sender cold/dia (warmup 100→250→500→750). Editável no painel (`admin_settings` > env). |
| `ALERT_SENDER_MAX_BOUNCE_RATE` | **10** | 5.0 | % de **hard** bounce (KL-108) que pausa um sender. Baixar p/ 5 quando as listas estiverem limpas. |
| `ALERT_ROLE_PENALTY` | **-5** | -5 (KL-136) | Penalidade de lead scoring p/ prefixo role-based (`contato@`…). KL-137: o score só ORDENA (não filtra) — afeta só a ORDEM de envio. |
| `EMAIL_VERIFY_MAX_PER_CYCLE` | **200** | 200 (KL-129) | Máx de NÃO-verificados verificados via Power por ciclo. Editável no painel. |
| `PROFILE_VIEW_DAILY_LIMIT` | **500** | 200 | Teto diário de avisos "perfil consultado" (`perfil.klarim.net`, KL-101). Editável no painel. |

> **KL-137 (simplificação radical) — REMOVIDAS do código e do `.env` da VM:**
> `ALERT_UNSAFE_SCORE_GATE`, `ALERT_CATCH_ALL_SCORE_GATE`, `ALERT_TRUST_DOMAIN_DOWNGRADE` (e
> `ALERT_SCORE_THRESHOLD`, que só filtrava o lead scoring). A regra de envio virou **binária**
> (`is_safe_to_send`: só `safe`/`valid`/`role`) — sem gates de score. **Pós-deploy:** apagar essas
> vars do `.env` da VM (se presentes) — são ignoradas, mas confundem. Também revisar/remover o
> override `ALERT_SENDER_MAX_BOUNCE_RATE` (o circuit breaker do KL-108 continua com default 5% hard).

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
