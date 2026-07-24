# KL-97 + KL-98 — Gestão do dono no dashboard: monitoramento/notificações + perfil público/selo

**Cards:** KL-97 + KL-98 (implementados juntos — mesmo dashboard, mesmas tabelas, mesma auth) · **Status:** ✅

## Descoberta (o que já existia)
Boa parte da fundação já vinha de cards anteriores: o **selo** (KL-44 P5: `GET /seal/{domain}` +
`web/public/seal/widget.js` + `SealSection` no dashboard), a **edição admin de perfil**
(`PUT /targets/{id}/profile` + `update_site_profile_fields` + a regra de ouro `edited_by_admin`),
as **vigílias** por plano (`_vigilia_allowed_types`) e a leitura read-only (`GET /account/vigilias`).
O que faltava era a camada do **dono**: controlar, não só ver.

## Segurança (regra central)
**Ownership em TODO endpoint** via `_owned_site(request, target_id, min_level, require_owner)`:
auth → `_require_level` → `get_user_site` (o vínculo tem que ser do próprio usuário). Nível **≥1**
p/ monitoramento/notificações; **nível 3 + `is_owner`** p/ perfil/visibilidade/selo. Rate limit
10/min nas escritas (`_cfg_rate_limit`). Sanitização estrita (strip HTML, limites, valida CNPJ/
telefone/URL → 422). `contact_email` do target nunca sai (não vive em `site_profile`).

## KL-97 — Monitoramento e notificações
- **`GET/PUT /account/sites/{id}/monitoring`**: o GET lista TODAS as vigílias (`list_site_vigilias`,
  habilitadas ou não) com `configurable` (o plano habilita?) + `requires_plan` (`_VIGILIA_MIN_PLAN`:
  core+uptime=Pro, changes/phishing=Agency). O PUT (`set_vigilia_enabled`) liga/desliga por-tipo
  (cria se não existe; threshold do `score` no `last_data` JSONB via `jsonb_set`); **toggle fora do
  plano → 403 `requires_plan`**; vigília desligada é **preservada** (enabled=false), não deletada.
- **`GET/PUT /account/notification-preferences`**: novas colunas em `users` (`bulletin_frequency`
  NULL=usa a do plano · `bulletin_hour` · `notify_vigilia`/`notify_bulletin`/`notify_news`). O
  **`list_users_due_bulletin` foi reescrito** p/ **frequência EFETIVA**: o override do user vence o
  plano (free=mensal/pro=semanal/agency=diário); `off` e `notify_bulletin=false` não recebem;
  `immediate`→`daily` na cadência do worker. O bulletin worker não muda (só a query que ele chama).

## KL-98 — Perfil público e selo
- **`PUT /account/sites/{id}/profile`** (nível 3 + posse): edita 15 campos + tags.
  `_sanitize_owner_profile` remove HTML, aplica limites e valida CNPJ/telefone/URL (422). O store
  ganhou `update_site_profile_fields(..., actor='owner')` — marca `edited_by_owner` e **acumula os
  campos tocados** em `owner_edited_fields` (dedup `ARRAY(SELECT DISTINCT unnest(existente || novos))`).
- **Preservação contra a IA (dupla defesa):** `merge_ai_into_profile` pula os campos em
  `owner_edited_fields`; e o `upsert_site_profile._upd` ganhou um CASE **por-campo**
  (`'col' = ANY(owner_edited_fields)`) — assim, mesmo que o dono **limpe** um campo, o enrich não o
  repreenche. (A regra `edited_by_admin` do operador segue valendo em paralelo.)
- **`PUT /account/sites/{id}/visibility`**: o dono liga/desliga a landing `/site/{domain}`
  (reusa `set_profile_visibility`; o público já respeita `public_visible` + sai do sitemap).
- **Selo:** colunas `site_profile.seal_enabled`/`seal_style`. `GET /account/sites/{id}/seal` devolve
  o estado + 3 variantes (badge/footer/floating) com `embed_code`; `PUT` grava (`set_seal_config`).
  O público `GET /seal/{domain}` agora inclui `enabled`/`style`/`verified`; o `widget.js` ganhou
  `data-style` (footer = barra full-width, floating = fixed bottom-right) e **esconde o selo se
  `enabled=false`**.

## Tracking (KL-57)
4 eventos no `_KNOWN_EVENTS`: `vigilia_toggled`, `bulletin_frequency_changed`, `profile_edited`,
`seal_configured` (disparados pelo frontend).

## Frontend
- **`MonitoringConfig.jsx`** (modal): toggles de vigília (cadeado + "Disponível no Pro/Agency" quando
  fora do plano), dropdown de threshold do score (5/10/20), e preferências de notificação (frequência
  do boletim + toggles). Salva nos 2 PUTs.
- **`ProfileEditor.jsx`** (modal, 2 colunas): formulário (15 campos + tags + visibilidade) à esquerda,
  **preview ao vivo** + config do selo (enable + estilo + embed_code com "Copiar código") à direita.
- Abertos por **"⚙️ Configurar"** / **"✏️ Editar perfil"** (só nível 3) na `MonitoringSection`.

## Testes
`test_kl97_98_owner.py` (**+18**): monitoring GET/PUT (posse 404, plan-gating 403, threshold),
notificações (defaults/update/422), perfil (nível 3 obrigatório, sanitização HTML, `owner_edited_fields`,
CNPJ/telefone inválidos 422), visibilidade, selo GET/PUT + público reflete `enabled`, e a **preservação
da IA** (`merge_ai_into_profile` pula campos/ tags do dono). **Suite: 1688 backend** + 108 `node --test`;
build Astro OK. O SQL novo (array-dedup, `jsonb_set`, o CASE de frequência efetiva) foi validado no
**Postgres 16 da VM** (transação com rollback).

## Validação pós-deploy
**KL-97:** dashboard → site → "⚙️ Configurar" → toggles funcionam; desligar SSL → salvar → recarregar
persistido; frequência do boletim → salva. Sem posse → 404. **KL-98:** "✏️ Editar perfil" → editar →
preview atualiza → salvar → `/site/{domain}` reflete; visibilidade "Oculto" → landing some; ativar selo
→ copiar código → `GET /api/seal/{domain}` traz `enabled`. Sem `is_owner`/nível 3 → 403. Fechar KL-97 e
KL-98 no Jira após validação.
