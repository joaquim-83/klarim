import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, StatCard, Loading, ErrorBox, SemaphoreDot, Pagination, formatDate } from '../../components/admin/ui'

const PAGE_SIZE = 25

export default function Alertas() {
  const [page, setPage] = useState(0)
  const stats = useAsync(() => admin.alertsStats(), [])
  const { data, loading, error } = useAsync(
    () => admin.alerts({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [page],
  )
  const rows = data?.alerts || []

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Alertas</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Hoje" value={stats.data?.today ?? 0} accent="#FF6B35" />
        <StatCard label="Semana" value={stats.data?.week ?? 0} />
        <StatCard label="Mês" value={stats.data?.month ?? 0} />
        <StatCard label="Total" value={stats.data?.total ?? 0} />
      </div>

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">E-mail</th>
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">email_id</th>
                  <th className="py-2">Enviado</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="border-t border-klarim-border">
                    <td className="py-2 pr-3 text-xs">{a.contact_email}</td>
                    <td className="py-2 pr-3 font-mono text-xs text-klarim-muted">{a.url || `alvo #${a.target_id}`}</td>
                    <td className="py-2 pr-3"><SemaphoreDot semaphore={a.semaphore} score={a.score} /></td>
                    <td className="py-2 pr-3 text-xs">{a.status}</td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-klarim-muted">{a.email_id || '—'}</td>
                    <td className="py-2 text-xs text-klarim-muted">{formatDate(a.sent_at)}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-klarim-muted">Nenhum alerta.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
      </Card>
    </div>
  )
}
