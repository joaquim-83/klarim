# KL-26 — Cobertura de testes: e2e, multi-tenant, regressão de score, edge cases, email pipeline, frontend

**Data:** 2026-07-26 · **Status:** ✅ · **Regra-mãe cumprida:** zero mudança em código de produção.

## Resultado

- **Backend:** 1748 → **1848 passed** (+100 testes novos), 1 skipped, 0 falhas.
- **Frontend:** **117 node --test** (+12 no `web/src/lib/scanView.test.js`), 0 falhas.
- **Total novo: +112** (alvo da spec: +80-120). Todos determinísticos; APIs externas (Reoon/Resend/
  AbacatePay/DNS) sempre mockadas.
- **Nenhum código de produção alterado.** Só `tests/*`, `tests/conftest.py` (infra de teste) e o arquivo
  de teste frontend existente.

## Arquivos

### 1. `tests/test_e2e_flows.py` (6) — fluxos cross-módulo
- **Flow C** (dono verificado): verify/start (dns_txt) → DNS mock → verify/check → nível 3 + `is_owner`
  → PUT profile (sanitiza HTML, `edited_by_owner`) → PUT seal → GET `/seal/{domain}` público reflete
  `enabled`+`verified`.
- **Flow D** (técnico): vê o monitoramento do site do cliente mas PUT profile/seal → **403 not_owner**.
- **Flow F** (unsubscribe completo): token HMAC → GET `/remover` → POST `/remover` (unsubscribed +
  blocklist + evento) → `_validate_batch` do alert worker **pula** o alvo → POST idempotente.
- **Flow B** (prontidão de cold alert): lead scoring (e-mail no domínio +30, score na zona +20) →
  verificação `safe` → `is_safe_to_send` True; e-mail `invalid` → nunca envia.
- **Flow E** (pagamento PIX): charge pendente → `mark_status(PAID)` (efeito do webhook) → `payment_stats`
  reflete a receita.

### 2. `tests/test_multi_tenant.py` (36) — isolamento
- **IDOR bidirecional** (A↔B) em 10 endpoints `/account/sites/{id}/*` → **404** (parametrizado, 20 casos);
  `intelligence` (admin) → **401**.
- **Escalação vertical**: token de usuário em `/targets`, `/scans`, `/admin/*`, `/alerts` → **401**.
- **Vazamento**: `/account/me` só o próprio e-mail; `/account/monitoring-status` de domínio alheio →
  `monitoring:false` sem vazar o dono; anônimo → `{logged_in:false}`.
- **Mass assignment**: `SignupInlineBody`/`NotificationPrefsBody`/`OwnerProfileBody` com `account_level`/
  `plan`/`user_id` extras → **ignorados** (`extra='ignore'` do Pydantic); endpoint de notif-prefs não
  propaga `account_level`.

### 3. `tests/test_score_regression.py` (17) — score determinístico
- Extremos (100 verde / 0 vermelho), INCONCLUSO neutro, determinismo (3 rodadas = mesmo score).
- Tabela do semáforo: 90+0alta=verde · 90+1alta=amarelo · 89=amarelo · 50=amarelo · 49=vermelho ·
  FAIL crítica rebaixa verde mesmo com score alto.
- Sites de referência (seguro=100 verde / problemático<50 vermelho).
- **Guarda de mudança de peso/threshold**: pesos (5/3/2/1) e thresholds (90/50) fixos + um mix com
  score exato (92) que falha com mensagem clara se `scoring.py` mudar (alerta **intencional**, não bug).

### 4. `tests/test_scanner_edge_cases.py` (21) — o scanner sobrevive a tudo
- Gate KL-94: nxdomain→`domain_not_found`, erro DNS→`dns_error`, offline→`unreachable`, qualquer
  resposta→acessível.
- Check de header: timeout/conn-error/redirect-infinito → **INCONCLUSO** (nunca PASS falso); header
  ausente→FAIL; forte→PASS; `Content-Type` com path-traversal é inerte.
