import { useState, useEffect } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import {
  Card, Loading, ErrorBox, Button, PlatformBadge, SourceBadge,
  SemaphoreDot, Pagination, relativeTime, STATUS_LABEL, AlertScoreBadge,
} from './ui'
import { SectorEditor, SECTOR_OPTIONS } from './SectorEditor'
import { StatusEditor, EmailEditor } from './TargetEditors'
import { ProfileEditModal } from './ProfileEditor'
import AdminShell from './AdminShell'
import AlvosFilters from './AlvosFilters'
import {
  readFiltersFromURL, filtersToQueryString, filtersToApiParams, activeFilterCount,
} from '../../lib/admin/alvosFilters'

// Portado de frontend/src/pages/admin/Alvos.jsx (KL-51 fase 2). Link → <a href>; editores
// inline (SectorEditor/StatusEditor/EmailEditor/ProfileEditModal) já portados na fase 1.
const STATUS_OPTS = ['discovered', 'scanned', 'alerted', 'sem_contato', 'unsubscribed', 'descartado']
const PLATFORM_OPTS = ['duda', 'wordpress', 'cra', 'wix', 'shopify', 'squarespace', 'unknown']
// KL-54: filtro cobre a taxonomia completa (deriva de SECTOR_OPTIONS).
const SECTOR_OPTS = SECTOR_OPTIONS.map((o) => o.value)
const SOURCE_OPTS = ['public', 'discovery', 'admin', 'manual']
const PAGE_SIZE = 25

// ID de sessão estável do admin (para o evento admin_filter_used; sem PII).
function adminSession() {
  try {
    let s = localStorage.getItem('klarim_admin_session')
    if (!s) { s = 'adm-' + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem('klarim_admin_session', s) }
    return s
  } catch { return 'adm-anon' }
}

