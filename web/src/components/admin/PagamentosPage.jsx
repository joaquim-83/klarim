import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, StatCard, Loading, ErrorBox, Badge, Pagination, formatDate } from './ui'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/Pagamentos.jsx (KL-51 fase 1). O único <Link> vira
// <a href> (nav dura → Nginx → AlvoDetalhe ainda no Vite até a fase 2).
const PAGE_SIZE = 25
const STATUS_COLOR = { PAID: '#00D26A', PENDING: '#F0C000', EXPIRED: '#8B949E', CANCELLED: '#F85149', REFUNDED: '#A855F7' }

export default function PagamentosPage() {
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(0)

  const meta = useAsync(() => Promise.all([admin.paymentsStats(), admin.alertsStats(), admin.subscriptionPaymentStats()])
    .then(([pay, alerts, subs]) => ({ pay, alerts, subs })), [])
  const { data, loading, error } = useAsync(
    () => admin.payments({ status, limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [status, page],
  )
  const rows = data?.payments || []

  const paid = meta.data?.pay?.paid_count ?? 0
  const alertsTotal = meta.data?.alerts?.total ?? 0
  const conversion = alertsTotal > 0 ? ((paid / alertsTotal) * 100).toFixed(1) + '%' : '—'

  return (
    <AdminShell active="pagamentos">
      <div className="space-y-4">
        <h1 className="text-xl font-bold">Pagamentos</h1>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Receita (PAID)" value={meta.data?.pay?.revenue_display ?? 'R$ 0,00'} accent="#00D26A" />
          <StatCard label="Pagos" value={paid} accent="#00D26A" />
          <StatCard label="Total cobranças" value={meta.data?.pay?.total ?? 0} />
          <StatCard label="Conversão (alerta→pago)" value={conversion} accent="#FF6B35" />
        </div>

        {/* KL-44 P6 — receita de ASSINATURAS (PIX) */}
        <Card>
          <p className="mb-3 text-sm font-semibold">Assinaturas (KL-44 P6)</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Receita assinaturas" value={`R$ ${(((meta.data?.subs?.total_paid_amount) || 0) / 100).toFixed(2).replace('.', ',')}`} accent="#00D26A" />
            <StatCard label="Assinaturas pagas" value={meta.data?.subs?.total_paid_count ?? 0} accent="#00D26A" />
            <StatCard label="Pro pagos" value={meta.data?.subs?.by_plan?.pro?.count ?? 0} />
            <StatCard label="Agency pagos" value={meta.data?.subs?.by_plan?.agency?.count ?? 0} accent="#FF6B35" />
          </div>
          {(meta.data?.subs?.recent || []).length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-klarim-muted">
              {meta.data.subs.recent.map((r, i) => (
                <li key={i} className="flex flex-wrap gap-x-4">
                  <span>{formatDate(r.paid_at)}</span>
                  <span className="text-klarim-text">{r.plan} · R$ {((r.amount || 0) / 100).toFixed(2).replace('.', ',')}</span>
                  <span>{r.email}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0) }}
          className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert">
          <option value="">Todos os status</option>
          <option value="PAID">PAID</option>
          <option value="PENDING">PENDING</option>
          <option value="EXPIRED">EXPIRED</option>
        </select>

        <Card>
          {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-3">Cobrança</th>
                    <th className="py-2 pr-3">Site</th>
                    <th className="py-2 pr-3">Comprador</th>
                    <th className="py-2 pr-3">Valor</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Relatório?</th>
                    <th className="py-2">Data</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.charge_id} className="border-t border-klarim-border">
                      <td className="py-2 pr-3 font-mono text-[11px] text-klarim-muted">{p.charge_id}</td>
                      <td className="py-2 pr-3 font-mono text-xs">
                        {p.target_id
                          ? <a href={`/painel/alvos/${p.target_id}`} className="text-klarim-alert hover:underline">{p.target_url}</a>
                          : p.target_url}
                      </td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{p.buyer_email || '—'}</td>
                      <td className="py-2 pr-3 font-semibold">{p.amount_display}</td>
                      <td className="py-2 pr-3"><Badge color={STATUS_COLOR[p.status] || '#8B949E'}>{p.status}</Badge></td>
                      <td className="py-2 pr-3 text-xs">{p.report_email_sent ? '✅' : '—'}</td>
                      <td className="py-2 text-xs text-klarim-muted">{formatDate(p.created_at)}</td>
                    </tr>
                  ))}
                  {rows.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-klarim-muted">Nenhum pagamento.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
        </Card>
      </div>
    </AdminShell>
  )
}
