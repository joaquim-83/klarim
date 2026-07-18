# KL-78 — 9 fixes de UX, segurança e lógica de negócio

**Card:** KL-78 · **Prioridade:** High · **Data:** 2026-07-18

---

## Item 9 (BUG CRÍTICO) — alertas de vigília para site apenas consultado

### Diagnóstico (dados reais de produção)

O usuário `jscidinei@gmail.com` (id 3, conta de 2026-07-13) tinha em `user_sites`:
`igoove.com` (dono verificado ✓) **e `catho.com.br`** (`is_owner=False`,
`verification_method=NULL`, adicionado 2026-07-18) — com **6 vigílias ativas** (ssl, score,
reputation, uptime, domain, email) que dispararam o alerta de reputação (HIBP). jscidinei é
gmail e não é dono do catho.com.br (portal nacional de empregos): monitoramento indevido.

**Causa raiz:** o scan **não** adiciona a `user_sites` (confirmado). Quem adiciona:
1. `_create_account_record` (histórico KL-25, `api/main.py`): no signup, auto-vinculava
   **todos** os sites já escaneados pelo e-mail — inclusive não-possuídos (`link_user_site(…,
   is_owner=owns)` com `owns` podendo ser `False`).
2. `_process_claim` (KL-68, chamado no **signup E login** com `?url=`): ao reivindicar um site
   vindo do fluxo scan→cadastro, vinculava mesmo quem **não** é dono (linha `link_user_site(…,
   is_owner=can_own)` com `can_own=False`).

Ambos alimentam `_sync_user_vigilias`/`_create_site_vigilias`, que criam vigílias para **todo**
`user_sites` → alertas. (A seleção da vigília em si já usa `user_sites`, não `scanned_by_email`
— o problema era a poluição do `user_sites`.)

### Fix

**Scan ≠ monitoramento.** Só auto-vincula (auto-monitora) quando a propriedade é
**auto-verificada** (e-mail == `contact_email` OU domínio do e-mail == domínio do site):
- `_process_claim`: não-donos **não** entram em `user_sites` — retornam `can_monitor=True`
  para o frontend oferecer o botão explícito "Monitorar este site" (`POST /account/sites`).
- histórico do signup: só vincula sites **comprovadamente possuídos**; o resto fica só no
  histórico de consultas (`scanned_by_email`).
- `account_add_site` (botão "Monitorar", explícito) permanece — é a ação consciente do
  usuário. Assim o PME dono-com-gmail (que não auto-verifica) monitora clicando "Monitorar";
  o operador que só consultou o catho **não** monitora nada.

**Limpeza de dados:** removido `catho.com.br` (+ 6 vigílias) do jscidinei. Os outros 5
`user_sites` não-donos são **donos-PME com gmail** (radiogermanica, versatilsc, poll360,
ventosulnet) monitorando o **próprio** site — legítimos, mantidos (o fix impede novas
poluições; eles podem verificar a posse pelo código enviado ao contato do site).

---

## Itens 1–8

| # | Fix | Arquivos |
|---|-----|----------|
| 1 | Endereço/descrição com `break-words` + `min-w-0` (não escapa o card em 375px) | `web/src/pages/site/[domain].astro` |
| 2 | Setor "outro" → **por último**, rotulado "Não classificados", card de menor destaque, **noindex**, fora do sitemap | `discovery/store.py` (`public_sector_index` inclui outro + `ORDER BY (sector='outro'), count DESC`), `api/main.py`, `web/src/pages/setores.astro`, `setor/[slug].astro`, `sitemap.xml.js` |
| 3 | Selo "Monitorado por Klarim" só com **score 100 E conta atribuída** (`has_account`); selo único (removido ⭐≥90/✅≥80) | `api/main.py` (`_score_badge`), `web/src/lib/badge.js` + `discovery/store.py` (`site_has_account`, `has_account` em `public_sector_sites`/`list_sector_ranking`), páginas de ranking/setor + Dashboard/ShareScore |
| 4 | Domínio clicável → site real (nova aba, `rel="noopener nofollow"`): "Visitar site" no perfil + ícone ↗ nas tabelas de ranking | `web/src/pages/site/[domain].astro`, `setor/[slug].astro`, `ranking/[sector].astro` |
| 5 | `seguranca@klarim.net` (canal de contato) → `scan@klarim.net` em 3 pontos visíveis ao usuário (o **remetente** transacional continua `seguranca@`) | `ClaimSite.jsx`, `SiteDetail.jsx`, `api/main.py:739` |
| 6 | Label "Plano" → "Plano atual" | `web/src/components/account/PlanSection.jsx` |
| 7 | Nav logado: "Planos" → `/planos` (página pública comparativa), não `/dashboard#plano` | `web/src/components/Header.astro` |
| 8 | **SSRF guard** no scan (bloqueia localhost/IP privado/loopback/link-local + `169.254.169.254` metadata + nomes `.internal/.local` + resolve DNS best-effort); **rate limit** no `GET /scan` (10/10min/IP); validação de formato de e-mail no `/recovery/request` | `api/main.py` (`_scan_host_is_safe`/`_ip_is_internal` em `_safe_scan`, `scan_full`, `recovery_request`) |

### Auditoria de segurança (item 8) — resumo

Já protegidos ✅: `/contact` (sanitização HTML + honeypot + 3/h/IP + iframe sandbox no inbox);
`/account/*` (regex de e-mail, senha ≥8, rate limits); queries parametrizadas (`%s`, sem SQLi).
Corrigidos ❌→✅: **SSRF** (scanner buscava URLs arbitrárias sem barrar hosts internos — o maior
risco, dado que empresas de segurança sondam a plataforma); **`GET /scan` sem rate limit**;
`/recovery/request` com validação de e-mail fraca (`"@" in email`).
Resíduo conhecido: SSRF via **redirect** (um site público redirecionando para IP interno) não é
barrado no transporte httpx — risco menor (o scan devolve resultados de checks, não corpos),
registrado para hardening futuro.

---

## Testes

- `pytest` → **1007 passed, 1 skipped**.
- Novos/atualizados: `tests/test_kl78_fixes.py` (SSRF unit + `GET /scan` 400/429), badge
  score-100-com-conta (`test_kl42_social.py`), signup não vincula scans não-possuídos
  (`test_accounts.py`).
- SQL validado contra Postgres real (outro por último, `has_account` em `public_sector_sites`/
  `list_sector_ranking`, `site_has_account`). Build Astro verde.

---

## Decisões e desvios

1. **Item 4:** só o perfil e as **tabelas** de ranking ganharam link externo. Cards de
   cross-linking / `/melhores` / cards mobile são âncoras de card inteiro (`<a>` → `/site/…`,
   intenção KL-74 de navegação interna); aninhar link externo é HTML inválido e mina o KL-74 —
   deixados como navegação para o perfil (onde há o "Visitar site").
2. **Item 9:** a limpeza foi **cirúrgica** (só catho/jscidinei). Os 5 não-donos restantes são
   PMEs com gmail monitorando o próprio site — remover prejudicaria clientes legítimos.
3. **Item 3:** a maioria dos sites perde o selo (intencional — vira conquista real). O selo do
   `/melhores` (⭐ hardcoded) marca **score perfeito** (vitrine), não é o selo "Monitorado";
   mantido.
