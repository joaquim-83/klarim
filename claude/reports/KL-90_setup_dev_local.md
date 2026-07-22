# KL-90 — Prompt 0: Setup de desenvolvimento local

**Data:** 2026-07-21
**Card:** KL-90 (Prompt 0 de N)
**Tipo:** infraestrutura de desenvolvimento (não é feature de produto)
**Deploy:** **NENHUM.** Tudo local — sem push, sem CI, sem alteração em produção.

---

## Objetivo

O Klarim **nunca rodou localmente** — sempre foi deploy direto para produção. Este
prompt cria o ambiente de desenvolvimento para testar o **Dashboard v2** (e qualquer
trabalho futuro) **antes** de subir. É um investimento de base: um lugar seguro para
iterar frontend + API com dados realistas, sem tocar na VM e sem enviar e-mail ou
cobrança real.

---

## O que foi entregue

| Arquivo | Papel |
|---|---|
| `docker-compose.dev.yml` | Stack de dev: `db`, `redis`, `api` (hot reload), `astro` (dev server), `web` (Nginx HTTP). **Sem** workers de produção. |
| `.env.dev` | Variáveis de dev (secrets fake, e-mail/pagamento/GCS desligados, `DRY_RUN_EMAIL=true`). |
| `frontend/nginx/dev.conf` | Nginx HTTP puro: proxy `/api/`→`api:8000`, `/`→`astro:4321` (com WebSocket p/ HMR). Sem SSL/CSP/rate limit. |
| `scripts/seed_dev.py` | Popula o banco local com dados representativos do dashboard. Idempotente. |
| `docs/DEV.md` | Guia de desenvolvimento local (subir, acessar, popular, resetar, validar). |
| `claude.md` | Nova subseção "Desenvolvimento local (KL-90 P0)" na §6. |

Isolamento total da produção: a stack real continua em `docker-compose.yml` +
`frontend/nginx/{http.conf,https.conf.template}`. Os arquivos `*dev*` **nunca** vão
para a VM.

---

## Decisões de projeto

- **Sem workers em dev.** `discovery`, `scan/worker`, `alert`, `rescan` e `vigília` não
  sobem — para testar o dashboard não é preciso escanear nem descobrir alvos. O
  `seed_dev.py` injeta os dados direto no banco. Isso remove toda dependência de
  Resend/AbacatePay/GCS/Cloudflare e CT logs.
- **A API é quem "migra".** Não há Alembic — o schema nasce do `ensure_schema` no
  `lifespan` da API (idempotente, `CREATE TABLE IF NOT EXISTS`/`ADD COLUMN IF NOT
  EXISTS`). O container `api` roda isso no boot; é o único que mexe no schema.
- **Portas deslocadas** (5433/6380) para não conflitar com um Postgres/Redis local.
  O acesso principal é `http://localhost:3000` (Nginx). Astro (4321) e API (8000)
  ficam expostos direto para debug.
- **Hot reload de verdade.** API com `uvicorn --reload` (watcher limitado aos pacotes
  Python via `--reload-dir`, para não observar `web/`/`.venv`/`node_modules` do host).
  Astro com `npm run dev` e HMR via WebSocket através do Nginx.
- **`node_modules` do Astro em volume nomeado** (não no bind mount) — evita colisão com
  o `node_modules` do host e persiste entre restarts; o `npm install` só roda quando o
  volume está vazio.
- **Golden rule de DB respeitada:** `.env.dev` usa os `POSTGRES_*` individuais (o store
  os prefere quando `POSTGRES_HOST` está setado), não `DATABASE_URL`.
- **`.env.dev` já é gitignored** pela regra `.env.*` do `.gitignore` (com a exceção
  `!.env.example`) — nenhuma mudança no `.gitignore` foi necessária.
- **Guarda de segurança no seed:** `seed_dev.py` recusa rodar se não for dev
  (`KLARIM_DEV_MODE=true` ou `POSTGRES_HOST` ∈ db/localhost/127.0.0.1) — impede
  semear/limpar produção por acidente.

---

## O seed (`scripts/seed_dev.py`)

Idempotente (apaga o que ele criou, por e-mail/domínio, e recria numa transação).

- **3 usuários** (senha `dev123456`):
  - `dono@exemplo.com.br` — confirmado, **Pro trial** (vence em 24 dias), 5 sites.
  - `tecnico@agencia.com.br` — técnico, Pro trial, sem sites.
  - `novo@teste.com.br` — **não confirmado**, Free, sem sites.
- **5 sites monitorados** (usuário 1), scores variados e 3 setores:
  hotel (83/🟡 hotelaria) · clínica (100/🟢 saúde) · loja (42/🔴 e-commerce) ·
  blog (65/🟡 tecnologia) · empresa (20/🔴 serviços). O **primário** do dashboard é
  `hotel-exemplo` (maior `added_at`).
- **50 scans** (10 por site) formando o **histórico de score** (gráfico de tendência,
  ex.: loja caindo 60→42). O scan mais recente de cada site carrega os **48 checks**
  reais (id/nome/severidade/OWASP/CWE/LGPD) com mix PASS/FAIL/INCONCLUSO e evidência.
- **Riscos derivados dos checks FAIL** (KL-20 — não são tabela). `loja-exemplo` (42)
  falha **SPF, HSTS e CSP** (entre outros), cada um com **fix por plataforma**
  (WordPress/Nginx/Apache) em `details.fix_inline`.
