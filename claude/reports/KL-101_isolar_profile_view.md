# KL-101 — Isolar profile_view no subdomínio `perfil.klarim.net`

**Card:** KL-101 (High) · **Status:** ✅ código pronto + testado · **Deploy:** ⚠️ **PENDENTE** de o
dono confirmar que `perfil.klarim.net` está verificado no Resend (senão os envios falham).

---

## Contexto

O aviso "perfil consultado" (`profile_view`) era o **último cold** saindo pelo domínio
transacional `klarim.net` (~15k/semana, via `_proactive_from` = `alerta@klarim.net`). Os cold
alerts já haviam sido isolados (KL-91, → `alertas./aviso.klarim.net`). Este card tira o
profile_view do `klarim.net` para o domínio ficar **100% transacional** — o que protege a
entrega das confirmações de conta (o problema original que motivou o KL-91).

## O que mudou

### 1. Remetente dedicado (`notifier/email_client.py`)
- Novo `_profile_view_from()` → `notifica@perfil.klarim.net` (`PROFILE_VIEW_FROM_EMAIL`/`_NAME`,
  default no código). **NÃO rotaciona** com os cold alerts do KL-91 — o volume do profile_view
  (~2k/dia) destruiria o warmup deles (a 100/dia). Subdomínio próprio, warmup próprio.
- `send_profile_view` agora usa `_profile_view_from()` (era `_proactive_from` = klarim.net).

### 2. Template texto puro SEM links (`build_profile_view_text`)
- Reescrito para o texto do card: sem link clicável (antes tinha `/site/{domain}` + link de
  descadastro), sem urgência, `klarim.net` só como texto, **opt-out por resposta** (header
  `List-Unsubscribe: <mailto:scan@klarim.net?subject=remover>`, como o KL-91 — sem One-Click, que
  exige URL https). Assunto: `{domain} foi consultado na Klarim`. A assinatura mantém `score`/
  `semaphore`/`cta_url` (chamadores inalterados) mas o corpo novo não os usa.

### 3. `email_log.from_domain = perfil.klarim.net`
Automático — `_send` extrai o `from_domain` do campo `from` (`_domain_of_from`), que já existe
desde o KL-91. Nada a fazer além de trocar o remetente.

### 4. Volume — dedup por dono + teto de warmup (`api/main.py::_profile_view_notify`)
- **Dedup por DONO: 1 aviso por e-mail por dia** (`notify_owner:{email}` Redis, NX EX 86400) —
  requisito do card. Some com a dedup por **domínio/24h** que já existia (`notify:{domain}`).
- **Teto diário de warmup do subdomínio** (`PROFILE_VIEW_DAILY_LIMIT`, default 200, editável no
  painel): contador `profileview:daily:{YYYYMMDD}` (INCR só quando ENVIA de fato). Ao bater o
  teto, pula. Começa em 200 e sobe manualmente (mesma lógica do cold alert warmup).
- Fail-open: qualquer erro de Redis não derruba o envio.

## Mapa final de remetentes (KL-101 fecha o isolamento)
| Remetente | Domínio | Tipo | Volume |
|---|---|---|---|
| `klarim@klarim.net` | klarim.net | Transacional | ~50/dia |
| `scan@alertas.klarim.net` / `scan@aviso.klarim.net` | alertas./aviso.klarim.net | Cold alert (KL-91) | ~100/dia cada (warmup) |
| `notifica@perfil.klarim.net` | perfil.klarim.net | Profile view (KL-101) | teto 200/dia (warmup) |

**`klarim.net` = 100% transacional, zero cold.**

## Segurança
Sem novos endpoints/inputs. O remetente vem de env (não hardcoded com credencial). Opt-out por
resposta respeitado via blocklist (o inbox `scan@klarim.net` recebe os "remover"). O corpo não
tem links → sem vetor de tracking/phishing. `contact_email` nunca aparece no corpo/log em claro.

## Testes
`test_kl101_profile_view.py` (+7): remetente dedicado (default+override), template sem links,
dedup por dono (mesmo domínio + entre domínios), teto diário (cap 0 → nada; INCR no envio),
config editável. Ajustados os testes que assumiam o remetente/HTML antigos
(`test_alert_plain_text`, `test_alert_sender_migration`, `test_unsubscribe_fix`). **Suite: 1613
passed, 1 skipped.**

## Deploy (quando o dono confirmar a verificação no Resend)
1. **Pré-requisito manual (dono):** `perfil.klarim.net` no Cloudflare DNS + verificado no Resend
   (mesmo processo de alertas/aviso). **Avisar quando estiver verificado.**
2. `.env` da VM: opcional `PROFILE_VIEW_FROM_EMAIL=notifica@perfil.klarim.net` (o default do código
   já é esse) + `PROFILE_VIEW_DAILY_LIMIT=200`.
3. Push → CI → **recriar** os containers `api` (lê o env; é quem envia o profile_view).
4. Validar: `email_log` da última hora → profile_view com `from_domain=perfil.klarim.net`; **zero**
   profile_view por `klarim.net`/`alerta@klarim.net`; dedup (mesmo dono ≤1/dia).

⚠️ **Não deployar antes da verificação** — o Resend rejeita `from` de domínio não verificado → os
profile_view falhariam (pior que hoje). Por isso o código está pronto mas o deploy aguarda o "ok".
