import { useEffect, useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Badge } from './ui'
import AdminShell from './AdminShell'
import PaginationBar from './analytics/PaginationBar'
import SortableTable from './analytics/SortableTable'
import SessionCard, { CAMPAIGN_COLOR, EV_COLOR } from './analytics/SessionCard'
import {
  sortRows, paginate, journeyStepKind, STEP_COLOR, bounceColor, clickRateColor,
  deltaMeta, filterSectors, parseTabHash, buildTabHash,
} from '../../lib/admin/analyticsUtils'

// KL-83 — Analytics admin (Prompts 1+2). 4 abas: Visão geral, Eventos, Páginas, Jornadas.
// Uma chamada por endpoint; cache no backend (5 min). Lazy: cada aba só busca quando ativa.
const PERIODS = [
  { key: 'today', label: 'Hoje' }, { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' }, { key: '90d', label: '90 dias' },
]
const TABS = [
  { key: 'overview', label: 'Visão geral' }, { key: 'events', label: 'Eventos' },
  { key: 'pages', label: 'Páginas' }, { key: 'journeys', label: 'Jornadas' },
]
const TAB_KEYS = TABS.map((t) => t.key)
const TOOLTIP_STYLE = { backgroundColor: '#161B22', border: '1px solid #30363D', borderRadius: 8, color: '#E6EDF3' }
const TREND_COLORS = { visitors: '#58A6FF', scans: '#FF6B35', accounts: '#00D26A' }
const TREND_LABEL = { visitors: 'Visitantes', scans: 'Scans', accounts: 'Contas' }

function fmtNum(n) { return (n ?? 0).toLocaleString('pt-BR') }
function fmtDelta(pct) {
  if (pct == null) return <span className="text-klarim-muted">—</span>
  const up = pct >= 0
  return <span style={{ color: up ? '#00D26A' : '#F85149' }}>{up ? '↑' : '↓'} {Math.abs(pct)}%</span>
}
function fmtDateTime(s) {
  if (!s) return '—'
  try { return new Date(/[Z+]/.test(s) ? s : `${s}Z`).toLocaleString('pt-BR') } catch { return s }
}
function trunc(s, n = 35) { return (s || '').length > n ? `${s.slice(0, n)}…` : (s || '') }

export default function AdminAnalytics() {
  const [period, setPeriod] = useState('7d')
  const [nav, setNav] = useState(() =>
    parseTabHash(typeof window !== 'undefined' ? window.location.hash : '', TAB_KEYS))

  function navigate(tab, params = {}) {
    if (typeof window !== 'undefined') window.location.hash = buildTabHash(tab, params)
    setNav({ tab, params })
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

        <div className="flex flex-wrap gap-2 border-b border-klarim-border">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => navigate(t.key)}
              className={`-mb-px rounded-t px-4 py-2 text-sm ${nav.tab === t.key ? 'border-b-2 border-klarim-alert font-semibold text-klarim-text' : 'text-klarim-muted hover:text-klarim-text'}`}>
              {t.label}
            </button>
          ))}
        </div>

        {nav.tab === 'overview' && <OverviewTab period={period} />}
        {nav.tab === 'events' && (
          <EventsTab key={JSON.stringify(nav.params)} period={period} initialParams={nav.params} />
        )}
        {nav.tab === 'pages' && <PagesTab period={period} navigate={navigate} />}
        {nav.tab === 'journeys' && <JourneysTab period={period} navigate={navigate} />}
      </div>
    </AdminShell>
  )
}

