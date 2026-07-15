import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox } from './ui'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/Config.jsx (KL-51 fase 1). Read-only.
const ROWS = [
  { key: 'discovery_batch_size', label: 'Batch de descoberta', env: 'DISCOVERY_BATCH_SIZE', unit: 'domínios/ciclo' },
  { key: 'discovery_interval_minutes', label: 'Intervalo de descoberta', env: 'DISCOVERY_INTERVAL_MINUTES', unit: 'min' },
  { key: 'alert_interval_minutes', label: 'Intervalo de alertas', env: 'ALERT_INTERVAL_MINUTES', unit: 'min' },
  { key: 'alert_batch_size', label: 'Alertas por batch', env: 'ALERT_BATCH_SIZE', unit: 'e-mails' },
  { key: 'alert_batches_per_cycle', label: 'Batches por ciclo', env: 'ALERT_BATCHES_PER_CYCLE', unit: 'batches' },
  { key: 'alert_batch_pause', label: 'Pausa entre batches', env: 'ALERT_BATCH_PAUSE', unit: 's' },
  { key: 'alert_monthly_limit', label: 'Cota mensal de e-mail', env: 'ALERT_MONTHLY_LIMIT', unit: 'e-mails/mês' },
  { key: 'rescan_interval_hours', label: 'Intervalo de re-scan', env: 'RESCAN_INTERVAL_HOURS', unit: 'h' },
  { key: 'rescan_age_days', label: 'Idade para re-scan', env: 'RESCAN_AGE_DAYS', unit: 'dias' },
  { key: 'worker_max_scans_per_hour', label: 'Máx. scans/hora', env: 'WORKER_MAX_SCANS_PER_HOUR', unit: 'scans' },
]

export default function ConfigPage() {
  const { data, loading, error } = useAsync(() => admin.config(), [])

  return (
    <AdminShell active="config">
      <div className="space-y-4">
        <h1 className="text-xl font-bold">Configurações</h1>
        <p className="text-sm text-klarim-muted">
          Parâmetros operacionais em uso (somente leitura). A edição é feita no
          <code className="mx-1 rounded bg-klarim-border/50 px-1">.env</code> da VM seguida de redeploy.
          Edição ao vivo fica para uma versão futura.
        </p>

        <Card>
          {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-4">Parâmetro</th>
                    <th className="py-2 pr-4">Variável</th>
                    <th className="py-2">Valor atual</th>
                  </tr>
                </thead>
                <tbody>
                  {ROWS.map((r) => (
                    <tr key={r.key} className="border-t border-klarim-border">
                      <td className="py-2 pr-4">{r.label}</td>
                      <td className="py-2 pr-4"><code className="text-xs text-klarim-muted">{r.env}</code></td>
                      <td className="py-2 font-semibold">
                        {data?.[r.key]} <span className="text-xs font-normal text-klarim-muted">{r.unit}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </AdminShell>
  )
}
