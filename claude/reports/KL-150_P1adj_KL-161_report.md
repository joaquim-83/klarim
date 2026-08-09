# KL-150 (ajuste) + KL-161 — Menu "Desenvolvedor" + conformidade LGPD completa

> **Status: DEPLOYADO EM PRODUÇÃO ✅** (09/08/2026) — validação visual aprovada pelo Cidinei,
> push + CI/CD verde, verificação pós-deploy OK. Ver a seção **Deploy** no fim.

## Resumo

Dois cards numa entrega: (KL-150 ajuste) o menu de devs volta a ser um **dropdown "Desenvolvedor ▼"**
(antes era link direto); (KL-161) **conformidade LGPD completa** — canal público de direitos (DSAR),
DPO, atualização de Privacidade/Termos, footer, "Remover meus dados" no perfil, ROPA interno e o
remetente `privacidade@klarim.net`.

---

## KL-150 (ajuste) — Menu "Desenvolvedor ▼"

Reverti o link direto "Security Gate" (KL-150 P1) para **dropdown "Desenvolvedor ▼"** com 1 sub-item
(Security Gate) — intencional, preparado para novos produtos. `nav.js` ganhou a constante
`DEV_DROPDOWN_LABEL='Desenvolvedor'` (testável); o `Header.astro` usa `<NavDropdown label={DEV_DROPDOWN_LABEL}
links={DEV_LINKS}>` nos dois estados (logado/deslogado) e no drawer mobile. O mecanismo de
abrir/fechar (Fix 1 do KL-150 P1 no `header.js`) já cobre o novo dropdown — nada a mudar lá.

**Validado no browser:** header exibe "Para empresas ▼ · Desenvolvedor ▼ · Blog"; clicar
"Desenvolvedor" abre e mostra "Security Gate" (e fecha "Para empresas").

---

## KL-161 — Conformidade LGPD

### 1. Canal de direitos `/lgpd` (DSAR) + `POST /api/lgpd/request`
- **Página** `web/src/pages/lgpd.astro` + ilha `components/lgpd/LGPDForm.jsx` (form: tipo, nome,
  e-mail, CPF opcional mascarado, descrição). Lê `?tipo=` da URL e pré-seleciona (ex.: o link
  "Remover meus dados" do perfil manda `?tipo=exclusao`). Lógica pura em `web/src/lib/lgpd.js`
  (tipos, `tipoFromParam`, `validateLgpdForm`) — espelha o backend.
- **Endpoint** `POST /lgpd/request` (público, sem conta): valida (tipo, e-mail, nome, descrição ≥10),
  **CPF opcional** (se inválido → avisa `cpf_warning`, NÃO bloqueia e não grava o valor), **rate limit
  3/e-mail/dia**, grava em `lgpd_requests` e dispara 2 e-mails best-effort (confirmação ao titular +
  notificação ao operador). Resposta: `{id, message, confirmation_sent, cpf_warning}`.
- **Tabela** `lgpd_requests` (id UUID, type, name, email, cpf, description, status pending→in_progress→
  resolved/denied, admin_notes, created_at, resolved_at) no `ensure_schema` idempotente.
- **E-mails** (`notifier/email_client.py`): `send_lgpd_confirmation` (texto puro, protocolo, 15 dias
  úteis) + `send_lgpd_admin_notification` (HTML, todos os campos, Reply-To = e-mail do titular).
  Remetente `privacidade@klarim.net` (env `LGPD_FROM_EMAIL`); operador `klarimscan@gmail.com` (env
  `LGPD_ADMIN_EMAIL`). Registrados no `email_log` (types `lgpd_confirmation`/`lgpd_admin`).