export default function AlvosPage() {
  // KL-104 P2 — 15 filtros num único objeto, sincronizado com a URL (deep-link/bookmark).
  const [filters, setFilters] = useState(
    () => readFiltersFromURL(typeof window !== 'undefined' ? window.location.search : ''),
  )
  const [page, setPage] = useState(0)
  const [msg, setMsg] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [profileTarget, setProfileTarget] = useState(null)  // landing modal (KL-56)
  const [reclassifying, setReclassifying] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [bulkSector, setBulkSector] = useState('hotel')
  const [bulkBusy, setBulkBusy] = useState(false)

  // A URL reflete os filtros (replaceState → não polui o histórico).
  useEffect(() => {
    if (typeof window === 'undefined') return
    const q = filtersToQueryString(filters)
    window.history.replaceState(null, '', q ? `${window.location.pathname}?${q}` : window.location.pathname)
  }, [filters])

  function setFilter(key, value) {
    setFilters((prev) => {
      const next = { ...prev }
      if (value === undefined || value === null || value === '') delete next[key]
      else next[key] = value
      return next
    })
    setPage(0)
  }
  function clearFilters() { setFilters({}); setPage(0) }

  // Params da API (search trimado; string estável p/ debounce coalescer teclas/cliques).
  const apiParams = filtersToApiParams(filters)
  if (typeof apiParams.search === 'string') {
    apiParams.search = apiParams.search.trim()
    if (!apiParams.search) delete apiParams.search
  }
  const apiKey = useDebounce(JSON.stringify(apiParams), 300)

  const { data, loading, error, reload } = useAsync(
    () => admin.targets({ ...JSON.parse(apiKey), limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [apiKey, page],
  )

  // Lista de tecnologias para o dropdown (cacheada no backend 1h).
  const { data: techData } = useAsync(() => admin.techList(), [])
  const techList = techData?.technologies || []

  // KL-57: registra quais combinações de filtro são usadas (fire-and-forget, sem PII).
  useEffect(() => {
    const p = JSON.parse(apiKey)
    if (activeFilterCount(p) === 0) return
    const keys = Object.keys(p).filter((k) => k !== 'search').sort()
    if (keys.length === 0) return
    try {
      fetch('/api/events', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_type: 'admin_filter_used', session_id: adminSession(),
          metadata: { filters: keys, combo: keys.join('+') },
        }),
      }).catch(() => {})
    } catch { /* nunca quebra a página */ }
  }, [apiKey])

  function toggleSel(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function classifyBulk() {
    const ids = [...selected]
    if (ids.length === 0) return
    setBulkBusy(true)
    setMsg('')
    try {
      const r = await admin.classifyBatch(ids, bulkSector)
      setMsg(`${r.updated} alvo(s) classificados como ${bulkSector}.`)
      setSelected(new Set())
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBulkBusy(false)
    }
  }

  async function reclassifyDomains() {
    setReclassifying(true)
    setMsg('')
    try {
      const r = await admin.reclassifyDomains()
      setMsg(`Reclassificação por domínio: ${r.changed} de ${r.processed} alvos alterados.`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setReclassifying(false)
    }
  }

  async function act(id, fn, label) {
    setBusyId(id)
    setMsg('')
    try {
      await fn(id)
      setMsg(`${label} ✓`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusyId(null)
    }
  }

  // FIX scan admin: síncrono → mostra o score e atualiza a linha.
  async function scanNow(t) {
    setBusyId(t.id)
    setMsg('')
    try {
      const r = await admin.scanTarget(t.id)
      setMsg(r.score != null
        ? `Scan de ${t.domain || t.url} concluído: ${r.score}/100`
        : 'Scan enfileirado ✓')
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusyId(null)
    }
  }

  const rows = data?.targets || []  // busca é server-side (por URL/domínio/e-mail)

  return (
    <AdminShell active="alvos">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Alvos</h1>
          <div className="flex gap-2">
            <Button disabled={reclassifying} onClick={reclassifyDomains}>
              {reclassifying ? 'Reclassificando…' : 'Reclassificar domínios'}
            </Button>
            <Button variant="primary" onClick={() => setShowAdd(true)}>+ Adicionar alvo</Button>
          </div>
        </div>

        {/* Filtros avançados (KL-104 P2) */}
        <AlvosFilters
          filters={filters}
          setFilter={setFilter}
          clearFilters={clearFilters}
          total={data?.total}
          totalAll={data?.total_all}
          techList={techList}
          statusOpts={STATUS_OPTS}
          statusLabel={STATUS_LABEL}
          sectorOpts={SECTOR_OPTS}
          platformOpts={PLATFORM_OPTS}
          sourceOpts={SOURCE_OPTS}
        />

        {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

        {/* Ação em massa (aparece ao selecionar alvos) */}
        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-klarim-alert/40 bg-klarim-alert/10 px-3 py-2 text-sm">
            <span className="font-semibold">{selected.size} selecionado(s)</span>
            <span className="text-klarim-muted">→ classificar como</span>
            <select
              value={bulkSector}
              onChange={(e) => setBulkSector(e.target.value)}
              className="rounded border border-klarim-border bg-klarim-surface px-2 py-1 text-sm text-klarim-text outline-none focus:border-klarim-alert"
            >
              {SECTOR_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <Button variant="primary" disabled={bulkBusy} onClick={classifyBulk}>
              {bulkBusy ? `Classificando ${selected.size}…` : 'Classificar selecionados'}
            </Button>
            <button onClick={() => setSelected(new Set())} className="text-klarim-muted hover:text-klarim-text">Limpar seleção</button>
          </div>
        )}

        <Card>
          {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-2">
                      <input
                        type="checkbox"
                        aria-label="Selecionar todos"
                        checked={rows.length > 0 && rows.every((t) => selected.has(t.id))}
                        onChange={(e) => setSelected((prev) => {
                          const next = new Set(prev)
                          rows.forEach((t) => e.target.checked ? next.add(t.id) : next.delete(t.id))
                          return next
                        })}
                      />
                    </th>
                    <th className="py-2 pr-3">Site</th>
                    <th className="py-2 pr-3">Plataforma</th>
                    <th className="py-2 pr-3">Setor</th>
                    <th className="py-2 pr-3">Score</th>
                    <th className="py-2 pr-3" title="Lead score de qualidade do alerta (KL-85)">Alert</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Origem</th>
                    <th className="py-2 pr-3">E-mail</th>
                    <th className="py-2 pr-3">Último scan</th>
                    <th className="py-2">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => (
                    <tr key={t.id} className="border-t border-klarim-border align-middle">
                      <td className="py-2 pr-2">
                        <input
                          type="checkbox"
                          aria-label={`Selecionar ${t.domain || t.url}`}
                          checked={selected.has(t.id)}
                          onChange={() => toggleSel(t.id)}
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <a href={t.url} target="_blank" rel="noreferrer" className="font-mono text-xs text-klarim-alert hover:underline">
                          {t.domain || t.url}
                        </a>
                      </td>
                      <td className="py-2 pr-3"><PlatformBadge platform={t.platform} /></td>
                      <td className="py-2 pr-3">
                        <SectorEditor
                          target={t}
                          onSaved={(_u, note) => { setMsg(note); reload() }}
                          onError={(m) => setMsg(m)}
                        />
                      </td>
                      <td className="py-2 pr-3">
                        {t.last_scan_score != null
                          ? <SemaphoreDot semaphore={t.last_semaphore} score={t.last_scan_score} />
                          : <span className="text-klarim-muted">—</span>}
                      </td>
                      <td className="py-2 pr-3"><AlertScoreBadge score={t.alert_quality_score} /></td>
                      <td className="py-2 pr-3">
                        <StatusEditor target={t} onSaved={(_u, note) => { setMsg(note); reload() }} onError={(m) => setMsg(m)} />
                      </td>
                      <td className="py-2 pr-3"><SourceBadge source={t.source} /></td>
                      <td className="py-2 pr-3">
                        <EmailEditor target={t} onSaved={(_u, note) => { setMsg(note); reload() }} onError={(m) => setMsg(m)} />
                      </td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{t.last_scan_at ? relativeTime(t.last_scan_at) : '—'}</td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-1">
                          <Button disabled={busyId === t.id} onClick={() => scanNow(t)}>{busyId === t.id ? 'Escaneando…' : 'Escanear'}</Button>
                          <Button disabled={busyId === t.id || !t.contact_email} onClick={() => act(t.id, admin.alertTarget, 'Alerta enviado')}>Alertar</Button>
                          {t.has_profile && ['scanned', 'alerted'].includes(t.status) && (
                            <Button
                              onClick={() => setProfileTarget(t)}
                              title="Ver / editar / (des)ativar a landing pública"
                            >
                              {t.public_visible === false ? 'Landing 🔒' : 'Landing'}
                            </Button>
                          )}
                          <a href={`/painel/alvos/${t.id}`}><Button>Detalhes</Button></a>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={11} className="py-8 text-center text-klarim-muted">Nenhum alvo.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={page} setPage={setPage}
            hasNext={data?.total != null ? (page + 1) * PAGE_SIZE < data.total : (data?.targets || []).length === PAGE_SIZE} />
        </Card>

        {showAdd && <AddTargetModal onClose={() => setShowAdd(false)} onAdded={(note) => { setShowAdd(false); if (note) setMsg(note); reload() }} />}
        {profileTarget && (
          <ProfileEditModal
            target={profileTarget}
            onClose={() => setProfileTarget(null)}
            onSaved={(note) => { setMsg(note); reload() }}
          />
        )}
      </div>
    </AdminShell>
  )
}

function AddTargetModal({ onClose, onAdded }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('')

  async function submit() {
    setBusy(true)
    setError('')
    try {
      setPhase('Adicionando…')
      const added = await admin.addTarget(url.trim())
      // FIX: "Adicionar e escanear" → dispara o scan síncrono no alvo recém-criado.
      let note = `${added.domain || added.url} adicionado`
      if (added.target_id) {
        setPhase('Escaneando…')
        try {
          const r = await admin.scanTarget(added.target_id)
          if (r.score != null) note += ` · scan: ${r.score}/100`
        } catch { /* o alvo foi criado; o scan pode ser refeito pela lista */ }
      }
      onAdded(note)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
      setPhase('')
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-bold">Adicionar alvo</h3>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoFocus
          placeholder="https://www.exemplo.com.br"
          className="mb-4 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
        />
        {error && <div className="mb-4"><ErrorBox message={error} /></div>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" disabled={busy || !url.trim()} onClick={submit}>
            {busy ? (phase || 'Processando…') : 'Adicionar e escanear'}
          </Button>
        </div>
      </div>
    </div>
  )
}
