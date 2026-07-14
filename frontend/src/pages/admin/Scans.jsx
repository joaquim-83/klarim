import { useState } from 'react'
import { Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, Loading, ErrorBox, Button, SemaphoreDot, SourceBadge, Pagination, formatDate } from '../../components/admin/ui'

const PAGE_SIZE = 25
const SOURCES = ['public', 'discovery', 'admin', 'manual', 'rescan', 'demo']

// --- período (KL-56) — default: últimos 7 dias (não "tudo desde o início") --- //
function ymd(d) {
  return d.toISOString().slice(0, 10)
}
function daysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return ymd(d)
}
// from_date/to_date (YYYY-MM-DD) por período; undefined = sem filtro (Todos).
function periodRange(period, customFrom, customTo) {
  const today = ymd(new Date())
  if (period === 'today') return { from_date: today, to_date: today }
  if (period === '7d') return { from_date: daysAgo(6), to_date: today }
  if (period === '30d') return { from_date: daysAgo(29), to_date: today }
  if (period === 'custom') return { from_date: customFrom || undefined, to_date: customTo || undefined }
  return { from_date: undefined, to_date: undefined } // 'all'
}

export default function Scans() {
  const [semaphore, setSemaphore] = useState('')
  const [source, setSource] = useState('')
  const [scoreMin, setScoreMin] = useState('')
  const [scoreMax, setScoreMax] = useState('')
  const [period, setPeriod] = useState('7d')       // default: últimos 7 dias
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [page, setPage] = useState(0)

  const { from_date, to_date } = periodRange(period, customFrom, customTo)

  const { data, loading, error } = useAsync(
    () => admin.scans({
      score_min: scoreMin, score_max: scoreMax, source,
      from_date, to_date,
      limit: PAGE_SIZE, offset: page * PAGE_SIZE,   // KL-56: paginação real (offset)
    }),
    [scoreMin, scoreMax, source, from_date, to_date, page],
  )

  // O filtro de semáforo é aplicado no cliente (a API filtra por score).
  const rows = (data?.scans || []).filter((s) => !semaphore || s.semaphore === semaphore)

  const reset = () => setPage(0)
  const inputCls = 'rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert'

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Scans</h1>

      <div className="flex flex-wrap items-center gap-2">
        <select value={period} onChange={(e) => { setPeriod(e.target.value); reset() }} className={inputCls}>
          <option value="today">Hoje</option>
          <option value="7d">Últimos 7 dias</option>
          <option value="30d">Últimos 30 dias</option>
          <option value="custom">Personalizado</option>
          <option value="all">Todos</option>
        </select>
        {period === 'custom' && (
          <>
            <input type="date" value={customFrom} onChange={(e) => { setCustomFrom(e.target.value); reset() }}
              className={inputCls} aria-label="Data início" />
            <span className="text-sm text-klarim-muted">até</span>
            <input type="date" value={customTo} onChange={(e) => { setCustomTo(e.target.value); reset() }}
              className={inputCls} aria-label="Data fim" />
          </>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <select value={semaphore} onChange={(e) => setSemaphore(e.target.value)} className={inputCls}>
          <option value="">Todos os semáforos</option>
          <option value="verde">🟢 Verde</option>
          <option value="amarelo">🟡 Amarelo</option>
          <option value="vermelho">🔴 Vermelho</option>
        </select>
        <input type="number" value={scoreMin} onChange={(e) => { setScoreMin(e.target.value); reset() }}
          placeholder="Score mín." className={`w-28 ${inputCls}`} />
        <input type="number" value={scoreMax} onChange={(e) => { setScoreMax(e.target.value); reset() }}
          placeholder="Score máx." className={`w-28 ${inputCls}`} />
        <select value={source} onChange={(e) => { setSource(e.target.value); reset() }} className={inputCls}>
          <option value="">Todas as origens</option>
          {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">PASS / FAIL / INC.</th>
                  <th className="py-2 pr-3">Origem</th>
                  <th className="py-2 pr-3">Data</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className="border-t border-klarim-border">
                    <td className="py-2 pr-3 font-mono text-xs">{s.url}</td>
                    <td className="py-2 pr-3"><SemaphoreDot semaphore={s.semaphore} score={s.score} /></td>
                    <td className="py-2 pr-3 text-xs text-klarim-muted">
                      {s.pass_count}✓ / {s.fail_count}✗ / {s.inconclusive_count}?
                    </td>
                    <td className="py-2 pr-3"><SourceBadge source={s.source} /></td>
                    <td className="py-2 pr-3 text-xs text-klarim-muted">{formatDate(s.scanned_at)}</td>
                    <td className="py-2 text-right">
                      <Link to={`/painel/scans/${s.id}`}><Button>Ver detalhes</Button></Link>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-klarim-muted">Nenhum scan no período.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={(data?.scans || []).length === PAGE_SIZE} />
      </Card>
    </div>
  )
}
