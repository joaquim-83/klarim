# KL-38 — Content analysis passivo (comentários, debug, open redirect, senha)

**Card:** KL-38 · **Prioridade:** Média · **Último card da Fase 0** (scanner profissional).
**Objetivo:** analisar o **HTML servido** em busca de padrões de risco. 4 checks novos
(45–48), tier pago. O scanner passa de 44 para **48 checks**.

---

## Os 4 checks

| # | Check | Sev. | OWASP / CWE | Detecta |
|---|-------|------|-------------|---------|
| 45 | Comentários HTML | Média/Alta | A01 / CWE-615 | `<!-- ... -->` com credencial/chave/token (ALTA), IP/servidor/banco/TODO-segurança/path (MÉDIA) |
| 46 | Debug mode | Alta/Média | A05 / CWE-489 | stack traces / erros de framework no HTML + página de erro; headers de debug (MÉDIA) |
| 47 | Open redirect | Baixa/Média | A01 / CWE-601 | presença de params de redirect (`?redirect=`, `?next=`…); >5 → MÉDIA |
| 48 | Password fields | Baixa | A04 / CWE-522 (LGPD 46+11) | `<input type=password>` sem `autocomplete=off/new-password` ou sem `name`/`id` |

**Nova categoria OWASP:** `_A04 = "A04:2025 Insecure Design"` em `classifications.py`.

### Detalhes de implementação

- **check_45:** uma **whitelist** (copyright, meta, tracking, markers de template,
  condicionais de IE, vazios) é aplicada **antes** dos padrões sensíveis — evita falso
  positivo em `<!-- © 2026 -->`. Severidade dinâmica (ALTA se há credencial/path; senão MÉDIA).
- **check_46:** faz strip de `<script>/<style>` antes de casar os padrões (não dispara com
  "Traceback" dentro de um JS). Além da homepage, faz **um GET numa URL inexistente**
  (`/klarim-nonexistent-debug-check-404`) para pegar a página de erro — passivo (qualquer
  navegador faria o mesmo), best-effort.
- **check_47:** **detecção passiva** — só a presença do padrão, não testa se o redirect é
  explorável (depende da validação no servidor) → severidade BAIXA (ou MÉDIA se >5).
- **check_48:** não é sobre ter login (muitos sites não têm) — é sobre proteger o campo
  **quando existe**. Sem campo de senha = PASS (não aplicável).

## Relatórios (identidade dual)

`RISK_MESSAGES` (executivo, com analogias: "cofre que mostra a combinação na porta quando
erram a senha"), `ACCESSIBLE` e `TECHNICAL` (com o fix: remover comentários, DEBUG=False,
whitelist de redirect, `autocomplete=new-password`) para os 4. Categorias de risco
atualizadas (INVASAO: 45/46; GOLPES: 47; VAZAMENTO: 48).

## Testes (`tests/test_kl38_content.py`, 18)

Comentários (credencial→ALTA, TODO→MÉDIA, IP→MÉDIA, copyright→PASS/safe, sem→PASS), debug
(stack trace/PHP/Whoops→FAIL, limpo→PASS, headers→MÉDIA, ignora conteúdo de `<script>`),
redirect (1→BAIXA, >5→MÉDIA, nenhum→PASS), senha (sem autocomplete→FAIL, com→PASS, sem
campo→PASS), classificações dos 4. Mock de `fetch` por check. Contagens ajustadas
**44→48 / pagos 29→33** em `test_kl27_funnel.py`, `test_classifications.py`,
`test_checks_16_29.py`.

## Deploy

**Flush `scan:*` no Redis após deploy** (novos checks mudam scores). Docs: `claude.md` §36,
`README.md`. Sem novas dependências.

## Arquivos

**Novos:** `check_45_html_comments.py`, `check_46_debug_mode.py`, `check_47_open_redirect.py`,
`check_48_password_fields.py`, `tests/test_kl38_content.py`, este relatório. **Alterados:**
`scanner/checks/classifications.py` (A04 + 4), `reporter/generator.py`,
`reporter/risk_messages.py`, `tests/test_checks_16_29.py`, `tests/test_kl27_funnel.py`,
`tests/test_classifications.py`, `claude.md`, `README.md`.
