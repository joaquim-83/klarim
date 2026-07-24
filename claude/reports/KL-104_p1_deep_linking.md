# KL-104 Parte 1 — Deep linking entre páginas do admin

**Card:** KL-104 (High) · **Parte 1 de 3** · **Status:** ✅

## Objetivo
Acabar com o copy/paste de domínios no painel: toda menção a um domínio em tabela do admin
vira link para o detalhe do alvo (`/painel/alvos/{id}`); o detalhe ganha links de saída para o
público. Frontend-only (+ 2 campos `target_id` em responses existentes).

## Entregue

### 1. Componente reutilizável `DomainLink` (`web/src/components/admin/ui.jsx`)
`<DomainLink domain targetId />` → `<a href="/painel/alvos/{targetId}">{domain}</a>` (cor de link
do admin `text-klarim-alert`, hover underline, mesma aba/SPA). Sem `targetId` → texto puro (nunca
link quebrado). `<a href>` interno (padrão KL-51: sem react-router).

### 2. Domínios clicáveis (DRY, o mesmo componente em todas)
| Página | Campo | target_id |
|---|---|---|
| **Scans** (`ScansPage`) | `s.url` | `s.target_id` (já vinha de `list_scans`) |
| **Alertas enviados** (`AlertasPage`) | `a.url` | `a.target_id` (alert_log) |
| **Consultas de perfil** (`AlertasPage`) | domínio consultado | `e.target_id` (novo em `analytics_events`) + mantém link "perfil ↗" p/ `/site/{domain}` |
| **Analytics / Eventos** (`AdminAnalytics`) | domínio do evento | `e.target_id` (novo em `aa_events`) |
| **Sites monitorados** (`ClientesPage` + `UsuariosPage`) | `s.domain` | `s.target_id` |

### 3. Links de saída no detalhe do alvo (`AlvoDetalhePage`)
Abaixo do header: **"🌐 Ver perfil público →"** (`klarim.net/site/{domain}`, nova aba — só se
`profile.public_visible !== false`) e **"🔍 Ver último scan →"** (`klarim.net/scan?url={domain}`,
nova aba — só se `last_scan_at`).

### 4. Backend (adição de campo, não endpoint novo)
`site_events` já tinha a coluna `target_id`; adicionei-a ao SELECT de `analytics_events` (aba
Consultas) e de `aa_events` (Analytics/Eventos). Auth admin inalterada; nenhum dado sensível novo
(só o id do alvo, que o admin já vê).

## Não coberto nesta parte (transparência)
- **Leads:** a lista de leads é chaveada por **e-mail** (mostra e-mail/score/setor, **não** domínio)
  — não há domínio para linkar na tabela. (O detalhe do lead pode ganhar isso na Parte 3.)
- **Comportamento / IPs:** as tabelas de `access_log` (top domínios por IP, ip-detail) mostram
  domínios agregados; deep-linkar exige um JOIN `access_log`→`targets` por domínio numa tabela
  grande. Como a **Parte 3** entrega a "visão 360° do alvo" (comportamento por-alvo), deixei o
  deep-link de comportamento para lá — evita o JOIN caro num surface secundário.
- **Assinantes** (`AssinantesPage`): mostra **contagem** de sites (N/max), não domínios individuais
  — os domínios monitorados aparecem em Clientes/Usuários (já cobertos).

## Segurança
Endpoints admin seguem sob JWT admin (nada relaxado). `DomainLink` só monta uma URL interna a
partir de `target_id` (int) — sem input livre. Os links de saída usam `rel="noreferrer"` +
`encodeURIComponent` no domínio. `contact_email` nunca aparece.

## Testes
`test_kl104_deeplink.py` (+2): `analytics_events` e `aa_events` retornam `target_id`. **Suite:
1633 passed.** Build Astro OK (DomainLink no bundle admin). Navegação validada no build + pós-deploy.

## Validação pós-deploy
Painel → Scans/Alertas/Consultas/Analytics/Sites monitorados: domínio é link → `/painel/alvos/{id}`.
Detalhe do alvo → "Ver perfil público" (nova aba `/site/{domain}`) e "Ver último scan" (nova aba
`/scan?url={domain}`); ocultos quando não aplicável.