// =========================================================================== #
// Aba 1 — Visão geral
// =========================================================================== #
const METRIC_ORDER = [
  ['unique_visitors', 'Visitantes únicos'], ['scans_manual', 'Scans realizados'],
  ['accounts_created', 'Contas criadas'], ['conversion_rate', 'Conversão visitante→conta', '%'],
  ['pageviews_per_session', 'Pageviews/sessão'], ['alert_click_rate', 'Clique em alertas', '%'],
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
        {METRIC_ORDER.map(([key, label, suffix]) => (
          <MetricCard key={key} label={label} m={data.metrics[key]} suffix={suffix} />
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
  return (trend.dates || []).map((date, i) => {
    const row = { date }
    for (const [k, arr] of Object.entries(trend.series || {})) row[k] = arr[i] ?? 0
    return row
  })
}

function MetricCard({ label, m, suffix }) {
  const value = m?.value ?? 0
  const spark = (m?.sparkline || []).map((v, i) => ({ i, v }))
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface p-4">
      <p className="text-xs text-klarim-muted">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-2">
        <span className="text-2xl font-bold">
          {typeof value === 'number' ? value.toLocaleString('pt-BR') : value}{suffix || ''}
        </span>
        <span className="text-xs">{fmtDelta(m?.change_pct)}</span>
      </div>
      <div style={{ width: '100%', height: 40 }} className="mt-2">
        {spark.length > 1 && (
          <ResponsiveContainer>
            <LineChart data={spark}><Line type="monotone" dataKey="v" stroke="#FF6B35" strokeWidth={1.5} dot={false} /></LineChart>
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

function EventsTab({ period, initialParams = {} }) {
  const [types, setTypes] = useState([])
  const [domain, setDomain] = useState('')
  const [campaign, setCampaign] = useState('')
  const [path, setPath] = useState(initialParams.path || '')
  const [groupBy, setGroupBy] = useState(initialParams.group === 'session')
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
      const rows = []; const per = 100; const pages = Math.min(Math.ceil(total / per), 60)
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
      a.click(); URL.revokeObjectURL(a.href)
    } finally { setExporting('') }
  }

  const counters = data?.counters; const pag = data?.pagination
  return (
    <div className="space-y-4">
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
        groupBy
          ? ((data.sessions || []).length ? <div className="space-y-2">{data.sessions.map((s) => <SessionCard key={s.session_id} s={s} />)}</div> : <p className="text-sm text-klarim-muted">Nenhuma sessão.</p>)
          : <EventsTable events={data.events} />}

      {pag && (
        <PaginationBar page={page} pages={pag.pages} total={pag.total} onPage={setPage}
          limit={limit} onLimit={groupBy ? undefined : setLimit} />
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
          <tr>{['Hora', 'Tipo', 'Página', 'Domínio', 'Campanha', 'Sessão'].map((h) =>
            <th key={h} scope="col" className="px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-t border-klarim-border">
              <td className="whitespace-nowrap px-3 py-2 text-klarim-muted">{fmtDateTime(e.created_at)}</td>
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

// =========================================================================== #
// Aba 3 — Páginas
// =========================================================================== #
const PAGE_COLUMNS = [
  { key: 'path', label: 'Página', align: 'left' },
  { key: 'views', label: 'Views', align: 'right' },
  { key: 'sessions', label: 'Sessões', align: 'right' },
  { key: 'bounce_rate', label: 'Bounce', align: 'right' },
  { key: 'next_page', label: 'Próxima', align: 'left', sortable: false },
  { key: 'conversion', label: 'Conv.', align: 'right' },
  { key: 'delta_views', label: 'Δ', align: 'right' },
]

function PagesTab({ period, navigate }) {
  const [sort, setSort] = useState('views')
  const [order, setOrder] = useState('desc')
  const [search, setSearch] = useState('')
  const [grouped, setGrouped] = useState(false)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(25)
  const dSearch = useDebounce(search, 300)

  const { data, loading, error } = useAsync(
    () => admin.aaPages({ period, ...(dSearch ? { search: dSearch } : {}) }), [period, dSearch])

  useEffect(() => { setPage(1) }, [period, dSearch, sort, order, grouped])

  function onSort(key) {
    if (sort === key) setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    else { setSort(key); setOrder('desc') }
  }
  function rowFor(p) {
    return (
      <tr key={p.path} className="cursor-pointer border-t border-klarim-border hover:bg-klarim-bg"
        onClick={() => navigate('events', { path: p.path })} title={`Ver eventos de ${p.path}`}>
        <td className="max-w-xs truncate px-3 py-2" title={p.path}>{trunc(p.path)}</td>
        <td className="px-3 py-2 text-right">{fmtNum(p.views)}</td>
        <td className="px-3 py-2 text-right text-klarim-muted">{fmtNum(p.sessions)}</td>
        <td className="px-3 py-2 text-right" style={{ color: bounceColor(p.bounce_rate) }}>{p.bounce_rate}%</td>
        <td className="max-w-[10rem] truncate px-3 py-2 text-klarim-muted" title={p.next_page || ''}>{trunc(p.next_page || '—', 22)}</td>
        <td className="px-3 py-2 text-right">{p.conversion > 0 ? <Badge color="#00D26A">{p.conversion}%</Badge> : <span className="text-klarim-muted">0%</span>}</td>
        <td className="px-3 py-2 text-right"><span style={{ color: deltaMeta(p.delta_views).color }}>{deltaMeta(p.delta_views).text}</span></td>
      </tr>
    )
  }

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />
  const allPages = sortRows(data.pages || [], sort, order)

  return (
    <div className="space-y-4">
      <Card title="Páginas visitadas">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar página…"
            className="w-64 max-w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text" />
          <label className="flex items-center gap-2 text-sm text-klarim-muted">
            <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
            Agrupar por tipo
          </label>
        </div>
      </Card>

      {grouped ? (
        <div className="space-y-2">
          {(data.groups || []).slice().sort((a, b) => b.total_views - a.total_views).map((g) => (
            <PageGroup key={g.group} group={g} rows={allPages.filter((p) => p.group === g.group)}
              columns={PAGE_COLUMNS} sort={sort} order={order} onSort={onSort} rowFor={rowFor} />
          ))}
        </div>
      ) : (
        <>
          <SortableTable columns={PAGE_COLUMNS} rows={paginate(allPages, page, limit).slice}
            sort={sort} order={order} onSort={onSort} renderRow={rowFor} empty="Nenhuma página no período." />
          <PaginationBar page={page} pages={paginate(allPages, page, limit).pages}
            total={allPages.length} onPage={setPage} limit={limit} onLimit={setLimit} />
        </>
      )}
    </div>
  )
}

function PageGroup({ group, rows, columns, sort, order, onSort, rowFor }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm">
        <span><span aria-hidden="true">{open ? '▼' : '▶'}</span> <strong>{group.group}</strong></span>
        <span className="text-klarim-muted">{fmtNum(group.total_views)} views · {fmtNum(group.pages_count)} páginas</span>
      </button>
      {open && (
        <div className="border-t border-klarim-border p-2">
          <SortableTable columns={columns} rows={rows} sort={sort} order={order} onSort={onSort}
            renderRow={rowFor} empty="Nenhuma página neste grupo." />
        </div>
      )}
    </div>
  )
}

// =========================================================================== #
// Aba 4 — Jornadas
// =========================================================================== #
const SECTOR_COLUMNS = [
  { key: 'sector', label: 'Setor', align: 'left' },
  { key: 'alerts_sent', label: 'Alertas', align: 'right' },
  { key: 'clicks', label: 'Cliques', align: 'right' },
  { key: 'click_rate', label: 'Taxa', align: 'right' },
  { key: 'scans', label: 'Scans', align: 'right' },
  { key: 'accounts', label: 'Contas', align: 'right' },
]

function JourneysTab({ period, navigate }) {
  const { data, loading, error } = useAsync(
    () => Promise.all([admin.aaJourneys(period, 10), admin.aaFunnelBySector(period)])
      .then(([j, s]) => ({ paths: j.paths || [], sectors: s.sectors || [] })), [period])

  return (
    <div className="space-y-6">
      {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
        <>
          <TopJourneys paths={data.paths} />
          <SectorFunnel sectors={data.sectors} />
        </>
      )}
      <SessionsDrilldown period={period} navigate={navigate} />
    </div>
  )
}

function TopJourneys({ paths }) {
  const max = Math.max(1, ...paths.map((p) => p.count))
  return (
    <Card title="Caminhos mais comuns">
      {paths.length === 0 ? <p className="text-sm text-klarim-muted">Sem jornadas no período.</p> : (
        <ol className="space-y-3">
          {paths.map((p, i) => (
            <li key={i}>
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 text-sm text-klarim-muted">{i + 1}.</span>
                  {p.sequence.map((step, j) => (
                    <span key={j} className="flex items-center gap-1.5">
                      {j > 0 && <span className="text-klarim-muted" aria-hidden="true">→</span>}
                      <span className="rounded px-2 py-0.5 text-xs"
                        style={{ background: `${STEP_COLOR[journeyStepKind(step)]}22`, color: STEP_COLOR[journeyStepKind(step)] }}>
                        {step}
                      </span>
                    </span>
                  ))}
                </div>
                <span className="shrink-0 text-sm text-klarim-muted">{fmtNum(p.count)} sessões</span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <div className="h-1.5 rounded-full bg-klarim-alert/40" style={{ width: `${(p.count / max) * 100}%`, minWidth: 4 }} />
                <span className="text-xs" style={{ color: p.conversion_rate > 0 ? '#00D26A' : '#8B949E' }}>
                  ({p.conversion_rate}% conv.)
                </span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

function SectorFunnel({ sectors }) {
  const [sort, setSort] = useState('click_rate')
  const [order, setOrder] = useState('desc')
  const { shown, hidden } = filterSectors(sectors, 20)
  const rows = sortRows(shown, sort, order)
  function onSort(key) {
    if (sort === key) setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    else { setSort(key); setOrder('desc') }
  }
  return (
    <Card title="Conversão por setor">
      <SortableTable columns={SECTOR_COLUMNS} rows={rows} sort={sort} order={order} onSort={onSort}
        empty="Nenhum setor com alertas no período."
        renderRow={(s) => (
          <tr key={s.sector} className="border-t border-klarim-border">
            <td className="px-3 py-2">{s.sector}</td>
            <td className="px-3 py-2 text-right text-klarim-muted">{fmtNum(s.alerts_sent)}</td>
            <td className="px-3 py-2 text-right">{fmtNum(s.clicks)}</td>
            <td className="px-3 py-2 text-right font-semibold" style={{ color: clickRateColor(s.click_rate) }}>{s.click_rate}%</td>
            <td className="px-3 py-2 text-right">{fmtNum(s.scans)}</td>
            <td className="px-3 py-2 text-right">{fmtNum(s.accounts)}</td>
          </tr>
        )} />
      {hidden > 0 && <p className="mt-2 text-xs text-klarim-muted">e mais {hidden} setor(es) com menos alertas.</p>}
    </Card>
  )
}

function SessionsDrilldown({ period, navigate }) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true; setLoading(true)
    admin.aaSessions({ period, page, limit: 10 }).then((d) => { if (alive) { setData(d); setLoading(false) } })
      .catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [period, page])
  useEffect(() => { setPage(1) }, [period])
  // convertidas primeiro, depois mais recentes
  const sessions = data ? sortRows([...(data.sessions || [])].map((s, i) => ({ ...s, _i: i })), 'converted', 'desc') : []
  return (
    <Card title="Sessões recentes">
      <div className="mb-2 flex justify-end">
        <button onClick={() => navigate('events', { group: 'session' })}
          className="text-sm text-klarim-alert hover:underline">ver todas →</button>
      </div>
      {loading ? <Loading /> : sessions.length === 0 ? <p className="text-sm text-klarim-muted">Sem sessões.</p> : (
        <div className="space-y-2">{sessions.map((s) => <SessionCard key={s.session_id} s={s} />)}</div>
      )}
      {data?.pagination && (
        <div className="mt-3">
          <PaginationBar page={page} pages={data.pagination.pages} total={data.pagination.total} onPage={setPage} />
        </div>
      )}
    </Card>
  )
}
