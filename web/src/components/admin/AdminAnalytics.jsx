import { useEffect, useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Badge, DomainLink } from './ui'
import AdminShell from './AdminShell'
import PaginationBar from './analytics/PaginationBar'
import SortableTable from './analytics/SortableTable'
import SessionCard, { CAMPAIGN_COLOR, EV_COLOR } from './analytics/SessionCard'
import {
  sortRows, paginate, journeyStepKind, STEP_COLOR, bounceColor, clickRateColor,
  deltaMeta, filterSectors, parseTabHash, buildTabHash,
  DATA_SOURCE, dailySeriesToTrend, sparkFromDaily, serverFunnelStages,
  retentionBars, heatColor, DOW_LABELS,
} from '../../lib/admin/analyticsUtils'

// KL-83 — Analytics admin (Prompts 1+2). 4 abas: Visão geral, Eventos, Páginas, Jornadas.
// Uma chamada por endpoint; cache no backend (5 min). Lazy: cada aba só busca quando ativa.
const PERIODS = [
  { key: 'today', label: 'Hoje' }, { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' }, { key: '90d', label: '90 dias' },
]
const TABS = [
  { key: 'overview', label: 'Visão geral' }, { key: 'behavior', label: 'Comportamento' },
  { key: 'events', label: 'Eventos' }, { key: 'pages', label: 'Páginas' },
  { key: 'journeys', label: 'Jornadas' },
]
const TAB_KEYS = TABS.map((t) => t.key)
const TOOLTIP_STYLE = { backgroundColor: '#161B22', border: '1px solid #30363D', borderRadius: 8, color: '#E6EDF3' }

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
  const [includeBots, setIncludeBots] = useState(false)  // KL-64: default só humanos
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
          <div className="flex flex-wrap items-center gap-3">
            {/* KL-64: toggle "incluir bots/pre-fetch" (default OFF = só humanos verificados). */}
            <label className="flex cursor-pointer items-center gap-2 text-xs text-klarim-muted">
              <input type="checkbox" checked={includeBots}
                onChange={(e) => setIncludeBots(e.target.checked)} className="accent-klarim-alert" />
              Incluir bots/pre-fetch
            </label>
            <div className="flex gap-1 rounded-lg border border-klarim-border bg-klarim-surface p-1">
              {PERIODS.map((p) => (
                <button key={p.key} onClick={() => setPeriod(p.key)}
                  className={`rounded px-3 py-1 text-sm ${period === p.key ? 'bg-klarim-alert text-klarim-bg font-semibold' : 'text-klarim-muted hover:text-klarim-text'}`}>
                  {p.label}
                </button>
              ))}
            </div>
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

        {nav.tab === 'overview' && (
          <OverviewTab period={period} includeBots={includeBots} navigate={navigate}
            funnelSource={nav.params.funnel === 'server' ? 'server' : 'email'} />
        )}
        {nav.tab === 'behavior' && <BehaviorTab period={period} />}
        {nav.tab === 'events' && (
          <EventsTab key={JSON.stringify(nav.params)} period={period} initialParams={nav.params}
            includeBots={includeBots} />
        )}
        {nav.tab === 'pages' && <PagesTab period={period} navigate={navigate} includeBots={includeBots} />}
        {nav.tab === 'journeys' && <JourneysTab period={period} navigate={navigate} includeBots={includeBots} />}
      </div>
    </AdminShell>
  )
}

// =========================================================================== #
// Aba 1 — Visão geral (KL-92 P2: access_log = fonte primária dos KPIs de visitante)
// =========================================================================== #
const SERVER_TREND_COLORS = { visitors_br: '#58A6FF', scans: '#FF6B35', accounts: '#00D26A' }
const SERVER_TREND_LABEL = { visitors_br: 'Visitantes BR', scans: 'Scans', accounts: 'Contas' }

// Badge discreto de fonte do dado (📡 server / 📱 tracker) — ajuda o admin durante a transição.
function DataSourceBadge({ source }) {
  const meta = DATA_SOURCE[source]
  if (!meta) return null
  return (
    <span title={meta.title} className="shrink-0 text-[10px] font-normal text-klarim-muted">
      {meta.icon} {meta.label}
    </span>
  )
}

