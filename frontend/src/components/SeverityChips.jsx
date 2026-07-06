// Chips coloridos com a contagem de falhas por severidade.
const ITEMS = [
  { key: 'critica', label: 'Críticos', color: '#F85149' },
  { key: 'alta', label: 'Altos', color: '#FF6B35' },
  { key: 'media', label: 'Médios', color: '#F0C000' },
  { key: 'baixa', label: 'Baixos', color: '#58A6FF' },
]

export default function SeverityChips({ counts = {} }) {
  return (
    <div className="flex flex-wrap justify-center gap-2 sm:gap-3">
      {ITEMS.map((it) => (
        <div
          key={it.key}
          className="flex items-baseline gap-1.5 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-2"
        >
          <span className="text-xl font-extrabold" style={{ color: it.color }}>
            {counts[it.key] ?? 0}
          </span>
          <span className="text-xs uppercase tracking-wide text-klarim-muted">{it.label}</span>
        </div>
      ))}
    </div>
  )
}
