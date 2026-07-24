# KL-107 — Segurança: ownership no verify/check + aviso ao dono quando terceiro adiciona o site

**Card:** KL-107 (auditoria de segurança 24/07) · **Status:** ✅

A auditoria testou IDOR em 7 endpoints, escalação vertical em 3 admin, mass assignment e vazamento —
**10 testes passaram**, 2 achados corrigidos aqui.

## Achado 1 — IDOR no `verify/check`
`POST /account/sites/{id}/verify/check` era o **único** `/account/sites/{id}/*` sem ownership check:
devolvia `200 {status: no_pending}` para um `site_id` de OUTRO usuário (`get_pending_domain_verification`
filtra por user, então nunca achava pendência de terceiro → 200 genérico). Risco baixo (não dá acesso
nem dado sensível) mas permitia **enumerar** quais sites têm verificação de domínio pendente.
**Fix:** `get_user_site(user_id, target_id)` logo após o gate de nível → **404** se o site não é da
conta (idêntico aos demais endpoints; comparação constant-time via SQL). Confirmado que `verify/start`
já bloqueava (tinha o check desde o KL-99).

## Achado 2 — Terceiro adiciona site com dono verificado (Opção B: permitir + avisar)
`POST /account/sites` permite um não-dono monitorar (is_owner=false) um site que já tem dono verificado.
**Decisão de produto:** NÃO bloquear (o modelo agência→técnico do KL-70 depende de terceiros poderem
monitorar), mas **avisar o dono**.
- **`store.get_site_owner(target_id)`** — o dono VERIFICADO (`is_owner=TRUE AND verified_at IS NOT NULL`),
  id + e-mail, ou None.
- **`_notify_owner_site_added(target_id, added_by_email, domain)`** (fire-and-forget, `_spawn` no
  handler; nunca derruba o add_site): se há dono e ele ≠ quem adicionou, envia o aviso e loga o evento.
  **Dedup 1/dia/target** (Redis `notify_site_added:{tid}`, TTL 24h — evita spam se vários adicionarem o
  mesmo site).
- **`KlarimMailer.send_owner_site_added`** — e-mail **transacional** (`klarim@klarim.net`, `RESEND_FROM`),
  **TEXTO PURO**, informativo, **sem link de ação** (nada de "clique para bloquear"); explica que o
  terceiro só recebe alertas públicos, não tem acesso a painel/dados/perfil. `email_type='owner_notification'`.
- **KL-57:** evento `owner_notification_sent` (`_KNOWN_EVENTS` + `log_event`); a contagem autoritativa
  ("quantos donos avisados") vem do `email_log` (`email_type='owner_notification'`) → sinal de demanda
  por monitoramento multi-user (input p/ o KL-70).

## Segurança (revisão)
- Ownership via SQL (safe/constant-time). O aviso **só revela o e-mail** de quem adicionou — nunca
  id/plano/dados de conta. Rate-limit/dedup por target/dia. E-mail informativo, sem ação automática.
  A adição do site **não muda** (response idêntico); a notificação é best-effort (try/except).

## Testes
`test_kl107_security.py` (**+9**): `_notify_owner_site_added` (envia ao dono + evento; dedup 24h; sem
dono → skip; próprio dono → skip; falha de e-mail engolida) + HTTP (verify/check de terceiro → 404;
site próprio sem pendência → 200 no_pending; verify/start de terceiro → 404; add_site de terceiro com
dono verificado → 200 is_owner=false). **Suite: 1698 backend** + 108 `node --test`.

## Validação pós-deploy
1. `POST /account/sites/{site_de_outro}/verify/check` → **404** (não 200).
2. Conta teste adiciona um site com dono verificado → e-mail `owner_notification` ao dono (Resend +
   `email_log`), sender `klarim@klarim.net`, subject com o domínio.
3. Repetir a adição no mesmo dia → **sem** e-mail duplicado (dedup).
4. Domínio SEM dono verificado → nenhuma notificação. Fechar KL-107 no Jira.
