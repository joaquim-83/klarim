# KL-33 — Fingerprint de tecnologia + CVE matching (Retire.js)

**Card:** KL-33 · **Prioridade:** CRÍTICA — o achado mais impactante do scanner.
**Objetivo:** detectar **versões** de bibliotecas JS e CMS (passivo) e cruzar com CVEs
conhecidos. "jQuery 2.1.4 com 12 vulnerabilidades conhecidas" é concreto e acionável,
diferente de "falta um header". Novo **check 30**, com severidade dinâmica pelo CVSS.

---

## Parte 1 — Detecção de versão (passiva)

`scanner/checks/check_30_vulnerable_components.py::detect_versions(html, headers,
script_urls)` — função **pura e testável**:
- **JS libraries** via `<script src>` e conteúdo **inline** (primeiros 50KB) —
  `VERSION_PATTERNS` (jQuery, Bootstrap, Angular, AngularJS, React, Vue, Lodash, Moment,
  Handlebars, Underscore), case-insensitive.
- **CMS** via `<meta generator>` e assets versionados `?ver=` — `CMS_VERSION_PATTERNS`
  (WordPress — o de maior impacto, 18,6% dos alvos —, Joomla, Drupal).
- **PHP** e **servidor** via headers (`X-Powered-By`, `Server`).

Tudo vem do que o site **já entrega** — nenhuma versão é sondada ativamente (100% passivo).

## Parte 2 — Base de CVEs (`scanner/cve_db.py`)

`CVEDatabase` (singleton `get_cve_db()`):
- Baixa a base **Retire.js** (`https://raw.githubusercontent.com/RetireJS/retire.js/
  master/repository/jsrepository.json`, ~500KB) em **runtime** (nunca em build).
- Cache em `KLARIM_CVE_CACHE` (padrão `/tmp/klarim_retirejs_cache.json`), **TTL 24h**,
  escrita atômica. **Fail-open**: download/parse falho → tenta cache velho → base vazia →
  o check vira INCONCLUSO. **Nunca** derruba o scan.
- `lookup_js(lib, version)` casa a versão contra `below`/`atOrAbove` das
  `vulnerabilities` do Retire.js (via `packaging.version`); `recommended_upgrade` (menor
  versão segura = maior `below` casado); `covers`; `severity_from_cves`/`max_cvss`.
- **NVD/NIST** (`lookup_nvd`) para CMS/PHP/servidor fica atrás de `NVD_ENABLED`
  (**default `false`**) — pronto mas inerte até ter rede/chave.

> Nota: o Retire.js fornece **severidade textual** (low/medium/high/critical), não CVSS
> numérico. Não inventamos CVSS: o display usa a label; `severity_from_cves` usa CVSS real
> quando houver (NVD) e cai para a maior label textual do Retire.js.

## Parte 3 — check_30 (score + severidade dinâmica)

FAIL se algum componente tem CVE (severidade pelo maior CVSS/label); PASS se detectou
componente(s) cobertos pela base e nenhum é vulnerável; INCONCLUSO se nada foi detectado
ou só há componentes fora da base (ex.: WordPress com NVD off). `details.components`
carrega `{library, version, source, cves:[{id,severity,cvss,summary}], recommendation}` +
`total_cves` + `max_cvss`. É **check pago** (ORDER 30 > 15) e entra no score — sites com
jQuery/WordPress antigos caem de score (esperado e desejado).

## Parte 4 — Classificação e relatórios

- **`classifications.py` (KL-34/35):** `check_30` → **A06:2025 Vulnerable and Outdated
  Components** / **CWE-1104** / **Art. 46** (carimbado pelo runner; nova categoria A06).
- **Executivo (informal):** `RISK_MESSAGES` — "é como dirigir um carro que teve vários
  recalls e você nunca levou na oficina". `ACCESSIBLE` idem.
- **Técnico:** `TECHNICAL` com impacto + correção (atualizar libs/CMS) e, por finding, os
  CVE-IDs (via `details.components`) + a linha OWASP/CWE/LGPD (KL-34/35).

## Testes (`tests/test_kl33_components.py`, 20 — todos verdes)

Detecção (jQuery src, WordPress meta + `?ver=`, Bootstrap, PHP/servidor, banner inline),
lookup Retire.js (vulnerável/seguro, `recommended_upgrade`, `covers`), severidade dinâmica
(CVSS→severity, label fallback, `max_cvss`), INCONCLUSO (sem versão / só CMS com NVD off),
cache (expirado→re-download, download falho→cache velho, sem cache+sem rede→fail-open),
FAIL com CVE details + recomendação, PASS com componente atualizado, classificação A06.
Ajustados os testes de contagem (29→30 checks / 14→15 pagos) em `test_kl27_funnel.py` e
`test_classifications.py`. Copy dinâmica: mensagem do resumo usa a contagem real; e-mail de
monitoramento ficou genérico ("todas as verificações").

## Dependências / deploy

- `packaging` adicionado ao `requirements.txt` (comparação semver).
- Base Retire.js baixada em runtime (não em build); cache default em `/tmp` (writable, sem
  depender de mount RW — monte um volume nesse caminho para persistir entre restarts).
- **Flush `scan:*` no Redis após deploy** (novo check altera scores).

## Arquivos

**Novos:** `scanner/cve_db.py`, `scanner/checks/check_30_vulnerable_components.py`,
`tests/test_kl33_components.py`, este relatório. **Alterados:** `scanner/checks/
classifications.py` (A06 + check_30), `reporter/generator.py` (ACCESSIBLE/TECHNICAL),
`reporter/risk_messages.py`, `api/main.py` (copy dinâmica), `notifier/templates/
monitor_offer.html`, `requirements.txt`, `tests/test_kl27_funnel.py`,
`tests/test_classifications.py`, `claude.md`, `README.md`.