### 2. `/privacidade` atualizada
- §1 **Controlador e Encarregado (DPO)** → formulário `/lgpd` + `privacidade@klarim.net`.
- §2 dados do **Security Gate** (nome, CPF, endereço, telefone, e-mail, API key, histórico).
- §5 **Reoon** clarificado (verificação de deliverability, sem impacto no envio ao titular).
- §6 **retenção** detalhada (KYC = conta + 30 dias; audit CPF+IP+URL = 2 anos; logs 90 dias) e o
  prazo **48 horas → até 15 dias úteis (ANPD)**.
- §7 direitos + revogação + **link para `/lgpd`**.

### 3. `/termos` — nova **§9 Security Gate** (KYC, auditoria por CPF, uso indevido → suspensão, PIX,
cancelamento por `/lgpd`); §4 (perfil público) com prazo 48h → **15 dias úteis** + link `/lgpd`.

### 4. Footer — "Seus direitos (LGPD)" → `/lgpd` (em "Empresa & Legal").

### 5. Perfil público `/site/{domínio}` — "**Remover meus dados (LGPD)**" → `/lgpd?tipo=exclusao`.

### 6. ROPA interno — `docs/LGPD.md` (tabela de tratamento + DPO + canal + notas técnicas).

### 7. `privacidade@klarim.net` — remetente. O domínio `klarim.net` já é verificado no Resend →
basta o alias para **enviar** (nenhuma config técnica extra). **Recebimento**: se não houver MX/
forwarding, o endereço é só remetente e o **formulário `/lgpd` é o canal oficial** (as solicitações
ficam em `lgpd_requests` + notificação ao operador). Documentado no `docs/LGPD.md`.

### 8. Nginx — `lgpd` adicionado às allowlists de conteúdo (`http.conf` + `https.conf.template`),
ao lado de `privacidade`. `/api/lgpd/request` já é coberto pelo proxy `/api/` existente.

---

## Testes

- **Backend `pytest`: 2300 passed, 1 skipped** (+12 novos em `tests/test_kl161_lgpd.py`: sucesso,
  422 sem e-mail/tipo inválido/descrição curta, CPF inválido→aviso, CPF válido→formatado, rate limit
  4ª→429, rate limit por-e-mail, confirmação/notificação enviadas, sem e-mail quando desabilitado).
- **Frontend `node --test`: 211 passed** (+8: `DEV_DROPDOWN_LABEL` + 7 de `lgpd.test.js`).
- **`npm run build`: OK.**
- **`nginx -t`: OK** (http.conf + https.conf.template renderizado, exatamente como a CI).

## Validação no browser (docker-compose.dev.yml) — feita

| # | Item | Resultado |
|---|---|---|
| 12 | Header "Desenvolvedor ▼" (dropdown) | ✅ |
| 13 | Dropdown abre → "Security Gate" (fecha "Para empresas") | ✅ |
| 14 | `/lgpd` form com os 5 campos + 6 tipos | ✅ |
| — | `?tipo=exclusao` pré-seleciona "Exclusão" | ✅ |
| 15 | Submit → "Solicitação enviada" + protocolo (UUID) | ✅ |
| — | Endpoint end-to-end (registro em `lgpd_requests`, 422 tipo inválido) | ✅ |
| 16 | `/privacidade`: DPO, dados do Gate, retenção, canal `/lgpd`, Reoon, sem "48 horas" | ✅ |
| 17 | `/termos`: §9 Security Gate + `/lgpd`, sem "48 horas" | ✅ |
| 18 | Footer: "Seus direitos (LGPD)" → `/lgpd` | ✅ |
| 19 | Perfil: "Remover meus dados (LGPD)" → `/lgpd?tipo=exclusao` | ✅ |
| — | Console do browser | zero erro/CSP |

## Arquivos

