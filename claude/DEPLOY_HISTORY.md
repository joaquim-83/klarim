# Klarim — Histórico de deploys

> Registro de deploys em produção (runs de CI, datas, verificações pós-deploy).
> Extraído do `CLAUDE.md` na compactação (2026-08-12). O estado **atual** vive no
> `CLAUDE.md`; este arquivo é só o log histórico. Detalhe de cada tarefa em
> `claude/reports/KL-xxx_*.md` e `docs/HISTORY.md`.

## Infra de deploy
- **VM:** `klarim-prod` (e2-standard-4, 4 vCPU/16GB, 200GB pd-ssd), zona `us-central1-a`,
  IP estático `34.135.194.208`. Migração KL-77 Fase 1 (2026-07-19).
- **CI/CD** deploya por instance-name (`GCP_INSTANCE_NAME=klarim-prod`). OS Login DESABILITADO
  (SSH do CI por metadata). VM antiga `instance-20260706-112125` (35.238.72.10) em standby 24h como fallback.
- **KL-124:** `deploy/deploy.sh` usa `up -d --force-recreate --no-deps api astro web worker discovery`
  (escopado aos 5 apps, preserva postgres/redis) + rollback automático (`PREV_COMMIT`, health check).

## Runs registrados
| Data | Card(s) | Run | Nota |
|---|---|---|---|
| 2026-07-22 | KL-90 P3 (dashboard v2 → prod) | commit `6bbf1d2`, CI 4/4 | `/dashboard` = v2; `/dashboard/v2` 301 → `/dashboard`; workers 4/4; score 100 |
| 2026-08-09 | KL-150 P1 + KL-161 (LGPD) | CI run #293 | `privacidade@klarim.net` confirmado no Resend; tabela `lgpd_requests` criada |
| 2026-08-09 | KL-160 (nginx rate limit + Gate SPA fix + admin scan) | — (score 100/100 🟢 prod) | bloqueio IP direto subiu sem quebrar; rate_limit Gate → 100 após fix concorrente |
| 2026-08-10 | KL-150 P2 (6 pendências) | CI run #298 | `/public/best` total=670 real; home sem ProductSplit |
| 2026-08-10 | KL-150 P2 ajustes | CI run #300 | flush `public:best`+`public:stats` no Redis; /melhores=730 |
| 2026-08-10 | Fix painel (HTML raw + analytics bots) | CI run #302 | visitors_br 422 pós-filtro de UA |
| 2026-08-11 | KL-163 P1+P2 (PDF de run + endereço KYC) | run #31512717649 | coluna `address_data jsonb`; CSP libera `viacep.com.br`; 7/7 containers |
| 2026-08-12 | KL-134 P1+P2 (micro-ferramentas SEO) | run #31652293384 (commit `6b49e51`) | Test·Build·Nginx·Deploy(3m47s)·Security Gate todos verdes. Pós-deploy: `/api/tools/{stats,ssl,headers,email}` 200 + corretos (ssl grade A, email 4/4, stats total_sites 116049); `/ferramentas/` + `/ferramentas/verificar-ssl` 200; LGPD page H1 + FAQPage JSON-LD servidos (allowlist nginx OK) |
| 2026-08-12 | KL-164 (fix checks LGPD: DSAR/DPO multi-página + X-XSS informativo) | run #31654464519 (commit `154bd9e`) | Todos os jobs verdes. Pós-deploy: LGPD `klarim.net` **8/8 "Adequado"** (DSAR/DPO em `/privacidade`); Headers **6/6** (X-XSS `informational`); `example.com` **3/8** DSAR/DPO FAIL (not pass-always). Cache `scan:*` NÃO flushado — o tool LGPD é live; o `privacy_score` de scans cacheados auto-expira em ≤1h |

## Pendentes de deploy / validação do dono (à data da compactação)
- **KL-99** (conta sem senha + 3 níveis + verificação de domínio) — validado local, deploy pendente.
- **KL-91** (rotação de senders cold) — validado local, deploy pendente (código já referenciado como vigente).
- **KL-101** (isolar profile_view em `perfil.klarim.net`) — pendente de verificar o subdomínio no Resend.
- **KL-110** (verificação Reoon Power pré-envio) — ativa só com `REOON_API_KEY` na VM (superado no fluxo de envio pelo KL-145).

## Fixes emergenciais aplicados direto na VM (depois commitados)
- **KL-108/emergencial 26/07:** `ALERT_SENDER_MAX_BOUNCE_RATE=12` no `.env` (senders cold pausados pelo bounce combinado); revertido após o fix hard/soft.
- **KL-125/emergencial 28/07:** 3.703 unknowns resetados + cache limpo (superado pelo KL-127/145).
- **KL-122/27-07:** gate `unknown`/`catch_all` de `>50` → `>20` aplicado em prod antes de commitar.

## Regra crítica de deploy (KL-127)
`api`/`worker`/`discovery` usam a MESMA imagem (`build: .`). O deploy com `--force-recreate`
garante código uniforme. **NUNCA** editar arquivo via `docker exec` (causou divergência de
containers no incidente KL-127). Validação pós-deploy: `diff`/md5 dos módulos entre containers = vazio.
