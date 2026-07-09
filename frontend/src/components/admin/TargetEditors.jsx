// Edição inline do status e do e-mail de um alvo (operador).
// Mesmo padrão do SectorEditor: badge/texto + ✏️ → editor inline com ✓/✗.
import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { StatusBadge, STATUS_LABEL } from './ui'

// Status editáveis pelo operador (batem com _VALID_STATUSES no backend).
export const STATUS_OPTIONS = [
  'discovered', 'scanned', 'alerted', 'converted',
  'sem_contato', 'descartado', 'unsubscribed',
]

const _selectCls =
  'rounded border border-klarim-border bg-klarim-surface px-1.5 py-0.5 text-xs text-klarim-text outline-none focus:border-klarim-alert'

// Badge de status + edição inline. `onSaved(updatedTarget, note)`.
export function StatusEditor({ target, onSaved, onError }) {
  const [editing, setEditing] = useState(false)
  const [sel, setSel] = useState(target.status || 'discovered')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const updated = await admin.updateStatus(target.id, sel)
      setEditing(false)
      onSaved?.(updated, `Status atualizado para ${STATUS_LABEL[sel] || sel}`)
    } catch (e) {
      onError?.(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        <select value={sel} onChange={(e) => setSel(e.target.value)} className={_selectCls}>
          {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
        </select>
        <button onClick={save} disabled={busy} title="Salvar" className="text-sm" style={{ color: '#00D26A' }}>✓</button>
        <button onClick={() => { setEditing(false); setSel(target.status || 'discovered') }}
          disabled={busy} title="Cancelar" className="text-sm text-klarim-muted">✗</button>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1">
      <StatusBadge status={target.status} />
      <button onClick={() => { setSel(target.status || 'discovered'); setEditing(true) }}
        title="Editar status" className="text-xs text-klarim-muted hover:text-klarim-text">✏️</button>
    </span>
  )
}

// E-mail de contato + edição inline. `onSaved(updatedTarget, note)`.
export function EmailEditor({ target, onSaved, onError }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(target.contact_email || '')
  const [busy, setBusy] = useState(false)

  async function save() {
    const email = val.trim()
    if (!email) return
    setBusy(true)
    try {
      const updated = await admin.updateEmail(target.id, email)
      setEditing(false)
      onSaved?.(updated, 'E-mail atualizado')
    } catch (e) {
      onError?.(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        <input
          type="email"
          value={val}
          autoFocus
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
          placeholder="email@dominio.com.br"
          className="w-44 rounded border border-klarim-border bg-klarim-surface px-1.5 py-0.5 text-xs text-klarim-text outline-none focus:border-klarim-alert"
        />
        <button onClick={save} disabled={busy || !val.trim()} title="Salvar" className="text-sm" style={{ color: '#00D26A' }}>✓</button>
        <button onClick={() => { setEditing(false); setVal(target.contact_email || '') }}
          disabled={busy} title="Cancelar" className="text-sm text-klarim-muted">✗</button>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1">
      <span className={target.contact_email ? 'text-xs' : 'text-xs italic text-klarim-muted'}>
        {target.contact_email || 'Sem contato'}
      </span>
      <button onClick={() => { setVal(target.contact_email || ''); setEditing(true) }}
        title="Editar e-mail" className="text-xs text-klarim-muted hover:text-klarim-text">✏️</button>
    </span>
  )
}
