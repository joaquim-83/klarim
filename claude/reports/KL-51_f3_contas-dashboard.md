# KL-51 Fase 3 — Sistema de contas + dashboard do usuário

> **Status:** implementado e testado; deploy via CI.
> **Objetivo:** transformar o visitante que viu o resultado do scan em **usuário
> retido** — conta, dashboard, monitoramento mensal.

## Verificação do cron de enriquecimento (pré-requisito obrigatório)

Antes de desenvolver, verifiquei a saúde do cron `enrich_all` na VM:

| Item | Resultado |
|------|-----------|
| Crontab (`crontab -l`) | ✅ presente: `0 6,14,22 * * * … enrich_all.py --limit 500` |
| Daemon cron | ✅ `active` |
| `enrich_all.py --dry-run` | ✅ funciona (achou 500 candidatos G1) |
| Comando verbatim do cron (limit 5, real) | ✅ `exit=0` — 1 perfil + 1 IA + 1 CNAE gravados |
| `enrichment_cron.log` | ⚠️ **não existia** — o cron ainda **não tinha disparado** desde a configuração |

**Diagnóstico:** o cron está **funcional**. O log não existia apenas porque nenhum
disparo agendado (06/14/22 UTC) tinha ocorrido desde a configuração (feita hoje entre
06:00 e 14:00 UTC). A execução manual do comando verbatim provou o mecanismo completo
(`docker compose exec -T api …` + redirect do log) e criou o log. **Nenhuma correção
necessária** — o próximo disparo (14:00 UTC) popula o log normalmente.

## O que foi entregue

### Backend — contas de usuário

- **`api/auth_users.py` (novo):** hash de senha com **bcrypt**, JWT de usuário (30d),
  `require_user`/`optional_user` (aceitam `Authorization: Bearer` **ou** o cookie
  `klarim_session`).
- **Endpoints `/account/*` (`api/main.py`):** `signup`, `login`, `logout`, `forgot`
  (código 6 dígitos, resposta genérica anti-enumeração), `reset`, `me`, e `sites`
  (`GET` lista · `GET /{id}` detalhe · `POST` adiciona · `DELETE` · `POST /{id}/claim`).
- **Segurança dos 2 domínios de auth:** o operador (`/auth/login`, Bearer 24h) e as
  contas (`/account`, cookie 30d) são assinados com o mesmo `JWT_SECRET`; cada token
  leva `typ` (`admin`/`user`) e cada camada só aceita o seu — um cookie de usuário
  **não** acessa `/targets`. Cookie `Secure/HttpOnly/SameSite=Lax`.
- **Tabelas (`discovery/store.py`):** `users`, `user_sites`, `password_resets` +
  ~15 métodos. `bcrypt` no `requirements.txt`.

### Frontend Astro

- **Páginas SSR** `/cadastrar`, `/entrar`, `/recuperar-senha`, `/dashboard`,
  `/dashboard/site/[id]` + ilhas React (`components/account/`).
- **`src/middleware.js`:** protege `/dashboard/*` validando o cookie no backend
  (`GET /account/me`), injeta `Astro.locals.user`, redireciona a `/entrar?redirect=`.
- **`Header.astro` dinâmico:** cookie HttpOnly → script consulta `/api/account/me` e
  alterna Entrar/Cadastrar ↔ Dashboard/Sair.
- **CTA de cadastro** no resultado do scan (`ScanFlow.jsx`) com e-mail+url pré-preenchidos.
- **Dashboard:** cards de site (score/semáforo/próximo scan/setor), benchmark do setor,
  info do plano (1 site no free), adicionar site (403 + CTA de upgrade no limite).
- **Detalhe do site:** gráfico de evolução (**SVG inline**, sem Recharts no bundle),
  48 checks por categoria, perfil comercial (descrição/tipo/CNAE/tags/maturidade), PDFs.

### Monitoramento mensal

- **`scripts/monitor_rescan.py` (novo, cron diário):** re-scan **completo (48)** de
  sites de contas ativas com último scan >30d → salva → e-mail de evolução
  (`send_account_evolution` + `account_evolution.html`). **Independente** do rescan
  worker antigo (pausado; não toca `alert_log`/`rescan_log`). Deduplica o scan por site.

## Decisões de arquitetura

1. **Namespace `/account/*` (não `/api/auth/*`):** `/auth/login` já é o login do
   operador. Para não colidir, as contas de usuário ficam em `/account/*`. Documentado.
2. **Validação de sessão do dashboard via API (não JWT local no Astro):** o middleware
   chama `GET /account/me` em vez de verificar o JWT com o segredo no Node — evita
   compartilhar o `JWT_SECRET` com o container Astro.
3. **Header client-side:** o cookie é HttpOnly (JS não lê), então o header alterna via
   fetch a `/account/me` — funciona igual em páginas estáticas e SSR.
4. **Gráfico SVG inline** em vez de Recharts (que não está no bundle Astro) — mantém o
   bundle público leve.

## Testes

- **`tests/test_accounts.py` (novo):** 17 testes offline (TestClient + FakeStore):
  hash/verify de senha, separação de `typ` admin↔user, signup (sucesso/duplicado 409/
  senha curta/e-mail inválido/vínculo do site com detecção de dono), login, `me`
  (401 sem token, token expirado), forgot genérico, reset (fluxo + código errado),
  sites (adicionar + **limite 403**, listar, remover, claim por e-mail).
- **`conftest.py`:** os novos buckets de rate limit (`_signup/_forgot/_reset_attempts`)
  entram no reset autouse entre testes.
- **SQL** validado com sqlglot (DDL + métodos). O `list_user_sites` teve o LATERAL
  corrigido de `created_at` → `scanned_at` (coluna real de `scans`).

**Limitação offline:** as partes DB-backed (queries reais) e o build Astro completo
rodam no CI (ambiente limpo) — localmente a suíte é hermética (sem Postgres) e o
`astro build` trava por um problema de ambiente (esbuild/vite); as ilhas foram validadas
por transform esbuild individual (todas parseiam) e o build oficial é o job `build-web`.

## Deploy / operação

- **Sem migration manual:** `ensure_schema` cria as tabelas no boot da API.
- **Sem novo segredo:** `JWT_SECRET` já existe no `.env` da VM (admin). bcrypt entra
  via `requirements.txt`.
- **Cron do monitoramento (a configurar na VM após o deploy):**
  `0 3 * * * cd /opt/klarim && docker compose exec -T api python scripts/monitor_rescan.py --limit 100 >> /opt/klarim/monitor_rescan.log 2>&1`