function OverviewTab({ period, includeBots, navigate, funnelSource }) {
  // Fontes independentes: se uma falhar, a outra ainda renderiza (KL-92 P2).
  const server = useAsync(() => admin.aaServerMetrics(period), [period])
  const tracker = useAsync(() => admin.aaMetrics(period, includeBots), [period, includeBots])
  const emailFunnel = useAsync(() => admin.aaFunnel(period, includeBots), [period, includeBots])

  return (
    <div className="space-y-6">
      <KpiGrid server={server} tracker={tracker} />
      <TrendBlock server={server} />
      <FunnelBlock server={server} emailFunnel={emailFunnel} funnelSource={funnelSource} navigate={navigate} />
    </div>
  )
}

function fmtVal(v, suffix = '') {
  if (v == null) return '—'
  return `${typeof v === 'number' ? v.toLocaleString('pt-BR') : v}${suffix}`
}

function KpiGrid({ server, tracker }) {
  const sm = server.data; const daily = sm?.daily_series; const tm = tracker.data?.metrics
  const sState = { loading: server.loading, error: server.error }
  const tState = { loading: tracker.loading, error: tracker.error }
  const alertClick = tm?.alert_click_rate
  const cards = [
    { label: 'Visitantes BR', value: sm?.visitors_br, spark: sparkFromDaily(daily, 'visitors_br'), source: 'server', state: sState },
    { label: 'Scans', value: sm?.scans, spark: sparkFromDaily(daily, 'scans'), source: 'server', state: sState },
    { label: 'Contas criadas', value: sm?.accounts, spark: sparkFromDaily(daily, 'accounts'), source: 'server', state: sState },
    { label: 'Bots filtrados', value: sm?.bots_filtered, source: 'server', state: sState },
    { label: 'Conversão', value: sm?.server_funnel?.conversion_rates?.overall, suffix: '%', source: 'server', state: sState },
    { label: 'Clique em alertas', value: alertClick?.value, suffix: '%', changePct: alertClick?.change_pct, spark: alertClick?.sparkline || [], source: 'tracker', state: tState },
  ]
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((c) => <KpiCard key={c.label} {...c} />)}
    </div>
  )
}

