# KL-71 — Fix: 9 bugs no fluxo de propriedade, técnico e landing page

**Card:** KL-71 · **Prioridade:** Highest
**Dependências:** KL-68 ✅, KL-44 P3 ✅, KL-67 ✅
**Status:** ✅ Concluído — 9 bugs corrigidos, 921+ testes passando, deploy pendente de push.

Caso de teste real: `igoove.com` (contact_email `jscidinei@gmail.com`), reivindicado por
`cidinei@igoove.com`, dono verificado `jscidinei@gmail.com`, técnico `jccidinei@gmail.com`.

---

## Bug 1 — Tier 1 não verificava match de domínio do e-mail

**Diagnóstico:** `_process_claim`/`account_add_site` só comparavam o e-mail **exato** ao
`contact_email`. `cidinei@igoove.com` reivindicando `igoove.com` não auto-verificava (o
contato público era `jscidinei@gmail.com`).

**Correção:** novo helper `_ownership_method(email, target_id)` com precedência:
`auto_email` (e-mail == contact_email) → `auto_domain` (domínio do e-mail == domínio do
site, removido `www.`). **Exceção:** provedores públicos (`PUBLIC_EMAIL_PROVIDERS`:
gmail/hotmail/outlook/uol/…) **nunca** valem para domain-match (`email@gmail.com` não prova
ser dono de gmail.com). **First-come** preservado (`site_has_owner` bloqueia). Aplicado em
`_process_claim`, `account_add_site`, o histórico de `_create_account_record` e o
`/account/sites/{id}/claim` (que ganhou 409 se já há dono).

## Bug 2 — CTA "Reivindicar" aparecia com dono verificado

**Diagnóstico:** `ClaimSite.jsx` (perfil público) mostrava "Reivindicar este site" mesmo
com `owner_verified=true` (a API já devolvia a flag).

**Correção:** deslogado + `ownerVerified` → "✓ Este site tem um dono verificado. **Criar
conta e monitorar →**" (não "Reivindicar"). Logado sem monitorar + `ownerVerified` → título
e texto ajustados, só "Monitorar". Logado monitorando sem dono já ocultava o botão de
verificar (mantido).

## Bug 3 — Sem feedback quando o site já tem dono

**Diagnóstico:** `/account/ownership/status` não distinguia "sem contato p/ verificar" de
"outro usuário é dono".

**Correção:** o endpoint passou a devolver `has_other_owner`. `SiteDetail`
(`OwnershipSection`) mostra: "ℹ️ Este site já tem um dono verificado. Se você é o
proprietário legítimo, entre em contato com seguranca@klarim.net."

## Bug 4 — Link do convite de técnico não abria o laudo

**Diagnóstico:** o template `build_technician_invite` **já** usava `{SITE_BASE}/laudo/{code}`,
e o endpoint criava o laudo. A causa: `_make_shared_report` devolve `None` quando o site
**ainda não tem scan** → `code=""` → link quebrado `…/laudo/`.

**Correção:** no `technician_invite`, se não há laudo (sem scan), **escaneia agora**
(`_safe_scan`, síncrono) e recria o laudo. Fallback extra no template: sem `code`, o link
aponta para o perfil público `/site/{domain}` em vez de um laudo vazio.

## Bug 5 — Dashboard não linkava para a landing pública

**Correção:** link "Ver perfil público → /site/{domain}" (nova aba, `rel=noopener`) no
`SiteDetail` (sob o domínio) e no `SiteCard` do dashboard ("🌐 Perfil público").

## Bug 6 — Sem validação de conflito de papel

**Correção:** `technician_invite` valida antes de criar o vínculo (todos **422**):
auto-convite (`email == dono`), convidar o dono verificado como técnico (`get_target_owner`),
e técnico já vinculado ativo (via `get_technician_links`).

## Bug 7 — Perfil de técnico sem indicação de role

**Correção:** `Dashboard.jsx` mostra o badge **"🔧 Profissional de TI"** no header e na
seção de plano quando `role ∈ {technician, both}` (`_user_public` já devolve `role`). A
seção "Sites dos meus clientes" (com estado vazio) já existia.

## Bug 8 — Usuário não podia remover site do monitoramento

**Diagnóstico:** `DELETE /account/sites/{id}` existia mas só fazia `unlink` e não tinha
botão no dashboard.

**Correção:** o endpoint agora é **self-service completo** — revoga a posse
(`mark_ownership_revoked` se era dono) e **desativa as vigílias** do site
(`disable_user_site_vigilias`, método novo), **sem notificação** (diferente do remove-site
admin). `SiteCard` ganhou botão "Remover" com confirmação.

## Bug 9 — Painel admin não distinguia role

**Correção:** `list_users_with_sites` passou a trazer `u.role`; `UsuariosPage` ganhou a
coluna **"Perfil"** com badge (👤 Dono / 🔧 Técnico / 👤🔧 Ambos) na tabela e no detalhe
expandido.

---

## Testes (`tests/test_accounts.py`, +20 casos KL-71)

- `_ownership_method`: e-mail exato, domain-match, `www.`, provedor público rejeitado,
  domínio diferente.
- add-site domain-match auto-verifica; respeita first-come; `/claim` por domínio.
- `ownership_status.has_other_owner`.
- convite técnico cria laudo + link `/laudo/{code}`; template usa o laudo; fallback p/
  perfil sem scan.
- conflito de papel: auto-convite / dono-como-técnico / já-vinculado → 422.
- remoção self-service (remove + desativa vigílias) + 404.
- `test_claim_requires_email_match` ajustado (o domain-match do KL-71 tornou o caso antigo
  um match legítimo — agora usa domínios distintos).

**Suite completa: 921+ passed, 1 skipped.**

## Regras invioláveis

- **`contact_email` nunca exposto** — domain-match compara só domínios; e-mail do dono ao
  técnico segue mascarado.
- **Scanner/profiler intocados** (o scan do convite reusa `get_or_scan`, passivo).
- **First-come** preservado em todos os caminhos (claim, add-site, verify → 409/negação).
- **Rate limit Redis+fallback** mantido (convite 10/h, laudo 30/h, ownership 5/h).
- Provedores públicos nunca auto-verificam por domínio.

## Documentação

`claude.md` (card KL-71), `docs/API.md` (ownership/status, claim, DELETE, invite),
`docs/SECURITY.md` (auto_domain + PUBLIC_EMAIL_PROVIDERS, conflito de papel, remoção
self-service).

## Deploy

Sem migration (usa colunas existentes; `role` já no schema). Sem flush de Redis.
