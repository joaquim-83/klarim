# KL-56 — Gestão de landing + paginação de scans + inbox de e-mail

**Data:** 2026-07-13
**Card:** KL-56
**Status:** entregue, deploy verde

Três frentes no painel admin + a integração da caixa `scan@klarim.net` (Hostinger
Agentic Mail).

---

## 1. Gestão de landing pages na página Alvos

Cada alvo com `site_profile` e status `scanned`/`alerted` ganhou o botão **"Landing"**
na coluna de ações, que abre um modal (`components/admin/ProfileEditor.jsx`) com:

- **Ver landing** — abre `/site/{dominio}` em nova aba.
- **Editar perfil** — `description` (textarea), `business_type`, `company_name`, `tags`
  (separadas por vírgula).
- **Toggle "Landing pública"** — liga/desliga a página.

**Backend:**
- `site_profile` ganhou 3 colunas (via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`):
  `public_visible` (default TRUE), `edited_by_admin` (default FALSE), `edited_by_admin_at`.
- **`PUT /targets/{id}/profile`** (JWT admin, `ProfileEditBody`) → `update_site_profile_fields`
  atualiza só os campos editáveis e marca **`edited_by_admin=TRUE`**.
- **Proteção da edição manual:** o guard vive no `ON CONFLICT` do `upsert_site_profile` —
  `CASE WHEN site_profile.edited_by_admin THEN <valor antigo> ELSE EXCLUDED.<col> END` para
  description/business_type/company_name/tags. Assim o enrich (scan worker / `enrich_all`)
  **nunca sobrescreve** o que o operador editou. `public_visible`/`edited_by_admin` não
  entram no upsert (o enrich não os toca).
- **`PATCH /targets/{id}/profile/visibility`** (`VisibilityBody`) → `set_profile_visibility`.
  - `GET /public/profile/{domain}` retorna **`not_found`** quando `public_visible=FALSE`
    (some do site, igual a descartado).
  - `list_public_profile_domains` (sitemap) ganhou `AND COALESCE(sp.public_visible, TRUE)
    = TRUE` → landings desligadas somem do sitemap.
- `list_targets` traz `has_profile` + `public_visible` (LEFT JOIN `site_profile`) — a linha
  sabe o estado da landing sem N+1.

**MCP:** `toggle_profile_visibility(target_id, visible)` e `update_site_profile(target_id,
description?, business_type?, company_name?, tags?)`.

---

## 2. Página Scans — paginação real + filtro por data

**Bug:** o frontend tinha `page` na dependência do `useAsync` mas **não enviava `offset`**
— toda página repetia a primeira, e nunca passava de 25 linhas.

**Fix:**
- `store.list_scans` ganhou **`offset`** + **`from_date`/`to_date`** (`YYYY-MM-DD`, `to_date`
  inclusivo via `scanned_at < to_date + 1 day`).
- `GET /scans` expõe `offset`, `from_date`, `to_date`.
- `Scans.jsx` envia `offset = page * PAGE_SIZE` e um **seletor de período**: Hoje / Últimos
  7 dias / Últimos 30 dias / Personalizado (2 date pickers) / Todos — **default: últimos 7
  dias** (não "tudo desde o início").
- Os outros chamadores de `list_scans` (atividade recente, `public_profile`) seguem sem
  filtro de data (default preservado).

---

## 3. Inbox `scan@klarim.net` (Hostinger Agentic Mail)

Webhook recebe os e-mails que chegam em `scan@klarim.net` e grava numa tabela; o painel
lê e gerencia.

**Pesquisa da API.** A Hostinger Agentic Mail é powered by **AgentMail**. O payload do
evento `message.received` é `{type, event_type, event_id, message{message_id, from, to[],
subject, preview, text, html, timestamp}, thread{…}}`. A AgentMail nativa autentica via
**Svix**, mas a Hostinger hPanel usa o **token plano** que o dono configurou (o
`HOSTINGER_WEBHOOK_TOKEN`). Por isso a validação é por token + **log do raw** na 1ª
recepção (para adaptar o parser se o formato real divergir).

**Tabela `inbox_messages`** (independente, sem FK): `message_id` UNIQUE (dedup),
`from_address/from_name/to_address/subject/body_preview/body_html/received_at`,
`is_read/is_starred/is_archived` + 2 índices.

**Webhook `POST /email/webhook` (público, auth própria).** `/email` é prefixo admin, então
o webhook entrou no **`_PUBLIC_UNDER_PROTECTED`** (`_is_protected` → False): não passa pelo
JWT admin, tem token próprio.
- `_hostinger_token_ok` valida o token (**constant-time**, **fail-closed** sem a env) aceito
  em `Authorization: Bearer`, headers custom comuns **ou** `?token=`.
- `parse_inbox_payload` (função **pura**, testável): formato AgentMail **e** achatado;
  `email.utils.parseaddr` no `from`; sintetiza `message_id` (hash) se faltar; payload não
  reconhecido → **loga o raw** e responde 200 (Hostinger não re-tenta).
- Grava via `insert_inbox_message` (ON CONFLICT DO NOTHING → dedup por `message_id`).

**API admin (JWT):** `GET /admin/inbox` (filtros `box=all|unread|starred|archived`,
paginado), `GET /admin/inbox/unread-count` (declarado **antes** de `/{msg_id}`),
`GET /admin/inbox/{id}` (corpo completo, marca lida ao abrir), `POST …/{id}/read|star|archive`.

**Frontend:** `pages/admin/Inbox.jsx` (rota `/painel/inbox`, `lazy`) — lista com ●/○,
estrela, arquivar, filtros; **badge de não-lidas** no `AdminLayout` (poll 60s).

**Segurança (inviolável).** O corpo HTML vem de remetente **externo** (não confiável) →
renderizado em **`<iframe sandbox="">`** (sem scripts, origem opaca). **Nunca**
`dangerouslySetInnerHTML` — senão um e-mail malicioso rodaria script na origem do painel e
roubaria o JWT do operador. Responder = link `mailto:`/webmail (envio via API Hostinger
fica para fase opcional, `HOSTINGER_API_TOKEN`).

**Config (segredos — só na VM).** `HOSTINGER_WEBHOOK_TOKEN` + `HOSTINGER_API_TOKEN`
adicionados ao `/opt/klarim/.env` da VM (nunca no git); os serviços usam `env_file: .env`.
Placeholders documentados em `.env.example`. Webhook já configurado na Hostinger para
`https://klarim.net/api/email/webhook` (cai no `location /api/` existente, sem mudança de
Nginx).

