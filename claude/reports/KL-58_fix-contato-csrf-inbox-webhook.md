# KL-58 — Fixes urgentes: contato (CSRF) + inbox (webhook Hostinger)

**Data:** 2026-07-14

Dois problemas reportados no mobile/produção. Um era **código** (contato) e foi corrigido;
o outro é **configuração da Hostinger** (inbox) — o código já funcionava, comprovado por
diagnóstico em produção.

---

## 1. Contato — "Cross-site POST form submissions are forbidden" (CORRIGIDO)

**Causa:** a página `/contato` era Astro SSR com um `<form method="POST">` que postava para a
própria rota. O Astro tem proteção CSRF (`security.checkOrigin`, ligada por padrão em páginas
on-demand) que rejeita POSTs cujo `Origin` não bate com o `Host`. Atrás do **Cloudflare**
(proxy), o `Origin` pode não casar o `Host` interno → **403 "Cross-site POST form submissions
are forbidden"**, especialmente no mobile.

**Fix (opção b — a mais robusta):** o formulário virou uma **ilha React**
(`web/src/components/ContactForm.jsx`) que faz **POST client-side (fetch) para `/api/contact`**
— o endpoint FastAPI que já existe e **não** tem a proteção CSRF do Astro. A página
`contato.astro` deixou de processar POST (agora só renderiza a ilha), então o `checkOrigin`
nunca é acionado.

- Feedback **inline**: enviando → enviado (card verde) / erro (429 = rate limit, ou genérico).
- **Honeypot** anti-bot preservado (campo `website` oculto, checado no client).
- O `/api/contact` já lê o **X-Real-IP** que o Nginx injeta → o rate limit (3/h/IP) continua
  por usuário real. Fallback `mailto:scan@klarim.net` mantido para quem está sem JS.
- Não mexi no `checkOrigin` global (fica seguro para futuras rotas) — `/contato` era a única
  página Astro que processava POST.

**Teste:** validar no mobile após o deploy (form → "Mensagem enviada!").

## 2. Inbox — e-mails não chegam no painel (CAUSA: config da Hostinger)

**Diagnóstico em produção (via SSH na VM):**
- `HOSTINGER_WEBHOOK_TOKEN` **está** no `.env` ✅
- POST público `https://klarim.net/api/email/webhook` (com o token) → **HTTP 200** ✅
  (Cloudflare `172.69.x` roteou; Nginx encaminhou; auth passou; app respondeu)
- Enviando um payload **AgentMail realista** → `{"ok":true,"stored":true}` e o inbox foi de
  **0 → 1** ✅ (pipeline Cloudflare → Nginx → FastAPI → auth → parse → store → painel **funciona
  ponta a ponta**; há uma mensagem de teste `[TESTE] Pipeline do inbox OK…` no painel — pode
  arquivar)
- Logs do `api`: **zero** `POST /email/webhook` reais da Hostinger (só os meus testes). O
  webhook do Resend (`/webhooks/resend`) funciona normalmente.

**Conclusão:** o código, o token, o Nginx e o Cloudflare estão **corretos**. O problema é que a
**Hostinger nunca enviou o webhook** — falta **configurar/ativar** o webhook do Agentic Mail no
hPanel para `scan@klarim.net`.

**Ação do dono (no hPanel da Hostinger):** apontar o webhook de e-mails recebidos para
`https://klarim.net/api/email/webhook`, method **POST**, com o header
`Authorization: Bearer <HOSTINGER_WEBHOOK_TOKEN>` **ou** a URL `…/webhook?token=<TOKEN>`
(ambos aceitos). Enviar um e-mail de teste para `scan@klarim.net` e conferir o painel.

**Hardening de código (para robustez quando a Hostinger enviar):**
- **Diagnóstico de auth (`email_webhook`):** em 401, loga os **nomes** dos headers + chaves de
  query + `token_set` (nunca valores/segredos) — se a Hostinger mandar o token num header
  diferente, isso aparece nos logs em vez de um 401 cego.
- **Mais formas de token (`_hostinger_token_ok`):** aceita também `Authorization` sem o prefixo
  `Bearer`, `x-webhook-secret`/`webhook-secret`, e as queries `secret`/`webhookSecret`.
- **Parser mais tolerante (`parse_inbox_payload`):** desembrulha wrappers comuns
  (`data`/`payload`/`body`/`email`) e aceita uma **lista** de eventos (usa o primeiro) — além
  dos formatos AgentMail e achatado que já suportava. Payload não reconhecido continua logando
  o **raw** para adaptar.

**Regra inviolável preservada:** o webhook é **fail-closed** (sem token, tudo 401) e tem auth
própria (não JWT admin); o corpo HTML externo continua isolado em `<iframe sandbox>` no painel.

## Testes
`tests/test_kl56_admin_inbox.py`: +2 (unwrap `data`, aceita lista) → parser 5 passed; arquivo
inteiro verde. Contato validado no CI (build-web).
