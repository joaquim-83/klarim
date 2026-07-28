# KL-123 — Vigílias expandíveis: detalhe clicável com dados contextuais, ações e orientação

**Data:** 2026-07-28 · **Status:** ✅ implementado, testado (1873 backend + 124 node) e validado por build.
**Deploy:** pendente de push + CI verde. **Validação em navegador (dev docker):** pendente (Docker não
estava rodando na máquina; a camada HTTP está coberta por testes de integração via `TestClient`).

---

## Problema

Os cards de vigília no dashboard (`web/src/components/dashboard-v2/MonitoringSection.jsx`) mostravam
só **rótulo + status** (OK / Atenção / Crítico), sem interação. O dono via
"🔴 Typosquat / phishing — Crítico" mas **não conseguia ver quais domínios** foram detectados nem
agir. Toda a inteligência (domínios suspeitos, dias até o SSL vencer, quais checks caíram o score…)
existia no **e-mail de alerta**, mas não no dashboard — o produto principal (monitoramento) era opaco.

## Solução

Os cards agora **expandem** ao clicar e mostram o detalhe contextual daquela vigília, em **linguagem
acessível** ao dono (PME, sem equipe de segurança), com **orientação prática** e **ações** (verificar
domínio, denunciar, "não é ameaça", marcar como resolvido). A maioria dos dados já vivia em
`vigilias.last_data` (JSONB) ou em tabelas existentes — são leituras.

---

## Backend

### `api/vigilia_details.py` (novo — derivação PURA/testável)
Uma função `build_<tipo>` por tipo devolve o miolo do payload
`{status, summary, data, guidance, actions, pending_count}` a partir dos dados brutos. Nenhuma faz I/O
nem levanta — dado ausente vira um payload gracioso (`unknown`, "Aguardando a primeira verificação").
Reusa `check_num`/`norm_status` (de `api/dashboard.py`) e `RISK_MESSAGES` (`reporter/risk_messages.py`)
para o texto de negócio dos checks — **sem duplicar** o mapa acessível dos 48 checks.

| Tipo | `data` (resumo) | Fonte |
|---|---|---|
| **phishing** | `alerts[]` (domínio, tipo de similaridade em PT, distância, dismissed) | `typosquat_alerts` |
| **ssl** | issuer, subject_cn, validade, `days_left` | `last_data` + check 03 + `site_profile.certificate_authority` |
| **domain** | `expiry_date`, `days_left` | `last_data` (RDAP) |
| **score** | current/previous/`delta`, **`checks_changed`** (PASS↔FAIL entre 2 scans), `score_history` | `get_recent_scans_with_checks(5)` |
| **email** | SPF/DKIM/DMARC (`pass`/`absent`/`unknown`) | checks do último scan |
| **reputation** | `blacklisted[]`, google_safe_browsing | `last_data` + check 29 |
| **uptime** | código HTTP, tempo de resposta, falhas seguidas | `last_data` |
| **changes** | `last_snapshot` (título/leitura) | `last_data.snapshot` |

**Decisão importante:** o `status` de SSL/domínio **espelha o worker de vigília** (`api/vigilias.py`:
SSL crítico só ≤1 dia, warning ≤30) — assim o detalhe **não "vira vermelho"** ao expandir um card que o
worker marcou amarelo. A *orientação* pode ser mais enfática que o semáforo quando resta pouco tempo.

### Endpoints (`api/main.py`, `_owned_site` = auth + nível ≥1 + posse do vínculo)
- `GET /account/sites/{id}/vigilias/{tipo}/details` — orquestra as queries (`_build_vigilia_details`),
  injeta `tipo`/`label`/`history` e delega a montagem à função pura. **Tipo inválido → 404**; site de
  outro usuário → 404 (`_owned_site` nunca vaza).
- `POST /account/sites/{id}/vigilias/phishing/dismiss/{alert_id}` — "não é ameaça": descarte escopado
  por **(id, target, user)** → **404** se o alerta não é da conta (anti-IDOR).
- `POST /account/sites/{id}/vigilias/{tipo}/acknowledge` — grava `acknowledged_at` no `last_data`
  (some o badge do card até um novo alerta).