---

## Arquivos

| Arquivo | O quê |
|---------|-------|
| `discovery/store.py` | +3 colunas em `site_profile`; tabela `inbox_messages`; `update_site_profile_fields`, `set_profile_visibility`, guard no `upsert_site_profile`; `list_public_profile_domains` filtra `public_visible`; `list_targets` traz `has_profile`/`public_visible`; `list_scans` +offset/datas; 8 métodos de inbox |
| `api/main.py` | `_PUBLIC_UNDER_PROTECTED` (webhook público); `PUT /targets/{id}/profile` + `PATCH …/visibility` + models; `public_profile` respeita `public_visible`; `POST /email/webhook` + `parse_inbox_payload` + `_hostinger_token_ok`; 6 endpoints admin de inbox; `GET /scans` +offset/datas |
| `mcp_server/tools/targets.py` | `toggle_profile_visibility`, `update_site_profile` |
| `frontend/src/lib/adminApi.js` | verbo `put`; `updateProfile`/`setProfileVisibility`; 6 helpers de inbox |
| `frontend/src/components/admin/ProfileEditor.jsx` | **novo** — modal de gestão da landing |
| `frontend/src/pages/admin/Alvos.jsx` | botão "Landing" + modal |
| `frontend/src/pages/admin/Scans.jsx` | offset + seletor de período |
| `frontend/src/pages/admin/Inbox.jsx` | **novo** — página do inbox (iframe sandbox) |
| `frontend/src/components/admin/AdminLayout.jsx` | item "Inbox" + badge de não-lidas |
| `frontend/src/App.jsx` | rota `/painel/inbox` (lazy) |
| `.env.example` | placeholders `HOSTINGER_*` |
| `tests/test_kl56_admin_inbox.py` | **novo** — 24 testes offline (os 15 do escopo + extras) |

---

## Testes

`tests/test_kl56_admin_inbox.py` — **24 passando** (offline, TestClient + FakeStore), cobrindo
os 15 itens do escopo:

1. PUT profile atualiza description + tags (e marca `edited_by_admin`).
2. `public_visible=false` → `/public/profile` = `not_found`.
3. `public_visible=true` → `/public/profile` = `ok`.
4. SQL do sitemap contém o guard `public_visible` (teste no nível do store, cursor gravador).
5. MCP `toggle_profile_visibility` (+ `update_site_profile`).
6/8. `/scans?offset=` retorna páginas distintas + offset repassado ao store.
7. `/scans?from_date=&to_date=` repassados ao store.
9/10/11. Webhook: token válido grava · token inválido 401 · `message_id` duplicado ignorado.
12/13/14/15. Inbox admin: lista ordenada · marca lida (+ abrir marca lida) · unread-count ·
estrela/arquivar (arquivada some de "all", aparece em "archived").

Extras: parser AgentMail/achatado/evento-não-mensagem, token via `?token=`, rotas protegidas.

Suítes relacionadas (`target_edit`, `kl51_f4`, `kl51_f5`, `mcp_server`, `ingest`,
`enrich_all`, `manual_classify`) seguem verdes. Frontend Vite validado por esbuild
(transform de cada arquivo + bundle do grafo de imports a partir de `main.jsx`).

---

## Regra inviolável

Webhook **fail-closed** e com auth própria (não JWT admin); corpo de e-mail externo
**nunca** no DOM do painel (só `<iframe sandbox>`); `edited_by_admin` protege a edição
manual do perfil contra o enrich; landing desligada some do site **e** do sitemap. Tokens
Hostinger só na VM.
