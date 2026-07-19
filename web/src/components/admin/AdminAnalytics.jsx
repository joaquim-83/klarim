import { useEffect, useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Badge } from './ui'
import AdminShell from './AdminShell'

// KL-83 — Analytics admin redesenhado (Prompt 1). Abas: Visão geral (dashboard executivo) +
// Eventos (stream com filtros). Páginas/Jornadas = placeholder "Em breve" (Prompt 2). Uma
// chamada por endpoint, cache no backend (5 min). Admin sempre dark (tokens klarim-*).
const PERIODS = [
  { key: 'today', label: 'Hoje' }, { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' }, { key: '90d', label: '90 dias' },
]
const TABS = [
  { key: 'overview', label: 'Visão geral', soon: false },
  { key: 'events', label: 'Eventos', soon: false },
  { key: 'pages', label: 'Páginas', soon: true },
  { key: 'journeys', label: 'Jornadas', soon: true },
]
const CAMPAIGN_COLOR = {
  alerta: '#3B82F6', profile_view: '#00D26A', alerta_score100: '#F0C000',
  '(sem campanha)': '#8B949E',
}
const TOOLTIP_STYLE = { backgroundColor: '#161B22', border: '1px solid #30363D', borderRadius: 8, color: '#E6EDF3' }
const TREND_COLORS = { visitors: '#58A6FF', scans: '#FF6B35', accounts: '#00D26A' }
const TREND_LABEL = { visitors: 'Visitantes', scans: 'Scans', accounts: 'Contas' }

function fmtNum(n) { return (n ?? 0).toLocaleString('pt-BR') }
function fmtDelta(pct) {
  if (pct == null) return <span className="text-klarim-muted">—</span>
  const up = pct >= 0
  return <span style={{ color: up ? '#00D26A' : '#F85149' }}>{up ? '↑' : '↓'} {Math.abs(pct)}%</span>
}
function fmtDate(s) {
  if (!s) return '—'
  try { return new Date(/[Z+]/.test(s) ? s : `${s}Z`).toLocaleString('pt-BR') } catch { return s }
}
function secs(s) { const n = Math.round(Number(s || 0)); return n < 60 ? `${n}s` : `${Math.floor(n / 60)}min ${n % 60}s` }

export default function AdminAnalytics() {
  const [period, setPeriod] = useState('7d')
  const [tab, setTab] = useState(() => {
    if (typeof window === 'undefined') return 'overview'
    const h = window.location.hash.replace('#', '')
    return TABS.some((t) => t.key === h && !t.soon) ? h : 'overview'
  })
  function goTab(k) {
    if (TABS.find((t) => t.key === k)?.soon) return
    setTab(k)
    window.location.hash = k
  }

  return (
    <AdminShell active="analytics">
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Analytics</h1>
          <div className="flex gap-1 rounded-lg border border-klarim-border bg-klarim-surface p-1">
            {PERIODS.map((p) => (
              <button key={p.key} onClick={() => setPeriod(p.key)}
                className={`rounded px-3 py-1 text-sm ${period === p.key ? 'bg-klarim-alert text-klarim-bg font-semibold' : 'text-klarim-muted hover:text-klarim-text'}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Abas */}
        <div className="flex flex-wrap gap-2 border-b border-klarim-border">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => goTab(t.key)} disabled={t.soon}
              className={`relative -mb-px rounded-t px-4 py-2 text-sm ${tab === t.key ? 'border-b-2 border-klarim-alert font-semibold text-klarim-text' : 'text-klarim-muted hover:text-klarim-text'} ${t.soon ? 'cursor-not-allowed opacity-60' : ''}`}>
              {t.label}
              {t.soon && <span className="ml-1.5 rounded bg-klarim-border px-1.5 py-0.5 text-[10px]">Em breve</span>}
            </button>
          ))}
        </div>

        {tab === 'overview' && <OverviewTab period={period} />}
        {tab === 'events' && <EventsTab period={period} />}
        {(tab === 'pages' || tab === 'journeys') && <SoonTab />}
      </div>
    </AdminShell>
  )
}

// =========================================================================== #
// Aba 1 — Visão geral
// =========================================================================== #
const METRIC_ORDER = [
  ['unique_visitors', 'Visitantes únicos', (v) => fmtNum(v)],
  ['scans_manual', 'Scans realizados', (v) => fmtNum(v)],
  ['accounts_created', 'Contas criadas', (v) => fmtNum(v)],
  ['conversion_rate', 'Conversão visitante→conta', (v) => `${v}%`],
  ['pageviews_per_session', 'Pageviews/sessão', (v) => v],
  ['alert_click_rate', 'Clique em alertas', (v) => `${v}%`],
]

function OverviewTab({ period }) {
  const { data, loading, error } = useAsync(
    () => Promise.all([admin.aaMetrics(period), admin.aaTrend(period), admin.aaFunnel(period)])
      .then(([metrics, trend, funnel]) => ({ metrics: metrics.metrics, trend, funnel: funnel.stages })),
    [period],
  )
  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {METRIC_ORDER.map(([key, label, fmt]) => (
          <MetricCard key={key} label={label} m={data.metrics[key]} fmt={fmt} />
        ))}
      </div>
      <Card title="Tendência">
        <div style={{ width: '100%', height: 250 }}>
          <ResponsiveContainer>
            <LineChart data={trendData(data.trend)}>
              <XAxis dataKey="date" stroke="#8B949E" fontSize={10} tickFormatter={(d) => (d || '').slice(5)} />
              <YAxis stroke="#8B949E" fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              {Object.keys(data.trend.series || {}).map((k) => (
                <Line key={k} type="monotone" dataKey={k} name={TREND_LABEL[k] || k}
                  stroke={TREND_COLORS[k] || '#8B949E'} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <FunnelChart stages={data.funnel} />
    </div>
  )
}

function trendData(trend) {
  const dates = trend.dates || []
  return dates.map((date, i) => {
    const row = { date }
    for (const [k, arr] of Object.entries(trend.series || {})) row[k] = arr[i] ?? 0
    return row
  })
}

function MetricCard({ label, m }) {
  const value = m?.value ?? 0
  const spark = (m?.sparkline || []).map((v, i) => ({ i, v }))
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface p-4">
      <p className="text-xs text-klarim-muted">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-2">
        <span className="text-2xl font-bold">{typeof value === 'number' ? value.toLocaleString('pt-BR') : value}</span>
        <span className="text-xs">{fmtDelta(m?.change_pct)}</span>
      </div>
      <div style={{ width: '100%', height: 40 }} className="mt-2">
        {spark.length > 1 && (
          <ResponsiveContainer>
            <LineChart data={spark}>
              <Line type="monotone" dataKey="v" stroke="#FF6B35" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

function FunnelChart({ stages }) {
  const max = Math.max(1, ...stages.map((s) => s.total))
  return (
    <Card title="Funil de conversão">
      <div className="space-y-1">
        {stages.map((s, i) => {
          const widthPct = (s.total / max) * 100
          const entries = Object.entries(s.by_campaign || {}).filter(([, v]) => v > 0)
          return (
            <div key={s.name}>
              {i > 0 && (
                <p className="py-0.5 text-center text-xs text-klarim-muted">
                  {s.conversion_from_previous != null ? `${s.conversion_from_previous}% ↓` : ''}
                </p>
              )}
              <div className="flex items-center gap-3">
                <span className="w-40 shrink-0 text-right text-xs text-klarim-muted">{s.label}</span>
                <div className={`h-7 flex-1 overflow-hidden rounded ${s.bottleneck ? 'ring-2 ring-klarim-fail' : ''}`}
                  style={{ width: `${Math.max(widthPct, 3)}%`, minWidth: 40, background: '#0D1117' }}>
                  <div className="flex h-full">
                    {entries.length === 0 ? <div className="h-full w-full bg-klarim-border" /> :
                      entries.map(([c, v]) => (
                        <div key={c} title={`${c}: ${v}`} style={{ flex: v, background: CAMPAIGN_COLOR[c] || '#8B949E' }} />
                      ))}
                  </div>
                </div>
                <span className="w-14 shrink-0 text-sm font-semibold">{fmtNum(s.total)}</span>
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-klarim-muted">
        {Object.entries(CAMPAIGN_COLOR).map(([c, color]) => (
          <span key={c} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />{c}
          </span>
        ))}
        {stages.some((s) => s.bottleneck) && <span className="text-klarim-fail">▮ gargalo</span>}
      </div>
    </Card>
  )
}

// =========================================================================== #
// Aba 2 — Eventos
// =========================================================================== #
const EVENT_TYPES = [
  'page_view', 'profile_view', 'scan_started', 'scan_completed', 'scan_anonymous',
  'code_requested', 'account_created_alert', 'cta_clicked', 'payment_created', 'payment_completed',
]
const EV_COLOR = {
  page_view: '#8B949E', profile_view: '#2DD4BF', scan_started: '#58A6FF',
  scan_completed: '#58A6FF', scan_anonymous: '#58A6FF', cta_clicked: '#A855F7',
  payment_created: '#F0C000', payment_completed: '#00D26A', account_created_alert: '#FF6B35',
}

function EventsTab({ period }) {
  const [types, setTypes] = useState([])
  const [domain, setDomain] = useState('')
  const [campaign, setCampaign] = useState('')
  const [path, setPath] = useState('')
  const [groupBy, setGroupBy] = useState(false)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(50)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState('')

  const dDomain = useDebounce(domain, 300)
  const dPath = useDebounce(path, 300)
  const filters = useMemo(() => ({
    period, page, limit,
    ...(types.length ? { type: types.join(',') } : {}),
    ...(dDomain ? { domain: dDomain } : {}),
    ...(campaign ? { campaign } : {}),
    ...(dPath ? { path: dPath } : {}),
  }), [period, page, limit, types, dDomain, campaign, dPath])

  useEffect(() => { setPage(1) }, [period, types, dDomain, campaign, dPath, groupBy, limit])

  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    const call = groupBy ? admin.aaSessions({ period, page, limit }) : admin.aaEvents(filters)
    call.then((d) => { if (alive) { setData(d); setLoading(false) } })
      .catch((e) => { if (alive) { setError(String(e.message || e)); setLoading(false) } })
    return () => { alive = false }
  }, [filters, groupBy])

  function toggleType(t) {
    setTypes((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t])
  }

  async function exportCSV() {
    const total = data?.counters?.events ?? data?.pagination?.total ?? 0
    if (total > 5000 && !window.confirm(`Exportando ${total} eventos, pode demorar. Continuar?`)) return
    setExporting('Exportando…')
    try {
      const rows = []
      const per = 100
      const pages = Math.min(Math.ceil(total / per), 60) // teto de 6000
      for (let pg = 1; pg <= pages; pg++) {
        const d = await admin.aaEvents({ ...filters, page: pg, limit: per })
        rows.push(...(d.events || []))
        if ((d.events || []).length < per) break
      }
      const header = ['created_at', 'event_type', 'session_id', 'page_url', 'target_url', 'utm_campaign']
      const csv = [header.join(',')].concat(rows.map((r) =>
        header.map((h) => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(','))).join('\n')
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `eventos_${period}_${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
    } finally { setExporting('') }
  }

  const counters = data?.counters
  const pag = data?.pagination

  return (
    <div className="space-y-4">
      {/* Filtros */}
      <Card title="Filtros">
        <div className="flex flex-wrap gap-2">
          {EVENT_TYPES.map((t) => (
            <button key={t} onClick={() => toggleType(t)}
              className={`rounded-full border px-2.5 py-1 text-xs ${types.includes(t) ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-alert' : 'border-klarim-border text-klarim-muted hover:text-klarim-text'}`}>
              {t}
            </button>
          ))}
        </div>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="Domínio (ex: hotel.com.br)"
            className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text" />
          <input value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="Campanha (utm)"
            className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text" />
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="Página (ex: /cadastrar)"
            className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text" />
        </div>
        {!groupBy && counters && (
          <p className="mt-3 text-xs text-klarim-muted">
            {fmtNum(counters.events)} eventos · {fmtNum(counters.sessions)} sessões · {fmtNum(counters.domains)} domínios · {fmtNum(counters.scans)} scans · {fmtNum(counters.accounts)} contas
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <label className="flex items-center gap-2 text-sm text-klarim-muted">
            <input type="checkbox" checked={groupBy} onChange={(e) => setGroupBy(e.target.checked)} />
            Agrupar por sessão
          </label>
          <button onClick={exportCSV} disabled={!!exporting || groupBy}
            className="rounded-lg border border-klarim-border px-3 py-1.5 text-sm text-klarim-text hover:bg-klarim-bg disabled:opacity-50">
            {exporting || 'Exportar CSV'}
          </button>
        </div>
      </Card>

      {loading ? <Loading /> : error ? <ErrorBox message={error} /> :
        groupBy ? <SessionsView sessions={data.sessions} /> : <EventsTable events={data.events} />}

      {/* Paginação */}
      {pag && pag.pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
            className="rounded border border-klarim-border px-3 py-1 disabled:opacity-40">← Anterior</button>
          <span className="text-klarim-muted">Página {page} de {pag.pages}</span>
          <button disabled={page >= pag.pages} onClick={() => setPage((p) => p + 1)}
            className="rounded border border-klarim-border px-3 py-1 disabled:opacity-40">Próxima →</button>
          {!groupBy && (
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
              className="rounded border border-klarim-border bg-klarim-bg px-2 py-1">
              {[25, 50, 100].map((n) => <option key={n} value={n}>{n}/pág</option>)}
            </select>
          )}
        </div>
      )}
    </div>
  )
}

function EventsTable({ events }) {
  if (!events || events.length === 0) return <p className="text-sm text-klarim-muted">Nenhum evento.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-klarim-border">
      <table className="w-full text-sm">
        <thead className="bg-klarim-surface text-left text-xs text-klarim-muted">
          <tr>
            {['Hora', 'Tipo', 'Página', 'Domínio', 'Campanha', 'Sessão'].map((h) =>
              <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-t border-klarim-border">
              <td className="whitespace-nowrap px-3 py-2 text-klarim-muted">{fmtDate(e.created_at)}</td>
              <td className="px-3 py-2"><Badge color={EV_COLOR[e.event_type] || '#8B949E'}>{e.event_type}</Badge></td>
              <td className="max-w-xs truncate px-3 py-2">{e.page_url || '—'}</td>
              <td className="px-3 py-2 text-klarim-muted">{domainOf(e)}</td>
              <td className="px-3 py-2 text-klarim-muted">{e.utm_campaign || '—'}</td>
              <td className="px-3 py-2 font-mono text-xs text-klarim-muted">{(e.session_id || '').slice(0, 8)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function domainOf(e) {
  const raw = e.target_url || e.page_url || ''
  const mm = raw.match(/\/site\/([^/?]+)/)
  if (mm) return mm[1]
  try { return new URL(raw.startsWith('http') ? raw : `https://${raw}`).hostname.replace(/^www\./, '') } catch { return '—' }
}

function SessionsView({ sessions }) {
  if (!sessions || sessions.length === 0) return <p className="text-sm text-klarim-muted">Nenhuma sessão.</p>
  return (
    <div className="space-y-2">
      {sessions.map((s) => <SessionCard key={s.session_id} s={s} />)}
    </div>
  )
}

function SessionCard({ s }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between px-4 py-3 text-left">
        <span className="text-sm">
          <span className="font-mono text-klarim-muted">#{(s.session_id || '').slice(0, 8)}…</span>
          <span className="ml-2 text-klarim-muted">{s.event_count} eventos · {secs(s.duration_seconds)}</span>
        </span>
        <span>
          {s.campaign && <Badge color={CAMPAIGN_COLOR[s.campaign] || '#8B949E'}>{s.campaign}</Badge>}
          <span className="ml-2"><Badge color={s.converted ? '#00D26A' : '#8B949E'}>{s.converted ? 'Converteu' : 'Abandonou'}</Badge></span>
        </span>
      </button>
      {open && (
        <ul className="border-t border-klarim-border px-4 py-2">
          {(s.events || []).map((e, i) => (
            <li key={i} className="flex items-center gap-3 py-1 text-xs">
              <span className="w-32 shrink-0 text-klarim-muted">{fmtDate(e.created_at)}</span>
              <Badge color={EV_COLOR[e.event_type] || '#8B949E'}>{e.event_type}</Badge>
              <span className="truncate text-klarim-muted">{e.page_url || e.domain || e.target_url || ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SoonTab() {
  return (
    <Card title="Em breve">
      <p className="text-sm text-klarim-muted">
        Esta aba (tabela de páginas / jornadas de usuário) chega no próximo lote do KL-83. O
        backend já está pronto — a visualização será entregue no Prompt 2.
      </p>
    </Card>
  )
}
