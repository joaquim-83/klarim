# Desenvolvimento local (KL-90)

Ambiente Docker **totalmente local** para desenvolver e testar o frontend + API
**antes** de subir para produção. Nada aqui faz deploy, push ou CI — e **nenhum
e-mail/pagamento real** sai (`DRY_RUN_EMAIL=true`, integrações desligadas).

Usa arquivos próprios, isolados da produção:

| Arquivo | Papel |
|---|---|
| `docker-compose.dev.yml` | stack de dev (db, redis, api, astro, web) — sem workers |
| `.env.dev` | variáveis de dev (secrets fake, e-mail/pagamento/GCS off) |
| `frontend/nginx/dev.conf` | Nginx HTTP puro (sem SSL/CSP/rate limit) |
| `scripts/seed_dev.py` | popula o banco com dados de teste do dashboard |

> A stack de **produção** continua em `docker-compose.yml` + `frontend/nginx/http.conf`
> e `https.conf.template`. Os arquivos `*dev*` **nunca** vão para a VM.

## Pré-requisitos

- Docker + Docker Compose
- (Opcional) Node.js **22+** para rodar o frontend fora do container (o Astro 7
  exige `>=22.12`; o container `astro` usa `node:22-slim`)

## Subir o ambiente

```bash
docker compose -f docker-compose.dev.yml up --build
```

A primeira subida compila a imagem da API e o `npm install` do Astro (alguns
minutos). A API cria o schema do Postgres automaticamente no boot (`ensure_schema`).

## Acessar

| Serviço | URL | Observação |
|---|---|---|
| Frontend (via Nginx) | http://localhost:3000 | acesso principal pelo browser |
| Astro dev (direto) | http://localhost:4321 | hot reload / HMR |
| API (direto) | http://localhost:8000 | `/docs` liga com `KLARIM_DEV_MODE=true` |
| PostgreSQL | localhost:**5433** | user `klarim` · pass `klarim` · db `klarim` |
| Redis | localhost:**6380** | sem persistência |

As portas 5433/6380 evitam conflito com um Postgres/Redis instalado no host.

## Popular com dados de teste

```bash
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
```

Cria 3 usuários, 5 sites monitorados (scores 20–100), 50 scans (histórico de
tendência + 48 checks no scan mais recente), 10 vigílias e perfis públicos, além
de sites de preenchimento por setor para o benchmark/ranking ficarem realistas.
É **idempotente**: rodar de novo apaga o que ele criou e recria.

### Login de teste

| E-mail | Senha | Perfil |
|---|---|---|
| `dono@exemplo.com.br` | `dev123456` | 5 sites monitorados, plano **Pro** trial |
| `tecnico@agencia.com.br` | `dev123456` | técnico, plano Pro trial, sem sites |
| `novo@teste.com.br` | `dev123456` | conta **não confirmada**, Free, sem sites |

## Hot reload

- **API:** editar um `.py` em `api/`, `scanner/`, `discovery/`, `reporter/`,
  `notifier/`, `payments/` ou `mcp_server/` → o Uvicorn (`--reload`) recarrega sozinho.
- **Frontend:** editar um `.astro`/`.jsx` em `web/` → o browser atualiza (HMR).

## Parar

```bash
docker compose -f docker-compose.dev.yml down
```

## Resetar o banco (do zero)

```bash
docker compose -f docker-compose.dev.yml down -v      # apaga o volume do Postgres
docker compose -f docker-compose.dev.yml up --build
# re-popular:
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
```

## Validação rápida

```bash
curl -s http://localhost:8000/health           # {"status":"ok"}
curl -s http://localhost:3000/api/health        # {"status":"ok"}  (API via Nginx)
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:4321/    # 200 (Astro)

# login (retorna um token/cookie de sessão de usuário)
curl -s -X POST http://localhost:8000/account/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"dono@exemplo.com.br","password":"dev123456"}'
```

## Notas

- O `.env.dev` já é ignorado pelo git (`.env.*` no `.gitignore`). Não coloque
  credenciais reais nele.
- Os **workers** (discovery/scan/alert/rescan/vigília) **não** rodam em dev — o
  seed injeta os dados direto no banco, então não é preciso escanear para testar
  o dashboard.
- `KLARIM_DEV_MODE=true` habilita o Swagger em `http://localhost:8000/docs`.
