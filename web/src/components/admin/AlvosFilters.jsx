// KL-104 P2 — barra de filtros avançados da página Alvos.
// Layout 2 linhas (linha 2 colapsável) + barra de totais. Estado/URL/params vivem no pai
// (AlvosPage); aqui é só a UI. Lógica pura em ../../lib/admin/alvosFilters.js.
import { useState } from 'react'
import { multiValues, toggleMultiValue, activeFilterCount, nextToggle } from '../../lib/admin/alvosFilters'

const SCORE_OPTS = [['90-100', '90-100 🟢'], ['50-89', '50-89'], ['0-49', '0-49 🔴'], ['sem', 'Sem score']]
const SEM_OPTS = [['verde', '🟢 Verde'], ['amarelo', '🟡 Amarelo'], ['vermelho', '🔴 Vermelho'], ['sem', 'Sem score']]
const LEAD_OPTS = [['alto', 'Alto (≥60)'], ['medio', 'Médio (30-59)'], ['baixo', 'Baixo (<30)'], ['sem', 'Sem lead score']]
const SCAN_OPTS = [['hoje', 'Escaneado hoje'], ['7d', 'Últimos 7 dias'], ['30d', 'Últimos 30 dias'], ['nunca', 'Nunca escaneado']]
const SITE_TYPE_OPTS = ['institucional', 'ecommerce', 'saas', 'portal', 'blog', 'parked', 'abandonado']

