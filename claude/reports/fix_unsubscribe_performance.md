# Fix unsubscribe + performance (LCP/CLS)

**Data:** 2026-07-17 · **Sem card Jira** — fixes operacionais e de performance
**Status:** ✅ Concluído — 967 testes passando, deploy verde.

---

## TAREFA 1 — Unsubscribe

### T1A — `/api/unsubscribe` sem params → HTML branded (não 422 JSON)

**Problema:** o endpoint validava `email`/`token` como query params obrigatórios → sem
eles, o FastAPI devolvia `{"detail":[{"type":"missing",...}]}` (422 JSON cru). Bots de
e-mail (Gmail/Outlook/Apple Mail) fazem pre-fetch dos links → JSON feio.

**Fix:** `email`/`token` agora **opcionais** (`Query(default=None)`). A lógica foi extraída
para `_process_unsubscribe(email, token)`; se qualquer um falta → página HTML branded
"Link incompleto" (não 422). **A validação HMAC-SHA256 constant-time (`hmac.compare_digest`)
NÃO mudou** — só o early return de params ausentes.

### T1B — Headers `List-Unsubscribe` (one-click RFC 8058)

Novo `list_unsubscribe_headers(url)` (`notifier/email_client.py`) devolve
`List-Unsubscribe: <url>` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. Injetado
no payload Resend (`headers`) dos e-mails **proativos**: **alerta** (`_alert_params`, cobre
single + batch), **profile_view** (`send_profile_view`) e **evolution** (rescan —
consistência; também é proativo a lead com link de descadastro). O `token` é o mesmo HMAC
já usado no link do corpo.

Para o one-click funcionar (o cliente faz **POST** ao `List-Unsubscribe` sem interação),
adicionei **`POST /unsubscribe`** — mesma lógica/segurança do GET.

### T1C — Auditoria: todos os workers respeitam `status='unsubscribed'`

`mark_unsubscribed(email)` seta `targets.status='unsubscribed'`. Auditei cada worker que
e-maila o **contact_email** do alvo:

| Worker | E-mail | Respeita? |
|---|---|---|
| **Alert** | alerta (contact_email) | ✅ query `t.status = 'scanned'` (exclui unsubscribed) |
| **Rescan** | evolution (contact_email) | ✅ query `t.status IN ('scanned','alerted')` |
| **Profile-view** | "perfil consultado" | ✅ `if status in ('descartado','unsubscribed'): return` |
| **Bulletin** | boletim (users.email) | N/A — e-mail de **conta**, não de lead |
| **Vigília** | alertas (users.email) | N/A — e-mail de **conta** |

Nenhuma mudança de worker necessária — a proteção já existia via filtro de status.
(Boletim/vigília são e-mails de conta a usuários registrados, **não** proativos a leads →
corretamente **sem** `List-Unsubscribe`.)

---

## TAREFA 3 — Performance (LCP/CLS)

### 3A — Beacon nos perfis `/site/{domain}`

`/site/[domain].astro` **usa `Base.astro`** (que tem o snippet do Cloudflare) → já coberto.

### 3B — LCP

- **OG image** (`/og/{domain}.png`): **já** tem cache em processo (24h) +
  `Cache-Control: public, max-age=86400`.
- **Fonts:** o site usa **fonts do sistema** (`var(--font-sans)`, sem Google Fonts / sem
  `@font-face`) → **não há FOUT** nem download de fonte bloqueante.
- **Assets estáticos** (`/_astro/*`, `/assets/`): o Nginx **já** serve `expires 1y` +
  `Cache-Control: public, immutable` (Astro usa content-hash → `immutable` é seguro).
- **Cache hit Cloudflare 12,5%:** o lado do Nginx/origem já está ótimo (immutable nos
  assets, no-store no HTML dinâmico). Elevar o hit rate depende de **Cache Rules no painel
  do Cloudflare** (cachear `/_astro/*` na edge) — **fora do código**, precisa de acesso ao
  painel. Anotado como pendência de infra.

### 3C — CLS (footer "pulando")

**Causa real:** não é o rodapé em si (estático, altura estável) — é a **ilha React
hidratando**: o SSR renderiza "Carregando…" (curto) e o cliente expande para o estado
final (alto), empurrando tudo abaixo (rodapé) → CLS. O Cloudflare RUM atribui o shift ao
rodapé (elemento visível que se moveu).

**Fixes (reservam a altura):**
- **`ClaimSite`** (ilha do perfil público, alto tráfego orgânico): `min-h-[168px]` no card
  → o "Carregando…" reserva a altura do estado resolvido.
- **`Dashboard`** (loading): `min-h-screen` no placeholder → o rodapé não pula quando os
  sites chegam.
- **`Footer`**: `min-h-[260px]` + `contain:layout` (conforme pedido; estabiliza a altura).

### 3D — Medição

TTFB (curl, proxy de latência de servidor) **pós-deploy**:
`/` ~0,67s · `/site/igoove.com` ~0,82s · `/planos` ~0,64s — resposta de servidor rápida
(<1s). O LCP P75 de 4,67s do RUM é dominado por **render/hidratação no cliente + latência
de edge do Cloudflare** (não pelo servidor). Os fixes de CLS atacam o layout shift
diretamente; a melhora de LCP virá do cache de edge (Cache Rules CF) — o único lever
restante é o painel do Cloudflare. LCP/CLS reais só medem via RUM/Lighthouse ao longo dos
próximos dias.

---

## Segurança

- Unsubscribe: HMAC constant-time inalterado; o early return só trata params ausentes. O
  POST one-click tem a **mesma** validação do GET. `/unsubscribe` continua público (não
  entra nos prefixos protegidos).

## Testes (`tests/test_unsubscribe_fix.py`, 9)

no-params → HTML (não 422); só e-mail sem token → HTML; token inválido → 400 "Link
inválido"; token válido → descadastra; **POST one-click** válido → descadastra;
`list_unsubscribe_headers` (com/sem url); `_alert_params` e `send_profile_view` incluem o
header. **Suite: 967 passed, 1 skipped.**

## Documentação

`claude.md` (List-Unsubscribe + one-click + params opcionais), `docs/API.md`
(`GET/POST /unsubscribe`).

## Pendência de infra (fora do código)

Configurar **Cache Rules no Cloudflare** para cachear `/_astro/*` (e demais assets
immutable) na edge — elevar o hit rate de 12,5% e reduzir o LCP para o tráfego repetido.
