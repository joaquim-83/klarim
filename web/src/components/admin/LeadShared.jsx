// Compartilhado entre Leads e LeadDetalhe (KL-61). Extraído de Leads.jsx na migração
// Astro (KL-51 fase 1) — antes o LeadDetalhe importava de Leads, agora ambos daqui.
import { Badge } from './ui'

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
