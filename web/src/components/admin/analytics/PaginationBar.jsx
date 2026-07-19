// KL-83 — barra de paginação reutilizável (Abas 2, 3, 4). Opcional: seletor de tamanho.
export default function PaginationBar({ page, pages, onPage, total, limit, onLimit, sizes = [25, 50, 100] }) {
  if ((pages || 1) <= 1) return null
  return (
    <div className="flex flex-wrap items-center justify-center gap-3 text-sm">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}
        className="rounded border border-klarim-border px-3 py-1 disabled:opacity-40 hover:bg-klarim-bg">← Anterior</button>
      <span className="text-klarim-muted">
        Página {page} de {pages}{total != null ? ` · ${total.toLocaleString('pt-BR')} itens` : ''}
      </span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}
        className="rounded border border-klarim-border px-3 py-1 disabled:opacity-40 hover:bg-klarim-bg">Próxima →</button>
      {onLimit && (
        <select value={limit} onChange={(e) => onLimit(Number(e.target.value))}
          aria-label="Itens por página"
          className="rounded border border-klarim-border bg-klarim-bg px-2 py-1">
          {sizes.map((n) => <option key={n} value={n}>{n}/pág</option>)}
        </select>
      )}
    </div>
  )
}
