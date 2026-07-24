# KL-105 — Frontend de conversão: InlineSignup imediato + estados B/C + magic link

**Card:** KL-105 · **Status:** ✅

## Contexto e descoberta
O card pedia layout 2 colunas, InlineSignup com 3 estados, magic link no `/entrar` e endpoint de
status. **Ao mapear o código, boa parte já existia do KL-99:** o layout 2 colunas
(`md:grid-cols-2`), `InlineSignup.jsx`, `MonitorConsent.jsx`, o endpoint `/account/magic-link` e o
botão "Enviar link de acesso" no `/entrar`. Os nomes de endpoint do card (`/api/auth/register`) eram
suposições — o real é `/account/signup-inline`. Então o trabalho foi o **delta** sobre o KL-99, não
um rebuild.

## Mudança central — `POST /account/signup-inline` converte na hora
O KL-99 fazia o signup inline criar a conta **pendente** + enviar e-mail de confirmação; o
monitoramento só ativava ao confirmar. Isso é a fricção que o card ataca (145 scans → 4 contas,
2,8%). Agora (espelhando o `monitor-from-alert`):
- cria conta nível 1 (`source=inline`), **vincula o site + cria as vigílias + auto-verifica posse
  Tier 1** (e-mail == domínio) e **loga** (cookie de sessão) — tudo numa chamada;
- retorna `{status: monitoring_active}` (+cookie) ou `{status: already_exists}`;
- **sem confirmação prévia** (lição KL-89: 97% abandonavam o gate de e-mail);
- rate limit **5/min & 30/dia por IP** (era 3/h) + blocklist de descartáveis;
- envia um welcome que **valida o endereço**: um bounce cai na blocklist (webhook Resend) →
  alertas futuros suprimidos.

## Novo endpoint — `GET /account/monitoring-status?domain=`
Auth **opcional** (`optional_user` — sem sessão → `{logged_in:false, monitoring:false}`, nunca 401).
Logado → `{logged_in:true, monitoring:bool, user_email}`. Rate limit 30/min/IP. Só o e-mail do
PRÓPRIO usuário; nunca dado de terceiros. O CTA do logado usa isso para os estados B/C.

## Frontend
- **`InlineSignup.jsx`** (estado A, visitante orgânico): sucesso agora é **inline** "✅ Monitoramento
  ativado! Você receberá alertas em {email}" (sem redirect/modal) + link ao dashboard (já logado).
  `already_exists` → **dispara magic link automaticamente** + "Conta já existe — enviamos um link de
  acesso". Botão **desabilitado até e-mail válido** (`isValidEmail`), loading "Processando…". CTA
  `border-2 border-brand-500` + benefícios com `✓` verde + **texto legal** (Termos/Privacidade). 4
  eventos KL-57: `inline_signup_shown` (mount), `inline_signup_click`, `inline_signup_success`,
  `inline_signup_existing` (+ mantém os antigos `signup_inline_clicked`/`account_created` p/ continuidade).
- **`MonitorConsent.jsx`** (mode=account, logado): no mount busca `monitoring-status(domain)` →
  **estado B** "Você já monitora este site" (+painel) se já monitora; senão o **estado C** "Sim,
  monitorar" existente (`POST /account/sites`). mode=alert inalterado.
- **`/entrar`** (`LoginForm.jsx`): magic link já existia (KL-99) — verificado, sem mudança.
- **Layout 2 colunas** (`ScanResultDetail.jsx`): já existia — score/PDF à esquerda, CTA à direita
  (acima do fold, `md:grid-cols-2 md:items-start`), detalhes técnicos abaixo. Sem mudança estrutural.

## Segurança (revisão)
- Rate limits: signup-inline 5/min & 30/dia; monitoring-status 30/min. Validação de e-mail no
  backend (`_ACCOUNT_EMAIL_RE` + trim/lowercase + blocklist de descartáveis) — não confia no front.
- `monitoring-status` nunca vaza dado de terceiros (só booleano + e-mail do próprio user); auth
  opcional via `optional_user` (falha → anônimo, nunca 401).
- Cookie de sessão: `klarim_session` HttpOnly/Secure/SameSite=Lax (inalterado).
- **Trade-off documentado:** ativar monitoramento sem confirmação de e-mail é uma decisão de
  produto do card (conversão > double-opt-in). Mitigação: o welcome valida o endereço e um bounce
  auto-suprime (blocklist). **Follow-up recomendado** se a reputação exigir: gatear o 1º alerta de
  vigília/boletim em `email_confirmed` para contas `source=inline`.

## Testes
- Backend `test_kl99_levels.py` (**+4 líquido**): `signup-inline` nova semântica (monitoring_active +
  cookie + vigílias + posse), rate limit 5/min, `already_exists` sem cookie, e `monitoring-status`
  (anônimo / logado monitorando / logado não-monitorando). **Suite: 1666 passed.**
- Frontend `scanView.test.js` (**+1** `node --test`): `isValidEmail`. **108 node --test.** Build Astro OK.

## Validação pós-deploy (a fazer)
1. Desktop 1440px `klarim.net/scan?url=…` → 2 colunas, CTA laranja à direita sem scroll.
2. E-mail novo → "Monitoramento ativado!" inline + `user_sites` no banco + cookie de sessão.
3. E-mail existente → "Conta já existe" + magic link no inbox.
4. Logado monitorando → "Você já monitora este site"; logado sem monitorar → "Sim, monitorar".
5. `/entrar` → "Enviar link de acesso" funciona.
6. Rate limit: 6ª tentativa/min do mesmo IP → 429.
7. `_KNOWN_EVENTS` inclui os 4 eventos `inline_signup_*`.
