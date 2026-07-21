# Fix urgente — confirmação de e-mail à prova de pre-fetch (exige clique)

**Origem:** risco identificado após a entrega da página `/confirmado` (2026-07-21) · **Status:**
Implementado (aguardando deploy verde)

---

## 1. Problema

A entrega anterior (`/confirmar` SSR) **confirmava a conta no load da página**: o `confirmar.astro`
fazia `fetch('/api/account/confirm?token=')` no server-side ao carregar. Como servidores de e-mail
(Gmail, Outlook, scanners de segurança) fazem **pre-fetch (GET)** dos links, o simples recebimento
do e-mail **confirmaria a conta sem o usuário clicar**.

## 2. Correção — confirmação POST-only (só o clique humano confirma)

O pre-fetch usa **GET**; nunca **POST**. Então a confirmação passou a exigir um **POST** (submit de
formulário). Nenhum GET (nem da página, nem da API linkada) confirma mais.

**Fluxo novo:**
```
signup → e-mail de klarim@ com link para a PÁGINA /confirmado?token=
→ (pre-fetch faz GET → renderiza só o BOTÃO, NÃO confirma)
→ usuário clica "Confirmar meu e-mail" (<form method="POST" action="/api/account/confirm">)
→ POST /account/confirm confirma no banco
→ 303 redirect para /confirmado?status=ok  (sem token na URL)
→ "✅ E-mail confirmado!" + botão "Entrar na minha conta →"
```

**Mudanças:**
- `_send_welcome_confirmation`: link do e-mail → `{_SITE}/confirmado?token=` (a **página**, não a API).
- **`POST /account/confirm`** (novo): confirma e redireciona (303) para `/confirmado?status=ok|already|
  invalid`. O token HMAC (uso único) é a própria credencial → sem CSRF token; CSP `form-action 'self'`
  permite o POST same-origin.
- `GET /account/confirm` (JSON): mantido por compat, mas **nenhum e-mail/página o linka** → o
  pre-fetch não o alcança. Lógica extraída para `_do_confirm_email()` (compartilhada GET+POST).
- **`confirmado.astro`**: com `?token=` → renderiza o **formulário POST** (botão "Confirmar meu
  e-mail"), **sem chamar a API no load**; com `?status=` → estado de feedback (ok/already/invalid).
- **`confirmar.astro`** (legado): só **redireciona** para `/confirmado?token=` (não chama a API) —
  e-mails antigos entram no mesmo fluxo de clique.

## 3. Por que POST-form (e não JS-on-click)

O card sugeria confirmar via `fetch` no clique. Escolhi um **`<form method="POST">` sem JavaScript**
porque é **estritamente mais robusto**: (a) POST é imune a pre-fetch por **método HTTP** (nenhum
scanner submete POST, nem os que executam JS); (b) **funciona sem JavaScript** (acessível); (c) **zero
risco de CSP** (a CSP estrita do site exige hash para script inline — um form não usa script). Atende
todos os critérios de validação do card, com o mesmo resultado visual.

## 4. Validação

- `curl /confirmado?token=FAKE` → HTML com o **formulário POST** ("Confirme seu e-mail", `method="POST"`,
  `action="/api/account/confirm"`, `name="token"`), **sem chamar a API** → conta NÃO confirmada. ✓
- `curl /confirmado?status=ok` → "E-mail confirmado!" + "Entrar na minha conta". ✓
- `curl /confirmar?token=X` → 302 para `/confirmado?token=X` (sem chamar a API). ✓
- `POST /account/confirm {token}` → 303 → `/confirmado?status=ok`, `email_confirmed=true`; 2º POST →
  `?status=already` (idempotente); token inválido → `?status=invalid`. **3 testes novos** (`tests/
  test_kl82_slice2_signup.py`); os testes GET legados seguem passando. Backend: **94 passed** no módulo.
- Astro build OK (confirmado/confirmar compilam).

## 5. Arquivos

**Alterados:** `api/main.py` (`_do_confirm_email` + `POST /account/confirm` + link do e-mail + `Form`
import), `web/src/pages/confirmado.astro` (form POST + estados), `web/src/pages/confirmar.astro`
(redireciona sem chamar a API), `tests/test_kl82_slice2_signup.py` (+3 testes POST), `CLAUDE.md`,
`docs/SECURITY.md`.

## 6. Pós-deploy (VM)

1. `curl -s https://klarim.net/confirmado?token=QUALQUER` → deve trazer o **formulário** (botão), sem
   confirmar nada.
2. Criar conta de teste → clicar o botão → `email_confirmed=true` **só após o clique**.
3. **Pendente do fix anterior:** trocar `RESEND_FROM=Klarim <klarim@klarim.net>` no `.env` da VM +
   `docker compose up -d --force-recreate api discovery worker`.
