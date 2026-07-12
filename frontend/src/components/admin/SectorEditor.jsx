// Edição inline do setor de um alvo (classificação manual — refino KL-11).
// Reutilizado na lista de alvos e na tela de detalhe.
import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { Badge } from './ui'

// Taxonomia de setores (KL-54) — espelha discovery/sector_taxonomy.py.
// Ordenada por macro-setor para o dropdown ficar legível. A fonte da verdade é o
// backend (GET /api/sectors); esta cópia mantém o editor funcional offline.
export const SECTOR_OPTIONS = [
  // Alimentação & Bebidas
  { value: 'restaurante', label: 'Restaurante' },
  { value: 'bar_lanchonete', label: 'Bar / Lanchonete / Hamburgueria' },
  { value: 'padaria_confeitaria', label: 'Padaria / Confeitaria' },
  { value: 'delivery', label: 'Delivery / Food Truck' },
  // Saúde
  { value: 'clinica', label: 'Clínica Médica' },
  { value: 'odontologia', label: 'Odontologia' },
  { value: 'farmacia', label: 'Farmácia / Manipulação' },
  { value: 'laboratorio', label: 'Laboratório / Diagnóstico' },
  { value: 'psicologia', label: 'Psicologia / Terapia' },
  { value: 'veterinaria', label: 'Veterinária' },
  { value: 'hospital', label: 'Hospital / Pronto-socorro' },
  { value: 'nutricao', label: 'Nutrição / Saúde Funcional' },
  // Beleza & Bem-estar
  { value: 'salao_barbearia', label: 'Salão / Barbearia' },
  { value: 'estetica_spa', label: 'Estética / Spa' },
  { value: 'academia', label: 'Academia / Pilates / Yoga' },
  // Comércio
  { value: 'ecommerce', label: 'E-commerce / Loja Online' },
  { value: 'loja_moda', label: 'Moda / Calçados / Acessórios' },
  { value: 'otica', label: 'Ótica' },
  { value: 'supermercado', label: 'Supermercado / Mercearia' },
  { value: 'petshop', label: 'Pet Shop' },
  { value: 'material_construcao', label: 'Material de Construção' },
  { value: 'moveis_decoracao', label: 'Móveis / Decoração' },
  { value: 'eletronicos', label: 'Informática / Eletrônicos' },
  // Serviços Profissionais
  { value: 'contabilidade', label: 'Contabilidade' },
  { value: 'juridico', label: 'Advocacia / Jurídico' },
  { value: 'consultoria', label: 'Consultoria' },
  { value: 'agencia', label: 'Agência / Marketing / Design' },
  { value: 'tecnologia', label: 'Tecnologia / Software / TI' },
  { value: 'seguros_financeiro', label: 'Seguros / Financeiro' },
  { value: 'rh_recrutamento', label: 'RH / Recrutamento' },
  { value: 'grafica', label: 'Gráfica / Impressão' },
  // Imobiliário & Construção
  { value: 'imobiliaria', label: 'Imobiliária' },
  { value: 'construtora', label: 'Construtora / Incorporadora' },
  { value: 'arquitetura', label: 'Arquitetura / Design de Interiores' },
  { value: 'condominio', label: 'Condomínio / Administradora' },
  // Automotivo
  { value: 'automotivo', label: 'Oficina / Concessionária / Autopeças' },
  // Educação
  { value: 'escola', label: 'Escola' },
  { value: 'curso_idiomas', label: 'Curso Livre / Idiomas' },
  { value: 'faculdade', label: 'Faculdade / Ensino Superior' },
  // Hospedagem & Turismo
  { value: 'hotel', label: 'Hotel / Pousada' },
  { value: 'turismo_viagens', label: 'Turismo / Agência de Viagens' },
  // Eventos & Entretenimento
  { value: 'eventos_buffet', label: 'Eventos / Buffet / Cerimonial' },
  { value: 'fotografia', label: 'Fotografia / Vídeo / Produtora' },
  // Indústria / Transporte
  { value: 'industria', label: 'Indústria / Fábrica' },
  { value: 'transporte', label: 'Transporte / Logística' },
  // Institucional
  { value: 'religioso', label: 'Igreja / Instituição Religiosa' },
  { value: 'ong_associacao', label: 'ONG / Associação / Sindicato' },
  { value: 'governo', label: 'Governo / Órgão Público' },
  // Catch-all
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
