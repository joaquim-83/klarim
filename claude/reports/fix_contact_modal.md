# Fix — Substituir mailto por seção de contato inline (modal)

**Tipo:** Correção de UX (sem card Jira)
**Data:** 2026-07-08

## Problema

Clicar em "Contato" abria o app de e-mail nativo (`mailto:`), tirando o visitante
do site. Ele quer **ver o e-mail** e opcionalmente **enviar uma mensagem** sem sair.

## Solução — Opção A (modal de contato)

### Frontend

- **`components/ContactModal.jsx`** (novo): overlay responsivo com
  - o e-mail `scan@klarim.net` em destaque + botão **"Copiar"** (`navigator.clipboard`,
    feedback "Copiado ✓");
  - formulário **Nome (opcional) · E-mail · Mensagem** → `POST /api/contact`;
  - estados `sending`/`sent`/`error`; sucesso mostra "✅ Mensagem enviada!
    Responderemos em breve."; 429 mostra "Muitas mensagens…";
  - botão fechar (✕), fecha ao clicar fora, `role="dialog"`/`aria-modal`.
- **`components/Footer.jsx`**: o link "Contato" virou `<button>` que abre o modal
  (**sem `mailto:`**). "Sobre" e "Parceiros" seguem `#` (placeholder). O Footer é
  compartilhado por todas as telas públicas (Result, Scan, Payment, Report,
  Recuperação), então uma mudança cobre todas.

### Backend — `POST /contact` (público, sem JWT)

- `api/main.py`: valida e-mail (regex) e mensagem obrigatória; **sanitiza** os
  campos (`_sanitize_str`, anti-XSS, reusa o fix da auto-auditoria); **rate limit
  3/h por IP** (`X-Real-IP`, janela deslizante, `_contact_attempts`), 4ª → **429**
  com `Retry-After`; exige e-mail configurado (`_require_email`) e encaminha via
  `KlarimMailer.send_contact`. Retorna `{"ok": true}`.
- `notifier/email_client.py`: novo `send_contact(name, email, message,
  to_address='scan@klarim.net')` — HTML dark, faz `html.escape` (defense-in-depth)
  e define `reply_to` para o remetente (basta responder o e-mail).

## Testes

- `tests/test_contact.py` (5): rota pública; envia + sanitiza (`<script>`/`<b>`
  removidos, texto preservado); e-mail inválido → 422; mensagem vazia → 422; rate
  limit `200,200,200,429` + outro IP livre.
- `tests/conftest.py`: passou a zerar também `_contact_attempts` entre testes.
- **Suíte: 150 passed, 1 skipped.** Frontend `npm run build` OK.

## Validação (mapeada à tarefa)

| # | Item | Cobertura |
|---|------|-----------|
| 1 | "Contato" abre modal (não o app de e-mail) | Footer `<button>` → `ContactModal` |
| 2 | E-mail visível + copiar → clipboard | botão "Copiar" (`navigator.clipboard`) |
| 3 | Formulário envia → confirmação | `POST /contact` → "✅ Mensagem enviada!" |
| 4 | 4ª tentativa → bloqueada | `test_contact_rate_limited` (429) |
| 5 | Mobile responsivo | overlay `max-w-md`, `px-4`, inputs full-width |

## Observação

Para o e-mail chegar a `scan@klarim.net`, o domínio `klarim.net` precisa estar
verificado no Resend (já está — ver seção 12) e ter caixa/encaminhamento para
`scan@`. O endpoint só dispara o envio; a entrega depende do MX de `klarim.net`.

## Arquivos

- `frontend/src/components/ContactModal.jsx` (novo), `frontend/src/components/Footer.jsx`
- `api/main.py` (`POST /contact` + rate limit + sanitização)
- `notifier/email_client.py` (`send_contact`)
- `tests/test_contact.py` (novo), `tests/conftest.py`, `claude.md`
