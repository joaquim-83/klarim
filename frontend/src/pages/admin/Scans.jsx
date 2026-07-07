import { useState } from 'react'
import { Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, Loading, ErrorBox, Button, SemaphoreDot, SourceBadge, Pagination, formatDate } from '../../components/admin/ui'

const PAGE_SIZE = 25
const SOURCES = ['public', 'discovery', 'admin', 'manual', 'rescan']

export default function Scans() {
  const [semaphore, setSemaphore] = useState('')
  const [source, setSource] = useState('')
  const [scoreMin, setScoreMin] = useState('')
  const [scoreMax, setScoreMax] = useState('')
  const [page, setPage] = useState(0)

  const { data, loading, error } = useAsync(
    () => admin.scans({
      score_min: scoreMin, score_max: scoreMax, source, limit: PAGE_SIZE,
    }),
    [scoreMin, scoreMax, source, page],
  )

  // O filtro de semáforo é aplicado no cliente (a API filtra por score).
  const rows = (data?.scans || []).filter((s) => !semaphore || s.semaphore === semaphore)

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Scans</h1>

      <div className="flex flex-wrap gap-2">
        <select value={semaphore} onChange={(e) => setSemaphore(e.target.value)}
          className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert">
          <option value="">Todos os semáforos</option>
          <option value="verde">🟢 Verde</option>
          <option value="amarelo">🟡 Amarelo</option>
          <option value="vermelho">🔴 Vermelho</option>
        </select>
        <input type="number" value={scoreMin} onChange={(e) => { setScoreMin(e.target.value); setPage(0) }}
          placeholder="Score mín." className="w-28 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert" />
        <input type="number" value={scoreMax} onChange={(e) => { setScoreMax(e.target.value); setPage(0) }}
          placeholder="Score máx." className="w-28 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert" />
        <select value={source} onChange={(e) => { setSource(e.target.value); setPage(0) }}
          className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert">
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
                {rows.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-klarim-muted">Nenhum scan.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={(data?.scans || []).length === PAGE_SIZE} />
      </Card>
    </div>
  )
}
