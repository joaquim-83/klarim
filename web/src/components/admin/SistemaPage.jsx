import { useState, useEffect, useCallback } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { Card, StatCard, Loading, ErrorBox, relativeTime, formatDate } from './ui'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/Sistema.jsx (KL-51 fase 2). Auto-refresh 30s preservado.
const OK = '#00D26A'
const WARN = '#F0C000'
const BAD = '#F85149'

function dot(color) {
  return <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
}

function workerLight(alive) {
  return <span style={{ color: alive ? OK : BAD }}>{alive ? '🟢 Ativo' : '🔴 Parado'}</span>
}

const DEP_COLOR = { ok: OK, streaming: OK, unknown: WARN, error: BAD, disconnected: BAD }
const DEP_LABEL = {
  postgres: 'PostgreSQL', redis: 'Redis', ct_logs: 'CT logs',
  resend: 'Resend', abacatepay: 'AbacatePay',
}

const ACT_COLOR = { discovery: '#2DD4BF', alert: '#FF6B35', scan: '#3B82F6', rescan: '#A855F7', payment: '#00D26A', email: '#58A6FF', email_blocked: '#F85149' }

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between py-0.5 text-sm">
      <span className="text-klarim-muted">{label}</span>
      <span className="font-medium">{value ?? '—'}</span>
    </div>
  )
}

function WorkerCard({ title, alive, children }) {
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold">{title}</h3>
        {workerLight(alive)}
      </div>
      {children}
    </Card>
  )
}

const BOUNCE_COLOR = { ok: OK, warning: WARN, critical: BAD }
const BOUNCE_LABEL = { ok: '🟢 Saudável', warning: '🟡 Atenção', critical: '🔴 Crítico' }

export default function SistemaPage() {
  const [data, setData] = useState(null)
  const [activity, setActivity] = useState([])
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    Promise.all([admin.systemStatus(), admin.systemActivity(50), admin.emailHealth()])
      .then(([s, a, h]) => { setData(s); setActivity(a.activity || []); setHealth(h); setError('') })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Auto-refresh a cada 30s.
  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  let body
  if (loading) body = <Loading />
  else if (error && !data) body = <ErrorBox message={error} />
  else {
    const w = data.workers
    const deps = data.dependencies
    const em = data.email_metrics
    body = (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">Sistema</h1>
          <span className="text-xs text-klarim-muted">atualiza a cada 30s</span>
        </div>

        {/* Workers */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <WorkerCard title="Discovery" alive={w.discovery.alive}>
            <Row label="CT logs" value={w.discovery.source?.connected ? 'conectado' : 'desconectado'} />
            <Row label="Último ciclo" value={w.discovery.last_cycle_at ? relativeTime(w.discovery.last_cycle_at) : '—'} />
            <Row label="Próximo ciclo" value={w.discovery.next_cycle_at ? relativeTime(w.discovery.next_cycle_at) : '—'} />
            <Row label="Certs vistos" value={w.discovery.source?.total_seen} />
            <Row label=".com.br" value={w.discovery.source?.total_matched} />
            <Row label="Buffer" value={w.discovery.source?.buffer_size} />
            <Row label="Descobertos hoje" value={w.discovery.targets_discovered_today} />
          </WorkerCard>

          <WorkerCard title="Alert" alive={w.alert.alive}>
            <Row label="Último ciclo" value={w.alert.last_cycle_at ? relativeTime(w.alert.last_cycle_at) : '—'} />
            <Row label="Enviados hoje" value={w.alert.sent_today} />
            <Row label="Este mês" value={`${w.alert.sent_month} / ${w.alert.monthly_limit}`} />
            <Row label="Backlog de alertas" value={w.alert.backlog} />
          </WorkerCard>

          <WorkerCard title="Re-scan" alive={w.rescan.alive}>
            <Row label="Último ciclo" value={w.rescan.last_cycle_at ? relativeTime(w.rescan.last_cycle_at) : '—'} />
            <Row label="Re-scans hoje" value={w.rescan.rescanned_today} />
            <Row label="Próximos elegíveis" value={w.rescan.eligible} />
          </WorkerCard>

          <WorkerCard title="Scan" alive={w.scan.alive}>
            <Row label="Na fila" value={w.scan.queue_size} />
            <Row label="Completados hoje" value={w.scan.completed_today} />
            <Row label="Score médio hoje" value={w.scan.avg_score_today} />
            <Row label="Último scan" value={w.scan.last_scan_at ? relativeTime(w.scan.last_scan_at) : '—'} />
          </WorkerCard>
        </div>

        {/* Dependências */}
        <Card title="Health das dependências">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {['postgres', 'redis', 'ct_logs', 'resend', 'abacatepay'].map((k) => {
              const d = deps[k] || {}
              return (
                <div key={k} className="flex items-center gap-2 rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2">
                  {dot(DEP_COLOR[d.status] || WARN)}
                  <div>
                    <div className="text-sm font-medium">{DEP_LABEL[k]}</div>
                    <div className="text-xs text-klarim-muted">{d.status}{d.latency_ms != null ? ` · ${d.latency_ms}ms` : ''}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </Card>

        {/* Métricas de e-mail (cota mensal — Resend Pro, KL-23) */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <StatCard label="E-mails hoje" value={em.sent_today} accent="#FF6B35" />
          <StatCard label="Esta semana" value={em.sent_week} />
          <StatCard label="Este mês" value={`${em.sent_month} / ${em.monthly_limit}`} />
          <StatCard label="Uso mensal" value={em.monthly_usage_pct} accent="#F0C000" />
          <StatCard label="Backlog de alertas" value={em.backlog} />
        </div>

        {/* Saúde de e-mail — bounce/complaint (KL-24) */}
        {health && (
          <Card title="Saúde de e-mail (bounce)">
            <div className="mb-3 flex items-center gap-2">
              {dot(BOUNCE_COLOR[health.bounce_status] || WARN)}
              <span className="font-semibold" style={{ color: BOUNCE_COLOR[health.bounce_status] || WARN }}>
                {BOUNCE_LABEL[health.bounce_status] || health.bounce_status}
              </span>
              <span className="text-sm text-klarim-muted">
                — limite seguro &lt; 4% (o worker pausa &gt; 8%)
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <StatCard label="Bounce rate" value={`${health.bounce_rate}%`}
                accent={BOUNCE_COLOR[health.bounce_status]} />
              <StatCard label="Enviados" value={health.total_sent} />
              <StatCard label="Bounces perm." value={health.bounced_permanent} accent={BAD} />
              <StatCard label="Complaints" value={health.complained} accent={BAD} />
              <StatCard label="Blocklist" value={health.blocklist_size} />
            </div>
          </Card>
        )}

        {/* Log de atividade */}
        <Card title={`Atividade recente (${activity.length})`}>
          {activity.length === 0 ? (
            <p className="text-sm text-klarim-muted">Nenhuma atividade ainda.</p>
          ) : (
            <div className="max-h-96 space-y-1 overflow-y-auto">
              {activity.map((e, i) => (
                <div key={i} className="flex items-start gap-2 border-b border-klarim-border/50 py-1.5 text-sm">
                  <span className="mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase"
                    style={{ color: ACT_COLOR[e.type] || '#8B949E', backgroundColor: `${ACT_COLOR[e.type] || '#8B949E'}22` }}>
                    {e.type}
                  </span>
                  <span className="flex-1 text-klarim-text">{e.message}</span>
                  <span className="whitespace-nowrap text-xs text-klarim-muted">{formatDate(e.at)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    )
  }

  return <AdminShell active="sistema">{body}</AdminShell>
}
