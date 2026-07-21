# Fix urgente — validação de domínio antes do scan

**Origem:** varredura de segurança (2026-07-21) · **Status:** Implementado (aguardando deploy verde)

---

## 1. Problema

O scanner aceitava **qualquer string** como input e gerava score. `/scan?url=<script>alert(1)</script>`
retornava score 73 e **exibia o payload no corpo da página** (via o `domainOf` do ScanFlow, que caía
em `return url`). Não era XSS explorável (o React escapa), mas comprometia a credibilidade de um site
de segurança e gerava scans de domínios inexistentes.

## 2. Correção — barreira no backend (real) + UX no frontend

### Backend (barreira real)
- **`_valid_scan_domain(raw)`** (`api/main.py`, puro): extrai o hostname (tira protocolo/path/query),
  remove `www.`, e valida por **regex de domínio** (`_SCAN_DOMAIN_RE`: labels `[a-z0-9-]` sem hífen no
  começo/fim, TLD alfabético `[a-z]{2,63}`, total ≤253, ASCII). Devolve o domínio limpo ou `None`.
- **`/scan/result`** e **`/scan/summary`** validam **antes de escanear** → `None` retorna
  **`400 {"error":"invalid_domain","detail":"Informe um domínio válido (ex: exemplo.com.br)"}`**.

Rejeita: tags/aspas/espaços (`<script>…`, `exemplo."onerror".com`, `a b.com`), sem TLD (`naoexiste`,
`localhost`), IPs (`192.168.1.1` — TLD não-alfabético), TLD de 1 char (`x.c`), vazio. Aceita:
`exemplo.com.br`, `https://exemplo.com.br/path?x=1` → `exemplo.com.br`, `www.hotel.com.br` →
`hotel.com.br`, subdomínios.

### Frontend (UX + defesa em profundidade)
- **`ScanFlow.jsx`**: usa `safeScanDomain(url)` (já existia em `lib/scanTitle.js`) no lugar do
  `domainOf` vulnerável. Se o input não é domínio válido (`safeScanDomain` retorna `''`), **não
  escaneia** — mostra "Informe um domínio válido (ex: exemplo.com.br)". `?url=` vazio → "Digite um
  domínio". Também trata o **400** do backend (caso escape da validação client-side).
- **`ScanResultDetail.jsx`**: o `domain` exibido (card de score, CTA "Monitore {domínio}", breadcrumb,
  share) passa por `safeScanDomain` → **nunca** reflete input cru; input estranho vira `''` (cai nos
  fallbacks "este site").

## 3. Validação

| `?url=` | Resultado |
|---|---|
| `<script>alert(1)</script>` | 400 invalid_domain (sem score, sem reflexo) |
| `naoexiste` | 400 invalid_domain (sem TLD) |
| `exemplo.com.br` | scan normal |
| `https://exemplo.com.br/path` | scan normal (extrai domínio) |
| `` (vazio) | "Digite um domínio" |

## 4. Testes

- **`tests/test_scan_domain_validation.py`** — **22 testes**: `_valid_scan_domain` (5 válidos + 10
  inválidos) + endpoints (`/scan/result` e `/scan/summary` → 400 para tags/sem-TLD/espaço/vazio).
- `safeScanDomain` (frontend) já coberto por `scanTitle.test.js` (96 `node --test`).
- Suíte: **1469 backend passed** · **96 node --test** · Astro build OK.

## 5. Arquivos

**Novos:** `tests/test_scan_domain_validation.py`.

**Alterados:** `api/main.py` (`_valid_scan_domain` + `_SCAN_DOMAIN_RE` + validação em `/scan/result`
e `/scan/summary`), `web/src/components/scan/ScanFlow.jsx` (valida + não escaneia inválido + trata
400), `web/src/components/scan/ScanResultDetail.jsx` (sanitiza o domínio exibido), `docs/SECURITY.md`.

## 6. Pós-deploy (verificação em produção)

```
curl -s "https://klarim.net/api/scan/result?url=<script>alert(1)</script>"  → 400 invalid_domain
curl -s "https://klarim.net/api/scan/result?url=naoexiste"                   → 400 invalid_domain
# /scan?url=<script>... na UI → "Informe um domínio válido", sem score
```
