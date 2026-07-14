import {
  PieChart, Pie, Cell, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts'
import { Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import {
  Card, StatCard, Loading, ErrorBox, SemaphoreDot, relativeTime,
  STATUS_COLOR, STATUS_LABEL, PLATFORM_COLOR,
} from '../../components/admin/ui'

const TOOLTIP_STYLE = {
  backgroundColor: '#161B22', border: '1px solid #30363D', borderRadius: 8, color: '#E6EDF3',
}

// Rótulo do tipo de scan na atividade recente (Fix pós-KL-27).
const SCAN_TYPE = {
  public: ['Básico', '#3B82F6'], paid: ['Completo', '#00D26A'],
  rescan: ['Re-verificação', '#A371F7'], admin: ['Admin', '#F0C000'],
  manual: ['Manual', '#F0C000'], discovery: ['Descoberta', '#8B949E'],
  demo: ['Demo', '#FF6B35'],
}

function scanTypeBadge(source) {
  const [label, color] = SCAN_TYPE[source] || [source || '—', '#8B949E']
  return (
    <span className="rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color, border: `1px solid ${color}55` }}>
      {label}
    </span>
  )
}

function ChartCard({ title, children }) {
  return (
    <Card title={title}>
      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </div>
    </Card>
  )
}

// Estado de um worker para a saúde do sistema (KL-57): pausado > vivo > parado.
function workerState(w) {
  if (!w) return ['🔴', 'sem dados']
  if (w.enabled === false) return ['⏸️', 'pausado']
  if (w.alive) return ['▶️', 'ativo']
  return ['🔴', 'parado']
}

function depState(d) {
  if (!d) return ['—', '#8B949E']
  const ok = ['ok', 'streaming', 'connected'].includes(d.status)
  return [ok ? `ok (${d.latency_ms ?? '?'}ms)` : (d.status || 'erro'), ok ? '#00D26A' : '#F85149']
}

export default function Overview() {
  const { data, loading, error } = useAsync(() =>
    Promise.all([
      admin.targetsStats(), admin.alertsStats(), admin.scansStats(),
      admin.paymentsStats(), admin.scansDaily(30), admin.alertsDaily(30),
      admin.scans({ limit: 10, distinct_url: true }),  // 1 linha por site (Fix pós-KL-27)
      admin.dashboardStats(),                            // totalizadores KL-57
      admin.systemStatus().catch(() => null),            // saúde do sistema (best-effort)
    ]).then(([targets, alerts, scans, payments, scansD, alertsD, recent, dash, system]) => ({
      targets, alerts, scans, payments,
      scansD: scansD.series, alertsD: alertsD.series, recent: recent.scans,
      dash, system,
    })),
  )

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />

  const byStatus = data.targets.by_status || {}
  const byPlatform = data.targets.by_platform || {}
  const statusData = Object.entries(byStatus).map(([k, v]) => ({
    name: STATUS_LABEL[k] || k, value: v, color: STATUS_COLOR[k] || '#8B949E',
  }))
  const platformData = Object.entries(byPlatform).map(([k, v]) => ({
    name: k, value: v, color: PLATFORM_COLOR[k] || '#8B949E',
  }))

  const dash = data.dash || {}
  const dScans = dash.scans || {}
  const dProfiles = dash.profiles || {}
  const dAccounts = dash.accounts || {}
  const workers = data.system?.workers || {}
  const deps = data.system?.dependencies || {}

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Visão geral</h1>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Alvos" value={data.targets.total ?? 0} />
        <StatCard label="Escaneados" value={byStatus.scanned ?? 0} accent="#3B82F6" />
        <StatCard label="Alertas (mês)" value={data.alerts.month ?? 0} accent="#FF6B35" />
        <StatCard label="Pagamentos" value={data.payments.paid_count ?? 0} accent="#00D26A" />
        <StatCard label="Receita" value={data.payments.revenue_display ?? 'R$ 0,00'} accent="#00D26A" />
        <StatCard label="Score médio" value={data.scans.avg_score ?? 0} accent="#F0C000" />
      </div>

      {/* Totalizadores da plataforma (KL-57) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Scans (total)" value={dScans.total ?? 0} accent="#3B82F6" />
        <StatCard label="Landings" value={dProfiles.public ?? 0}
          sub={dProfiles.hidden ? `${dProfiles.hidden} ocultas` : undefined} accent="#A371F7" />
        <StatCard label="Score 100" value={dash.targets?.score_100 ?? 0} accent="#00D26A" />
        <StatCard label="Contas" value={dAccounts.total ?? 0}
          sub={`${dAccounts.sites_monitored ?? 0} sites`} accent="#FF6B35" />
        <StatCard label="Scans manuais" value={dScans.manual ?? 0}
          sub="site público" accent="#F0C000" />
        <StatCard label="Scans automáticos" value={dScans.automated ?? 0}
          sub="worker" accent="#8B949E" />
      </div>

      {/* Enriquecimento de perfis (KL-57) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Perfis" value={dProfiles.total ?? 0} />
        <StatCard label="Perfis com IA" value={dProfiles.with_ai ?? 0} accent="#A371F7" />
        <StatCard label="Perfis com CNAE" value={dProfiles.with_cnae ?? 0} accent="#3B82F6" />
        <StatCard label="Scans (7 dias)" value={dScans.last_7_days ?? 0} accent="#F0C000" />
      </div>

      {/* Saúde do sistema (KL-57) */}
      {data.system && (
        <Card title="Saúde do sistema">
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {['discovery', 'scan', 'alert', 'rescan'].map((w) => {
              const [icon, txt] = workerState(workers[w])
              return (
                <span key={w} className="text-klarim-text">
                  <span className="text-klarim-muted capitalize">{w}:</span> {icon} {txt}
                </span>
              )
            })}
          </div>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {['postgres', 'redis', 'ct_logs'].map((d) => {
              const [txt, color] = depState(deps[d])
              return (
                <span key={d} className="text-klarim-muted">
                  {d}: <span style={{ color }}>{txt}</span>
                </span>
              )
            })}
            <Link to="/painel/sistema" className="text-klarim-alert hover:underline">ver detalhes →</Link>
          </div>
        </Card>
      )}

      {/* Gráficos */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Alvos por status">
          <PieChart>
            <Pie data={statusData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
              {statusData.map((d, i) => <Cell key={i} fill={d.color} stroke="#0D1117" />)}
            </Pie>
            <Tooltip contentStyle={TOOLTIP_STYLE} />
          </PieChart>
        </ChartCard>

        <ChartCard title="Alvos por plataforma">
          <BarChart data={platformData}>
            <XAxis dataKey="name" stroke="#8B949E" fontSize={11} />
            <YAxis stroke="#8B949E" fontSize={11} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: '#ffffff10' }} />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {platformData.map((d, i) => <Cell key={i} fill={d.color} />)}
            </Bar>
          </BarChart>
        </ChartCard>

        <ChartCard title="Scans por dia (30d)">
          <LineChart data={data.scansD}>
            <XAxis dataKey="day" stroke="#8B949E" fontSize={10} tickFormatter={(d) => d.slice(5)} />
            <YAxis stroke="#8B949E" fontSize={11} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Line type="monotone" dataKey="count" stroke="#FF6B35" strokeWidth={2} dot={false} />
          </LineChart>
        </ChartCard>

        <ChartCard title="Alertas por dia (30d)">
          <LineChart data={data.alertsD}>
            <XAxis dataKey="day" stroke="#8B949E" fontSize={10} tickFormatter={(d) => d.slice(5)} />
            <YAxis stroke="#8B949E" fontSize={11} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Line type="monotone" dataKey="count" stroke="#00D26A" strokeWidth={2} dot={false} />
          </LineChart>
        </ChartCard>
      </div>

      {/* Atividade recente */}
      <Card title="Atividade recente (últimos scans)">
        {(!data.recent || data.recent.length === 0) ? (
          <p className="text-sm text-klarim-muted">Nenhum scan ainda.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-4">Site</th>
                  <th className="py-2 pr-4">Tipo</th>
                  <th className="py-2 pr-4">Score</th>
                  <th className="py-2 pr-4">Quando</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((s) => (
                  <tr key={s.id} className="border-t border-klarim-border">
                    <td className="py-2 pr-4 font-mono text-xs">{s.url}</td>
                    <td className="py-2 pr-4">{scanTypeBadge(s.source)}</td>
                    <td className="py-2 pr-4"><SemaphoreDot semaphore={s.semaphore} score={s.score} /></td>
                    <td className="py-2 pr-4 text-klarim-muted">{relativeTime(s.scanned_at)}</td>
                    <td className="py-2 text-right">
                      <Link to={`/painel/scans/${s.id}`} className="text-klarim-alert hover:underline">detalhes</Link>
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