- **10 vigílias** (ssl + score por site). Inclui `hotel` SSL ok (247 dias) e `loja`
  score **crítico** ("Score caiu de 60 para 42").
- **Perfis comerciais públicos** para os 5 sites + **sites de preenchimento** por setor
  (50) só para o **benchmark setorial** (min_count=10) e o **ranking** ficarem realistas.

Saída esperada:
```
Seed: 3 users, 5 sites, 50 scans, 10 vigilias criados (+ 50 sites de preenchimento p/ benchmark).
Login de teste: dono@exemplo.com.br / senha dev123456
```

---

## Validação (executada localmente)

`docker compose -f docker-compose.dev.yml up --build` sobe os 5 serviços; a API cria
o schema no boot. Resultados:

| Verificação | Resultado |
|---|---|
| `GET :8000/health` (API direta) | `{"status":"ok"}` |
| `GET :3000/api/health` (API via Nginx) | `{"status":"ok"}` |
| `GET :4321/` (Astro dev) | `200` |
| `GET :3000/` (Nginx → Astro) | `200`, `<title>Klarim — Segurança web…` |
| `GET :3000/setores` (Astro SSR público via Nginx) | `200` |
| `python -m scripts.seed_dev` | `Seed: 3 users, 5 sites, 50 scans, 10 vigilias criados (+ 50 sites de preenchimento…)` |
| Idempotência (seed 2×) | contagens estáveis: users=3, targets=55, scans=50, vigilias=10, subs=3 |
| `POST /account/login` (`dono@exemplo.com.br`) | 200 + cookie de sessão |
| `GET /account/dashboard-summary` (com cookie) | `has_site=true`, 5 sites, primário hotel 83/🟡, **rank 1/13**, benchmark 57, 10 pts de histórico, 6 categorias, plano Pro trial |
| Riscos (KL-20) do site 42 (`loja-exemplo`) | SPF ("Qualquer um pode enviar e-mail fingindo ser você"), CSP, DMARC… + `fix_inline` por plataforma em HSTS/CSP/SPF |
| Vigílias do primário | `{active:2, ok:2}` |
| Conta nova (`novo@teste.com.br`) | `has_site=false`, Free, checklist reduzido |
| Hot reload API | `touch api/main.py` → `WatchFiles detected changes… Reloading` → `startup complete` |
| Hot reload frontend | Vite HMR conectado (`[vite] connected`) |
| E-mail real (DRY_RUN) | 0 chamadas ao Resend nos logs |
| `pytest` (offline, no container, env CI) | **1510 passed, 1 skipped** (55s) |

> **Desvio do prompt (justificado):** o prompt pedia `Node 20` para o Astro, mas o
> **Astro 7 exige `>=22.12`** (o `node:20` é recusado no boot). Ajustado para
> `node:22-slim` — a mesma versão da imagem de produção (`web/Dockerfile`).

### Nota sobre o `pytest` dentro do container de dev

Rodar `pytest` **com as variáveis do `.env.dev` ativas** faz 4 testes divergirem —
**todos por causa do ambiente**, não do código (nenhum módulo de produção foi tocado):

| Teste | Causa (variável do `.env.dev`) |
|---|---|
| `test_security_hardening::test_docs_disabled_in_prod` | `KLARIM_DEV_MODE=true` liga o `/docs` (esperado em dev) |
| `test_kl44_p6_payment::test_webhook_activates_subscription` | `ABACATEPAY_WEBHOOK_SECRET` de dev |
| `test_kl44_p6_payment::test_webhook_expired_marks_payment` | idem |
| `test_notifier::test_send_alert_batch_counts_and_ids` | `JWT_SECRET`/`UNSUBSCRIBE_SECRET` setados → o CTA do alerta vira o link HMAC do KL-82 Slice 3 (`/api/alert-access`) em vez de `/site/{domain}` |

Com essas variáveis **desativadas** (env equivalente ao CI), a suíte fica **verde
(1510 passed)** — o mesmo baseline registrado no `claude.md`. Não ajusto o `.env.dev`
para "passar" esses testes: o `JWT_SECRET`/`KLARIM_DEV_MODE` são necessários para o
stack de dev (auth e `/docs`), e a suíte é feita para o ambiente limpo do CI.

---

## Como usar

```bash
# subir
docker compose -f docker-compose.dev.yml up --build
# popular
docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
# parar
docker compose -f docker-compose.dev.yml down
# resetar banco
docker compose -f docker-compose.dev.yml down -v && docker compose -f docker-compose.dev.yml up --build
```

Detalhes completos em `docs/DEV.md`.

---

## Segurança

- Nenhum endpoint novo, nenhum fluxo de dados novo — só infraestrutura de dev.
- Nenhuma credencial de produção no repositório (`.env.dev` tem só secrets fake e já é
  ignorado pelo git).
- `DRY_RUN_EMAIL=true` + integrações desligadas → o ambiente local **não consegue**
  enviar e-mail nem criar cobrança real.
- O Nginx de dev é HTTP puro **de propósito** (debug), separado da config de produção
  que mantém TLS + todos os security headers.
- O seed tem guarda anti-produção.

---

## Não faz parte deste prompt

- **Nenhum deploy.** Sem push, sem CI, sem alteração na VM.
- Os próximos prompts do KL-90 (Dashboard v2) usarão este ambiente para desenvolver e
  testar antes de subir.
