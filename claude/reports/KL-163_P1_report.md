# KL-163 (Prompt 1/2) — Relatório PDF do Security Gate

**Status:** ✅ implementado e validado no `docker-compose.dev.yml`. **SEM DEPLOY** — aguarda a
revisão visual do dono.

**Card:** KL-163 — Security Gate: relatório PDF, endereço estruturado, KYC (este prompt cobre só o
**relatório PDF de um run**; endereço estruturado e demais itens ficam para o Prompt 2).

---

## 1. O que foi entregue

O desenvolvedor agora **exporta o resultado de um scan do Gate como PDF** — pelo dashboard ou via
API/CLI. O PDF é denso e técnico (cabeçalho + todas as categorias com cada check + recomendação nas
falhas + resumo + rodapé paginado), com o **CPF sempre mascarado**.

### Endpoint `GET /gate/runs/{run_id}/report` (`api/gate.py`)

- **Auth:** API key (`X-API-Key`) **OU** sessão (cookie `klarim_session`) — reusa
  `_resolve_gate_account`. Sem nenhum dos dois → **401**.
- **Validação (nesta ordem):**
  - O run é buscado **sem filtro de conta** (`get_gate_run(run_id)`) para distinguir os dois casos:
    - run inexistente → **404**;
    - run de **outra conta** (`account_id` diferente) → **403**.
  - Conta **sem KYC** (`kyc_completed=false`) → **403** "Complete seu cadastro para gerar relatórios".
- **Resposta:** `application/pdf` com `Content-Disposition: attachment; filename="klarim-gate-{domínio}-{AAAA-MM-DD}.pdf"`.
- **Audit:** grava a ação `run_report` (compliance).
- **Nada da engine de scan é executado** — o endpoint só consome os `results` já persistidos em
  `gate_runs`.

> **Decisão:** o endpoint já existente `GET /gate/runs/{id}/pdf` (KL-152 P3) **fica intacto** — ele
> usa o template de **fornecedores** (vendor-style, categorias resumidas/redigidas). O `/report` é um
> template **novo e dedicado**, mais rico e gateado por KYC. Assim não há regressão no que já existe.

### Módulo novo `reporter/gate_run_report.py` (padrão testável do repo)

Separado do `reporter/gate_report.py` (que é a avaliação de FORNECEDORES do Enterprise, redigida):

- `build_gate_run_context(run, *, cpf_masked, plan_name, generated_at)` — **PURO** (dict do run →
  contexto; sem I/O nem relógio: a data e o CPF mascarado são injetados pelo chamador). Agrupa os
  checks por categoria na ordem canônica, conta pass/fail/erros e falhas por severidade, e destaca as
  falhas críticas/altas para o resumo.
- `build_gate_run_report_html(context)` — **PURO** (Jinja `Template` bare + `html.escape` dos campos
  dinâmicos no builder → sem risco de HTML malformado quebrar a renderização).
- `generate_gate_run_report_pdf(context)` — **async** (WeasyPrint em `asyncio.to_thread`, CPU-bound).
- Helpers: `fmt_scan_date` (created_at → `DD/MM/AAAA às HH:MM` no **fuso de Brasília**),
  `report_filename`, `mask_cpf` (em `api/validators.py`).

### Template PT-BR / A4 (print-friendly)

- **Cabeçalho:** domínio, data do scan (Brasília), **score + cor do semáforo** (verde ≥90 · amarelo
  50-89 · vermelho <50), resultado (Passou/Reprovou + "reprova em falha {severidade} ou pior"), plano,
  **Desenvolvedor: CPF `***.***.NNN-NN`** (só quando há CPF).
- **Resumo:** "Total de N verificações: X passaram, Y falharam" + tags de severidade + lista de
  **falhas prioritárias (críticas/altas)**.
- **Verificações por categoria:** para cada categoria (rótulos reusados de
  `security_gate.formatters.terminal._CATEGORY_LABELS` → **sem drift** quando um check novo é
  adicionado), uma tabela com cada check (ícone, nome humanizado, severidade colorida, status
  colorido) e, no **FAIL**, uma linha recuada em itálico com a **recomendação** (o campo `detail` do
  check — o `Result` da engine não tem um campo `recommendation` separado, e a engine **não foi
  alterada** neste prompt).
- **Score 100 (sem falhas):** caixa verde "Nenhum problema encontrado. Todas as N verificações
  passaram — score 100/100 ✅".
