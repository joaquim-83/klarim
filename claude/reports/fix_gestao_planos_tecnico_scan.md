# FIX — Gestão de planos + seção de técnico + scan do admin

**Data:** 2026-07-16
**Sem card Jira** — fixes em entregas existentes (KL-69, KL-44 P3, admin Alvos)
**Motivação:** desbloquear o teste do boletim (P3) — precisa promover uma conta para
Agency e vincular um técnico.
**Status:** ✅ Concluído — 906 testes passando, deploy pendente de push.

---

## Problema 1 — Gestão de planos ausente na página Usuários

### Diagnóstico

A página `/painel/usuarios` (KL-69) mostrava plano/status como **texto** e remetia para
"/painel/assinantes" (`UserDetail`, seção "Assinatura"). **Não havia controle de plano.**

**Descoberta importante:** o backend de gestão de plano **já existia** (KL-44 P1) e é
robusto — não foi preciso criar os 3 endpoints do card:
- `PATCH /admin/subscriptions/{account_id}/plan` → `plans.change_plan` + `_sync_user_vigilias`
  (cria as vigílias do novo plano em todos os sites; desativa as que o plano não permite).
  `change_plan('free')` **já** zera o status para `free`.
- `PATCH /admin/subscriptions/{account_id}/trial` → `plans.extend_trial` (+N dias).
- `account_id == users.id` (documentado no schema).

### Correção

- **`adminApi.js`** — 3 aliases finos sobre os endpoints existentes (sem duplicar backend):
  `changeUserPlan(id, plan)`, `extendUserTrial(id, days=30)`, `resetUserFree(id)`
  (este = `change_plan(id, 'free')`, que já ajusta status + vigílias).
- **`UsuariosPage.jsx`** — novo componente `SubscriptionEditor` no detalhe do usuário:
  - **Dropdown Free/Pro/Agency** pré-selecionado no plano atual → troca chama
    `changeUserPlan` → toast "Plano de {email} alterado para {plano}".
  - **"Estender trial 30d"** (só se status = trial) → toast.
  - **"Resetar para Free"** (só se plano ≠ free) → toast.
  - Cada ação recarrega a lista (`onChanged` → `load()`).

### Desvio do card

O card pedia **criar** `PUT /admin/users/{id}/plan`, `POST .../extend-trial`,
`POST .../reset-free`. **Reusei** os endpoints `/admin/subscriptions/*` já existentes e
testados (KL-44 P1) — a lógica pedida (ajuste de vigílias, status free, +30d de trial) já
está lá. Criar endpoints paralelos seria duplicação. Os nomes dos métodos no `adminApi`
seguem a intenção do card (`changeUserPlan`/`extendUserTrial`/`resetUserFree`).

---

## Problema 2 — Seção de técnico não visível no dashboard

### Diagnóstico

`TechnicianSection.jsx` (KL-44 P3) e `MonitoringSection` (P4) **já são renderizados** em
`SiteDetail.jsx` (linhas ~110/113), e o `SiteDetail` **é** montado em
`/dashboard/site/{id}` (`client:load`, hidrata OK — a CSP estrita do site whitelista os 3
hashes inline do Astro; o dashboard principal já hidrata pelo mesmo caminho). A
`TechnicianSection` renderiza **incondicionalmente** (não depende de ser dono).

**Causa real:** o **dashboard principal** (`Dashboard.jsx`) lista os sites com `SiteCard`
(resumo) e só levava ao técnico via **"Ver detalhes"** — um clique adiante. O dono não
encontrava o "Técnico responsável" / "Compartilhar laudo" na tela inicial.

### Correção

- **`Dashboard.jsx` / `SiteCard`** — botão **"🔧 Técnico e laudo ▸"** que expande, **no
  próprio card**, a `TechnicianSection` (vincular/revogar técnico + gerar laudo + WhatsApp).
  Importada em `Dashboard.jsx`. "Ver detalhes" continua levando ao `SiteDetail` completo
  (com Monitoramento contínuo do P4 + checks).
- O laudo compartilhável (código + link + WhatsApp) já vem da própria `TechnicianSection`
  (`POST /account/shared-report/create`).

---

## Problema 3 — Botão "Escanear" no admin não dava resultado

### Diagnóstico

O botão **já chamava** `admin.scanTarget(id)` → `POST /targets/{id}/scan`, que
**enfileirava** na fila Redis (`klarim:scan_queue`, consumida pelo scan worker). Ou seja,
funcionava — mas **assíncrono**: mostrava "Scan enfileirado ✓" e **nada visível
acontecia** (o resultado dependia do worker e do rate limit horário). Para testar/validar
na hora, parecia "não funcionar". O modal "Adicionar e escanear" também só enfileirava.

### Correção

- **Backend** — `POST /targets/{id}/scan?sync=1` roda a varredura **síncrona** reusando
  `get_or_scan` (o mesmo mecanismo do `/scan/summary` público: escaneia, cacheia e
  persiste como `source='admin'`) e devolve `score` / `semaphore` / `fail_count`. Sem
  `sync`, mantém o comportamento antigo (enfileira). Site lento pode se aproximar do
  `proxy_read_timeout` (180s), mas o resultado **cacheia** (retentativa pega o cache).
- **`adminApi.js`** — `scanTarget` passou a usar `?sync=1`; `enqueueScan` (assíncrono)
  ficou como legado.
- **`AlvosPage.jsx`** — `scanNow(t)`: mostra **"Escaneando…"** (spinner) e depois
  **"Scan de {domínio} concluído: {score}/100"** + recarrega a linha. O modal "Adicionar
  e escanear" agora encadeia add → scan síncrono e mostra o score.
- **`AlvoDetalhePage.jsx`** — `scanNow()`: mesma lógica no detalhe do alvo.

---

## Testes

- **`tests/test_fix_scan_sync.py`** (5 casos): endpoint protegido por JWT; `sync=1` devolve
  `score`/`semaphore` (100/verde); reporta `fail_count`; sem `sync` **enfileira** com
  `source='admin'`; alvo inexistente → 404.
- Gestão de plano: os endpoints `/admin/subscriptions/*` já são cobertos por
  `tests/test_subscriptions.py` (change_plan trial→free/keep-trial, extend_trial,
  endpoints admin, bulk). Os aliases do `adminApi` apenas os invocam.
- **Suite completa: 906 passed, 1 skipped.**

## Segurança

- Todos os endpoints tocados estão sob prefixos protegidos por **JWT admin**
  (`/targets/*`, `/admin/subscriptions/*`) — verificado (`test_scan_endpoint_protected`).
- Nenhum dado sensível novo exposto. O scan síncrono é a mesma varredura passiva de
  sempre. `contact_email` continua não exposto.

## Documentação

- **`claude.md`** — KL-69 ganhou a gestão de plano no detalhe do usuário; novo gotcha
  "Escanear no painel = síncrono".
- **`docs/API.md`** — `POST /targets/{id}/scan?sync=1` documentado; `/admin/subscriptions/*`
  detalhado como o mecanismo de gestão de plano da página Usuários.

## Deploy

Sem migration, sem flush de Redis. Só código (API + frontend). Pós-deploy: promover uma
conta para Agency em `/painel/usuarios` e vincular um técnico no dashboard para validar o
boletim (P3).
