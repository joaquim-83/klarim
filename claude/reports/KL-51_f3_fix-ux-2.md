# KL-51 Fase 3 — 2ª rodada de ajustes de UX

> Itens restantes pós-teste do dono (os do prompt anterior — scan 401/504, contato,
> scan logado, CTA topo, PDF dropdown — já estavam entregues).

## 1. Headline da landing

`web/src/pages/index.astro`: **"Descubra se o seu site é seguro — em 30 segundos."** →
**"Seu site é seguro? Descubra em 30 segundos."** A `description` (meta + og, derivada da
mesma constante no `Base.astro`) foi atualizada com a nova frase.

## 2. Scan a partir do dashboard + linguagem consistente

Convenção adotada em toda a plataforma:
- **Verificar / Consultar** = fazer scan (ilimitado para conta logada)
- **Monitorar** = adicionar ao dashboard com re-scan mensal (limitado pelo plano)

No dashboard (`Dashboard.jsx`):
- Novo bloco **"🔍 Verificar um site"** — form `GET /scan` (consulta livre; logado escaneia
  direto, sem código).
- O antigo **"+ Novo site"** virou **"+ Monitorar outro site"** (secundário); o botão de
  submit do form vira "Monitorar"; a seção "Meus sites" vira **"Sites monitorados"**.
- No limite do plano → mantém o CTA de upgrade (403 do `POST /account/sites`).

## 3. Histórico de consultas no dashboard

- **`GET /account/scan-history`** (JWT de usuário): scans que o e-mail solicitou (via
  `scans.scanned_by_email`, KL-25), **1 linha por URL** (mais recente), semáforo do banco
  (fallback por score p/ scans antigos). `store.get_scan_history_for_email` (DISTINCT ON).
- No dashboard: seção **"Histórico de consultas"** — cada item mostra domínio, score,
  semáforo e data, e abre o resultado (`/scan?url=` → logado reescaneia do cache). **Dedup**
  client-side dos sites já monitorados (não duplica o que o signup vinculou).
- **Vínculo no signup** (já existente do KL-51 f3): `get_targets_scanned_by_email` vincula os
  scans anteriores do e-mail a `user_sites` respeitando `max_sites` — coberto por teste.

## 4. Painel admin: Gestão de Clientes + limpeza

- A página **"Sites Monitorados"** (KL-29, `monitored_sites`) foi substituída por
  **"Gestão de Clientes"** (`/painel/clientes`, `Clientes.jsx`): lista as **contas de
  usuário** (`users` + `user_sites`) com e-mail, plano, sites (score + último scan + semáforo),
  data de criação, último login e status. Backend: **`GET /admin/clients`**
  (`store.list_users_with_sites`, 2 queries numa conexão, sem N+1). A rota antiga
  `/painel/monitorados` **redireciona** para `/painel/clientes`.
- O item **"Escanear"** saiu da navegação do painel (rota + import removidos) — escanear já
  é feito na página **Alvos**.

## Testes

`tests/test_accounts.py` → **29** (novos: `scan_history_requires_auth`,
`scan_history_returns_scans` (semáforo do banco + fallback por score),
`admin_clients_requires_admin` (401 sem token admin), `admin_clients_lists_accounts`
(total/active/total_sites + sites por conta)). SQL validado por sqlglot; Dashboard.jsx +
Clientes.jsx + App.jsx por esbuild; **grafo de imports do painel resolvido por esbuild
bundle** (o `astro build`/`vite build` local trava por problema de ambiente).

## Verificação pós-deploy

Headline nova na landing; `GET /api/account/scan-history` (401 sem sessão, lista com
sessão); painel `/painel/clientes` carrega as contas; `/painel/escanear` fora do menu.
