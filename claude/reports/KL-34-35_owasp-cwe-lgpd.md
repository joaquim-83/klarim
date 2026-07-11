# KL-34 + KL-35 — Classificação OWASP Top 10 2025 + CWE + LGPD

**Cards:** KL-34, KL-35 · **Prioridade:** Alta · **Esforço:** baixo (metadata).
**Objetivo:** dar peso institucional ao relatório — cada finding passa a citar OWASP
Top 10 2025, CWE e LGPD. Preserva a **identidade dual**: o executivo continua informal
(não cita frameworks por falha); o técnico e a API mostram tudo.

---

## Decisão de arquitetura (por que não editei os 29 checks)

O card sugeria criar constantes `OWASP/CWE/LGPD` no topo de cada `check_*.py` e passá-las
em cada `return CheckResult(...)`. Na prática, os 29 checks têm **2–6 return sites cada
(~100 no total)** — editar todos seria mecânico mas frágil e deixaria de fora o resultado
de fallback (quando um check levanta exceção).

Como o **`runner` já carimba `result.check_id`** depois de rodar cada check, coloquei o
mapeamento numa **fonte da verdade única** (`scanner/checks/classifications.py`) e o runner
**carimba `owasp`/`cwe`/`lgpd`** ali mesmo, pelo `check_id`. Mesmo resultado que o card
pede (cada `CheckResult` carrega a classificação, serializa na API), com uma tabela em vez
de ~100 edições, e cobrindo até o check que falha. A tabela é **idêntica** à do card.

## O que mudou

- **`scanner/checks/classifications.py` (novo).** `CLASSIFICATIONS`: 29 `check_id` →
  `(owasp, cwe, lgpd)`. `classify()`, `compliance_summary(results)` (conta as **FALHAS**
  por categoria OWASP e por artigo LGPD), `owasp_parts`/`lgpd_articles`/`LGPD_LABELS` e o
  `COMPLIANCE_DISCLAIMER` obrigatório. LGPD pode ser múltiplo (`"Art. 46, Art. 48"`);
  checks 12/20/26 têm LGPD `None`.
- **`CheckResult` (`base.py`).** Novos campos `owasp`/`cwe`/`lgpd` **opcionais** (`None`
  default → retrocompatível; `from_dict` de scan antigo não quebra). `from_dict` os lê.
- **`runner.py`.** Carimba a classificação onde já setava o `check_id`. Serializa sozinho
  no `to_dict` → flui para o cache/banco (`checks_json`) e para `GET /scans/{id}` **sem**
  mudança de API.
- **Relatório técnico** (`reporter/generator.py` + `templates/technical.html`): linha
  **Classificação** (OWASP/CWE/LGPD) por falha + **Sumário de conformidade** no fim
  (contagem por OWASP e por artigo LGPD + disclaimer). Usa o carimbo do `CheckResult` com
  **fallback** a `classify(check_id)` — robusto para PDFs de scans antigos.
- **Relatório executivo** (`templates/executive.html`): **sem** OWASP/CWE/LGPD por falha;
  só uma **nota institucional genérica** ("baseado em padrões internacionais de segurança
  (OWASP) e considera a LGPD").
- **Resultado web** (`api/main.py::_summary_payload`): no modo **completo** (`full=True`)
  as entradas de FALHA trazem `owasp`/`cwe`/`lgpd`; o **gratuito** não (mantém o gate do
  funil KL-27). Frontend `Result.jsx` renderiza a classificação nos FAILs expandidos.

## Testes (todos verdes)

- `tests/test_classifications.py` (novo): cobertura do mapa (**29/29**, sem órfãos),
  mapeamentos específicos, campos opcionais + round-trip `to_dict`/`from_dict`,
  retrocompatibilidade, `compliance_summary` contando só FALHAS, e **integração do runner**
  (carimbo pelo `check_id`, offline via monkeypatch).
- `tests/test_reporter.py` (+2): técnico contém a classificação por falha + sumário +
  disclaimer; **executivo não contém** `CWE-`, rótulo OWASP por falha nem o sumário (só a
  nota genérica).
- `tests/test_kl27_funnel.py` (+2): payload completo inclui `owasp/cwe/lgpd` nas falhas;
  payload gratuito **omite**.

## Deploy

Metadata não altera o score. Ainda assim, **flush `scan:*` no Redis** após o deploy para
os scans cacheados reganharem os campos. Docs atualizadas: `claude.md` (§31) e `README.md`.

## Arquivos

**Novos:** `scanner/checks/classifications.py`, `tests/test_classifications.py`, este
relatório. **Alterados:** `scanner/checks/base.py`, `scanner/runner.py`,
`reporter/generator.py`, `reporter/templates/technical.html`,
`reporter/templates/executive.html`, `api/main.py`, `frontend/src/pages/Result.jsx`,
`tests/test_reporter.py`, `tests/test_kl27_funnel.py`, `claude.md`, `README.md`.
