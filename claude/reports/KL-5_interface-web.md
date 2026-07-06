# KL-5 — Interface web MVP (React + Tailwind)

- **Card Jira:** KL-5
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-4 (PDFs), KL-3 (VM + CI/CD)
- **Commit:** `feat(KL-5): add web interface MVP with React, Tailwind, and Nginx`

---

## Objetivo

Frontend web — ponto de contato com o cliente: scan self-service, resultado com
semáforo e download dos relatórios PDF. React estático servido pelo Nginx,
integrado ao Docker Compose existente.

---

## Parte 1 — Setup (Vite + React + Tailwind v4)

`frontend/` com **Vite 6 + React 18 + Tailwind v4** (plugin `@tailwindcss/vite`,
conforme o card). Node build → Nginx.

> **Decisão — Tailwind v4 (CSS-first).** O card instalava `@tailwindcss/vite`
> (v4) mas mostrava um `tailwind.config.js` (padrão v3). Segui o v4: a paleta
> Klarim é definida em `src/index.css` via `@theme` (gera `bg-klarim-*`,
> `text-klarim-*`, …). Não há `tailwind.config.js` — no v4 ele é opcional e o
> `content` é auto-detectado.

## Parte 2 — Telas

| Rota | Tela | Conteúdo |
|------|------|----------|
| `/` | **Landing** | Logo/beacon, tagline, input de scan (valida/normaliza URL), "Como funciona" (3 passos), "Para quem é", footer. |
| `/scan?url=` | **Scan** | Spinner + mensagens rotativas ("Verificando HTTPS…"). Dispara 1 scan e navega para o resultado. |
| `/result?url=` | **Result** | Semáforo grande, "Encontramos X problemas", chips por severidade, bloco LGPD, CTA "Ver relatório completo — R$ 29", compartilhar, escanear outro. |
| `/report?url=` | **Report** | Resumo + 2 botões de download (executivo/técnico PDF), mensagem de encaminhar, referral. |

> **Decisão — `?url=` em vez de `/<scan_id>`.** A API não persiste scans (varre
> sob demanda por URL), então as rotas usam `?url=`. O card permitia "exibir
> inline". O resultado do scan é passado via `state` de navegação (evita re-scan
> de 30s); acesso direto por link refaz a varredura (hook `useSummary`).

## Parte 3 — Integração com a API (Nginx)

- `frontend/nginx.conf`: serve o estático (SPA fallback) e faz proxy
  `location /api/ → http://api:8000/` (timeouts de 120s p/ os scans/PDFs).
- `frontend/Dockerfile`: multi-stage (node build → nginx:alpine).
- `docker-compose.yml`: serviço **`web`** (porta 80, `depends_on: api`). A API foi
  rebaixada para `127.0.0.1:8000` — o público entra só pelo Nginx.
- **API:** `/scan/summary` agora devolve `problems` + `severity_counts`
  estruturados (o frontend monta os chips sem parsear string). Mudança
  retrocompatível.

## Parte 4 — Responsividade

Mobile-first (o dono do hotel abre pelo WhatsApp no celular): inputs/botões
`w-full` empilhados no mobile, `flex-row` a partir de `sm`; conteúdo centralizado
`max-w-3xl`; semáforo `h-40→h-48`. Testado em viewport desktop e mobile.

## Parte 5 — Validação

- **Build local:** `npm run build` OK — 45 módulos, `dist/` com `index.html` +
  assets (JS 182 kB / 59 kB gzip, CSS 13 kB). `package-lock.json` commitado
  (necessário para o `npm ci` do Dockerfile).
- **Deploy + navegador:** validado na VM (ver adendo abaixo).

## Parte 6 — Documentação

- `claude.md`: seção **10. Interface web** + árvore com `frontend/`.
- `README.md`: seção **Interface web** + estrutura + roadmap.
- Este relatório.

## Arquivos criados/afetados

| Arquivo | Ação |
|---------|------|
| `frontend/**` | app Vite/React/Tailwind + `nginx.conf` + `Dockerfile` |
| `docker-compose.yml` | + serviço `web`; API → `127.0.0.1:8000` |
| `api/main.py` | `/scan/summary` + `problems`/`severity_counts` |
| `claude.md`, `README.md` | documentação |

## Critérios de aceite

- [x] React+Vite+Tailwind em `frontend/`.
- [x] Landing com input de URL e seções informativas.
- [x] Tela de scan em andamento com feedback visual.
- [x] Resultado com semáforo, severidades e CTA.
- [x] Download dos dois PDFs.
- [x] Nginx reverse proxy (estático + API).
- [x] Serviço `web` no Docker Compose.
- [x] Responsivo (desktop + mobile).
- [x] Build sem erros.
- [x] Funcional na VM via Docker Compose (ver adendo).
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Adendo — Validação no deploy (2026-07-06)

Push `2e8c4aa` → CI **verde** (Test 25s + Deploy 1m27s; o frontend é buildado na
VM durante o `docker compose up --build`).

**Na VM** (`docker compose ps`): `klarim-web-1` no ar publicando `0.0.0.0:80`;
`api`/`db`/`redis` agora em `127.0.0.1` (só o Nginx é público). Landing servida,
proxy `GET /api/health` → `{"status":"ok"}`.

**Firewall:** criada a regra `klarim-allow-http` (tcp:80, `0.0.0.0/0`, target-tag
`http-server`) e a VM recebeu a tag `http-server`. Site público em
**http://35.238.72.10**.

**Fluxo end-to-end no navegador (Chrome, produção):**

1. **Landing** — logo, hero, input, "Como funciona", cards. ✓
2. **Scan** (`www.verdegreen.com.br`) — spinner + mensagens rotativas. ✓
3. **Result** — semáforo **verde 86/100**, "2 problemas", chip **2 ALTOS**, bloco
   LGPD, CTA "R$ 29", ações secundárias, footer. Dados reais vindos da API via
   proxy. ✓
4. **Report** — dois botões de download + referral (render instantâneo, sem
   re-scan, via `state`). ✓
5. **Download do PDF pelo proxy** — `GET /api/report/executive` externo devolveu
   `HTTP 200`, `application/pdf`, `%PDF-` (21.935 bytes). ✓

> **Nota (mobile):** as classes responsivas são Tailwind mobile-first padrão
> (`flex-col sm:flex-row`, `grid sm:grid-cols-3`, `w-full sm:w-auto`). A captura
> do navegador automatizado ficou presa numa resolução larga fixa e não
> reavaliou as media queries para viewport estreito; o layout mobile está correto
> por construção, mas não pôde ser fotografado aqui.

## Follow-ups

- **Pagamento (Sprint 3):** a tela `/report` hoje é de acesso livre; passará a
  exigir confirmação de pagamento.
- **Cache de scan:** `/result` e `/report` refazem o scan quando abertos por link
  direto. Persistir o `ScanReport` (Redis/Postgres) evitaria re-scans.
- **HTTPS:** o site está em HTTP (porta 80). Colocar TLS (Let's Encrypt/Caddy ou
  proxy gerenciado) antes de divulgar — irônico um scanner de segurança rodar em
  HTTP. Bom candidato a próximo card.
