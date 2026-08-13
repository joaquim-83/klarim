# KL-164 — Fix checks LGPD (DSAR/DPO multi-página + X-XSS-Protection informativo)

**Data:** 2026-08-12 · **Prioridade:** Alta · **Deploy:** direto (fix pequeno).

## Problema
A ferramenta LGPD (`/api/tools/lgpd`) mostrava **6/8** para o próprio `klarim.net`. Os checks de
**Canal DSAR** e **DPO** só olhavam a **homepage** — e a landing do klarim.net (KL-81, minimalista)
não tem esses elementos. Confirmado empiricamente: `/privacidade` tem "Encarregado/DPO/Proteção de
Dados" e `/lgpd` tem o formulário de direitos + texto ("seus direitos", "titular"), mas a homepage
não. Além disso, o `X-XSS-Protection` (header legado, deprecado) contava como falha no score da
ferramenta de Headers.

## 3 fixes

### 1 + 2. DSAR e DPO em múltiplas páginas (`scanner/privacy_checks.py`)
Os dois indicadores passam a procurar em páginas internas **quando a homepage falha** — passivo,
bounded, fail-open. Funções **puras/testáveis**:
- `_dsar_signal(html, links, path)` — link/texto de direitos (`_DSAR_*`), e-mail de privacidade/DPO,
  ou `<form>` numa página **dedicada** a direitos (`/lgpd`, `/direitos`…). O form genérico de
  `/contato` **não** conta (evita pass-always).
- `_dpo_signal(html)` — vocabulário de Encarregado/DPO (mesmo do check homepage).
- `_privacy_candidate_urls(base_url, links, need_dsar, need_dpo)` — monta as URLs a sondar:
  **links do footer** com vocabulário de direitos primeiro (evidência específica do site, teto
  `_PRIVACY_FOOTER_MAX=3`, **só mesma origem**), depois os paths fixos de maior valor
  (`/privacidade`, `/lgpd`, `/contato`, `/sobre`, `/direitos`), dedupe, **teto `_PRIVACY_EXTRA_MAX=6`**.
- `augment_privacy_checks(checks, pages)` — reavalia dsar/dpo contra páginas já buscadas; só faz
  **upgrade FAIL→PASS** (nunca rebaixa).
- `scan_privacy` orquestra o I/O: roda os 8 indicadores na homepage; **só se dsar OU dpo falharem**,
  busca as páginas candidatas (`base.fetch`, rate-limited 1 req/s/domínio, timeout 10s), com
  **early-exit** assim que ambos passam, e recomputa o score. Erro em página extra → ignora; erro
  geral → mantém o resultado da homepage.

**Custo:** o `scan_privacy` roda em todo scan completo (privacy_score). As buscas extras só ocorrem
quando a homepage falha dsar/dpo e param cedo (o klarim.net resolve em **1 fetch** — `/privacidade`
tem DPO e rights). Teto de 6 páginas/scan, serializadas pelo rate limiter por-domínio.

### 3. X-XSS-Protection → informativo (`api/tools.py`, ferramenta Headers)
Não existe check de X-XSS-Protection no scanner — ele só aparecia na **ferramenta de Headers** do
KL-134, contando no score "N/7". Agora a entrada tem `informational=True`: é **excluída do score**
(numerador e denominador) e exibida como **informativa**, não como falha. `build_headers_response`
passa a devolver `score` sobre os **6** headers modernos. Frontend: `statusMeta('info')` (ícone ℹ
neutro) + `HeadersResult` renderiza o header informativo com status `info` (não vermelho).

## Validação (dev, `docker-compose.dev.yml`)
| Alvo | Resultado |
|---|---|
| `klarim.net` LGPD | **8/8 "Adequado"** — DSAR e DPO PASS (encontrados em `/privacidade`, early-exit em 1 fetch) |
| `klarim.net` Headers | **6/6** — X-XSS-Protection `present:false, informational:true, importance:informativo` |
| `example.com` LGPD | **3/8** — DSAR **FAIL** + DPO **FAIL** (not pass-always) |
| `iana.org` LGPD | **4/8** — DSAR **FAIL** + DPO **FAIL** (not pass-always) |

**Browser:** ferramenta Headers mostra badge **6/6** verde e o X-XSS-Protection com ícone ℹ, badge
"INFORMATIVO" e texto "Sua ausência NÃO conta como falha". Zero erro no console.

## Testes
- **+11 backend** `tests/test_kl164_privacy_multipage.py` (sinais dsar/dpo positivos e negativos,
  candidatos [footer/mesma-origem/cap], augment upgrade/never-downgrade/**not-pass-always**,
  integração `scan_privacy` mockada: multipágina→PASS e sem-sinais→FAIL).
- `tests/test_kl134_tools.py` — headers `3/7`→`3/6` + assert X-XSS informativo.
- Frontend: `statusMeta('info')` em `tools.test.js`.
- **2389 pytest passed** (1 skipped) · **246 node --test** · build OK.

## Arquivos
- `scanner/privacy_checks.py` (multi-página), `api/tools.py` (X-XSS informativo),
  `web/src/lib/tools.js` (`statusMeta` info), `web/src/components/tools/Results.jsx` (render info),
  `tests/test_kl164_privacy_multipage.py` (novo), `tests/test_kl134_tools.py`, `web/src/lib/tools.test.js`.
