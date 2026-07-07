import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, StatCard, Loading, ErrorBox, Pagination, formatDate, EVOLUTION_META } from '../../components/admin/ui'

const PAGE_SIZE = 25

export default function Rescans() {
  const [evolution, setEvolution] = useState('')
  const [page, setPage] = useState(0)

  const stats = useAsync(() => admin.rescansStats(), [])
  const { data, loading, error } = useAsync(
    () => admin.rescans({ evolution, limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [evolution, page],
  )
  const rows = data?.rescans || []

  const byEv = stats.data?.by_evolution || {}
  const total = stats.data?.total || 0
  const pct = (n) => (total > 0 ? ` (${((n / total) * 100).toFixed(0)}%)` : '')

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Re-scans</h1>

      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Melhoraram 🟢" value={`${byEv.improved || 0}${pct(byEv.improved || 0)}`} accent="#00D26A" />
        <StatCard label="Pioraram 🔴" value={`${byEv.worsened || 0}${pct(byEv.worsened || 0)}`} accent="#F85149" />
        <StatCard label="Iguais ⚪" value={`${byEv.unchanged || 0}${pct(byEv.unchanged || 0)}`} />
      </div>

      <select value={evolution} onChange={(e) => { setEvolution(e.target.value); setPage(0) }}
        className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert">
        <option value="">Todas as evoluções</option>
        <option value="improved">Melhoraram</option>
        <option value="worsened">Pioraram</option>
        <option value="unchanged">Iguais</option>
      </select>

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Evolução</th>
                  <th className="py-2 pr-3">Semáforo</th>
                  <th className="py-2 pr-3">E-mail?</th>
                  <th className="py-2">Data</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const ev = EVOLUTION_META[r.evolution] || {}
                  return (
                    <tr key={r.id} className="border-t border-klarim-border">
                      <td className="py-2 pr-3 font-mono text-xs">{r.url || `alvo #${r.target_id}`}</td>
                      <td className="py-2 pr-3">{r.old_score} → <strong>{r.new_score}</strong></td>
                      <td className="py-2 pr-3" style={{ color: ev.color }}>{ev.icon} {ev.label}</td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{r.old_semaphore} → {r.new_semaphore}</td>
                      <td className="py-2 pr-3 text-xs">{r.email_id ? 'sim' : 'não'}</td>
                      <td className="py-2 text-xs text-klarim-muted">{formatDate(r.rescanned_at)}</td>
                    </tr>
                  )
                })}
                {rows.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-klarim-muted">Nenhum re-scan.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
      </Card>
    </div>
  )
}
