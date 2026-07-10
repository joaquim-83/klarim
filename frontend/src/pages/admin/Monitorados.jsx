import { useCallback, useEffect, useState } from 'react'
import { admin } from '../../lib/adminApi'
import { Card, StatCard, Loading, ErrorBox, Badge, Button, relativeTime } from '../../components/admin/ui'

const STATUS_COLOR = {
  pending: '#F0C000', active: '#00D26A', suspended: '#F85149', removed: '#8B949E',
}
const STATUS_LABEL = {
  pending: 'Pendente', active: 'Ativo', suspended: 'Suspenso', removed: 'Removido',
}
const FILTERS = ['', 'pending', 'active', 'suspended', 'removed']

export default function MonitoradosAdmin() {
  const [status, setStatus] = useState('')
  const [data, setData] = useState(null)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(async () => {
    setError('')
    try {
      const [list, st] = await Promise.all([
        admin.monitoredList(status || undefined),
        admin.monitoredStats(),
      ])
      setData(list.sites || [])
      setStats(st)
    } catch (e) {
      setError(e.message || 'Erro ao carregar.')
    }
  }, [status])

  useEffect(() => { load() }, [load])

  async function act(id, newStatus) {
    setBusyId(id)
    try {
      await admin.monitoredSetStatus(id, newStatus)
      await load()
    } catch (e) {
      setError(e.message || 'Falha na ação.')
    } finally {
      setBusyId(null)
    }
  }

  if (error) return <ErrorBox message={error} />
  if (data === null) return <Loading />

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Sites Monitorados</h1>

      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Total" value={stats.total ?? 0} />
          <StatCard label="Ativos" value={stats.active ?? 0} accent="#00D26A" />
          <StatCard label="Suspensos" value={stats.suspended ?? 0} accent="#F85149" />
          <StatCard label="Pendentes" value={stats.pending ?? 0} accent="#F0C000" />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f || 'all'}
            onClick={() => setStatus(f)}
            className={`rounded-lg border px-3 py-1.5 text-sm ${status === f ? 'border-klarim-alert text-klarim-text' : 'border-klarim-border text-klarim-muted'}`}
          >
            {f ? STATUS_LABEL[f] : 'Todos'}
          </button>
        ))}
      </div>

      <Card title={`Sites (${data.length})`}>
        {data.length === 0 ? (
          <p className="text-sm text-klarim-muted">Nenhum site.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-4">Domínio</th>
                  <th className="py-2 pr-4">Empresa</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Score</th>
                  <th className="py-2 pr-4">Último check</th>
                  <th className="py-2">Ações</th>
                </tr>
              </thead>
              <tbody>
                {data.map((s) => (
                  <tr key={s.id} className="border-t border-klarim-border align-top">
                    <td className="py-2 pr-4 font-mono text-xs">{s.domain}</td>
                    <td className="py-2 pr-4">{s.display_name || '—'}</td>
                    <td className="py-2 pr-4">
                      <Badge color={STATUS_COLOR[s.status]}>{STATUS_LABEL[s.status] || s.status}</Badge>
                      {s.status === 'suspended' && s.suspended_reason && (
                        <div className="mt-1 text-xs text-klarim-muted">{s.suspended_reason}</div>
                      )}
                    </td>
                    <td className="py-2 pr-4">{s.last_check_score ?? '—'}</td>
                    <td className="py-2 pr-4 text-klarim-muted">{relativeTime(s.last_check_at) || '—'}</td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1">
                        {s.status === 'pending' && (
                          <Button onClick={() => act(s.id, 'active')} disabled={busyId === s.id}>Aprovar</Button>
                        )}
                        {s.status === 'active' && (
                          <Button onClick={() => act(s.id, 'suspended')} disabled={busyId === s.id}>Suspender</Button>
                        )}
                        {s.status === 'suspended' && (
                          <Button onClick={() => act(s.id, 'active')} disabled={busyId === s.id}>Reativar</Button>
                        )}
                        {s.status !== 'removed' && (
                          <Button variant="danger" onClick={() => act(s.id, 'removed')} disabled={busyId === s.id}>Remover</Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