### Store (`discovery/store.py` — 4 métodos novos)
`get_site_typosquat_alerts(target,user)`, `dismiss_typosquat_alert(id,target,user)` (escopado),
`get_site_vigilia_alerts(user,domain,tipo)` (histórico), `acknowledge_vigilia(user,domain,tipo,ts)`
(`jsonb_set`+`to_jsonb`, mesmo padrão do `set_vigilia_enabled` já validado na VM). Todos parametrizados.

---

## Frontend

- **`web/src/components/dashboard-v2/VigiliaDetail.jsx`** — card expansível:
  **lazy-load** no 1º expand (`GET …/details`), cacheia no state (reabrir não refaz fetch), spinner
  em CSS, erro com mensagem, **badge** de `pending_count`, corpo específico por tipo, bloco de
  orientação (💡) e botões de ação. Phishing lista os domínios pendentes com **[Verificar site ↗]** e
  **[Não é ameaça]** por domínio; o dismiss é **otimista** (atualiza a lista + o badge **sem
  recarregar**, via `applyDismiss`). Mobile: alvos ≥44px, empilha, largura total.
- **`web/src/lib/vigiliaDetail.js`** (lógica PURA) — `statusMeta`, `showBadge`, `vigiliaLabel`,
  `emailStateLabel`, `applyDismiss` (imutável), `pendingAlerts`. 7 testes `node --test`.
- Integrado em `MonitoringSection.jsx` (substitui o card estático; removidos `ST`/`detail` mortos).

## Analytics (KL-57)
Eventos `vigilia_expand` (com `tipo`), `vigilia_dismiss`, `vigilia_action_click` no `_KNOWN_EVENTS`,
disparados pelo `window.klarimTrack` do front (sem PII).

---

## Segurança (regra de 2026-07-15)
- **Ownership em TODO endpoint** (`_owned_site`, nível ≥1). Site de outro usuário → 404.
- **Dismiss escopado por (id, target, user)** — o dono só descarta o alerta do próprio site (anti-IDOR;
  testado com o cross-user 404). Acknowledge idem (por user+domínio).
- **Tipo validado** contra `VIGILIA_TYPES` (404), evita chamada arbitrária.
- **Nenhum PII novo exposto** — o payload não traz `contact_email`/cnpj/whatsapp; typosquat expõe só o
  domínio suspeito (dado público de CT log).
- **Linguagem acessível**: nenhum OWASP/CWE/header raw na UI do dono (regra do card).

## Testes
- **Backend** (`tests/test_kl123_vigilia_details.py`, +20): details por tipo (phishing/ssl/score/…),
  dismiss (marca + pending cai), dismiss cross-user 404, alerta inexistente 404, acknowledge, tipo
  inválido 404, outro usuário 404, sem auth 401, degradação sem `last_data`, e as funções puras
  (bandas de urgência, listed/clean, online/offline, DMARC ausente, acknowledge zera o pending).
- **Frontend** (`web/src/lib/vigiliaDetail.test.js`, +7): status/badge/label, estado de e-mail,
  `applyDismiss` (recalcula pending/status/summary, imutável), no-op gracioso.
- `pytest`: **1873 passed, 1 skipped**. `npm run test:unit`: **124 pass**. `npm run build`: OK.

## Validação pós-deploy (manual — roteiro)
1. Dashboard → vigília phishing (Crítico) → expandir → lista de domínios suspeitos com ações.
2. "Não é ameaça" → domínio sai da lista, badge atualiza (sem reload).
3. Vigília SSL → expandir → emissor, dias restantes, orientação por urgência.
4. Vigília Score → expandir → delta + checks que mudaram.
5. Vigília sem dados → "Aguardando a primeira verificação".
6. Mobile 375px: expand legível, ações acessíveis.
7. Rodar a stack `docker-compose.dev.yml` (Docker precisa estar de pé) e validar desktop + mobile.

## Arquivos
- Novos: `api/vigilia_details.py`, `web/src/components/dashboard-v2/VigiliaDetail.jsx`,
  `web/src/lib/vigiliaDetail.js`, `web/src/lib/vigiliaDetail.test.js`,
  `tests/test_kl123_vigilia_details.py`.
- Alterados: `api/main.py` (3 endpoints + orquestrador + `_KNOWN_EVENTS`), `discovery/store.py`
  (4 métodos), `web/src/components/dashboard-v2/MonitoringSection.jsx`, `web/package.json`
  (`test:unit`), `docs/API.md`, `CLAUDE.md`.
