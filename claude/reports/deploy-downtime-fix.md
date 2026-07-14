# Fix de downtime no deploy — `deploy/deploy.sh`

**Data:** 2026-07-14 · **Prioridade:** urgente (afeta usuários reais)

## Problema
O `deploy/deploy.sh` fazia `docker compose down` **antes** do build, então a stack inteira
(incluindo o Nginx/front-door, Postgres e Redis) ficava **fora do ar durante todo o build**
(~2-5 min por deploy).

## Fix
Removido o `docker compose down`. Novo fluxo:

```bash
docker compose build                    # build com o site AINDA no ar
docker compose up -d --remove-orphans   # recria só os containers cuja imagem/config mudou
```

- Postgres/Redis (sem `build`) **nem são tocados** → zero downtime na camada de dados.
- O `up -d` já recria só o que mudou; o `--remove-orphans` preserva a limpeza que o antigo
  `down --remove-orphans` fazia.
- O `prune` (builder + image) continua **após** o `up` (inalterado).

## Nota importante (auto-atualização do script)
O `deploy.sh` se atualiza no próprio `git pull`. O bash lê o script inteiro (<8KB) no início,
então a mudança **só passa a valer no deploy SEGUINTE** ao que a instalou. O deploy que
subiu este commit ainda rodou o script antigo (com downtime) uma última vez.

## Teste (medido em produção)
Poller no front-door do Nginx (`http://localhost/`) a cada 0.2s durante um deploy real com o
**novo** script:

| Cenário | Downtime medido |
|---|---|
| **Antes** (`compose down` + build) | site fora por **todo o build** (~2-5 min) |
| **Depois — deploy completo** (build 336s + prune + health checks) | **≈ 6,2s** (26/1371 amostras) |
| **Depois — recreate do front-door** (`up -d --force-recreate web`) | **≈ 1,1s** |

O `deploy.log` confirmou o novo fluxo: `docker compose build (site continua no ar…)` →
`docker compose up -d`. Health check pós-teste: front-door/api/astro **200**, containers `Up`.

**Resultado:** downtime por deploy caiu de **minutos → ~6 segundos** (melhor que a meta de
10-30s). CI verde (Test/Build web/Nginx/Deploy). Commit `0ff4224`.
