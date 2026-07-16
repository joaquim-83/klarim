import { useEffect, useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { Button, ErrorBox, Loading } from './ui'

// Gestão da landing pública de um alvo (KL-56): ver a página, editar os campos do
// perfil (description/business_type/tags/company_name) e ligar/desligar a landing.
// Portado de frontend/src/components/admin/ProfileEditor.jsx (KL-51 fase 1).
export function ProfileEditModal({ target, onClose, onSaved }) {
  const domain = target.domain || ''
  const siteUrl = `https://klarim.net/site/${domain}`
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [visible, setVisible] = useState(target.public_visible !== false)
  const [edited, setEdited] = useState(false)
  const [lowConf, setLowConf] = useState([])   // KL-67: campos suspeitos (⚠️)
  const [form, setForm] = useState({
    description: '', business_type: '', company_name: '', tags: '',
    phone: '', whatsapp: '', address: '',
    instagram: '', facebook: '', linkedin: '', youtube: '', tiktok: '',
  })

  useEffect(() => {
    let alive = true
    admin.target(target.id)
      .then((full) => {
        if (!alive) return
        const p = full?.profile || {}
        setForm({
          description: p.description || '', business_type: p.business_type || '',
          company_name: p.company_name || '',
          tags: Array.isArray(p.tags) ? p.tags.join(', ') : (p.tags || ''),
          phone: p.phone || '', whatsapp: p.whatsapp || '', address: p.address || '',
          instagram: p.instagram || '', facebook: p.facebook || '',
          linkedin: p.linkedin || '', youtube: p.youtube || '', tiktok: p.tiktok || '',
        })
        setEdited(!!p.edited_by_admin)
        setLowConf(Array.isArray(p.low_confidence_fields) ? p.low_confidence_fields : [])
        if (typeof p.public_visible === 'boolean') setVisible(p.public_visible)
      })
      .catch((e) => alive && setErr(e.message))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [target.id])

  async function saveProfile() {
    setBusy(true); setErr('')
    try {
      await admin.updateProfile(target.id, { ...form })   // KL-67: envia todos os campos
      onSaved?.('Perfil atualizado ✓')
      onClose()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function toggleVisible() {
    const next = !visible
    setBusy(true); setErr('')
    try {
      await admin.setProfileVisibility(target.id, next)
      setVisible(next)
      onSaved?.(`Landing ${next ? 'ativada' : 'desativada'} ✓`)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const field = 'w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm text-klarim-text outline-none focus:border-klarim-alert'

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between gap-2">
          <h3 className="text-lg font-bold">Landing — {domain}</h3>
          <a href={siteUrl} target="_blank" rel="noreferrer" className="whitespace-nowrap text-sm text-klarim-alert hover:underline">Ver landing ↗</a>
        </div>

        {loading ? <Loading /> : (
          <>
            {/* Toggle da landing pública */}
            <div className="mb-4 flex items-center justify-between rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2">
              <div>
                <div className="text-sm font-semibold">Landing pública</div>
                <div className="text-xs text-klarim-muted">
                  {visible ? `Visível em /site/${domain}` : 'Oculta — retorna "não disponível"'}
                </div>
              </div>
              <button
                onClick={toggleVisible}
                disabled={busy}
                className="rounded-full border border-klarim-border px-3 py-1 text-xs font-semibold"
                style={{ color: visible ? '#00D26A' : '#8B949E' }}
              >
                {visible ? '● Ativa' : '○ Inativa'}
              </button>
            </div>

            {edited && (
              <div className="mb-3 text-xs text-klarim-muted">
                ✏️ Editado à mão — o enrich automático não sobrescreve estes campos.
              </div>
            )}
            {lowConf.length > 0 && (
              <div className="mb-3 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-300">
                ⚠️ Dado extraído com baixa confiança (pode pertencer a outro site): {lowConf.join(', ')}. Revise ou limpe.
              </div>
            )}

            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs uppercase text-klarim-muted">Nome da empresa</label>
                <input className={field} value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase text-klarim-muted">Tipo de negócio</label>
                <input className={field} value={form.business_type} onChange={(e) => setForm({ ...form, business_type: e.target.value })} />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase text-klarim-muted">Descrição</label>
                <textarea rows={3} className={field} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase text-klarim-muted">Tags (separadas por vírgula)</label>
                <input className={field} value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="hotel, spa, café da manhã" />
              </div>

              {/* KL-67 — contatos editáveis (⚠️ nos de baixa confiança) */}
              <div className="border-t border-klarim-border pt-3 text-xs font-semibold uppercase text-klarim-muted">Contatos</div>
              {[
                ['phone', 'Telefone'], ['whatsapp', 'WhatsApp'], ['address', 'Endereço'],
                ['instagram', 'Instagram'], ['facebook', 'Facebook'], ['linkedin', 'LinkedIn'],
                ['youtube', 'YouTube'], ['tiktok', 'TikTok'],
              ].map(([key, label]) => (
                <div key={key}>
                  <label className="mb-1 block text-xs uppercase text-klarim-muted">
                    {label} {lowConf.includes(key) && <span title="Baixa confiança — pode pertencer a outro site" className="text-yellow-400">⚠️</span>}
                  </label>
                  <div className="flex gap-2">
                    <input className={field} value={form[key]}
                      onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
                    {form[key] && (
                      <button type="button" title="Limpar" onClick={() => setForm({ ...form, [key]: '' })}
                        className="shrink-0 rounded-lg border border-klarim-border px-2 text-xs text-klarim-muted hover:text-klarim-text">✕</button>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {err && <div className="mt-3"><ErrorBox message={err} /></div>}

            <div className="mt-5 flex justify-end gap-2">
              <Button onClick={onClose}>Fechar</Button>
              <Button variant="primary" disabled={busy} onClick={saveProfile}>
                {busy ? 'Salvando…' : 'Salvar perfil'}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
