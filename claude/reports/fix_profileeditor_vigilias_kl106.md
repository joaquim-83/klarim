# Fix ProfileEditor + vigílias default ativas + KL-106 (links + redirect painel)

**Tipo:** fix (regressão KL-98) + KL-106 · **Status:** ✅

## Parte 1 — ProfileEditor (regressão KL-98)
**Problema:** a modal "Editar perfil" abria com os campos VAZIOS (o `initial` vindo do dashboard-summary
é parcial) e o layout desktop/mobile desalinhava.
**Fix:**
- No mount, faz `GET /account/sites/{target_id}` (que já retorna `profile` completo via
  `get_site_profile`) e popula o formulário + a visibilidade.
- `Modal` ganhou prop **`size`** (`md`/`lg`/`xl`); o ProfileEditor usa `size="xl"` (max-w-3xl) e grid
  **`md:grid-cols-2`** (2 colunas no desktop, empilha no mobile). O overlay alinha ao topo (`items-start`)
  e rola verticalmente.

## Parte 2 — Vigílias ativas por padrão (a raiz era uma CORRIDA)
**Diagnóstico (prod):** TODAS as contas são Pro-trial e TODAS as 69 vigílias existentes estão
`enabled=true` — mas **5 users com site monitorado tinham ZERO vigílias**. Causa: `_create_account_record`
cria o trial Pro via `_spawn` (fire-and-forget); `_create_site_vigilias` roda **logo em seguida** e
`_vigilia_allowed_types` lê `get_subscription` **antes** de o trial existir → fallback `'free'` (que não
tinha vigília nenhuma) → nenhuma vigília criada.
**Fix (3 camadas):**
1. O trial passou de `_spawn` para **`await`** no `_create_account_record` (a assinatura existe antes de
   criar as vigílias). Best-effort (try/except — a conta já foi criada).
2. O plano **Free passa a incluir as 5 vigílias core** (ssl/domain/score/email/reputation) via `UPDATE
   plans` idempotente (o seed é `ON CONFLICT DO NOTHING`, não atualizaria prod). Assim, mesmo se a corrida
   voltar, o fallback free já habilita as 5 → as vigílias nascem ativas. `uptime`=Pro, `changes`/`phishing`
   =Agency. `_VIGILIA_MIN_PLAN` reduzido a `{uptime:pro, changes:agency, phishing:agency}`.
3. **Backfill** `scripts/backfill_vigilias.py` (idempotente): para todo site de conta ativa, cria as
   vigílias que faltam (ativas, conforme o plano) e reativa as desligadas. Rodar 1x no deploy:
   `docker compose exec api python -m scripts.backfill_vigilias`.

Efeito colateral positivo: quando o trial Pro expira → Free, o dono **mantém as 5 core** (o downgrade só
desliga uptime), em vez de perder todo o monitoramento.

## Parte 3 — KL-106
### 3a. Links "Ver landing page"
Auditoria (`grep`) do `web/src`: a maioria já usava `/site/{domain}`. O bug real estava no
**`ScoreCard.jsx`** — "Ver landing page →" apontava para `https://{site.domain}` (o **site real do
cliente**). Corrigido para o **perfil Klarim** (`profileUrl(domain)` = `/site/{domain}`), e adicionei um
link separado **"Visitar site ↗"** para o site real (nova aba, `rel="noopener noreferrer"`). Os demais
"Ver landing" (admin `ProfileEditor` = `/site/{domain}`) e "URL do target" (info, admin) já estavam certos.
### 3b. Redirect 301 de painel.klarim.net
O bloco nginx de `painel.${DOMAIN}` servia um build **Vite antigo** (SPA no root + ilhas Astro). Como o
admin migrou para `${DOMAIN}/painel` (Astro), troquei o bloco inteiro (170 linhas) por um **`return 301
https://${DOMAIN}$request_uri`** — o path é preservado (`painel.klarim.net/painel/alvos` →
`klarim.net/painel/alvos`). Mesmo certificado (o SAN cobre `painel.`); os cookies de sessão vivem em
`klarim.net` → no máximo um re-login. O bloco de porta 80 já redireciona HTTP→HTTPS. **`nginx -t` valida
na CI** (config inválida derruba o site).

## Segurança
Sanitização do perfil inalterada (KL-98). O 301 não vaza dado nem quebra sessão (cookies em klarim.net).
Backfill respeita o plano (não cria vigília paga p/ conta free).

## Testes
+1 backend (`test_free_plan_gets_5_core_vigilias_configurable` — as 5 core configuráveis, uptime/changes
gated). **Suite: 1689 backend** + 108 `node --test`; build Astro OK; `nginx -t` na CI.

## Validação pós-deploy
1. Dashboard → "Editar perfil" (klarim.net) → campos pré-preenchidos; desktop 2 colunas, mobile empilhado.
2. Rodar o backfill → os 5 users sem vigília passam a ter as 5 core ativas.
3. Nova conta via InlineSignup → vigílias ativas no dashboard.
4. "Ver landing page" → `/site/{domain}`; "Visitar site ↗" → site real.
5. `curl -I https://painel.klarim.net` → **301** → `https://klarim.net`; `.../painel/alvos` → 301 preserva o path.
