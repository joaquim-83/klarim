# KL-77 Fase 1 — Migração para VM maior (e2-standard-4)

**Card:** KL-77 (Fase 1 de 3) · **Data:** 2026-07-19 · **Modo:** CLI executa, dono aprova nos GATES
**Risco:** Highest (único card que pode derrubar produção) — executado sem downtime perceptível.

---

## Resultado

Produção migrada de `instance-20260706-112125` (e2-medium, IP efêmero) para **`klarim-prod`**
(e2-standard-4, 4 vCPU/16GB, **200GB pd-ssd**, IP estático). Config **idêntica** (scan rate
50/h, mesmos 7 workers, mesmo `.env`, sem GCS). VM antiga em **standby 24h** como fallback.

## Inventário — antes → depois

| | VM antiga | VM nova |
|---|---|---|
| Instância | instance-20260706-112125 | **klarim-prod** |
| Máquina | e2-medium (2 vCPU/4GB) | **e2-standard-4 (4 vCPU/16GB)** |
| Disco | ~10GB (71% cheio, **2.8GB livres**) | **200GB pd-ssd** (2% usado) |
| IP | 35.238.72.10 (efêmero) | **34.135.194.208** (estático `klarim-static-ip`) |
| PostgreSQL | 16.14 (dockerizado) | 16.14 (dockerizado) |
| Docker/Compose | (antigo) | 29.6.2 / v5.3.1 |

## Migração do banco — contagens (integridade)

`pg_dump -Fc` (15M) → `pg_restore` por **stream SSH** (`--ssh-flag=-T`, sem arquivo
intermediário), exit 0, zero warnings. TOC do arquivo válido (31 tabelas).

| Tabela | Antiga (no cutover) | Nova | Δ |
|---|---|---|---|
| targets | 35.052 | 35.044 | −8 |
| scans | 13.783 | 13.781 | −2 |
| site_profile | 14.644 | 14.640 | −4 |
| **users / user_sites / vigilias / subscription_payments / technician_links / shared_reports / bulletins** | — | — | **0 (idênticos)** |

Todas as tabelas de **usuário/negócio idênticas** (zero perda). As 3 que crescem ficaram
−8/−2/−4 = writes vivos da antiga entre o dump e a comparação (auto-discovery, não-usuário).

## Validação (10 testes + HTTPS)

Contra a VM nova e, pós-DNS, contra `https://klarim.net` (tráfego confirmado nos logs do
nginx novo): api health ✅, homepage/planos/setores/perfil **200**, `/api/public/sectors` **48**,
scan **score 97 / 48 checks** ✅, redis **PONG**, mcp/sse **401** (auth), Safe Browsing key set,
dashboard **302**, painel.klarim.net **200**, `/api/account/me` deslogado **401**. **HTTPS** com
cert Let's Encrypt (copiado da VM antiga, válido até 05/out/2026).

## Passos-chave e decisões

- **`.env`** copiado old→new via **base64 por pipe** (segredos nunca no meu contexto);
  **md5 idêntico** (`215e245b…`), 44 keys, `GOOGLE_SAFE_BROWSING_KEY` presente.
- **Cert TLS:** o nginx só sobe HTTPS com o cert LE presente no host (`/etc/letsencrypt`,
  montado `:ro`) + `DOMAIN` no `.env`. Como o certbot só emitiria APÓS o DNS apontar
  (galinha-e-ovo com o proxy Cloudflare), **copiei `/etc/letsencrypt` da VM antiga** (cert de
  `klarim.net`, portável) e **reiniciei** o `web` (não `up -d` — a entrypoint só re-avalia o
  cert num restart). HTTPS OK antes do cutover.
- **Desvio do runbook — `enable-oslogin`:** criei a VM **sem** OS Login. A VM antiga e o
  projeto têm OS Login **desabilitado** e o CI/CD deploya via **injeção de chave por metadata**
  (`gcloud compute ssh`). Habilitar OS Login quebraria o SSH do CI (a SA do WIF tem
  `setMetadata`, não papéis de OS Login).
- **Correção do runbook — secret do CI:** o workflow usa **`secrets.GCP_INSTANCE`** (o card
  dizia `GCP_INSTANCE_NAME`, que não existe). Confirmado que `GCP_INSTANCE` foi atualizado.
- **Handoff dos workers:** parei `discovery`+`worker` na antiga e iniciei `discovery` na nova
  → **só a VM de produção emaila**. A antiga fica web/db/redis up (fallback), sem workers.

## ⚠️ Incidente (reporte honesto)

Ao subir os serviços na VM nova (pré-DNS), o alert worker disparou um ciclo em ~3 min e
**enviou 9 e-mails de alerta** antes de eu parar o `discovery` — alertas frios que **podem
duplicar** os da VM antiga (impacto: 9 e-mails, dentro da cota; sem dado de usuário). Já
parado. **Lição:** parar `discovery` **antes/junto** com `docker compose up -d` na próxima VM
(Fase 2), ou usar o kill-switch `STOP_ALERTS` no `.env` da VM nova até o cutover.

## CI/CD

Secret `GCP_INSTANCE` atualizado (2026-07-19 09:45) → o deploy do GitHub Actions passa a
SSH na `klarim-prod`. Validado por um push de teste (deploy verde na VM nova — ver abaixo).

## Pendências (pós-24h)

- Após 24h estáveis: `gcloud compute instances stop instance-20260706-112125`; após 7 dias:
  `delete`. **Não** executado ainda (standby).
- Webhook AbacatePay: usa o **domínio** (`klarim.net`) → resolve automático para a VM nova,
  sem mudança.
- Fase 2 (GCS + aceleração do scan) só após 24h de estabilidade.

## Docs atualizados

`claude.md` (§1), `docs/DEPLOY.md` (infra + runbook de migração + tempo de build).
