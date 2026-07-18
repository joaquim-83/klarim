# FIX URGENTE — Ocultar indicadores detalhados de privacidade do público

**Prioridade:** EMERGÊNCIA · Fix de compliance imediato (sem card Jira)
**Data:** 2026-07-17

---

## Problema

As landing pages públicas (`/site/{domain}`) expunham os **8 indicadores de privacidade
detalhados** (PASS/FAIL por indicador, com referência LGPD) a **qualquer visitante não
logado**. A exposição vazava as falhas de compliance da empresa por trás do site — risco
para a empresa, para o responsável técnico, responsabilidade legal para o Klarim e vetor
de engenharia social.

### Causa-raiz encontrada (pior do que o relatado)

O código **não** apenas "mostrava para deslogado". A implementação anterior era o pior
dos dois mundos:

1. **API pública** (`/public/profile/{domain}`) devolvia o objeto `privacy` **completo**
   (`checks[]` com PASS/FAIL + `lgpd_ref` + `disclaimer`) para todo mundo.
2. **A página `/site/{domain}`** renderizava os detalhes no **SSR** dentro de um bloco
   `data-auth="in"` apenas escondido por CSS (`class="hidden"`) — ou seja, os 8
   indicadores estavam **no HTML de origem** (view-source) de qualquer visitante deslogado.
3. Pior: o script que alterna `data-auth` vive no `Header.astro` e usa
   `document.querySelector` (**singular**) — ele só alterna o **primeiro** par
   `data-auth` do DOM (o do header). O bloco da página de perfil **nunca** era revelado.
   Resultado: detalhes vazavam na origem **e** nem sequer apareciam para o usuário logado.

### Vetor adicional encontrado e fechado

O resumo **gratuito/anônimo** do scan (`/scan/summary`, endpoint público) também devolvia
o objeto `privacy` completo (embora a UI atual do `ScanFlow` não o renderize). Como a
regra é "detalhes só em superfícies autenticadas + laudo", esse endpoint era outra porta
aberta para o mesmo vazamento (bastava um `curl`). Foi fechado no mesmo fix.

---

## Regra aplicada

| Superfície | Comportamento |
|---|---|
| **Público (deslogado)** | Só resumo `N/8` + 🔒 cadeado. **Sem** indicadores. |
| **Público (logado)** | Detalhes completos (✅/❌ + ref LGPD + disclaimer). |
| **Laudo `/laudo/{code}`** | Detalhes completos (compartilhado de propósito pelo dono). |
| **Dashboard** | Detalhes completos (usuário autenticado, dados próprios). |
| **Admin** | Detalhes completos. |
| **Selo `/api/seal/{domain}`** | Só `privacy_score` / `privacy_total`. |

---

## Mudanças

### Backend — `api/main.py`

1. **Helper `_privacy_summary(privacy)`** (novo) — reduz o objeto de privacidade ao par
   público `{score, total}`, descartando `checks[]` e `disclaimer`. Retorna `None` quando
   não há dado.

2. **`GET /public/profile/{domain}`** — passou a devolver
   `"privacy": _privacy_summary(privacy)` (antes: objeto completo). **Fecha o vazamento
   na API pública e, por consequência, no SSR da página** (o Astro nunca mais recebe os
   `checks`).

3. **`GET /account/privacy/{domain}`** (novo, autenticado) — exige sessão de usuário
   (`auth_users.require_user`) e devolve os indicadores **detalhados** de um domínio.
   É a fonte que a ilha do perfil público consulta quando há login. Respeita a mesma
   visibilidade do perfil público (`descartado` / `public_visible=False` → `not_found`).

4. **`_summary_payload(...)` (`/scan/summary`)** — no resumo **gratuito** (`full=False`)
   agora devolve só `{score, total}`; no resultado **completo** (`full=True`,
   pós-pagamento/verificação) mantém os detalhes, igual aos demais checks pagos.

### Frontend

5. **Nova ilha `web/src/components/PrivacyPanel.jsx`** (`client:load`):
   * Deslogado → resumo `N/8` com 🔒 + CTA "Criar conta gratuita para ver os detalhes".
   * Logado → busca `/api/account/privacy/{domain}` e renderiza os ✅/❌ + ref LGPD +
     disclaimer.
   * Detecta login via `/api/account/me` (cookie de sessão é HttpOnly; o JWT nunca é lido
     por JS).

6. **`web/src/pages/site/[domain].astro`** — o bloco SSR que embutia os `checks` (e o
   `data-auth="in"` que nunca funcionava) foi **removido** e substituído pela ilha
   `<PrivacyPanel client:load … />`. Removida também a const `PRIVACY_DISCLAIMER` (agora
   vive na ilha). Nenhum detalhe de indicador é mais renderizado no SSR.

### Sem mudança (já corretos)

* **`/laudo/{code}`** — usa `/public/laudo/{code}`, que **não** foi tocado → mantém detalhes.
* **Dashboard** (`SiteDetail.jsx` → `/account/sites/{id}`, autenticado) → mantém detalhes.
* **Selo** (`/seal/{domain}`) — já devolvia só `privacy_score`/`privacy_total`.

---

## Testes

Novo arquivo **`tests/test_fix_privacidade_publica.py`**:

* `test_public_profile_hides_privacy_details` — perfil público devolve só `{score,total}`;
  garante ausência de `checks`/`lgpd_ref`/`disclaimer` no corpo.
* `test_public_profile_privacy_none_when_no_scan` — sem scan → `privacy: null`.
* `test_account_privacy_requires_auth` — `/account/privacy` sem sessão → **401**.
* `test_account_privacy_returns_details_when_logged_in` — logado → `checks` completos.
* `test_account_privacy_hidden_site_not_found` / `..._unknown_domain` — visibilidade.
* `test_scan_summary_free_hides_privacy_details` — resumo grátis só `{score,total}`.
* `test_scan_summary_full_keeps_privacy_details` — resultado completo mantém detalhes.

Regressão: `tests/test_kl44_p5_privacy.py` (selo/benchmark/checks) continua verde.

**Resultado da execução:** ✅ **25 passed** — `pytest tests/test_fix_privacidade_publica.py
tests/test_kl44_p5_privacy.py` (8 testes novos do fix + 17 de regressão do KL-44 P5).

---

## Verificação manual sugerida (deslogado)

```bash
# API pública — NÃO deve conter "checks"/"lgpd_ref"
curl -s https://klarim.net/api/public/profile/<dominio> | grep -i "lgpd_ref\|checks"   # (vazio)

# Endpoint logado sem sessão → 401
curl -s -o /dev/null -w "%{http_code}\n" https://klarim.net/api/account/privacy/<dominio>  # 401

# View-source da página deslogado — não deve conter os indicadores
curl -s https://klarim.net/site/<dominio> | grep -i "lgpd\|✅\|❌"   # (vazio)
```
