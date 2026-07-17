# UX da área logada + fix Schema.org

**Data:** 2026-07-17 · **Sem card Jira** — melhorias de UX e fix de SEO
**Status:** ✅ 7 itens concluídos, deploy verde.

---

## 1. Fix Schema.org — Review inválido removido

**Problema:** o Google Search Console reprovava o `Review` com `itemReviewed: WebSite`
(WebSite não é um `itemReviewed` válido). Ficava no `jsonLd` da página em
`web/src/pages/site/[domain].astro`.

**Fix:** removido o `jsonLd` (WebPage + Review) da página de perfil (`const jsonLd =
undefined`). O `Base.astro` continua emitindo **Organization + WebSite** (site-wide,
válidos), e o score já é capturado por `<title>`/`<meta description>`/OG tags — não precisa
de schema Review.

## 2. Histórico de consultas — remover itens

**Backend:** `DELETE /account/scan-history/{scan_id}` (JWT do usuário) →
`store.remove_scan_history(email, scan_id)`: **desvincula do e-mail** todos os scans da
mesma URL (senão o próximo mais recente reapareceria) — **preserva os scans** (só limpa
`scanned_by_email`), com checagem de ownership (o scan tem de ser do próprio e-mail). 404
se não for do usuário.

**Frontend:** botão **✕** em cada linha do histórico (`Dashboard.jsx`), com confirmação
(`Remover {domain} do histórico?`) e remoção **otimista** (sem reload).

## 3. "Ver resultado →" → "Ver" (abre a landing, não re-escaneia)

O link do histórico ia para `/scan?url=` (disparava um novo scan). Trocado por **"Ver"** →
`/site/{domain}` em **nova aba** (`target="_blank" rel="noopener"`), mostrando o perfil
público com o score existente. Quem quiser refazer o scan usa o botão da própria landing.

## 4. UX de planos e upgrade no dashboard

**Modal de comparação antes do QR:** o `UpgradeModal` (`PlanSection.jsx`) começa agora no
estado **`compare`** — mostra "Seu plano atual" vs. "Upgrade para {plano} — R$X/mês" com as
features de cada um. O QR PIX só é gerado **após "Confirmar upgrade → R$X/mês"** (antes
gerava direto na montagem).

**Nav "Planos" contextual:** quando logado, o link "Planos" no header aponta para
`/dashboard#plano` (âncora na seção de plano do dashboard — contexto do usuário), não a
página pública. Adicionei `id="plano"` ao redor do `PlanSection`.

## 5. "Editar perfil" na landing do dono → "Solicitar edição"

Na landing `/site/{domain}`, o dono verificado via "Editar perfil →" apontava para
`/dashboard/conta` (perfil pessoal — confuso). Trocado por **"Solicitar edição do perfil
público"** (Opção A) que expande a mensagem: *"A edição do perfil público está em
desenvolvimento. Por enquanto, entre em contato com seguranca@klarim.net…"*.

## 6. Nav renomeado: "Dashboard" → "Home", "Minha conta" → "Conta"

No `Header.astro` (nav autenticado), textos encurtados para o mobile (hrefs inalterados:
`/dashboard`, `/dashboard/conta`).

## 7. Badge "Klarim Approved" → "Monitorado por Klarim"

**Regra inviolável:** nunca "Approved"/"Certificado"/"Compliant". O selo derivado do score
(≥90 e ≥80) usava "Klarim Verified"/"Klarim Approved". Ambos passaram a **"Monitorado por
Klarim"** (o ícone diferencia a faixa: ≥90 ⭐ · ≥80 ✅), em **frontend** (`lib/badge.js`) e
**backend** (`_score_badge`) — mantidos em sincronia. `level` passou de
`verified`/`approved` para `high`/`mid`.

**Decisão:** o card só citava "Approved", mas "Verified" também é endosso — troquei os dois
para consistência com o posicionamento "Monitorado por Klarim" (KL-44 P5).

---

## Testes

- `test_kl42_social.py`: `test_badge_high`/`test_badge_mid` (label "Monitorado por Klarim",
  `level` high/mid); asserts de `level` atualizados (verified→high, approved→mid).
- `test_accounts.py`: `test_remove_scan_history` (remove + some do histórico),
  `_not_found` (404), `_requires_auth` (401).
- Suite completa executada (sem regressões).

## Regras invioláveis respeitadas

Nunca "Approved"/"Verified"/"Certificado" (só comentários-lembrete restam); `contact_email`
nunca exposto; remoção de histórico preserva o scan (só desvincula o e-mail).

## Decisões / desvios

- **Remover do histórico = limpar `scanned_by_email`** (não apaga o scan) — menos
  destrutivo; o perfil público e as métricas do scan seguem intactos.
- **Schema Review removido por inteiro** (não só o `itemReviewed`) — o score é capturado
  por title/meta/OG, então a página não perde SEO.
- **Badge**: troquei também "Verified" (não só "Approved") por coerência com a regra.
