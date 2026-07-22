# KL-90 Prompt 3 (final) — Swap: Dashboard v2 em produção

**Data:** 2026-07-22
**Commit:** `6bbf1d2` · **Deploy:** GitHub Actions run `29921402747` — **4/4 verde**
**Produção:** https://klarim.net

O Dashboard v2 assumiu a rota `/dashboard` em produção. Deploy verde, validado pós-deploy.

---

## 1. Swap de rotas
- `web/src/pages/dashboard/index.astro` agora monta **`DashboardV2`** (o antigo montava `Dashboard.jsx`).
- `web/src/components/account/Dashboard.jsx` (antigo) **removido** (só era importado pela index; confirmado sem outras referências).
- `web/src/pages/dashboard/v2.astro` **removido**.
- **Redirect:** `src/middleware.js` faz **`/dashboard/v2` → 301 `/dashboard`** ANTES da checagem de auth
  (vale logado ou não). `SiteDetail` (`/dashboard/site/[id]`) e seus componentes foram mantidos.
- Referências a `/dashboard/v2` no código trocadas por `/dashboard` (Header "Meu dashboard", conta
  back-link, planos-auth upgrade).

## 2. Header global
O `Header.astro` (avatar + busca + `header.js` externo) já é incluído por todas as páginas públicas
(index, setores, scan, planos, setor, site, melhores, estatísticas — 20 páginas). Item confirmado.

## 3. Commit seletivo (proteção de PII)
Em vez de `git add -A`, adicionei **explicitamente** só os arquivos do KL-90. A árvore tinha 23
arquivos untracked **pré-existentes** de sessões anteriores — incluindo `emails-sent-*.csv`
(e-mails de clientes = PII) e PDFs/logs. **Nenhum CSV/PDF/log/PII foi commitado** (verificado). O
`.env.dev` é gitignored. Os arquivos pré-existentes seguem untracked (fora do escopo do KL-90).

## 4. CI/CD — 4/4 verde
| Job | Resultado |
|---|---|
| Build web (Astro) — install, test:unit, build | ✅ |
| Test — pytest | ✅ |
| Nginx config check — `nginx -t` (http.conf + https rendered) | ✅ |
| Deploy to GCP VM — docker compose up | ✅ |

> `frontend/nginx/dev.conf` **não** entra no build de produção (o Dockerfile copia os `.conf` por
> nome; o `nginx -t` do CI valida só `http.conf`/`https.conf`) — sem risco de conflito.

## 5. Validação pós-deploy (produção)
| Critério | Resultado |
|---|---|
| Páginas públicas: `/`, `/scan`, `/setor/tecnologia`, `/melhores`, `/planos` | **200** |
| `/api/health` | `{"status":"ok"}` |
| `/site/klarim.net` (perfil público) | 200 |
| Scripts externos `/header.js`, `/planos-auth.js`, `/theme.js` | 200 (CSP `script-src 'self'`) |
| **`/dashboard/v2` → 301 → `/dashboard`** | ✅ (prova que o código novo está no ar) |
| `/dashboard` sem auth → `/entrar` (302) | ✅ (protegido pelo middleware) |
| `/api/account/dashboard-summary` sem auth | **401** ✅ |
| Console do browser (páginas públicas, CSP estrita) | **zero erros, zero violação de CSP**; GA4 e scripts carregam |
| **Score do klarim.net** (self-scan `refresh=1`) | **100 · verde** ✅ |
| **Workers** (`get_system_status`) | discovery/alert/rescan/scan **4/4 alive**; postgres/redis ok |
| Flush Redis | **não necessário** — o `dashboard-summary` não é cacheado (é computado ao vivo) e o scoring/checks não mudaram |

### Ressalva honesta
Não tenho **credenciais de uma conta real de produção**, então não fiz login no `/dashboard`
autenticado em produção. Mitigação: o endpoint responde **401** sem auth (correto), a rota está
protegida e serve o componente `DashboardV2` (deployado), o padrão de islands+CSP foi validado nas
páginas públicas (sem violação de CSP), e o componente é **idêntico** ao que foi validado à exaustão
no ambiente de dev (score/seletor/monitoramento/riscos/selo/técnico/plano QR/remover site/técnico-role).

---

## Critérios de "entregue"
- [x] CI/CD 4/4 verde
- [x] `/dashboard` serve o v2 em produção (deployado; protegido; o 301 do v2 prova o código novo no ar)
- [x] `/dashboard/v2` → 301 `/dashboard`
- [x] Páginas públicas 200
- [x] API health OK
- [x] Zero erros no console (páginas públicas, CSP estrita)
- [x] Workers alive (discovery, alert, rescan, scan)
- [x] Score klarim.net = 100 🟢

## Regras
- ✅ Relatório PT-BR · ✅ CI verde antes de reportar · ✅ produção validada · ✅ nenhum rollback necessário.
