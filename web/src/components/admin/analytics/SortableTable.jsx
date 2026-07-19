// KL-83 — tabela com headers clicáveis para sorting (Abas 3 e 4). `columns`:
// [{key, label, sortable?, align?}]. `renderRow(row, index)` renderiza cada <tr>. Acessível:
// <th scope="col"> + aria-sort no header ativo.
export default function SortableTable({ columns, rows, sort, order, onSort, renderRow, empty }) {
  if (!rows || rows.length === 0) {
    return <p className="text-sm text-klarim-muted">{empty || 'Sem dados no período.'}</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-klarim-border">
      <table className="w-full text-sm">
        <thead className="bg-klarim-surface text-xs text-klarim-muted">
          <tr>
            {columns.map((c) => {
              const active = sort === c.key
              const canSort = c.sortable !== false
              return (
                <th key={c.key} scope="col"
                  aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onClick={() => canSort && onSort(c.key)}
                  className={`px-3 py-2 font-medium ${c.align === 'right' ? 'text-right' : 'text-left'} ${canSort ? 'cursor-pointer select-none hover:text-klarim-text' : ''} ${active ? 'text-klarim-text' : ''}`}>
                  {c.label}{active ? (order === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>{rows.map(renderRow)}</tbody>
      </table>
    </div>
  )
}
