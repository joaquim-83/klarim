# KL-93 — Fix de segurança: endpoints expostos sem autenticação

**Card:** KL-93 (Highest) · **Status:** Implementado (aguardando deploy verde) · **Data:** 2026-07-20

---

## 1. Contexto

Uma varredura de segurança revelou que o **`POST /payment/create` criava cobranças PIX reais
(R$ 19) no AbacatePay sem nenhuma autenticação** — qualquer URL, sem rate limit, sem validação
de domínio, sem e-mail. Outros endpoints públicos expunham funcionalidades sensíveis sem
proteção adequada.

---

## 2. P0 — `/payment/create` (crítico)

**Antes:** aceitava qualquer `url` e gerava cobrança PIX real, sem e-mail, sem rate limit, sem
validar o domínio.

**Depois** (validações rodam ANTES de qualquer criação de cobrança, inclusive o modo demo):

1. **E-mail obrigatório** — sem e-mail válido (`_SCAN_EMAIL_RE`) → **422**.
2. **Rate limit 3/hora por IP** — `_redis_allow("payment_create", ip, 3, 3600, …)` → **429** na 4ª.
3. **Domínio existe na base + tem scan** — `_domain_scanned(url)` confere o alvo em `targets` e
   `last_scan_at`/`last_scan_score` (prova de scan) → **404** se não existe/nunca escaneado.

**Cleanup das cobranças fantasma:** `scripts/cleanup_phantom_payments.py` (idempotente) remove
por `charge_id` as 2 cobranças de teste (`pix_char_jWePxHqsFXNPy3wDNHkAac3T` /
`pix_char_wDYLwbyR3HQSDLLNgCUNkLtg`). Novo método `PaymentStore.delete(charge_id)` (Postgres +
Memory). Rodar na VM: `docker compose exec -T api python -m scripts.cleanup_phantom_payments`.

**Validação:**
```
POST /payment/create {"url":"https://klarim.net"}                          → 422 (sem e-mail)
POST /payment/create {"url":"https://naoexiste.com","email":"x@x.com"}     → 404 (domínio)
4× POST /payment/create (domínio válido + e-mail)                          → 200,200,200,429
```

---

## 3. P1 — outros endpoints

| Endpoint | Antes | Depois |
|---|---|---|
| `POST /notify/profile-view` | dispara e-mail ao dono sem auth | **rate limit 1/h por (IP,domínio)** (429) + teto interno 1/domínio/24h |
| `POST /monitoring/offer` | RL 10/h + authz + score-100 | **RL 3/h** + **404 se domínio não existe** (authz/score-100 mantidos) |
| `GET /monitoring/sites` | público | **JWT admin (401 sem token)** |
| `GET /report/executive` · `GET /report/technical` | público, scan full caro | **rate limit 5/h por IP** compartilhado (429) |
| `GET /scan/result` | — | **sem mudança** (ver §4) |

`/monitoring/sites`: era "público" mas a vitrine de sites monitorados migrou para o Astro
(KL-74), que **não** consome este endpoint — só páginas Vite legadas (`frontend/src/pages/`).
Tornar admin-only não afeta o site público atual.

---

## 4. Decisão: `/scan/result` NÃO foi alterado (conflito com KL-89)

O card pedia: se `tier=full` sem sessão, fazer downgrade para 15 checks. **Análise:**

- O endpoint **não tem** parâmetro `tier`/`full` client-controlável — o nível de acesso vem
  **exclusivamente** de `_access_level(request)` (cookies/tokens de sessão), e o corte é
  **server-side** (`_filter_scan_result`). Um cliente **não consegue** pedir `tier=full`; o
  servidor é autoritativo. Ou seja, o "bypass" descrito **não existe**.
- Reduzir o anônimo para 15 checks **reverteria** a correção de conversão do **KL-89** (mostrar
  valor antes de pedir conta): hoje o anônimo vê **nome + PASS/FAIL** dos 48 checks (padrão de
  scanner passivo público, tipo SSL Labs/Observatory), mas **nunca** evidência técnica nem LGPD
  (essas já são gated server-side).

**Consultei o dono** → decisão: **manter KL-89** (a filtragem server-side já é o controle de
segurança correto). Documentado aqui e em `docs/SECURITY.md`. Nenhuma mudança em `/scan/result`.

---

## 5. Security review (autoavaliação — regra 2026-07-15)

- Todos os rate limits usam `CF-Connecting-IP` (o firewall de origem KL-82 impede forjá-lo).
- `/payment/create`: superfície de abuso reduzida a "domínio real já escaneado + e-mail + 3/h/IP".
- `/monitoring/sites`: dado de operador agora exige JWT admin (`_is_admin_request`).
- Nenhum segredo/PII novo exposto; `contact_email` continua nunca em claro.
- `request: Request = None` (padrão já usado no projeto) — FastAPI injeta por tipo; chamadas
  diretas (testes) funcionam sem request.

---

## 6. Testes

`tests/test_kl93_security.py` — **16 testes**: `/payment/create` (422 sem e-mail, 422 e-mail
inválido, 404 domínio inexistente, 404 domínio sem scan, 429 no 4º, 200 no caminho válido/demo),
`/notify/profile-view` (429 no 2º mesmo domínio; domínios distintos OK), `/monitoring/sites`
(401 sem admin, 200 com admin), `/monitoring/offer` (404 domínio inexistente, 429 rate limit),
`/report/{executive,technical}` (429 no 6º; bucket compartilhado), e o `delete` idempotente.
Atualizados os testes de `test_monitoring.py` afetados pela nova auth/404. Suíte: **1444 passed**.

---

## 7. Arquivos

**Novos:** `scripts/cleanup_phantom_payments.py`, `tests/test_kl93_security.py`.

**Alterados:** `api/main.py` (validações + rate limits + auth), `payments/store.py`
(`delete` em Postgres+Memory), `tests/conftest.py` (limpa os buckets novos),
`tests/test_monitoring.py` (ajuste da auth/404), `CLAUDE.md`, `docs/SECURITY.md`.

---

## 8. Pós-deploy (VM)

1. Rodar o cleanup: `docker compose exec -T api python -m scripts.cleanup_phantom_payments`
   (ou `docker exec <api> …`) → confirma 2 cobranças fantasma removidas.
2. Verificar `POST /payment/create` sem e-mail → 422; domínio aleatório → 404; 4ª chamada → 429.
3. `GET /monitoring/sites` sem token → 401.
