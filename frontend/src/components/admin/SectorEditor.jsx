// Edição inline do setor de um alvo (classificação manual — refino KL-11).
// Reutilizado na lista de alvos e na tela de detalhe.
import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { Badge } from './ui'

// Setores válidos com rótulos amigáveis (batem com PRICE_TIERS no backend).
export const SECTOR_OPTIONS = [
  { value: 'hotel', label: 'Hotel / Pousada' },
  { value: 'clinica', label: 'Clínica / Saúde' },
  { value: 'escola', label: 'Escola / Educação' },
  { value: 'ecommerce', label: 'E-commerce / Loja' },
  { value: 'condominio', label: 'Condomínio' },
  { value: 'juridico', label: 'Jurídico / Advocacia' },
  { value: 'contabilidade', label: 'Contabilidade' },
  { value: 'restaurante', label: 'Restaurante / Food' },
  { value: 'imobiliaria', label: 'Imobiliária' },
  { value: 'automotivo', label: 'Automotivo' },
  { value: 'outro', label: 'Outro' },
]

export const SECTOR_LABEL = Object.fromEntries(SECTOR_OPTIONS.map((o) => [o.value, o.label]))

// Badge do setor com indicador visual de confiança + cadeado se manual.
//  manual → 🔒 · ≥0.8 sólido · 0.5–0.79 pontilhado · <0.5 cinza com "?".
export function SectorBadge({ sector, confidence, manual }) {
  const label = sector || 'outro'
  if (manual) {
    return (
      <span className="inline-flex items-center gap-1">
        <Badge>{label}</Badge>
        <span title="Classificado manualmente">🔒</span>
      </span>
    )
  }
  const c = confidence == null ? null : Number(confidence)
  const pct = c == null ? null : `${Math.round(c * 100)}%`
  if (c != null && c < 0.5) {
    return (
      <span title={`Classificação incerta${pct ? ` (${pct})` : ''}`}
        className="inline-block rounded-full border border-klarim-border bg-klarim-border/20 px-2 py-0.5 text-xs font-semibold text-klarim-muted">
        {label} ?
      </span>
    )
  }
  if (c != null && c < 0.8) {
    return (
      <span title={`Classificação provável (${pct})`}
        className="inline-block rounded-full border border-dashed border-klarim-alert/70 px-2 py-0.5 text-xs font-semibold text-klarim-text">
        {label}
      </span>
    )
  }
  return <span title={pct ? `Confiança ${pct}` : undefined}><Badge>{label}</Badge></span>
}

// Badge + edição inline. `onSaved(updatedTarget)` recebe o alvo atualizado.
export function SectorEditor({ target, onSaved, onError }) {
  const [editing, setEditing] = useState(false)
  const [sel, setSel] = useState(target.sector || 'outro')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const updated = await admin.classifyTarget(target.id, sel)
      setEditing(false)
      onSaved?.(updated, `Setor atualizado para ${SECTOR_LABEL[sel] || sel}`)
    } catch (e) {
      onError?.(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        <select
          value={sel}
          onChange={(e) => setSel(e.target.value)}
          className="rounded border border-klarim-border bg-klarim-surface px-1.5 py-0.5 text-xs text-klarim-text outline-none focus:border-klarim-alert"
        >
          {SECTOR_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button onClick={save} disabled={busy} title="Salvar" className="text-sm" style={{ color: '#00D26A' }}>✓</button>
        <button onClick={() => { setEditing(false); setSel(target.sector || 'outro') }}
          disabled={busy} title="Cancelar" className="text-sm text-klarim-muted">✗</button>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1">
      <SectorBadge sector={target.sector} confidence={target.classification_confidence}
        manual={target.classification_source === 'manual'} />
      <button onClick={() => { setSel(target.sector || 'outro'); setEditing(true) }}
        title="Editar setor" className="text-xs text-klarim-muted hover:text-klarim-text">✏️</button>
    </span>
  )
}