**Backend:** `discovery/store.py` (tabela `lgpd_requests` + `create/list_lgpd_request`),
`notifier/email_client.py` (2 métodos + 2 `EMAIL_TYPES`), `api/main.py` (endpoint + modelo + helpers).
**Frontend:** `web/src/pages/lgpd.astro` (novo), `web/src/components/lgpd/LGPDForm.jsx` (novo),
`web/src/lib/lgpd.js` (novo) + `lgpd.test.js` (novo), `web/src/lib/nav.js` + `nav.test.js`,
`web/src/components/Header.astro`, `web/src/components/Footer.astro`,
`web/src/pages/{privacidade,termos,site/[domain]}.astro`, `web/package.json`.
**Nginx:** `frontend/nginx/http.conf` + `https.conf.template`.
**Docs:** `docs/LGPD.md` (novo), `claude.md`.
**Testes:** `tests/test_kl161_lgpd.py` (novo).

## Deploy (pós-autorização)

- Sem migração manual: `ensure_schema` cria `lgpd_requests` no boot.
- **Sem flush Redis** (nada de scoring/score).
- Opcional no `.env` da VM: `LGPD_FROM_EMAIL` (default `privacidade@klarim.net`), `LGPD_ADMIN_EMAIL`
  (default `klarimscan@gmail.com`). Confirmar `privacidade@klarim.net` como remetente no Resend
  (o domínio já é verificado → deve funcionar direto).
- Nginx: `nginx -t` verde na CI; a rota `/lgpd` entra pela allowlist.

## Escopo NÃO tocado

Engine de scan, rate limiting do scanner, scanner público e o SEO (títulos/URLs/Schema.org do KL-132)
permaneceram intactos. `/lgpd` fica fora do sitemap (consistente com `/contato`, `/privacidade`).

---

## Deploy (09/08/2026)

Commits (direto no `main`, convenção do repo):
- `e2a37fe` — `feat(KL-150): menu Desenvolvedor + redirect dev + dashboard dev diferenciada`
- `2f7d371` — `feat(KL-161): conformidade LGPD — canal de direitos /lgpd + DPO + ROPA`
- `git push origin main`: `dc17918..2f7d371` ✓

**CI/CD — GitHub Actions run #293** (ID `31313545327`) — conclusão **success**:

| Step | Status | Tempo |
|---|---|---|
| Nginx config check | ✓ | 9s |
| Test (pytest) | ✓ | 1m42s |
| Build web (Astro) | ✓ | 15s |
| Deploy to GCP VM | ✓ | 3m46s |
| Security Gate (live, pós-deploy) | ✓ | 32s |

(Único aviso: deprecação do Node.js 20 nas actions — informativo, não bloqueia.)

**Verificação pós-deploy (produção `klarim.net`):**
- `GET /lgpd` → **200**; `GET /lgpd?tipo=exclusao` → **200**.
- `POST /api/lgpd/request` (`type=acesso`) → **200** `{id: 19f26d10-…, confirmation_sent: true,
  cpf_warning: false}` — o INSERT retornou o protocolo (tabela OK) e o e-mail foi despachado.
- `POST` com tipo inválido → **422**.
- **VM** (`sudo docker exec klarim-db-1 psql … "\d lgpd_requests"`): tabela criada pelo
  `ensure_schema` com todas as colunas + 3 índices (pkey, email, status). Linha de teste
  confirmada (`acesso · teste@teste.com · pending`).
- **E-mail (`confirmation_sent: true`)**: o remetente **`privacidade@klarim.net` FUNCIONA no
  Resend** (o domínio `klarim.net` já é verificado → o alias envia sem config extra). Foram
  disparados a confirmação ao titular (`teste@teste.com`) e a notificação ao operador
  (`klarimscan@gmail.com`).

**Limpeza:** a linha de teste do deploy (`teste@teste.com`, "Teste de deploy") foi **removida** da
produção — `lgpd_requests` voltou a 0 registros (fila LGPD limpa para o operador).

**Sem flush Redis** necessário (nada de scoring/score). Nenhum `.env` novo é obrigatório
(`LGPD_FROM_EMAIL`/`LGPD_ADMIN_EMAIL` têm defaults corretos).
