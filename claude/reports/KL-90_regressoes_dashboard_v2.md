# KL-90 — Corrigir regressões do Dashboard v2 (superset da produção)

**Data:** 2026-07-22
**Ambiente:** local. **Sem deploy/push/commit.**
**Objetivo:** o v2 deve ser um SUPERSET do dashboard de produção — nada de features perdidas.

---

## Investigação (obrigatória) — inventário feature-por-feature

Li **todos** os componentes do dashboard de produção. A experiência de produção mora em
**duas** páginas (não só uma):

**`/dashboard` → `web/src/components/account/Dashboard.jsx`:** saudação, banner confirmar
e-mail, badge de técnico, TechnicianClients (role=tech), saúde, riscos, checklist, evolução,
categorias, SharePanel (WhatsApp/LinkedIn/copiar), OtherSites (adicionar + lista),
ProfileOnboarding, **PlanSection** (checkout PIX/QR completo).

**`/dashboard/site/[id]` → `web/src/components/account/SiteDetail.jsx`:** **TechnicianSection**
(convidar/revogar/**laudo**), **SealSection** (selo), indicadores de privacidade (LGPD),
OwnershipVerification, monitoramento, riscos, evolução, perfil, 48 checks com evidência,
`has_other_owner`.

O v2 tinha consolidado o `/dashboard` mas **perdeu as features do site-detail**. Estratégia:
**reusar os componentes de produção** (não reescrever) e restaurar o que faltava.

### Features migradas / restauradas: **11**
As 7 regressões listadas + affordance + 3 encontradas na investigação (confirm-email banner,
badge de técnico, histórico de pagamentos — este último vem junto no PlanSection reusado).

### Features que existiam e NÃO estavam no v2 → implementadas agora: **8**

| # | Regressão | Como foi resolvida |
|---|---|---|
| 1 | **Selo "Monitorado por Klarim"** | Novo `SealSection.jsx` (espelha o de produção): snippet `/seal/widget.js` (tema/tamanho → copiar). **Gated por plano**: Free vê upsell; Pro/Agency vê o snippet. |
| 2 | **TechnicianSection completa** | O botão "Vincular Técnico" abre modal que renderiza o **`TechnicianSection` de produção** (convite + lista + status + **revogar** via `POST /account/technician/revoke`). |
| 3 | **PlanSection com QR PIX** | Trocado o `PlanCard` simples pelo **`PlanSection` de produção**: countdown de trial, modal de upgrade (compara planos → QR PIX AbacatePay + polling), downgrade, **histórico de pagamentos**. |
| 4 | **Remover site** | Botão ✕ por site no painel (aparece no hover no desktop, sempre no mobile) → `window.confirm` → `DELETE /account/sites/{id}` → recarrega. (5 botões confirmados no DOM.) |
| 5 | **Dashboard do técnico** | Novo `TechnicianClients.jsx` (`GET /account/technician/clients`) + badge **"Profissional de TI"**, renderizado quando `user.role` é technician/both. Validado logando como `tecnico@agencia.com.br`. |
| 6 | **`has_other_owner`** | `AddSiteModal`: se o add retorna `is_owner=false` sem verificação disponível → aviso "Este site já tem um dono verificado". |
| 7 | **Laudo compartilhável** | Vem no `TechnicianSection` reusado: `POST /account/shared-report/create` → link `/laudo/{code}` (30 dias) + WhatsApp. Validado (gerou `/laudo/6VGEUW9V`). |
| + | **Banner confirmar e-mail** | Novo `ConfirmEmailBanner.jsx` (só p/ conta não confirmada) — o Dashboard de produção tinha e o v2 perdeu. |

### Affordance (Riscos/Checklist collapsible)
`Collapsible.jsx`: **chevron grande que rotaciona** (›→ gira 90°), rótulo **"expandir/recolher"**,
hover no header + cursor-pointer. A seção **Riscos abre por padrão** (`defaultOpen`) e o
`RisksList` **já expande o 1º risco** (o mais crítico) → fica óbvio que os demais também abrem.

---

## Features encontradas mas NÃO portadas (com justificativa honesta)

Estas existem no **site-detail** de produção e dependem de dados/fluxos que o endpoint
`/dashboard-summary` (KL-90 P1) **não** expõe — portá-las exige mudança de backend, fora do
escopo "corrigir regressões de frontend". Ficam registradas como follow-up:

- **Indicadores de privacidade (8, LGPD)** — o `dashboard-summary` não retorna o bloco `privacy`;
  disponível hoje na página do site (`/dashboard/site/[id]`).
- **48 checks com evidência técnica** — o `dashboard-summary` filtra a evidência de propósito
  (o v2 mostra os checks por categoria, sem a evidência exploit-útil); a evidência completa fica
  no site-detail.
- **OwnershipVerification (reivindicar/verificar propriedade)** — fluxo de claim; os sites do seed
  já nascem verificados. Requer portar `OwnershipVerification.jsx` + estado do `dashboard-summary`.
- **ProfileOnboarding (editar empresa/telefone inline)** — já existe em `/dashboard/conta`
  (`AccountSettings`); o checklist do v2 aponta pra lá.
- **SharePanel WhatsApp/LinkedIn** — o v2 tem "Compartilhar" (copia o perfil) + o laudo tem
  WhatsApp; os botões diretos de WhatsApp/LinkedIn do perfil não foram adicionados (o laudo já cobre o WhatsApp).

> Recomendação: quando o v2 virar o `/dashboard` oficial (Prompt swap), decidir se estas migram
> pro dashboard (exige expor `privacy`/evidência no `dashboard-summary`) ou continuam no site-detail.

---

## Validação (navegador, com o seed — screenshots)

| Item | Resultado |
|---|---|
| 1 · Selo | seção com snippet + "Copiar código do selo" (Pro) |
| 2 · Técnico | modal com convite + lista/revogar |
| 3 · Plano | Pro trial · countdown 24d · modal de upgrade (compara → confirmar → QR) · downgrade |
| 4 · Remover | 5 botões ✕ no painel (um por site) |
| 5 · Técnico (role) | login `tecnico@` → "🔧 Sites dos meus clientes" + badge "Profissional de TI" |
| 6 · has_other_owner | aviso codificado no add |
| 7 · Laudo | gerou `/laudo/6VGEUW9V` (30 dias) + WhatsApp |
| Affordance | Riscos aberto + 1º risco expandido; chevron rotaciona; "expandir/recolher" |
| Tema claro/escuro | ok | Console | **zero erros** | Build | ✓ | `test:unit` | **96 pass** |

---

## Arquivos

**Novos (dashboard-v2):** `SealSection.jsx`, `TechnicianClients.jsx`, `ConfirmEmailBanner.jsx`.
**Reusados de produção:** `account/PlanSection.jsx`, `account/TechnicianSection.jsx`.
**Alterados:** `DashboardV2.jsx` (orquestra tudo + user prop + remover + role), `MonitoredSitesPanel.jsx`
(remover), `AddSiteModal.jsx` (has_other_owner), `Collapsible.jsx` (affordance), `RisksList.jsx`
(1º risco aberto), `Modal.jsx` (variante wide). **Removidos (órfãos):** `PlanCard.jsx`, `TechnicianModal.jsx`.

## Regras
- ✅ Sem deploy/push/commit · ✅ investigação obrigatória feita · ✅ tema claro/escuro · ✅ relatório PT-BR.
