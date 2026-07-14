import { useState } from 'react'
import { Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import {
  Card, StatCard, Loading, ErrorBox, Badge, Button, Pagination, formatDate, relativeTime,
} from '../../components/admin/ui'

const PAGE_SIZE = 25

// Cores por classificação PQL (frio → quente → qualificado).
export const CLASS_META = {
  cold: { label: 'Frio', color: '#58A6FF' },
  warm: { label: 'Morno', color: '#F0C000' },
  hot: { label: 'Quente', color: '#FF6B35' },
  pql: { label: 'PQL', color: '#00D26A' },
}

export function ClassBadge({ classification }) {
  const m = CLASS_META[classification] || { label: classification || '—', color: '#8B949E' }
  return <Badge color={m.color}>{m.label}</Badge>
}

export default function Leads() {
  const [page, setPage] = useState(0)
  const [classification, setClassification] = useState('')
  const [hasAccount, setHasAccount] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [recalcMsg, setRecalcMsg] = useState('')
  const [recalcing, setRecalcing] = useState(false)

  const stats = useAsync(() => admin.leadStats(), [])
  const { data, loading, error, reload } = useAsync(
    () => admin.leads({
      limit: PAGE_SIZE, offset: page * PAGE_SIZE,
      classification: classification || undefined,
      has_account: hasAccount || undefined,
      search: query || undefined,
    }),
    [page, classification, hasAccount, query],
  )
  const rows = data?.leads || []
  const byClass = data?.by_classification || {}

  function pick(cls) {
    setClassification((c) => (c === cls ? '' : cls))
    setPage(0)
  }

  function submitSearch(e) {
    e.preventDefault()
    setQuery(search.trim())
    setPage(0)
  }

  async function recalc() {
    setRecalcing(true)
    setRecalcMsg('')
    try {
      const r = await admin.recalcLeads()
      setRecalcMsg(`${r.recalculated} leads recalculados.`)
      stats.reload?.()
      reload?.()
    } catch (err) {
      setRecalcMsg(err.message || 'Falha ao recalcular.')
    } finally {
      setRecalcing(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Leads</h1>
        <div className="flex items-center gap-3">
          {recalcMsg && <span className="text-xs text-klarim-muted">{recalcMsg}</span>}
          <Button onClick={recalc} disabled={recalcing}>
            {recalcing ? 'Recalculando…' : '↻ Recalcular scores'}
          </Button>
        </div>
      </div>

      {/* Cards de classificação — clicáveis para filtrar. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(['pql', 'hot', 'warm', 'cold']).map((c) => {
          const active = classification === c
          return (
            <button key={c} onClick={() => pick(c)}
              className={`rounded-xl border bg-klarim-surface p-4 text-left transition ${
                active ? 'border-klarim-alert' : 'border-klarim-border hover:border-klarim-border/80'
              }`}>
              <div className="text-xs uppercase tracking-wide text-klarim-muted">{CLASS_META[c].label}</div>
              <div className="mt-1 text-2xl font-extrabold" style={{ color: CLASS_META[c].color }}>
                {stats.data?.by_classification?.[c] ?? byClass[c] ?? 0}
              </div>
              {active && <div className="mt-0.5 text-[11px] text-klarim-alert">filtrando ✓</div>}
            </button>
          )
        })}
      </div>

      {/* Métricas gerais + analytics KL-57. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Total" value={stats.data?.total ?? 0} />
        <StatCard label="Com conta" value={stats.data?.with_account ?? 0} accent="#00D26A" />
        <StatCard label="Monitorando" value={stats.data?.with_monitoring ?? 0} accent="#00D26A" />
        <StatCard label="Taxa PQL" value={`${stats.data?.pql_rate ?? 0}%`} accent="#FF6B35" />
        <StatCard label="Score médio" value={stats.data?.avg_lead_score ?? 0} />
        <StatCard label="Corporativos" value={stats.data?.corporate_emails ?? 0} />
      </div>

      {/* Setores com maior dor (menor avg worst_score) — insight de aquisição (KL-57). */}
      {stats.data?.pain_sectors?.length > 0 && (
        <Card title="Setores com maior dor (menor score médio)">
          <div className="flex flex-wrap gap-2">
            {stats.data.pain_sectors.map((s) => (
              <span key={s.sector} className="rounded-lg border border-klarim-border px-3 py-1.5 text-xs">
                <span className="text-klarim-text">{s.sector}</span>
                <span className="ml-2 font-semibold text-klarim-fail">{s.avg_worst_score}</span>
              </span>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <form onSubmit={submitSearch} className="flex flex-1 gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar por e-mail ou domínio…"
              className="min-w-0 flex-1 rounded-lg border border-klarim-border bg-klarim-bg px-3 py-1.5 text-sm text-klarim-text placeholder:text-klarim-muted"
            />
            <Button type="submit">Buscar</Button>
          </form>
          <select
            value={hasAccount}
            onChange={(e) => { setHasAccount(e.target.value); setPage(0) }}
            className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-1.5 text-sm text-klarim-text"
          >
            <option value="">Todos</option>
            <option value="true">Com conta</option>
            <option value="false">Sem conta</option>
          </select>
        </div>

        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">E-mail</th>
                  <th className="py-2 pr-3">Classe</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Scans</th>
                  <th className="py-2 pr-3">Pior score</th>
                  <th className="py-2 pr-3">Setor</th>
                  <th className="py-2 pr-3">Conta</th>
                  <th className="py-2">Atividade</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.id} className="border-t border-klarim-border hover:bg-klarim-border/20">
                    <td className="py-2 pr-3">
                      <Link to={`/painel/leads/${l.id}`} className="text-klarim-text hover:underline">
                        {l.email}
                      </Link>
                      {l.is_corporate_email && <span className="ml-1 text-[10px] text-klarim-muted">🏢</span>}
                      {l.opted_out && <span className="ml-1 text-[10px] text-klarim-fail">opt-out</span>}
                    </td>
                    <td className="py-2 pr-3"><ClassBadge classification={l.classification} /></td>
                    <td className="py-2 pr-3 font-semibold" style={{ color: CLASS_META[l.classification]?.color }}>{l.lead_score}</td>
                    <td className="py-2 pr-3 text-klarim-muted">{l.total_scans}</td>
                    <td className="py-2 pr-3 text-klarim-muted">{l.worst_score ?? '—'}</td>
                    <td className="py-2 pr-3 text-xs text-klarim-muted">{l.sector || '—'}</td>
                    <td className="py-2 pr-3">{l.has_account ? '✅' : '—'}{l.has_monitoring ? ' 👁' : ''}</td>
                    <td className="py-2 text-xs text-klarim-muted" title={formatDate(l.last_activity_at)}>{relativeTime(l.last_activity_at)}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={8} className="py-8 text-center text-klarim-muted">Nenhum lead.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-2 text-xs text-klarim-muted">{data?.total ?? 0} lead(s) no filtro atual.</div>
        <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
      </Card>
    </div>
  )
}