- **Rodapé (todas as páginas):** "Relatório gerado pelo Klarim Security Gate — scanner 100% passivo ·
  klarim.net/security-gate · {data} · Página X de Y" (via `@page { @bottom-center { counter(page) …
  counter(pages) } }`).

### `mask_cpf` (`api/validators.py`)

`mask_cpf("529.982.247-25")` → `"***.***.247-25"` (mantém só o 3º grupo + os 2 dígitos verificadores;
CPF malformado → `"***.***.***-**"`, nunca vaza um valor parcial). O CPF **completo** só existe no
audit log — **nunca** em um documento compartilhável.

### Frontend (`web/src/components/dashboard-v2/GatePortal.jsx` + `web/src/lib/gate/ux.js`)

- Helper **PURO** `reportButton(kycCompleted)` → com KYC `{show:true, label:'📄 Exportar relatório'}`;
  sem KYC `{show:false, message:'Complete seu cadastro para gerar relatórios.'}`.
- Componente `ReportButton` (baixa o PDF via **blob** — o endpoint devolve `attachment`; loading
  "Gerando PDF…"; erro inline; sem KYC mostra a mensagem de bloqueio com link para o cadastro).
- Renderizado no **RunDetail** (histórico do projeto) e no **ScanResultCard** (após o KYC, quando
  `access_level === 'complete'`, usando o `run_id` do scan avulso).

---

## 2. Segurança

- **CPF sempre mascarado** no PDF (teste dedicado garante que o CPF completo nunca aparece; o builder
  só recebe o valor já mascarado).
- **Autorização:** ownership por conta (403 em run de terceiro) + **gate de KYC** (403). Reusa o
  `_resolve_gate_account` (API key OU sessão), sem novo vetor de auth.
- **Sem SSRF/entrada de rede:** o relatório é gerado a partir de dados já persistidos; nenhuma URL de
  entrada é requisitada.
- **`html.escape`** de domínio, nome do check e `detail` no builder (defesa contra HTML malformado no
  render).
- **Sem nginx novo:** o endpoint vive sob `/api/gate/*`, já proxiado (o mesmo caminho do
  `/gate/runs/{id}/pdf`).

---

## 3. Testes

**Backend — `tests/test_kl163_gate_report.py` (+16, todos passam):**
1. sessão válida → **200** + `application/pdf` + `Content-Disposition attachment` (filename correto) +
   audit `run_report`.
2. API key válida → **200**.
3. sem auth → **401**.
4. run de outra conta → **403**.
5. sem KYC → **403** (mensagem "…cadastro…").
6. run inexistente → **404**.
7. HTML contém o domínio, a data (17:32Z → **14:32 Brasília**), o score e o **CPF mascarado**.
8. HTML contém os nomes dos checks + a **recomendação** (detail) dos FAIL + os rótulos de categoria +
   contagem por severidade.
9. score 100 → `no_findings` + "Nenhum problema encontrado" + **sem** a linha do desenvolvedor quando
   `cpf_masked=None`.
10. `generate_gate_run_report_pdf` renderiza um **PDF real** (`%PDF-`, WeasyPrint).
11. `mask_cpf` parametrizado (formatado/sem formatação/vazio/curto/None).

**Frontend — `web/src/lib/gate/ux.test.js` (+4 casos, `node --test`):**
- `reportButton(true)` → mostra o botão com o rótulo certo.
- `reportButton(false|undefined|null)` → mensagem de bloqueio (não botão).

**Suítes relacionadas:** `test_kl151_gate_product` + `test_kl152_vendors` + `test_kl153_backend` +
`test_kl163_gate_report` = **111 passed**. `npm run build` OK, `test:unit` verde.

---

## 4. Validação no navegador (dev — OBRIGATÓRIA)

Stack `docker-compose.dev.yml` (localhost:3000). Foi semeada uma conta dev (`gatedev@teste.com`,
KYC completo) com um projeto verificado e 2 runs (score 80 com falhas + score 100).

1. **Dashboard Gate → projeto "example.com.br" → Histórico → expandir um run:** o botão
   **"📄 Exportar relatório"** aparece no detalhe do run. ✅
2. **Clicar em "Exportar relatório":** dispara `GET /api/gate/runs/2/report` → **200
   application/pdf**; o PDF é baixado; o botão volta ao estado normal (sem travar). ✅
3. **Conteúdo do PDF (renderizado para PNG e inspecionado visualmente):**
   - Run score 80: cabeçalho com domínio/data(Brasília)/`80/100` em amarelo/`Reprovou`/`Free`/
     `CPF ***.***.777-35`; resumo "5 passaram, 3 falharam" + tags de severidade + falhas
     prioritárias; tabelas por categoria (Headers/SSL·TLS/E-mail SPF-DKIM-DMARC/Exposure) com as
     recomendações recuadas nos FAIL; rodapé "Página 1 de 2". ✅
   - Run score 100: `100/100` em verde, `Passou`, caixa verde **"Nenhum problema encontrado. Todas as
     5 verificações passaram — score 100/100"**, todas as categorias PASS. ✅
4. **Sem KYC:** validado por teste (backend 403 + `reportButton(false)` → mensagem). Na UI, a conta
   sem KYC vê "🔒 Complete seu cadastro para gerar relatórios" no lugar do botão.

> **Observação (não bloqueia):** os emojis do semáforo (🟢🟡🔴) e de status (✅❌) não são
> rasterizados no container Linux (sem fonte de emoji colorido), então no PDF eles ficam invisíveis —
> exatamente como no PDF de fornecedores já existente (`gate_report.py`). O significado é carregado
> pelas **cores** (score/PASS/FAIL) e pelos textos "Passou/Reprovou" e "PASS/FAIL", então o relatório
> é totalmente legível sem os emojis. No navegador (Chrome, com fonte de emoji) os ícones aparecem
> normalmente.

---

## 5. Arquivos

**Novos:** `reporter/gate_run_report.py`, `tests/test_kl163_gate_report.py`.
**Alterados:** `api/validators.py` (`mask_cpf`), `api/gate.py` (endpoint `/gate/runs/{id}/report`),
`web/src/lib/gate/ux.js` (`reportButton`/`REPORT_KYC_MESSAGE`),
`web/src/components/dashboard-v2/GatePortal.jsx` (`ReportButton` no RunDetail + ScanResultCard),
`web/src/lib/gate/ux.test.js`, `docs/API.md`, `CLAUDE.md`.

**Não alterado (regra do card):** a engine de scan, o KYC e o endereço (Prompt 2).

---

## 6. Próximo (Prompt 2)

Endereço estruturado + demais itens de KYC do card KL-163.
