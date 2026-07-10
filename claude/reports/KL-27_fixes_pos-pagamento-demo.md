# Ajustes pós-KL-27 — resultado completo na tela, duplicação de scans, modo demo

**Origem:** teste real do fluxo completo com pagamento (R$ 19). Três problemas
observados pelo operador. Complemento do KL-27.

---

## Fix 1 — Exibir os 29 checks na tela após o pagamento (não só os PDFs)

**Problema:** após pagar, o visitante ia para `/report` e via **apenas os botões de
PDF** — a lista de checks sumia.

**Solução (backend):**
- `_summary_payload(report, full=True)` passou a **enriquecer cada FAIL** com
  `evidence` (do `CheckResult`) + `impact`/`fix` (do `reporter.generator.TECHNICAL`,
  importado **lazy** para não puxar WeasyPrint no caminho gratuito). PASS/INCONCLUSO
  ficam só com o status. Os 14 pagos vêm com **status real** (não `locked`).
- `GET /scan/summary` agora aceita **`charge_id` de cobrança paga** (além do JWT
  admin e do scan token `full`) como autorização para o resultado completo. Quando
  completo, anexa `report_urls` (com a autorização certa) + `rescan_credits`
  (`_full_extras`).
- **Blindagem:** `_entry` força `status:"locked"` nos 14 pagos sempre que `not full`
  — mesmo que o report em mãos tenha 29 checks, o gratuito nunca vaza o resultado.
- **Anti-duplicação:** `/scan/summary` só **ingere** no caminho público gratuito;
  admin/pago/re-verificação já ingerem no próprio fluxo (evita 2ª linha em `scans`).

**Solução (frontend):**
- `Payment.jsx` passou a navegar para **`/result?url=…&charge_id=…`** (antes ia para
  `/report`).
- `Result.jsx`: no modo completo (`is_full`) mostra o score dos 29, as duas listas
  ("Verificações básicas (15)" + "Checks avançados (14)") com **✅/❌ e detalhe
  expansível** (evidência + impacto + correção) nos FAILs, depois os **PDFs**, e o
  **bloco de re-verificação** (se há crédito). O gratuito continua: 15 ✅/❌ + 14 🔒
  + CTA "Fazer scan completo — R$ 19".
- `fetchSummary(url, chargeId)` / `useSummary(url, chargeId)` passam o `charge_id`.
  `Landing` prefila a URL de `?url=` (usado pelo botão "re-verificação gratuita").

## Fix 2 — Duplicação de scans na "atividade recente"

**Diagnóstico (dados reais da VM):** o site de teste `trade.cidinei.com.br/v2`
tinha **3 scans, todos `source='admin'`**, em ~2 min — foram 3 disparos manuais do
operador durante o teste (não free+pago+rescan). Os demais sites tinham 1 scan cada:
**não há duplicação por bug** no `get_or_scan`/ingest.

**Solução:** a "atividade recente" do painel (`Overview`) passou a usar
`GET /scans?distinct_url=true` — `list_scans` com `DISTINCT ON (url)` devolve **só o
scan mais recente de cada URL**, reordenado por data. Cada linha ganhou um **badge de
tipo** (Básico/Completo/Re-verificação/Admin/Manual/Descoberta/Demo). Um site
escaneado N vezes agora aparece **1 vez**.

## Fix 3 — Modo demo (testar o fluxo sem pagamento real)

`_is_demo(email, url)` casa `DEMO_EMAIL` e/ou `DEMO_URL` (ambos vazios = **desligado**).
Efeitos quando demo:
- **`POST /scan/request-code`** — não envia e-mail; código fixo **`000000`** (`demo:true`).
- **`POST /scan/verify-code`** — aceita `000000` **sem consumir crédito** (repetível).
- **`POST /payment/create`** — cria cobrança **PAID instantânea** (`charge_id`
  `demo_…`), sem chamar a AbacatePay; o polling do frontend vê pago e vai para
  `/result`.
- **Scans** marcados **`source='demo'`** (badge "Demo" na atividade).
- **Alert Worker** pula alvos demo (`is_demo_target` no `_validate_batch`).
- **Cobranças demo não entram em `payments/stats`** (filtro `charge_id NOT LIKE
  'demo\_%'` no Postgres e em memória) — não inflam a receita.

⚠️ **Segurança:** o payload da spec sugeria `DEMO_URL=https://klarim.net`, o que
**liberaria relatório completo grátis do site real** e daria full report a qualquer
um que digitasse o e-mail demo. Por isso o modo demo é **desligado por padrão**
(vars vazias) e keia primariamente pelo **`DEMO_EMAIL`** (e-mail controlado do
operador). Documentado para **nunca** apontar `DEMO_URL` ao domínio de produção.

**Nota de escopo:** o filtro demo cobre a **receita** (o risco real de poluição) e a
atividade recente (badge). Um filtro demo em *todas* as métricas do painel
(funil, scan_stats, etc.) ficou fora — os scans demo já são distinguíveis por
`source='demo'` para um filtro futuro.

## Testes

- `tests/test_kl27_funnel.py` (+5): detalhe no payload completo, gratuito ainda
  bloqueia os pagos, `_is_demo`, `payments/stats` exclui demo.
- `tests/test_scan_verification.py` (+3): request-code demo (sem e-mail),
  verify-code demo (`000000`, sem consumir crédito), código demo errado.
- **Suíte:** offline verde. Build do frontend (Vite) OK.

## Deploy

- CI (`pytest`) → deploy na VM. **Configurar na VM** (`/opt/klarim/.env`):
  `DEMO_EMAIL=demo@klarim.net` (e `DEMO_URL` **vazio** ou um domínio de teste).
- Pós-deploy: flush do cache `scan:*` não é necessário (o payload mudou, não o
  formato do cache), mas não faz mal.

## Arquivos

**Backend:** `api/main.py` (payload completo, `charge_id` no summary, `_full_extras`,
demo), `discovery/store.py` (`list_scans distinct_url`), `payments/store.py`
(`payment_stats` exclui demo), `discovery/alert_worker.py` (`is_demo_target`).
**Frontend:** `lib/api.js`, `lib/useSummary.js`, `pages/Result.jsx`, `pages/Payment.jsx`,
`pages/Landing.jsx`, `pages/admin/Overview.jsx`.
**Config/docs:** `.env.example`, `claude.md`, este relatório.
**Testes:** `test_kl27_funnel.py`, `test_scan_verification.py`.
