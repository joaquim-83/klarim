# KL-124 — CI/CD: `--force-recreate` (escopado) + rollback automático no deploy.sh

**Data:** 2026-07-28 · **Status:** ✅ implementado, `bash -n` OK. **Validação do pipeline
(push → 4 jobs → deploy) = pendente** (requer push a `main`, que só faço a pedido do dono).

---

## Problema

O `deploy/deploy.sh` fazia `docker compose build` + `docker compose up -d --remove-orphans`.
O `up -d` **sem `--force-recreate`** só recria containers cuja **imagem** mudou. Mas o layer
cache do Docker (`COPY . .` com checksums iguais) pode **não detectar** mudança em arquivos
`.py` e manter o container antigo rodando **código velho**. Descoberto no **KL-123**: o código
novo estava na VM (confirmado por `git log`), mas os containers antigos seguiam no ar (o módulo
novo não era importado). Foi preciso rodar `--force-recreate` **manualmente**.

## Solução (`deploy/deploy.sh`)

### 1. `--force-recreate` escopado aos 5 apps
O recreate agora é:
```bash
docker compose up -d --remove-orphans                                        # garante db/redis no ar + remove órfãos
docker compose up -d --force-recreate --no-deps api astro web worker discovery   # recria os apps SEMPRE
```
**Decisão (validada com o dono):** a spec pedia `docker compose up -d --force-recreate
--remove-orphans` cru — mas esse comando, **sem nomes de serviço, recria TODOS**, inclusive
`postgres`/`redis`, reiniciando o banco a cada deploy (dados salvos nos volumes, mas conexões
caem). Isso contradizia a própria nota da spec ("Postgres e Redis não são afetados") e a
propriedade "zero downtime na camada de dados" que o script já garantia. Escopamos o
`--force-recreate` aos **5 serviços com `build:`** (`api`, `astro`, `web`, `worker`,
`discovery`) via `--no-deps` + nomes → app sempre recriado com o código novo, **db/redis
intactos**. Downtime ~10-30s só nos apps.

### 2. Rollback automático no health check
- `PREV_COMMIT=$(git rev-parse HEAD)` é capturado **antes** do `git pull` (aponta o código bom
  que está no ar).
- Função `rollback(svc)`: loga o erro + `docker compose logs --tail=40 $svc`, faz
  `git checkout $PREV_COMMIT`, `docker compose build`, recreate escopado dos apps, e `exit 1`.
- Os dois health checks (API `/health` e Astro `/`) chamam `rollback api` / `rollback astro`
  no lugar do antigo `exit 1`.
- ⚠️ Após o rollback o repo fica em **HEAD destacado** no `PREV_COMMIT`. O próximo deploy de
  CI reavança (`git pull --ff-only origin main` fast-forwarda o HEAD destacado para a nova
  origin/main quando o fix chegar). O operador deve investigar os logs antes.

### 3. Log do commit deployado
Linha final: `==> Deploy OK: commit <sha-curto> em <timestamp UTC>` (rastreabilidade: qual
código está rodando). O commit atual (rollback target) também é logado no início.

## Compatibilidade
- **Deploy manual inalterado:** `sudo bash /opt/klarim/deploy/deploy.sh` segue funcionando
  (mesmo caminho do CI). `set -euo pipefail` preservado.
- Postgres/Redis (volumes `pgdata`/`redisdata`) nunca são tocados pelo recreate.
- O prune de disco (KL — build cache + imagens dangling) e o certbot renew seguem iguais.

## Documentação
- `docs/DEPLOY.md` §2 (fluxo numerado do deploy.sh com force-recreate escopado + rollback) e
  §3 (job `deploy` referencia o rollback + o que aparece nos logs do Actions).
- `CLAUDE.md` §9 (entrada KL-124).

## Testes / validação
- `bash -n deploy/deploy.sh` → OK (sintaxe). shellcheck não instalado localmente.
- **Validação do pipeline completo (pendente de push):** commit trivial → CI roda os 4 jobs
  (Test, Build web, Nginx check, Deploy) → nos logs do Actions confirmar
  `up -d --force-recreate … api astro web worker discovery` + `Deploy OK: commit <sha>` →
  health check verde (API `/health` + Astro `/`) → `docker exec klarim-api-1 cat /app/CLAUDE.md
  | grep "KL-124"` confirma o código novo no container. **Fechar KL-124 no Jira após essa
  validação.**

## Arquivos
- Alterados: `deploy/deploy.sh`, `docs/DEPLOY.md`, `CLAUDE.md`.
- Novo: `claude/reports/KL-124_deploy_force_recreate_rollback.md`.
