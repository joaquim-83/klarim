# KL-82 — Confiança progressiva (Slice 1: scan anônimo + resultado progressivo)

**Card:** KL-82 · **Prioridade:** High · **Data:** 2026-07-19
**Contexto:** 97% de desistência no gate de verificação de e-mail (15 códigos gerados, 5
completaram). Este slice entrega valor **antes** de pedir compromisso.

---

## Escopo entregue (Slice 1 de 4)

O card tem 9 blocos interdependentes. Fatiado (com aprovação do dono) para deployar em partes
coerentes. **Este slice = Blocos 1, 5, 6, 7, 8 + linguagem (9)** — o Fluxo 1 (scan anônimo
result-first), que ataca direto a desistência. **Trazida a migração de `email_confirmed` do
Bloco 2** por ser pré-requisito do Bloco 5.

**Deferido para os próximos slices:** Bloco 2 restante (signup sem confirmação + `/confirmar` +
e-mail de boas-vindas com link), Blocos 3+4 (Fluxo 2 — sessão do alerta + `signup-from-alert`),
Bloco 9 restante (cleanup cron de contas não confirmadas + docs completas dos fluxos futuros).

## O que mudou

**Antes:** landing `/scan?url=` → ilha exigia **e-mail + código de 6 dígitos** antes de mostrar
qualquer resultado (KL-25). **Depois:** o resultado aparece na hora (result-first), sem e-mail,
com **níveis de acesso progressivos** que revelam mais conforme o compromisso.

## Backend

- **Migração (`discovery/store.py`):** `users` ganhou `email_confirmed` / `email_confirmed_at` /
  `confirmation_source`. **Desvio consciente do card:** coluna **SEM `DEFAULT false`** — o
  `ensure_schema` re-roda a cada boot, então um `DEFAULT false` + `UPDATE ... WHERE = false`
  re-confirmaria contas não-confirmadas a cada restart. Solução idempotente: coluna nullable +
  backfill `WHERE email_confirmed IS NULL` (contas pré-KL-82 → `'code'` uma vez; contas novas
  gravam `false` explícito e nunca são tocadas). `email_confirmed` entrou em `_USER_COLS`.
