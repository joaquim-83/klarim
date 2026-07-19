import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, StatCard, Loading, ErrorBox, Button, Badge } from './ui'
import AdminShell from './AdminShell'

// KL-84 — Taxonomia aberta de setores. Duas seções: (1) EMERGENTES (propostos pela IA,
// aguardando curadoria — aprovar / merge / rejeitar); (2) taxonomia viva (official+approved).
// Ilha admin client:only (CSP relaxada no /painel).

const STATUS_COLOR = {
  official: '#8B949E', approved: '#00D26A', proposed: '#F0C000',
  rejected: '#F85149', merged: '#58A6FF',
}
const STATUS_LABEL = {
  official: 'Oficial', approved: 'Aprovado', proposed: 'Proposto',
  rejected: 'Rejeitado', merged: 'Mesclado',
}

function StatusBadge({ status }) {
  return <Badge color={STATUS_COLOR[status] || '#8B949E'}>{STATUS_LABEL[status] || status}</Badge>
}

// Cartão de um setor emergente: exemplos sob demanda + ações de curadoria.
function EmergingCard({ sector, taxonomy, onDone }) {
  const [examples, setExamples] = useState(null)
  const [loadingEx, setLoadingEx] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [mergeInto, setMergeInto] = useState('')

  async function loadExamples() {
    setLoadingEx(true)
    try {
      const r = await admin.sectorExamples(sector.slug, 5)
      setExamples(r.examples || [])
    } catch (e) { setErr(e.message) } finally { setLoadingEx(false) }
  }

  async function act(fn, label) {
    setBusy(label); setErr('')
    try { await fn(); onDone() } catch (e) { setErr(e.message); setBusy('') }
  }

  return (
    <div className="rounded-xl border border-klarim-border bg-klarim-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-klarim-text">{sector.label}</span>
            <StatusBadge status={sector.status} />
          </div>
          <div className="mt-0.5 text-xs text-klarim-muted">
            <code>{sector.slug}</code> · macro: {sector.macro_sector} · {sector.site_count || 0} sites
          </div>
        </div>
        <button onClick={loadExamples} className="text-xs text-klarim-alert underline">
          {loadingEx ? 'Carregando…' : 'Ver exemplos'}
        </button>
      </div>

      {examples && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {examples.length === 0 && <span className="text-xs text-klarim-muted">Sem exemplos.</span>}
          {examples.map((d) => (
            <a key={d} href={`/site/${d}`} target="_blank" rel="noreferrer"
               className="rounded bg-klarim-bg px-2 py-1 text-xs text-klarim-text hover:text-klarim-alert">
              {d}
            </a>
          ))}
        </div>
      )}

      {err && <p className="mt-2 text-xs text-klarim-fail">{err}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={!!busy}
                onClick={() => act(() => admin.approveSector(sector.slug), 'approve')}>
          {busy === 'approve' ? '…' : 'Aprovar'}
        </Button>
        <Button variant="danger" disabled={!!busy}
                onClick={() => act(() => admin.rejectSector(sector.slug), 'reject')}>
          {busy === 'reject' ? '…' : 'Rejeitar'}
        </Button>
        <div className="flex items-center gap-1">
          <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}
                  className="rounded-lg border border-klarim-border bg-klarim-bg px-2 py-1.5 text-xs text-klarim-text outline-none">
            <option value="">Mesclar em…</option>
            {taxonomy.map((t) => <option key={t.slug} value={t.slug}>{t.label}</option>)}
          </select>
          <Button variant="ghost" disabled={!mergeInto || !!busy}
                  onClick={() => act(() => admin.mergeSector(sector.slug, mergeInto), 'merge')}>
            {busy === 'merge' ? '…' : 'Mesclar'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function SetoresPage() {
  const { data, error, loading, reload } = useAsync(() => admin.sectors('all'), [])
  const [macroFilter, setMacroFilter] = useState('')

  const stats = data?.stats || {}
  const emerging = data?.emerging || []
  const taxonomy = data?.taxonomy || []
  const byStatus = stats.by_status || {}
  const macros = [...new Set(taxonomy.map((t) => t.macro_sector))].sort()
  const shown = macroFilter ? taxonomy.filter((t) => t.macro_sector === macroFilter) : taxonomy

  return (
    <AdminShell active="setores">
      <div className="mb-4">
        <h1 className="text-xl font-bold text-klarim-text">Taxonomia de setores</h1>
        <p className="text-sm text-klarim-muted">
          Setores emergentes propostos pela IA + a taxonomia viva. Aprovar publica o setor em
          <code className="mx-1">/setores</code>; mesclar move os sites para um setor existente.
        </p>
      </div>

      {loading && <Loading label="Carregando setores…" />}
      {error && <ErrorBox message={error} />}

      {data && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <StatCard label="Classificados" value={(stats.total_classified || 0).toLocaleString('pt-BR')} />
            <StatCard label="Em 'outro'" value={`${(stats.outro_count || 0).toLocaleString('pt-BR')}`}
                      sub={`${stats.outro_pct || 0}%`} accent="#F0C000" />
            <StatCard label="Emergentes" value={byStatus.proposed || 0} accent="#F0C000" />
            <StatCard label="Aprovados" value={byStatus.approved || 0} accent="#00D26A" />
            <StatCard label="Oficiais" value={byStatus.official || 0} />
          </div>

          <Card title={`Setores emergentes (${emerging.length})`} className="mb-6">
            {emerging.length === 0 ? (
              <p className="text-sm text-klarim-muted">
                Nenhum setor proposto no momento. A IA sugere novos setores quando um negócio não
                encaixa em nenhum existente.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {emerging.map((s) => (
                  <EmergingCard key={s.slug} sector={s} taxonomy={taxonomy} onDone={reload} />
                ))}
              </div>
            )}
          </Card>

          <Card title={`Taxonomia viva (${taxonomy.length})`}>
            <div className="mb-3">
              <select value={macroFilter} onChange={(e) => setMacroFilter(e.target.value)}
                      className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text outline-none">
                <option value="">Todas as macros</option>
                {macros.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-sm">
                <thead className="text-xs uppercase text-klarim-muted">
                  <tr>
                    <th className="py-2">Setor</th><th>Slug</th><th>Macro</th>
                    <th className="text-right">Sites</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((t) => (
                    <tr key={t.slug} className="border-t border-klarim-border">
                      <td className="py-2 text-klarim-text">
                        {t.slug === 'outro'
                          ? t.label
                          : <a href={`/setor/${t.slug}`} target="_blank" rel="noreferrer"
                               className="hover:text-klarim-alert">{t.label}</a>}
                      </td>
                      <td className="text-klarim-muted"><code>{t.slug}</code></td>
                      <td className="text-klarim-muted">{t.macro_sector}</td>
                      <td className="text-right text-klarim-text">{(t.site_count || 0).toLocaleString('pt-BR')}</td>
                      <td><StatusBadge status={t.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </AdminShell>
  )
}
