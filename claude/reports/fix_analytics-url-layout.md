# Fix — Layout e URLs da página Analytics (`/painel/analytics`)

**Tipo:** Fix de UI (sem card Jira)
**Data:** 2026-07-08

## Problema

A tela **Analytics** do painel admin quebrava o layout. A seção "Páginas mais
visitadas" exibia URLs completas com UTM params e o `url=` encodado, por exemplo:

```
/result?url=https%3A%2F%2Ficlinic.com.br&utm_source=klarim&utm_medium=email&utm_campaign=alerta&utm_content=target_390
```

Isso empurrava a tabela para fora do container. Além do problema visual, a
agregação era **incorreta**: como o `page_url` inclui os UTM (que variam por lead
e por campanha — `utm_content=target_390`, `target_391`…), a mesma página se
fragmentava em dezenas de linhas com contagem 1, tornando o "top páginas" inútil.

## Correções

### Backend — agregação correta (`discovery/store.py`)

- Novo helper `_clean_page_key(raw)`: remove os UTM e, quando há `?url=<alvo>`,
  troca a query pela forma legível `"<path> → <hostname>"`
  (ex.: `/result → iclinic.com.br`). Sem query útil, retorna só o path.
- `analytics_pages()` passou a agrupar por page_url cru no banco (poucas linhas,
  reunindo as sessões distintas com `array_agg(DISTINCT session_id)`) e **funde**
  em Python as páginas que limpam para a mesma chave — somando views e unindo os
  sets de sessão (contagem distinta exata). Ordena por views e aplica o `limit`.
  Resultado: `/result?url=A&utm...` e `/result?url=A` contam como **a mesma
  página**.

### Frontend — limpeza + contenção de layout (`frontend/src/pages/admin/Analytics.jsx`)

- Nova função `cleanPageUrl(raw)` que espelha `_clean_page_key` (defensiva contra
  URLs cruas que ainda cheguem). É **idempotente**: se o valor não tem `?` (já
  veio limpo do backend), retorna como está.
- **Páginas mais visitadas:** a célula "Página" agora usa
  `<div className="max-w-[280px] truncate">` com `title={p.page_url}` (tooltip com
  o valor completo no hover) e exibe `cleanPageUrl(...)`.
- **Timeline de eventos:** exibe `cleanPageUrl(e.page_url)` (quando não há
  `target_url`), com `min-w-0 flex-1 truncate` e `title` para tooltip. Formato:
  `page_view · /result → iclinic.com.br · 08/07 17:48`.
- **Carrinho abandonado:** a coluna "Site" ganhou `max-w-[220px] truncate` +
  tooltip, evitando overflow com URLs longas.
- **Responsividade:** as tabelas de campanhas e páginas foram embrulhadas em
  `overflow-x-auto` (a de carrinho abandonado já tinha), garantindo que nenhuma
  ultrapasse a largura do container.
- **Campanhas:** já mostrava apenas `utm_campaign` (nome da campanha num Badge),
  não a URL — nenhuma mudança necessária.

## Validação

- `python3 -c "import discovery.store"` → OK; `pytest` → **114 passed, 1 skipped**.
- `_clean_page_key` testado nos casos-chave:
  - `/result?url=https%3A%2F%2Ficlinic.com.br&utm_...` → `/result → iclinic.com.br`
  - `/result?url=https%3A%2F%2Ficlinic.com.br` → `/result → iclinic.com.br` (mesma chave)
  - `/recuperar` → `/recuperar`; `/` → `/`
- `npm run build` (frontend) → build OK, sem erros de JSX.

## Arquivos alterados

- `discovery/store.py` — import `urllib.parse`, helper `_clean_page_key`,
  reescrita de `analytics_pages`.
- `frontend/src/pages/admin/Analytics.jsx` — `cleanPageUrl`, truncamento +
  tooltips nas tabelas, wrappers `overflow-x-auto`.
