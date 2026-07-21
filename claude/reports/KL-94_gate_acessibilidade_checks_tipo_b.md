# KL-94 — Gate de acessibilidade no scanner + auditoria de checks Tipo B

**Card:** KL-94 (High) · **Status:** Implementado (aguardando deploy verde + flush `scan:*`)
**Data:** 2026-07-21

---

## 1. Problema

O scanner gerava score para sites inacessíveis e retornava PASS em checks que não conseguiram
verificar o conteúdo. Um domínio offline recebia score ~73 baseado em PASS falsos (os checks
"Tipo B" — que verificam a AUSÊNCIA de algo ruim — davam PASS quando o conteúdo nem carregou).

---

## 2. Parte 1 — Gate de acessibilidade no runner

`scanner/runner.py::_accessibility_gate` roda **antes** dos 48 checks:

1. **DNS** (`dns_util.resolve_host_status`, novo): resolve A/AAAA? NXDOMAIN → `domain_not_found`;
   timeout/sem-nameserver → `dns_error` (transitório).
2. **HTTP**: `fetch(target, timeout=10)`. **Qualquer** resposta (200/301/403/503) = acessível →
   segue (SSL inválido não aborta — `verify=False`, o check_ssl marca FAIL). Falha de conexão →
   `unreachable`.

`ScanReport` ganhou `status` (`ok|domain_not_found|dns_error|unreachable`) + `error_detail`
(default `ok` — retrocompatível; `to_dict`/`from_dict` atualizados). Status != ok → `score=None`,
`results=[]`, `duration_s` = tempo até o abort.

**API** (`/scan/result`, `/scan/summary`): status != ok → **HTTP 200** com `{status, error_detail,
url, score:null, checks:[]}` (o domínio é válido, só não está acessível — o front diferencia pelo
`status`).

**Persistência:**
- `ok` → cache Redis + Postgres + GCS (como hoje).
- `unreachable` → **Postgres** (`scans.status='unreachable'`, `score=NULL`) para analytics de
  disponibilidade (KL-57). NÃO atualiza `update_scan_result` (não sobrescreve o último score).
- `domain_not_found`/`dns_error` → **não salva** (inexistente / erro transitório) e **não cacheia**.
- Nova coluna `scans.status VARCHAR(20) DEFAULT 'ok'` (migration idempotente); `save_scan` ganhou
  o param `status`.

**Frontend** (`ScanFlow.jsx`): status != ok → NÃO mostra score/checks; renderiza um card:
`domain_not_found` → 🔍 "Domínio não encontrado" + "Tentar outro domínio"; `unreachable`/`dns_error`
→ ⚠️ "Site inacessível" + "Tentar novamente".

---

## 3. Parte 2 — Auditoria dos checks Tipo B

Novo helper `scanner/checks/base.py::content_guard(resp, name, severity)` → devolve um
**INCONCLUSO** se a resposta não é confiável (5xx **ou** corpo < 100 chars), senão `None`.

**10 checks que analisam o HTML da página** (24 mixed-content, 25 form-security, 30 vulnerable-
components, 36 cache-control-forms, 45 html-comments, 46 debug-mode, 47 open-redirect, 48 password-
fields, 12 metatags, risky-sources): `guard = content_guard(resp, NAME, Sev); if guard: return
guard` logo após o `except` do fetch (que já retornava INCONCLUSO). Antes, um 5xx/corpo-vazio caía
no "não encontrei problema → PASS" (falso).

**4 checks multi-sonda** (20 info-disclosure, dirlist, sensitive, sourcemaps): sondam vários paths
num loop (`except: continue` por sonda é correto — arquivo ausente é PASS legítimo). Adicionado um
contador `responded`: se **nenhuma** sonda obteve resposta HTTP (site inacessível), retorna
**INCONCLUSO** em vez do PASS final. `check_20` de fato retornava **PASS** quando todas as sondas
falhavam — corrigido.

**Checks Tipo A** (presença de proteção: SPF/DKIM/DMARC/HSTS/CSP/XFO/DNSSEC/CAA…) — ausência = FAIL
é correto → **não mudaram**.

Efeito no score: mais INCONCLUSO (neutro) em vez de PASS falso → denominador menor, score mais
preciso. **⚠️ Flush `scan:*` obrigatório** no deploy (scans cacheados com scores antigos).

---

## 4. Testes