- `content_guard`: 5xx/corpo vazio→INCONCLUSO; corpo bom→None; encoding misto não crasha.
- Parser: HTML malformado/gigante(500KB)/lixo → extrai o que dá sem crash.
- Runner: 1 check que levanta → INCONCLUSO, o scan **não** falha.
- SSL: URL inválida→INCONCLUSO; cert inválido→FAIL (nunca crash).

### 5. `tests/test_email_pipeline.py` (20) — pipeline completo
- Circuit breaker KL-108: pausa por **hard**>5% (ignora soft alto); lê `hard_bounced`, não o combinado.
- Verificação→decisão KL-110: safe/catch_all(±score)/invalid/unknown-fail-open.
- List-Unsubscribe KL-102: cold tem `List-Unsubscribe`+One-Click com token; sem secret→só mailto;
  roundtrip do token normaliza case e rejeita tamper.
- Rotação KL-91: 2 ativos alternam; 1 pausado→só o ativo; todos no limite→None.
- Bounce webhook: permanente→descarta+blocklist; transitório→`soft_bounced` sem descarte; email_id
  desconhecido→sem crash; **integração** blocklist→`_validate_batch` pula o alvo.

### 6. `web/src/lib/scanView.test.js` (+12) — frontend
- `viewFlags` nulo/indefinido→anonymous (LGPD travado); só `confirmed` abre LGPD+evidência.
- `scoreHeadline` extremos preservam origem; **3 estados do CTA** mapeados às funções puras
  (`inlineSignupCopy` "Monitorar" vs `monitorConsentCopy` "Sim, monitorar" vs sem-CTA de conta).
- `getCategoryStatus` no início da faixa=active; `SCAN_CATEGORIES` contíguas sem buraco; `reportUrls`
  sempre strings; `maskEmail` nunca devolve o e-mail cru.

## Achado (não é bug de produção)

O `tests/conftest.py` (autouse) resetava vários rate-limiters in-memory mas **não** o `_account_cfg_hits`
(usado por `_cfg_rate_limit`, 10/60s por `user_id`). Como o Redis não está disponível nos testes, o
bucket in-memory **acumulava entre testes** que reusam o mesmo `user_id` → **429 espúrio** (o
`test_kl97_98_owner::test_seal_put_configures` quebrou quando os novos testes empurraram o user 10 acima
do teto). É contaminação de teste, não bug de produção. Corrigido adicionando `_account_cfg_hits.clear()`
ao reset do `conftest` (arquivo de teste — permitido pela regra; rule #7: corrigir o teste, não o código).
Nenhum outro `xfail` foi necessário — nenhum bug de produção encontrado.

## Desvios da spec (documentados, faithful ao intento)

- **Frontend em `web/src/lib/`, não `web/src/__tests__/`**: os testes `node --test` do projeto vivem em
  `web/src/lib/*.test.js` (rodados pelo `test:unit`); o caminho `web/src/__tests__/` da spec não existe.
  Estendi o arquivo real `web/src/lib/scanView.test.js` (arquivo de teste, não código de produção).
- **Sector-pills / stats-bar (KL-103) não testados em unit**: são código DOM inline em
  `web/public/landing-stats.js` (sem função pura exportada) — não testáveis com `node --test`. Cobertura
  real deles seria e2e de browser (fora do escopo desta suíte).
- **Semáforo em PT** (`verde`/`amarelo`/`vermelho`), não green/yellow/red — os testes usam os valores reais.
- **Payment endpoint** é `POST /payment/create` (não `/payments/create`); Flow E cobre o ciclo
  charge→paid via a store de pagamentos (o webhook completo já tem cobertura em `test_payments`/`test_kl93`).
- **`/admin/targets/{id}/intelligence`** devolve **401** (middleware admin) a um usuário comum, não 404 —
  é o comportamento correto (bloqueio antes do handler).

## Validação

`pytest` → 1848 passed, 1 skipped · `npm run test:unit` → 117 pass · cada arquivo novo passa isolado e no
conjunto · nenhum teste existente quebrado (após o fix do conftest).