- **`GET /scan/result?url=` (`api/main.py`):** escaneia **sem e-mail/auth** e devolve o payload
  **filtrado server-side** pelo nível de acesso. Nunca dispara monitoramento (KL-78: scan ≠
  monitoramento). Rate limit anônimo **5/h + 20/dia por IP** (429 amigável → "Crie uma conta
  gratuita para pesquisas ilimitadas"); conta logada é ilimitada.
- **Níveis (`_access_level`):** `anonymous` < `alert_session` < `unconfirmed` < `confirmed`.
  `email_confirmed` NULL (legado) conta como confirmado; só `false` explícito vira `unconfirmed`.
  `_get_alert_session` é stub (retorna None) até o Bloco 3.
- **Filtro (`_filter_scan_result`)** — corte server-side (não blur cosmético; **nunca** envia
  evidência/detalhe aos níveis baixos):
  - **anonymous:** score + semáforo + **barras por categoria (só proporção, sem números)** +
    **1 risco** + benchmark/checks travados.
  - **unconfirmed:** + benchmark + **2 riscos** + categorias com contagem + nomes dos checks
    **sem evidência** + PDF travado.
  - **confirmed / alert_session:** tudo (48 checks com evidência/impacto/correção, riscos
    completos, categorias, privacidade, PDF).
- **Categorias:** `_build_categories` espelha as 6 categorias do front (`checks.js`) em Python.
- **Analytics:** eventos `scan_anonymous`, `scan_authenticated`, `signup_inline_clicked`.

## Frontend

- **`ScanFlow.jsx` reescrito → result-first:** monta → progresso → `GET /api/scan/result`
  (`credentials:'include'` leva o cookie; o backend decide o nível) → renderiza o resultado.
  429 → tela "Limite atingido / crie conta". O fluxo de código (KL-25) fica **dormente** ao
  fim do arquivo (regra 9: não remover, só despriorizar; os endpoints seguem no backend).
- **`ScanResultDetail.jsx` (novo, `client:load`):** hero de score ("Este site tem score X. E o
  seu?") + share (WhatsApp/LinkedIn são `<a href>`, copiar via JS da ilha — **CSP-safe**, sem
  handler inline) + benchmark (travado no anônimo via `LockedSection` com blur+cadeado) + riscos
  (1/2/todos) + categorias (barras / resumo / accordion completo com FAIL Alta/Crítica aberto) +
  CTA por nível (`SignupInline` / `ConfirmEmailCTA`). Mobile-first (alvos ≥44px, `w-full sm:w-auto`).
- **`scan.astro`:** removido o fetch SSR de `/account/me` (a ilha resolve o acesso pelo cookie)
  → render mais rápido. Descrição/`noscript` neutralizados.
- **Linguagem (Bloco 9):** "Seu site"→"Este site", "Escanear outro"→"Pesquisar outro",
  "Nosso site tem score"→"Este site tem score" (`ShareScore`). Posse ("seu") reservada ao
  dashboard logado (não alterado).

## CSP

Sem mudança necessária: a ilha `client:load` reusa o runtime Astro já hasheado; o novo código é
bundle externo. Accordion = `<details>` (HTML), blur = CSS, share/cópia = JS da ilha (não inline).

## Testes

- **`tests/test_kl82_progressive.py` (11 novos):** `_check_category`/`_build_categories` (puros);
  `_filter_scan_result` nos 3 níveis (anonymous preview sem vazar evidência; unconfirmed parcial
  sem evidência; confirmed completo); endpoint `/scan/result` — anônimo sem e-mail e sem
  vazamento, logado recebe 48 checks + PDF, unconfirmed parcial, **rate limit 5/h → 429**,
  logado ilimitado.
- **Suite:** `1035 passed, 1 skipped` (1024 → 1035). Build Astro **verde**.

## Regras invioláveis atendidas

Rate limit anônimo (5/h, 20/dia) ✅ · scan ≠ monitoramento ✅ · `contact_email` nunca exposto
(não há novo caminho que o exponha) ✅ · mobile-first ≥44px ✅ · blur é preview, não punição ✅ ·
fluxo de código mantido como fallback ✅ · revisão de segurança (filtro server-side, não vaza
evidência a anonymous/unconfirmed; rate limit no caminho novo) ✅.

## Correção de segurança pós-deploy — rate limit efetivo atrás do Cloudflare

O smoke test em produção revelou que **7 chamadas anônimas rápidas não davam 429**. Causa: o
Nginx faz `proxy_set_header X-Real-IP $remote_addr`, mas atrás do Cloudflare `$remote_addr` é o
**IP do edge do CF**, não do visitante — então o rate limit por IP (do KL-82 **e de todos os
endpoints**: login, scan, ownership…) chaveava por edge, inefetivo por usuário real.

**Dois consertos (aprovados pelo dono):**
1. **`_client_ip`** passou a preferir **`CF-Connecting-IP`** (IP real que o CF sempre envia) →
   `X-Real-IP` → peer. Conserta o rate limit de todos os endpoints de uma vez (2 testes novos).
2. **Firewall de origem (GCP):** `443` do origin (`34.135.194.208`) agora só aceita os **ranges
   do Cloudflare** (regras `klarim-allow-cf-https` v4 + `klarim-allow-cf-https-v6`; removida a
   `klarim-allow-https` 0.0.0.0/0). Impede que alguém batendo direto no IP **forje** o
   `CF-Connecting-IP` para escapar do rate limit. **Porta 80 fica aberta** (renovação Let's
   Encrypt HTTP-01 + redirect http→https). Verificado: via CF 200; direto no origin:443 →
   timeout (bloqueado); direto no origin:80 → 301 (aberto). SSH (22) inalterado (CI intacto).

**Follow-up:** os ranges do Cloudflare mudam raramente — se o CF publicar novos, atualizar as
duas regras. Renovação do cert via HTTP-01 passa pelo CF (porta 80 aberta); se o CF "Always Use
HTTPS" interferir no futuro, migrar para DNS-01 ou origin cert do Cloudflare.

## Pendências (próximos slices)

Bloco 2 (contas sem confirmação + `/confirmar` + welcome link), Blocos 3+4 (Fluxo 2 do alerta),
cleanup cron de contas não confirmadas. `_get_alert_session` e o nível `alert_session` já estão
cabeados no filtro, prontos para o Bloco 3.
