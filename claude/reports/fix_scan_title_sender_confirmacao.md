# Fix — sanitização do título, remetente de e-mail e fluxo de confirmação

**Origem:** varredura de segurança + teste de confirmação (2026-07-21) · **Status:** Implementado
(aguardando deploy verde + troca do `.env` na VM)

---

## 1. Sanitizar `<title>` na página `/scan`

**Problema:** o `?url=` era refletido no `<title>` (o Astro escapa, então não é XSS explorável,
mas um scanner de segurança não pode refletir input não-sanitizado). O `safeDomain` antigo caía no
`catch { return u }` → devolvia o input cru quando não parseava.

**Fix:** `web/src/lib/scanTitle.js` (PURO, testado): `safeScanDomain(url)` extrai só o hostname
(ignora protocolo/path/query/tags) + strip `[^a-z0-9.-]` (defesa em profundidade) + exige um ponto;
`scanTitle(url)` monta o título:

| `?url=` | `<title>` |
|---|---|
| `https://igoove.com/path` | `Analisando igoove.com · Klarim` |
| `igoove.com` | `Analisando igoove.com · Klarim` |
| `<script>alert(1)</script>` | `Analisando um site · Klarim` |
| `` (vazio) | `Análise de segurança · Klarim` |

`scan.astro` usa `scanTitle(url)`. **11 testes** `node --test` (`scanTitle.test.js`), incluindo a
garantia de que o título nunca contém `<`/`>`.

> Nota: para input vazio mantive `Análise de segurança` (mais limpo que o "Analisando" solto do
> exemplo do card). Os dois casos de validação do card (`<script>` e `igoove.com`) passam.

---

## 2. Remetente transacional: `seguranca@` → `klarim@`

**Problema:** "seguranca" é keyword de phishing e, com domínio aged, elevava o spam score — a
confirmação de conta caía no spam.

**Descoberta:** o remetente **não** é hardcoded em `email_client.py` — todo transacional usa
`self.from_address = os.environ.get("RESEND_FROM") or DEFAULT_FROM`. As 4 ocorrências de
`seguranca@` no arquivo eram **docstrings**. `_mailer()` lê `RESEND_FROM` a **cada envio**.

**Fix (código + config):**
- Docstrings de `email_client.py` atualizadas (`seguranca@` → `klarim@`, `RESEND_FROM`).
- `.env.example`, `docs/DEPLOY.md`, `CLAUDE.md` (§3) e `docs/SECURITY.md` (mapa de remetentes).
- **VM:** trocar `RESEND_FROM=Klarim <klarim@klarim.net>` no `/opt/klarim/.env` + **recriar** o
  container `api` (e workers que e-mailam) — a troca do `.env` só vale ao recriar (env no start).

**Mapa de remetentes pós-fix (sem misturar):**

| Endereço | Uso | Env | Mudança |
|---|---|---|---|
| `klarim@klarim.net` | Transacional (confirmação, boas-vindas, convites, vigílias, senha) | `RESEND_FROM` | ✅ novo |
| `alerta@klarim.net` | Proativo/batch (alertas, perfil consultado, boletim ao dono) | `ALERT_FROM_EMAIL` | sem mudança |
| `scan@klarim.net` | Reply-To de TODOS + inbox | `REPLY_TO_DEFAULT` | sem mudança |

`klarim.net` é o domínio verificado no Resend (SPF/DKIM/DMARC) → qualquer local-part `@klarim.net`
envia sem mailbox real; as respostas caem no `scan@` (inbox Hostinger).

---

## 3. Fluxo de confirmação — página de feedback `/confirmado`

**Problema:** o link do e-mail (`/confirmar?token=`) redirecionava, no sucesso, para
`/dashboard?confirmed=1`. Como o usuário raramente tem sessão no navegador do clique, o
`/dashboard` (auth-gated) o jogava no `/entrar` **sem feedback** de que confirmou.

**Fix (a rota Astro chama a API internamente — opção prevista no card):**
- `confirmar.astro` (SSR, inalterado como entrada): valida o token via `/api/account/confirm`
  server-side e **redireciona para `/confirmado?status=ok|already|invalid`** em TODOS os casos. O
  token **nunca** vai à URL final (só o `status`) nem ao cliente.
- **Nova `confirmado.astro`** (SSR, `noindex`, **zero JS**): lê `?status=` e renderiza 3 estados
  com Header/Footer do Klarim (tema claro/escuro), botão sempre visível → `/entrar`:
  - `ok` → ✅ "E-mail confirmado!" · "Entrar na minha conta →"
  - `already` → ✅ "E-mail já confirmado" · "Entrar na minha conta →"
  - `invalid` → ⚠️ "Link inválido ou expirado" · "Ir para login →"
- **Nginx:** `confirmado` adicionado à allowlist de rotas públicas (`http.conf` + `https.conf.template`).
- **Backend `/account/confirm` inalterado** (segue retornando `{status}` — o Astro orquestra).

Fluxo pós-fix: cria conta → e-mail de `klarim@` → clica link → `/confirmar` confirma no banco →
`/confirmado?status=ok` → "✅ E-mail confirmado!" + botão → `/entrar`.

> "Página estática/sem JS" do card: é SSR (`prerender=false`) para ler o `?status=` server-side,
> mas **sem JavaScript no cliente** e sem token na URL — atende a intenção (segurança + simplicidade).

**Verificação visual (browser):** desktop 1440px (✅ sucesso) e o estado `invalid` conferidos —
card centralizado `max-w-md`, ícone tonalizado, botão brand `w-full sm:w-auto min-h-[44px]`, Header
+ Footer, tema claro. Os 3 estados renderizam o conteúdo correto (curl SSR).

---

## 4. Security review

- `/scan` não reflete mais input cru; o título nunca contém `<`/`>` (testado).
- `/confirmado` não expõe o token (só `status=`); `noindex`; sem JS (sem superfície client-side).
- Remetente: nenhuma mudança em Reply-To (`scan@`) nem no proativo (`alerta@`) — isolamento de
  reputação mantido.

## 5. Testes

- Frontend: **+11** `node --test` (`scanTitle.test.js`) → `web` agora **96 passed**. Build Astro OK
  (confirmado/confirmar/scan compilam).
- Backend: **1444 passed** (só docstrings mudaram; sem regressão).

## 6. Arquivos

**Novos:** `web/src/lib/scanTitle.js`, `web/src/lib/scanTitle.test.js`, `web/src/pages/confirmado.astro`.

**Alterados:** `web/src/pages/scan.astro`, `web/src/pages/confirmar.astro`, `web/package.json`,
`notifier/email_client.py` (docstrings), `frontend/nginx/{http.conf,https.conf.template}` (allowlist),
`.env.example`, `docs/DEPLOY.md`, `docs/SECURITY.md`, `CLAUDE.md`.

## 7. Pós-deploy (VM) — obrigatório para a Parte 2

```bash
# no /opt/klarim/.env: RESEND_FROM=Klarim <klarim@klarim.net>
sudo docker compose up -d --force-recreate api discovery worker
# validar: criar conta / reenviar confirmação → e-mail vem de klarim@klarim.net (email_log)
```
