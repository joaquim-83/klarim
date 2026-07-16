import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync, useDebounce } from '../../lib/admin/useAsync'
import {
  Card, Loading, ErrorBox, Button, PlatformBadge, SourceBadge,
  SemaphoreDot, Pagination, relativeTime, STATUS_LABEL,
} from './ui'
import { SectorEditor, SECTOR_OPTIONS } from './SectorEditor'
import { StatusEditor, EmailEditor } from './TargetEditors'
import { ProfileEditModal } from './ProfileEditor'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/Alvos.jsx (KL-51 fase 2). Link → <a href>; editores
// inline (SectorEditor/StatusEditor/EmailEditor/ProfileEditModal) já portados na fase 1.
const STATUS_OPTS = ['discovered', 'scanned', 'alerted', 'sem_contato', 'unsubscribed', 'descartado']
const PLATFORM_OPTS = ['duda', 'wordpress', 'cra', 'wix', 'shopify', 'squarespace', 'unknown']
// KL-54: filtro cobre a taxonomia completa (deriva de SECTOR_OPTIONS).
const SECTOR_OPTS = SECTOR_OPTIONS.map((o) => o.value)
const SOURCE_OPTS = ['public', 'discovery', 'admin', 'manual']
const PAGE_SIZE = 25

function Select({ value, onChange, options, allLabel, labels }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert"
    >
      <option value="">{allLabel}</option>
      {options.map((o) => <option key={o} value={o}>{labels?.[o] || o}</option>)}
    </select>
  )
}

export default function AlvosPage() {
  const [status, setStatus] = useState('')
  const [platform, setPlatform] = useState('')
  const [sector, setSector] = useState('')
  const [source, setSource] = useState('')
  const [lowConf, setLowConf] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [msg, setMsg] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [profileTarget, setProfileTarget] = useState(null)  // landing modal (KL-56)
  const [reclassifying, setReclassifying] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [bulkSector, setBulkSector] = useState('hotel')
  const [bulkBusy, setBulkBusy] = useState(false)

  const debouncedSearch = useDebounce(search.trim(), 300)

  const { data, loading, error, reload } = useAsync(
    () => admin.targets({
      status, platform, sector, source, low_confidence: lowConf || undefined,
      search: debouncedSearch || undefined,
      limit: PAGE_SIZE, offset: page * PAGE_SIZE,
    }),
    [status, platform, sector, source, lowConf, debouncedSearch, page],
  )

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

        {/* Filtros */}
        <div className="flex flex-wrap gap-2">
          <Select value={status} onChange={(v) => { setStatus(v); setPage(0) }} options={STATUS_OPTS} allLabel="Todos os status" labels={STATUS_LABEL} />
          <Select value={platform} onChange={(v) => { setPlatform(v); setPage(0) }} options={PLATFORM_OPTS} allLabel="Todas as plataformas" />
          <Select value={sector} onChange={(v) => { setSector(v); setPage(0) }} options={SECTOR_OPTS} allLabel="Todos os setores" />
          <Select value={source} onChange={(v) => { setSource(v); setPage(0) }} options={SOURCE_OPTS} allLabel="Todas as origens" />
          <button
            onClick={() => { setLowConf(!lowConf); setPage(0) }}
            className={`rounded-lg border px-3 py-1.5 text-sm ${lowConf ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-text' : 'border-klarim-border bg-klarim-surface text-klarim-muted hover:text-klarim-text'}`}
          >
            Classificação incerta
          </button>
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0) }}
            placeholder="Buscar por site ou email…"
            className="min-w-40 flex-1 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert"
          />
        </div>

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
                    <tr><td colSpan={10} className="py-8 text-center text-klarim-muted">Nenhum alvo.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={page} setPage={setPage} hasNext={(data?.targets || []).length === PAGE_SIZE} />
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