function Sel({ value, onChange, opts, allLabel }) {
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value || undefined)}
      className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert">
      <option value="">{allLabel}</option>
      {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

// Toggle 3-estados: todos (cinza) → sim (verde) → não (vermelho).
function Toggle3({ label, value, onChange }) {
  const cls = value === true ? 'border-klarim-ok bg-klarim-ok/15 text-klarim-ok'
    : value === false ? 'border-klarim-fail bg-klarim-fail/15 text-klarim-fail'
      : 'border-klarim-border bg-klarim-surface text-klarim-muted hover:text-klarim-text'
  const mark = value === true ? '✓ ' : value === false ? '✕ ' : ''
  return (
    <button onClick={() => onChange(nextToggle(value))} title="Clique: sim → não → todos"
      className={`rounded-lg border px-3 py-1.5 text-sm ${cls}`}>{mark}{label}</button>
  )
}

// Multi-select via <details> nativo (CSP-safe, sem estado de abertura). Checkbox por opção.
function Multi({ label, value, options, onToggle, searchable }) {
  const [q, setQ] = useState('')
  const sel = multiValues(value)
  const opts = searchable && q ? options.filter((o) => o.toLowerCase().includes(q.toLowerCase())) : options
  const summary = sel.length === 0 ? label : sel.length === 1 ? sel[0] : `${label}: ${sel.length}`
  const active = sel.length > 0
  return (
    <details className="relative">
      <summary className={`cursor-pointer list-none rounded-lg border px-3 py-1.5 text-sm ${active ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-text' : 'border-klarim-border bg-klarim-surface text-klarim-text'}`}>
        {summary} ▾
      </summary>
      <div className="absolute z-20 mt-1 max-h-64 w-56 overflow-auto rounded-lg border border-klarim-border bg-klarim-surface p-2 shadow-lg">
        {searchable && (
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar tecnologia…"
            className="mb-2 w-full rounded border border-klarim-border bg-klarim-bg px-2 py-1 text-xs text-klarim-text outline-none focus:border-klarim-alert" />
        )}
        {opts.map((o) => (
          <label key={o} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-klarim-text hover:bg-klarim-bg">
            <input type="checkbox" checked={sel.includes(o)} onChange={() => onToggle(o)} />
            <span className="truncate">{o}</span>
          </label>
        ))}
        {opts.length === 0 && <p className="px-1 py-1 text-xs text-klarim-muted">Nenhuma opção.</p>}
      </div>
    </details>
  )
}

export default function AlvosFilters({
  filters, setFilter, clearFilters, total, totalAll, techList,
  statusOpts, statusLabel, sectorOpts, platformOpts, sourceOpts,
}) {
  const [advanced, setAdvanced] = useState(() => activeFilterCount(filters) > 0 && !onlyBasic(filters))
  const active = activeFilterCount(filters)
  const fmt = (n) => (n ?? 0).toLocaleString('pt-BR')
  return (
    <div className="space-y-3">
      {/* Linha 1 — sempre visível */}
      <div className="flex flex-wrap items-center gap-2">
        <Sel value={filters.status} onChange={(v) => setFilter('status', v)} opts={statusOpts.map((s) => [s, statusLabel[s] || s])} allLabel="Todos os status" />
        <Sel value={filters.sector} onChange={(v) => setFilter('sector', v)} opts={sectorOpts.map((s) => [s, s])} allLabel="Todos os setores" />
        <Sel value={filters.score} onChange={(v) => setFilter('score', v)} opts={SCORE_OPTS} allLabel="Qualquer score" />
        <Sel value={filters.semaphore} onChange={(v) => setFilter('semaphore', v)} opts={SEM_OPTS} allLabel="Qualquer semáforo" />
        <Toggle3 label="Tem email" value={filters.has_email} onChange={(v) => setFilter('has_email', v)} />
        <input value={filters.search || ''} onChange={(e) => setFilter('search', e.target.value || undefined)}
          placeholder="Buscar por site ou email…"
          className="min-w-40 flex-1 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert" />
        <button onClick={() => setAdvanced(!advanced)}
          className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-muted hover:text-klarim-text">
          Filtros avançados {advanced ? '▲' : '▼'}
        </button>
      </div>

      {/* Linha 2 — avançados (colapsável) */}
      {advanced && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-klarim-border/60 bg-klarim-bg/40 p-2">
          <Sel value={filters.lead_score} onChange={(v) => setFilter('lead_score', v)} opts={LEAD_OPTS} allLabel="Qualquer lead score" />
          <Multi label="Site type" value={filters.site_type} options={SITE_TYPE_OPTS}
            onToggle={(o) => setFilter('site_type', toggleMultiValue(filters.site_type, o))} />
          <Multi label="Tecnologia" value={filters.tech} options={techList || []} searchable
            onToggle={(o) => setFilter('tech', toggleMultiValue(filters.tech, o))} />
          <Sel value={filters.last_scan} onChange={(v) => setFilter('last_scan', v)} opts={SCAN_OPTS} allLabel="Qualquer scan" />
          <Toggle3 label="Monitorado" value={filters.monitored} onChange={(v) => setFilter('monitored', v)} />
          <Toggle3 label="Dono verificado" value={filters.owner_verified} onChange={(v) => setFilter('owner_verified', v)} />
          <Toggle3 label="Perfil IA" value={filters.has_ai_profile} onChange={(v) => setFilter('has_ai_profile', v)} />
          <Sel value={filters.platform} onChange={(v) => setFilter('platform', v)} opts={platformOpts.map((p) => [p, p])} allLabel="Todas as plataformas" />
          <Sel value={filters.source} onChange={(v) => setFilter('source', v)} opts={sourceOpts.map((s) => [s, s])} allLabel="Todas as origens" />
          <Toggle3 label="Classificação incerta" value={filters.low_confidence} onChange={(v) => setFilter('low_confidence', v)} />
        </div>
      )}

      {/* Barra de totais */}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-klarim-text"><b>{fmt(total)}</b> alvos encontrados{' '}
          <span className="text-klarim-muted">(de {fmt(totalAll)})</span></span>
        {active > 0 && (
          <button onClick={clearFilters} className="text-klarim-alert hover:underline">Limpar filtros ✕</button>
        )}
      </div>
    </div>
  )
}

// Só os filtros da linha 1 estão ativos? (para decidir se abre a linha 2 no load com deep-link)
function onlyBasic(f) {
  const adv = ['lead_score', 'site_type', 'tech', 'last_scan', 'monitored', 'owner_verified', 'has_ai_profile', 'platform', 'source', 'low_confidence']
  return !adv.some((k) => f[k] === true || f[k] === false || (typeof f[k] === 'string' && f[k]))
}