function KpiCard({ label, value, suffix, spark, changePct, source, state }) {
  const data = spark ? spark.map((v, i) => ({ i, v: v ?? 0 })) : []
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-klarim-muted">{label}</p>
        <DataSourceBadge source={source} />
      </div>
      {state?.loading ? <div className="mt-2 h-8 animate-pulse rounded bg-klarim-border/40" />
        : state?.error ? <p className="mt-2 text-xs text-klarim-fail" title={state.error}>indisponível</p>
          : (
            <>
              <div className="mt-1 flex items-end justify-between gap-2">
                <span className="text-2xl font-bold">{fmtVal(value, suffix || '')}</span>
                {changePct != null && <span className="text-xs">{fmtDelta(changePct)}</span>}
              </div>
              <div style={{ width: '100%', height: 40 }} className="mt-2">
                {data.length > 1 && (
                  <ResponsiveContainer>
                    <LineChart data={data}><Line type="monotone" dataKey="v" stroke="#FF6B35" strokeWidth={1.5} dot={false} /></LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </>
          )}
    </div>
  )
}

function TrendBlock({ server }) {
  const rows = dailySeriesToTrend(server.data?.daily_series)
  return (
    <Card title={<span className="flex items-center justify-between">Tendência <DataSourceBadge source="server" /></span>}>
      {server.loading ? <Loading /> : server.error ? <ErrorBox message={server.error} /> : (
        <div style={{ width: '100%', height: 250 }}>
          <ResponsiveContainer>
            <LineChart data={rows}>
              <XAxis dataKey="date" stroke="#8B949E" fontSize={10} tickFormatter={(d) => (d || '').slice(5)} />
              <YAxis stroke="#8B949E" fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              {['visitors_br', 'scans', 'accounts'].map((k) => (
                <Line key={k} type="monotone" dataKey={k} name={SERVER_TREND_LABEL[k]}
                  stroke={SERVER_TREND_COLORS[k]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  )
}

function FunnelBlock({ server, emailFunnel, funnelSource, navigate }) {
  const isServer = funnelSource === 'server'
  const toggle = (key, icon, label) => (
    <button onClick={() => navigate('overview', { funnel: key })}
      className={`rounded px-2 py-0.5 text-xs ${(key === 'server') === isServer ? 'bg-klarim-alert text-klarim-bg font-semibold' : 'text-klarim-muted hover:text-klarim-text'}`}>
      {icon} {label}
    </button>
  )
  return (
    <Card title={
      <span className="flex items-center justify-between gap-2">
        <span>Funil de conversão <DataSourceBadge source={isServer ? 'server' : 'tracker'} /></span>
        <span className="flex gap-1 rounded-lg border border-klarim-border p-0.5">
          {toggle('email', '📱', 'email')}{toggle('server', '📡', 'server')}
        </span>
      </span>
    }>
      {isServer
        ? (server.loading ? <Loading /> : server.error ? <ErrorBox message={server.error} />
          : <ServerFunnelBars stages={serverFunnelStages(server.data?.server_funnel)} />)
        : (emailFunnel.loading ? <Loading /> : emailFunnel.error ? <ErrorBox message={emailFunnel.error} />
          : <><EmailFunnelBars stages={emailFunnel.data?.stages || []} /><FunnelLegend stages={emailFunnel.data?.stages || []} /></>)}
    </Card>
  )
}

function EmailFunnelBars({ stages }) {
  const max = Math.max(1, ...stages.map((s) => s.total))
  return (
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
  )
}

function FunnelLegend({ stages }) {
  return (
    <div className="mt-3 flex flex-wrap gap-3 text-xs text-klarim-muted">
      {Object.entries(CAMPAIGN_COLOR).map(([c, color]) => (
        <span key={c} className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />{c}
        </span>
      ))}
      {stages.some((s) => s.bottleneck) && <span className="text-klarim-fail">▮ gargalo</span>}
    </div>
  )
}

function ServerFunnelBars({ stages }) {
  const max = Math.max(1, ...stages.map((s) => s.total))
  return (
    <div className="space-y-1">
      {stages.map((s, i) => (
        <div key={s.key}>
          {i > 0 && (
            <p className="py-0.5 text-center text-xs text-klarim-muted">
              {s.rate != null ? `${s.rate}% ↓` : ''}
            </p>
          )}
          <div className="flex items-center gap-3">
            <span className="w-32 shrink-0 text-right text-xs text-klarim-muted">{s.label}</span>
            <div className="h-7 flex-1 overflow-hidden rounded" style={{ background: '#0D1117' }}>
              <div className="h-full rounded bg-klarim-alert/70"
                style={{ width: `${Math.max((s.total / max) * 100, 3)}%`, minWidth: 40 }} />
            </div>
            <span className="w-14 shrink-0 text-sm font-semibold">{fmtNum(s.total)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// =========================================================================== #
// Aba 2 — Eventos
// =========================================================================== #
const EVENT_TYPES = [
  'page_view', 'profile_view', 'scan_started', 'scan_completed', 'scan_anonymous',
  'code_requested', 'account_created_alert', 'cta_clicked', 'payment_created', 'payment_completed',
]

function EventsTab({ period, initialParams = {}, includeBots }) {
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
    ...(includeBots ? { include_bots: true } : {}),
  }), [period, page, limit, types, dDomain, campaign, dPath, includeBots])

  useEffect(() => { setPage(1) }, [period, types, dDomain, campaign, dPath, groupBy, limit, includeBots])

  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    const call = groupBy
      ? admin.aaSessions({ period, page, limit, ...(includeBots ? { include_bots: true } : {}) })
      : admin.aaEvents(filters)
    call.then((d) => { if (alive) { setData(d); setLoading(false) } })
      .catch((e) => { if (alive) { setError(String(e.message || e)); setLoading(false) } })
    return () => { alive = false }
  }, [filters, groupBy])

  function toggleType(t) {
    setTypes((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t])
  }

  // KL-64: export SERVER-SIDE (streaming) respeitando os filtros ativos + is_human — o browser
  // baixa direto (adminDownload envia o Bearer e lê o filename do Content-Disposition). Sem
  // paginar client-side (antes travava com 5k+); teto de 10k no backend com aviso.
  async function exportCSV() {
    setExporting('Exportando…')
    try {
      const { page: _p, limit: _l, ...f } = filters
      await admin.aaEventsExport(f)
    } catch (e) {
      window.alert(`Falha ao exportar: ${e.message || e}`)
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
              <td className="px-3 py-2"><DomainLink domain={domainOf(e)} targetId={e.target_id} /></td>
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

function PagesTab({ period, navigate, includeBots }) {
  const [sort, setSort] = useState('views')
  const [order, setOrder] = useState('desc')
  const [search, setSearch] = useState('')
  const [grouped, setGrouped] = useState(false)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(25)
  const dSearch = useDebounce(search, 300)

  const { data, loading, error } = useAsync(
    () => admin.aaPages({ period, ...(dSearch ? { search: dSearch } : {}), ...(includeBots ? { include_bots: true } : {}) }),
    [period, dSearch, includeBots])

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

function JourneysTab({ period, navigate, includeBots }) {
  const { data, loading, error } = useAsync(
    () => Promise.all([admin.aaJourneys(period, 10, includeBots), admin.aaFunnelBySector(period, includeBots)])
      .then(([j, s]) => ({ paths: j.paths || [], sectors: s.sectors || [] })), [period, includeBots])

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

// =========================================================================== #
// Aba 5 — Comportamento (KL-92 P2: inteligência do access_log server-side)
// =========================================================================== #
function BehaviorTab({ period }) {
  // Fontes independentes (loading/erro isolados): server-metrics (domínios+heatmap) e
  // ip-behavior (multi-site+jornada+retenção). Uma falhar não zera a outra.
  const server = useAsync(() => admin.aaServerMetrics(period), [period])
  const behavior = useAsync(() => admin.aaIpBehavior(period), [period])
  return (
    <div className="space-y-6">
      <TopDomainsBlock server={server} />
      <MultiSiteBlock behavior={behavior} />
      <PreSignupJourneyBlock behavior={behavior} />
      <RetentionBlock behavior={behavior} />
      <HeatmapBlock server={server} />
    </div>
  )
}

function BlockBody({ state, empty, children }) {
  if (state.loading) return <Loading />
  if (state.error) return <ErrorBox message={state.error} />
  if (empty) return <p className="text-sm text-klarim-muted">{empty}</p>
  return children
}

function TopDomainsBlock({ server }) {
  const rows = server.data?.top_domains || []
  return (
    <Card title={<span className="flex items-center justify-between">Domínios mais consultados <DataSourceBadge source="server" /></span>}>
      <BlockBody state={server} empty={!server.loading && !server.error && rows.length === 0 ? 'Nenhum domínio consultado no período.' : ''}>
        <div className="overflow-x-auto rounded-lg border border-klarim-border">
          <table className="w-full text-sm">
            <thead className="bg-klarim-surface text-left text-xs text-klarim-muted">
              <tr>{['Domínio', 'Views', 'IPs únicos', 'Scans'].map((h) => <th key={h} scope="col" className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.domain} className="border-t border-klarim-border">
                  <td className="px-3 py-2">{d.domain}</td>
                  <td className="px-3 py-2 text-right">{fmtNum(d.views)}</td>
                  <td className="px-3 py-2 text-right text-klarim-muted">{fmtNum(d.unique_ips)}</td>
                  <td className="px-3 py-2 text-right">{d.scans > 0 ? <Badge color="#FF6B35">{fmtNum(d.scans)}</Badge> : <span className="text-klarim-muted">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </BlockBody>
    </Card>
  )
}

function MultiSiteBlock({ behavior }) {
  const rows = behavior.data?.top_multi_site_ips || []
  const avg = behavior.data?.avg_sites_per_visitor
  return (
    <Card title={<span className="flex items-center justify-between">Visitantes multi-site <DataSourceBadge source="server" /></span>}>
      <BlockBody state={behavior} empty={!behavior.loading && !behavior.error && rows.length === 0 ? 'Nenhum visitante consultou mais de um site.' : ''}>
        <p className="mb-2 text-xs text-klarim-muted">
          {fmtNum(behavior.data?.multi_site_visitors)} visitantes viram &gt;1 site · média {avg ?? 0} sites/visitante
        </p>
        <div className="overflow-x-auto rounded-lg border border-klarim-border">
          <table className="w-full text-sm">
            <thead className="bg-klarim-surface text-left text-xs text-klarim-muted">
              <tr>{['IP', 'País', 'Sites', 'Domínios'].map((h) => <th key={h} scope="col" className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.ip_masked}-${i}`} className="border-t border-klarim-border">
                  <td className="px-3 py-2 font-mono text-xs">{r.ip_masked}</td>
                  <td className="px-3 py-2 text-klarim-muted">{r.country || '—'}</td>
                  <td className="px-3 py-2 text-right font-semibold">{fmtNum(r.sites)}</td>
                  <td className="max-w-xs truncate px-3 py-2 text-klarim-muted" title={(r.domains || []).join(', ')}>{(r.domains || []).join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </BlockBody>
    </Card>
  )
}

function PreSignupJourneyBlock({ behavior }) {
  const t = behavior.data?.typical_journey
  const journeys = behavior.data?.pre_signup_journey || []
  return (
    <Card title={<span className="flex items-center justify-between">Jornada pré-signup <DataSourceBadge source="server" /></span>}>
      <BlockBody state={behavior} empty={!behavior.loading && !behavior.error && journeys.length === 0 ? 'Sem contas criadas com atividade rastreável no período.' : ''}>
        {t && (
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <TypicalStat label="1ª ação" value={t.most_common_first_action || '—'} mono />
            <TypicalStat label="Passos até signup" value={t.avg_steps_before_signup} />
            <TypicalStat label="Min. até signup" value={t.avg_minutes_to_signup} />
            <TypicalStat label="Via alerta" value={`${t.pct_via_alert ?? 0}%`} />
          </div>
        )}
        <div className="space-y-3">
          {journeys.slice(0, 8).map((j, i) => (
            <div key={i} className="rounded-lg border border-klarim-border p-3">
              <div className="mb-1 flex items-center justify-between text-xs text-klarim-muted">
                <span>{j.via_alert ? '📧 via alerta' : '🔍 orgânico'}{j.user_id ? ` · conta #${j.user_id}` : ''}</span>
                <span>{j.returned_within_7d ? <span className="text-klarim-pass">voltou em 7d</span> : 'não voltou'}</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {j.steps_before.map((s, k) => (
                  <span key={`b-${k}`} className="flex items-center gap-1.5">
                    {k > 0 && <span className="text-klarim-muted" aria-hidden="true">→</span>}
                    <span className="rounded bg-klarim-alert/15 px-2 py-0.5 font-mono text-[11px] text-klarim-alert">{s.endpoint}</span>
                  </span>
                ))}
                {j.steps_after.length > 0 && <span className="text-klarim-muted" aria-hidden="true">⋯</span>}
                {j.steps_after.map((s, k) => (
                  <span key={`a-${k}`} className="rounded bg-klarim-border/40 px-2 py-0.5 font-mono text-[11px] text-klarim-muted">{s.endpoint}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </BlockBody>
    </Card>
  )
}

function TypicalStat({ label, value, mono }) {
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-bg p-3">
      <p className="text-[10px] uppercase text-klarim-muted">{label}</p>
      <p className={`mt-0.5 truncate text-sm font-semibold ${mono ? 'font-mono' : ''}`} title={String(value)}>{value}</p>
    </div>
  )
}

function RetentionBlock({ behavior }) {
  const bars = retentionBars(behavior.data?.post_signup_retention)
  return (
    <Card title={<span className="flex items-center justify-between">Retenção pós-signup <DataSourceBadge source="server" /></span>}>
      <BlockBody state={behavior}>
        <div className="space-y-3">
          {bars.map((b) => (
            <div key={b.key} className="flex items-center gap-3">
              <span className="w-8 shrink-0 text-sm font-semibold">{b.label}</span>
              <div className="h-6 flex-1 overflow-hidden rounded" style={{ background: '#0D1117' }}>
                <div className="h-full rounded bg-klarim-pass/70" style={{ width: `${Math.max(b.pct, 1)}%` }} />
              </div>
              <span className="w-24 shrink-0 text-right text-xs text-klarim-muted">{b.pct}% ({b.returned}/{b.total})</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-klarim-muted">% de quem criou conta que retornou em até 1/3/7 dias (por IP).</p>
      </BlockBody>
    </Card>
  )
}

function HeatmapBlock({ server }) {
  const hm = server.data?.hourly_heatmap
  const grid = hm?.grid || []
  const max = hm?.max || 0
  return (
    <Card title={<span className="flex items-center justify-between">Mapa de calor por hora <DataSourceBadge source="server" /></span>}>
      <BlockBody state={server} empty={!server.loading && !server.error && max === 0 ? 'Sem tráfego no período.' : ''}>
        <div className="overflow-x-auto">
          <table className="border-separate" style={{ borderSpacing: 2 }}>
            <thead>
              <tr>
                <th className="pr-2" />
                {Array.from({ length: 24 }, (_, h) => (
                  <th key={h} className="text-[9px] font-normal text-klarim-muted">{h % 6 === 0 ? h : ''}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.map((row, dow) => (
                <tr key={dow}>
                  <td className="pr-2 text-right text-[10px] text-klarim-muted">{DOW_LABELS[dow]}</td>
                  {row.map((count, h) => (
                    <td key={h} title={`${DOW_LABELS[dow]} ${h}h: ${count}`}
                      style={{ width: 14, height: 14, background: heatColor(count, max), border: '1px solid #21262D', borderRadius: 2 }} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-klarim-muted">Requests humanos por dia da semana × hora (UTC). Máx: {fmtNum(max)}.</p>
      </BlockBody>
    </Card>
  )
}
