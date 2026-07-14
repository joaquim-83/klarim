import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import {
  Card, Loading, ErrorBox, Button, SemaphoreDot, SourceBadge, formatDate, relativeTime,
} from '../../components/admin/ui'
import { CLASS_META, ClassBadge } from './Leads'

function Field({ label, children }) {
  return (
    <div>
      <div className="text-xs uppercase text-klarim-muted">{label}</div>
      <div className="mt-0.5 text-sm">{children}</div>
    </div>
  )
}

// Barra de progresso do score (0–100 clampado), colorida pela classificação.
function ScoreBar({ score, classification }) {
  const pct = Math.max(0, Math.min(100, score))
  const color = CLASS_META[classification]?.color || '#8B949E'
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-extrabold" style={{ color }}>{score}</span>
        <ClassBadge classification={classification} />
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-klarim-border">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

export default function LeadDetalhe() {
  const { id } = useParams()
  const { data: lead, loading, error, reload } = useAsync(() => admin.lead(id), [id])

  const [notes, setNotes] = useState('')
  const [tags, setTags] = useState('')
  const [optedOut, setOptedOut] = useState(false)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (lead) {
      setNotes(lead.notes || '')
      setTags((lead.tags || []).join(', '))
      setOptedOut(!!lead.opted_out)
    }
  }, [lead])

  async function save() {
    setSaving(true)
    setMsg('')
    try {
      await admin.updateLead(id, {
        notes,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        opted_out: optedOut,
      })
      setMsg('Salvo ✓')
      reload()
    } catch (e) {
      setMsg(e.message || 'Falha ao salvar.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />
  if (!lead) return <ErrorBox message="Lead não encontrado." />

  const breakdown = lead.score_breakdown || []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link to="/painel/leads" className="text-xs text-klarim-muted hover:underline">← Leads</Link>
          <h1 className="mt-1 break-all text-xl font-bold">{lead.email}</h1>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Score + composição */}
        <Card title="Lead score" className="lg:col-span-1">
          <ScoreBar score={lead.lead_score} classification={lead.classification} />
          <div className="mt-4 space-y-1.5">
            {breakdown.map((b) => (
              <div key={b.key}
                className={`flex items-center justify-between text-xs ${b.applied ? '' : 'opacity-40'}`}>
                <span className={b.applied ? 'text-klarim-text' : 'text-klarim-muted line-through'}>
                  {b.label}
                </span>
                <span className={`font-semibold ${b.points < 0 ? 'text-klarim-fail' : b.applied ? 'text-klarim-ok' : 'text-klarim-muted'}`}>
                  {b.applied ? (b.points > 0 ? `+${b.points}` : b.points) : '—'}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-klarim-muted">
            O score e a classificação são sempre calculados — não editáveis à mão.
          </p>
        </Card>

        {/* Dados */}
        <Card title="Dados do lead" className="lg:col-span-2">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="Classificação"><ClassBadge classification={lead.classification} /></Field>
            <Field label="E-mail corporativo">{lead.is_corporate_email ? 'Sim 🏢' : 'Não'}</Field>
            <Field label="Total de scans">{lead.total_scans}</Field>
            <Field label="URLs distintas">{(lead.urls_scanned || []).length}</Field>
            <Field label="Melhor score">{lead.best_score ?? '—'}</Field>
            <Field label="Pior score">{lead.worst_score ?? '—'}</Field>
            <Field label="Último score">{lead.last_score ?? '—'}</Field>
            <Field label="Setor">{lead.sector || '—'}</Field>
            <Field label="Plataforma">{lead.platform || '—'}</Field>
            <Field label="Conta">{lead.has_account ? '✅ Sim' : 'Não'}</Field>
            <Field label="Monitoramento">{lead.has_monitoring ? '👁 Ativo' : 'Não'}</Field>
            <Field label="Origem">{lead.source || '—'}</Field>
            <Field label="Primeiro scan">{formatDate(lead.first_scan_at)}</Field>
            <Field label="Última atividade">{relativeTime(lead.last_activity_at)}</Field>
            <Field label="Último domínio">{lead.last_domain || '—'}</Field>
          </div>
        </Card>
      </div>

      {/* Anotações + tags + opt-out (campos manuais) */}
      <Card title="Anotações e tags">
        <div className="space-y-3">
          <div>
            <label className="text-xs uppercase text-klarim-muted">Tags (separadas por vírgula)</label>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="ex.: prioridade, contato-feito"
              className="mt-1 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-1.5 text-sm text-klarim-text placeholder:text-klarim-muted"
            />
          </div>
          <div>
            <label className="text-xs uppercase text-klarim-muted">Notas</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={4}
              placeholder="Observações sobre o lead…"
              className="mt-1 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text placeholder:text-klarim-muted"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-klarim-text">
            <input type="checkbox" checked={optedOut} onChange={(e) => setOptedOut(e.target.checked)} />
            Opt-out (não contatar)
          </label>
          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={save} disabled={saving}>
              {saving ? 'Salvando…' : 'Salvar'}
            </Button>
            {msg && <span className="text-xs text-klarim-muted">{msg}</span>}
          </div>
        </div>
      </Card>

      {/* Scans do e-mail */}
      <Card title={`Scans deste e-mail (${(lead.scans || []).length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-klarim-muted">
                <th className="py-2 pr-3">Site</th>
                <th className="py-2 pr-3">Score</th>
                <th className="py-2 pr-3">Origem</th>
                <th className="py-2">Quando</th>
              </tr>
            </thead>
            <tbody>
              {(lead.scans || []).map((s) => (
                <tr key={s.id} className="border-t border-klarim-border">
                  <td className="py-2 pr-3 font-mono text-xs text-klarim-muted">
                    <Link to={`/painel/scans/${s.id}`} className="hover:underline">
                      {s.domain || s.url}
                    </Link>
                  </td>
                  <td className="py-2 pr-3"><SemaphoreDot semaphore={s.semaphore} score={s.score} /></td>
                  <td className="py-2 pr-3"><SourceBadge source={s.source} /></td>
                  <td className="py-2 text-xs text-klarim-muted">{formatDate(s.scanned_at)}</td>
                </tr>
              ))}
              {(lead.scans || []).length === 0 && (
                <tr><td colSpan={4} className="py-6 text-center text-klarim-muted">Nenhum scan.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
