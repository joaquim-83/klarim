# KL-155 — Domain rate limit por plano no Security Gate

## Contexto
Carry-over do KL-153 (quick fix). A camada 3 do rate limiter (`api/gate_rate_limiter.py`) usava uma
key **global** por domínio (`gate:rl:domain:{domain}`) com **TTL fixo 1800s (30 min)** para todos os
planos. Consequências:
- Um CI que faz 2 pushes do mesmo domínio em 30 min tomava 429 (a 2ª chamada ao `POST /gate/scan`).
- A key ser global fazia o lock de um **Free** (30 min) bloquear um **Pro** que escaneasse o mesmo
  domínio depois — interferência entre usuários.

## O que mudou (`api/gate_rate_limiter.py`)

**Opção A do card (adotada):** a key da camada 3 vira **por conta** e o TTL **varia pelo plano**.

- Nova key: `gate:rl:domain:{account_id}:{domain}` (era `gate:rl:domain:{domain}`).
- Novo mapa `DOMAIN_TTL_BY_PLAN = {"free": 1800, "pro": 300, "team": 0, "enterprise": 0}`
  (`DOMAIN_WINDOW_SEC` mantido = 1800, o default Free, por compat).
- `check_domain(redis, account_id, domain, plan_slug)`:
  - `ttl = DOMAIN_TTL_BY_PLAN.get(plan_slug, 1800)`;
  - **`ttl <= 0` (team/enterprise) → return None** (a camada 3 NÃO se aplica; nenhuma key é setada);
  - senão `SET NX EX ttl` na key por-conta; se já existe → bloqueado (retorna o TTL restante).
- `enforce(...)` passa `account_id` + `slug` ao `check_domain`.

| Plano | TTL | Comportamento |
|---|---|---|
| Free | 1800s (30 min) | mantém |
| Pro | 300s (5 min) | afrouxado |
| Team | — | SKIP |
| Enterprise | — | SKIP |

Como a key é por-conta, o cooldown por domínio é **por-usuário** (igual às demais camadas) e um Free e
um Pro no mesmo domínio **não interferem**.

## Testes — `tests/test_kl155_domain_rl.py` (+6)
Fake Redis com `tick(segundos)` para simular a passagem do tempo (expira as keys vencidas),
determinístico:
1. **Pro** mesmo domínio após 5 min → liberado (TTL 300s expirou).
2. **Free** mesmo domínio após 5 min → bloqueado (TTL 1800s ainda ativo).
3. **Team** mesmo domínio imediato → liberado (camada 3 não se aplica; `kv` vazio).
4. **Enterprise** mesmo domínio imediato → liberado.
5. **Pro e Free** no mesmo domínio → keys separadas por `account_id` (sem interferência); cada uma
   bloqueia a si mesma no 2º scan.
6. `DOMAIN_TTL_BY_PLAN` e `DOMAIN_WINDOW_SEC` (guarda do mapa).

`test_kl153_backend::test_domain_limit_same_domain_429` atualizado à nova assinatura
(`check_domain(r, 10, "acme.com.br", "free")`). **Suíte completa: 2283 passed, 1 skipped, 0 failed.**

## Regras respeitadas
Só o `api/gate_rate_limiter.py` mudou (+ testes/docs). **NÃO** alterou frontend nem scanner público.
Docs: `docs/SECURITY.md` §12 (tabela de TTL por plano), `claude.md`.

## Arquivos
**Novo:** `tests/test_kl155_domain_rl.py`.
**Editados:** `api/gate_rate_limiter.py`, `tests/test_kl153_backend.py` (1 assinatura),
`docs/SECURITY.md` (§12), `claude.md`.