- **`tests/test_kl94_gate.py`** — 11 testes: `content_guard` (5xx/vazio/ok/404-com-conteúdo), check
  Tipo B com 5xx e com corpo vazio → INCONCLUSO, gate (nxdomain/dns_error/unreachable/ok), roundtrip
  do status, e `/scan/result` devolvendo `status=unreachable`.
- O helper `content_guard` (min_len 100) exigiu **padding inerte** em fixtures curtos de 4 arquivos
  de teste de check (o HTML de teste era < 100 chars — não representava página real; o pad não tem
  markers, a detecção testada é preservada).
- Bypass do gate (sem rede) nos 2 testes que chamam o `run_scan` real (`test_runner_concurrency`,
  `test_classifications`).
- Suíte: **1480 backend passed** · **96 node --test** · Astro build OK.

---

## 5. Arquivos

**Novos:** `tests/test_kl94_gate.py`.

**Alterados:** `scanner/runner.py` (gate + ScanReport.status), `scanner/checks/dns_util.py`
(`resolve_host_status`), `scanner/checks/base.py` (`content_guard`), 14 `scanner/checks/check_*.py`
(guards Tipo B), `scanner/main.py` (worker: salva unreachable, não cacheia non-ok), `discovery/
ingest.py` (salva unreachable), `discovery/store.py` (`scans.status` + `save_scan`), `api/main.py`
(`/scan/result` e `/scan/summary` devolvem status), `web/src/components/scan/ScanFlow.jsx`
(InaccessibleCard), 4 test-files (pad/bypass), `CLAUDE.md`, `docs/ARCHITECTURE.md`.

---

## 5b. Complemento — tratamento de gate failures no scan worker

O scan worker automático agora trata o `ScanReport.status` do gate (antes crasharia/salvaria
lixo). Dispatch em `scanner/main.py::_persist_scan_report` (extraído do loop, testável):

- `ok` → salva score + **zera** `gate_fail_count` (site voltou).
- `unreachable` → grava `scans.status='unreachable'` (score NULL, analytics) + **conta falha**.
- `domain_not_found` → **conta falha** (não salva scan/GCS).
- `dns_error` → transitório → **no-op** (não salva, não conta; re-tentativa natural no próximo
  ciclo de discovery/rescan — não re-enfileira aqui para não criar loop apertado num DNS instável).

**Retry com backoff** (colunas novas `targets.gate_fail_count`/`gate_next_retry`,
`store.record_gate_failure`): 1ª falha +7d, 2ª +30d, 3ª **descarta** — MAS só se o alvo NUNCA teve
score (`last_scan_score IS NULL`, ex.: cert CT de domínio sem site). Um site que **já teve score
real é preservado** (nunca descartado; `last_scan_score` intacto — a `update_scan_result` só roda
no `ok`; segue re-testando a cada 30d). O worker **pula** o alvo enquanto `gate_next_retry` está no
futuro (`gate_retry_pending`). SQL validado contra Postgres 16 real.

**Alert worker** exclui inacessíveis (`_ALERT_ELIGIBLE_WHERE` += `gate_fail_count=0 AND
last_scan_score IS NOT NULL`) — a vigília (KL-44 P2) cobre uptime separadamente.

**Impacto:** ~30-50% dos ~3.000 alvos/dia falham o gate (certs CT sem site) → ~1.500 scans/dia a
menos, fila drena mais rápido, scores restantes mais confiáveis.

**Testes do complemento:** `tests/test_kl94_worker_gate.py` (8) — dispatch por status (ok reseta +
salva; domain_not_found conta sem salvar; unreachable salva analytics + conta sem tocar
last_scan_score; dns_error no-op; descarte na 3ª falha), contrato dos métodos de store e a exclusão
do alerta. Total KL-94: **1487 backend passed**.

## 6. Pós-deploy (VM) — obrigatório

```bash
# Flush dos scans cacheados (scores antigos com PASS falso):
sudo docker exec klarim-redis-1 redis-cli --scan --pattern 'scan:*' | xargs -r -L 100 sudo docker exec -i klarim-redis-1 redis-cli DEL

# Validar:
curl -s 'https://klarim.net/api/scan/result?url=dominio-que-nao-existe-xyz123.com.br'  → status=domain_not_found
curl -s 'https://klarim.net/api/scan/result?url=igoove.com'                            → status=ok, score
```
