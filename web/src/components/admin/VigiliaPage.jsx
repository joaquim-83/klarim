import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import { Card, StatCard, Loading, ErrorBox, Button, Badge, Pagination, relativeTime, formatDate } from './ui'
import AdminShell from './AdminShell'

// KL-44 P2 — Vigílias core no painel admin. Segue o padrão da migração Astro:
// componente React comum usado como ilha dentro de vigilias.astro (client:only).

const TYPE_LABEL = { ssl: 'SSL', domain: 'Domínio', score: 'Score', email: 'E-mail', reputation: 'Reputação' }
const TYPE_COLOR = { ssl: '#58A6FF', domain: '#A371F7', score: '#F0C000', email: '#3FB950', reputation: '#F85149' }
const STATUS_COLOR = { ok: '#00D26A', warning: '#F0C000', critical: '#F85149', error: '#8B949E' }
const SEV_COLOR = { info: '#58A6FF', warning: '#F0C000', critical: '#F85149' }
const PAGE_SIZE = 25

// Descreve o dado mais relevante do último check (por tipo de vigília).
function describeData(v) {
  const d = v.last_data || {}
  if ((v.tipo === 'ssl' || v.tipo === 'domain') && d.days_left != null) {
    return `Expira em ${d.days_left} dia(s)`
  }
  if (v.tipo === 'score' && d.current_score != null) {
    return `Score ${d.previous_score}→${d.current_score} (${d.delta})`
  }
  if (v.tipo === 'email' && Array.isArray(d.changed_checks) && d.changed_checks.length) {
    return `Afetados: ${d.changed_checks.join(', ')}`
  }
  if (v.tipo === 'reputation' && Array.isArray(d.blacklisted) && d.blacklisted.length) {
    return `Blacklist: ${d.blacklisted.join(', ')}`
  }
  if (d.error) return `Erro: ${d.error}`
  return '—'
}

export default function VigiliaPage() {
  const [tipo, setTipo] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const query = useDebounce(search, 300)
  const [page, setPage] = useState(0)
  const [detail, setDetail] = useState(null)

  const stats = useAsync(() => admin.vigiliaStats(), [])
  const { data, loading, error } = useAsync(
    () => admin.vigilias({
      tipo: tipo || undefined, status: status || undefined,
      domain: query || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE,
    }),
    [tipo, status, query, page])
  const rows = data?.vigilias || []
  const st = stats.data || {}
  const byStatus = st.by_status || {}

  return (
    <AdminShell active="vigilias">
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-bold">Vigílias</h1>
          <p className="text-sm text-klarim-muted">Monitoramento silencioso contínuo (SSL, domínio, score, e-mail, reputação).</p>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="Ativas" value={st.total_vigilias ?? 0} />
          <StatCard label="OK" value={byStatus.ok ?? 0} accent="#00D26A" />
          <StatCard label="Warning" value={byStatus.warning ?? 0} accent="#F0C000" />
          <StatCard label="Critical" value={byStatus.critical ?? 0} accent="#F85149" />
          <StatCard label="Alertas hoje" value={st.alerts_today ?? 0} />
          <StatCard label="Alertas 7d" value={st.alerts_7d ?? 0} />
        </div>

        {/* Filtros */}
        <div className="flex flex-wrap items-center gap-2">
          <select value={tipo} onChange={(e) => { setTipo(e.target.value); setPage(0) }}
            className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-2 text-sm">
            <option value="">Todos os tipos</option>
            {Object.entries(TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0) }}
            className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-2 text-sm">
            <option value="">Todos os status</option>
            <option value="ok">OK</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
            <option value="error">Error</option>
          </select>
          <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0) }}
            placeholder="Buscar domínio…"
            className="min-w-[180px] flex-1 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-2 text-sm" />
        </div>

        {/* Tabela */}
        <Card>
          {loading ? <Loading /> : error ? <ErrorBox message={error} /> : rows.length === 0 ? (
            <p className="py-8 text-center text-sm text-klarim-muted">Nenhuma vigília encontrada.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-klarim-border text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-3">Domínio</th>
                    <th className="py-2 pr-3">Tipo</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Dados</th>
                    <th className="py-2 pr-3">Último check</th>
                    <th className="py-2 pr-3">Alertas</th>
                    <th className="py-2 pr-3">Conta</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((v) => (
                    <tr key={v.id} onClick={() => setDetail(v)}
                      className="cursor-pointer border-b border-klarim-border/50 hover:bg-klarim-border/20">
                      <td className="py-2 pr-3 font-medium">{v.site_domain}</td>
                      <td className="py-2 pr-3"><Badge color={TYPE_COLOR[v.tipo]}>{TYPE_LABEL[v.tipo] || v.tipo}</Badge></td>
                      <td className="py-2 pr-3"><Badge color={STATUS_COLOR[v.last_status] || '#8B949E'}>{v.last_status || 'ok'}</Badge></td>
                      <td className="py-2 pr-3 text-klarim-muted">{describeData(v)}</td>
                      <td className="py-2 pr-3 text-klarim-muted">{v.last_check_at ? relativeTime(v.last_check_at) : '—'}</td>
                      <td className="py-2 pr-3">{v.alert_count || 0}</td>
                      <td className="py-2 pr-3 text-klarim-muted">{v.user_email}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
        </Card>
      </div>

      {detail && <VigiliaModal vigilia={detail} onClose={() => setDetail(null)} />}
    </AdminShell>
  )
}

function VigiliaModal({ vigilia, onClose }) {
  const { data, loading, error } = useAsync(() => admin.vigilia(vigilia.id), [vigilia.id])
  const alerts = data?.alerts || []
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl border border-klarim-border bg-klarim-surface p-5"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Badge color={TYPE_COLOR[vigilia.tipo]}>{TYPE_LABEL[vigilia.tipo] || vigilia.tipo}</Badge>
              <span className="font-bold">{vigilia.site_domain}</span>
            </div>
            <p className="mt-1 text-xs text-klarim-muted">{vigilia.user_email}</p>
          </div>
          <Button onClick={onClose}>Fechar</Button>
        </div>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-klarim-muted">Status:</span> <Badge color={STATUS_COLOR[data?.last_status] || '#8B949E'}>{data?.last_status || 'ok'}</Badge></div>
              <div><span className="text-klarim-muted">Alertas:</span> {data?.alert_count || 0}</div>
              <div><span className="text-klarim-muted">Último check:</span> {data?.last_check_at ? formatDate(data.last_check_at) : '—'}</div>
              <div><span className="text-klarim-muted">Próximo:</span> {data?.next_check_at ? formatDate(data.next_check_at) : '—'}</div>
            </div>
            <h3 className="mb-2 text-sm font-semibold">Histórico de alertas</h3>
            {alerts.length === 0 ? (
              <p className="text-sm text-klarim-muted">Nenhum alerta gerado ainda.</p>
            ) : (
              <ul className="space-y-2">
                {alerts.map((a) => (
                  <li key={a.id} className="rounded-lg border border-klarim-border/60 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium" style={{ color: SEV_COLOR[a.severity] || '#E6EDF3' }}>{a.title}</span>
                      <span className="text-xs text-klarim-muted">{formatDate(a.created_at)}</span>
                    </div>
                    <p className="mt-1 text-xs text-klarim-muted">{a.message}</p>
                    <div className="mt-1 text-[11px] text-klarim-muted">
                      {a.email_sent ? '✉️ e-mail enviado' : '✉️ não enviado'}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}
