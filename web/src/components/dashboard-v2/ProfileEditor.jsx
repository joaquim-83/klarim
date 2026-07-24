// KL-98 — editor do perfil público do site (dono verificado, nível 3): formulário + preview ao
// vivo + configuração do selo + visibilidade. Salva via PUT /account/sites/{id}/{profile,
// visibility,seal}. Campos com 🤖 foram preenchidos pela IA (edite se precisar).
import { useState, useEffect } from 'react';
import { apiGet, apiPut } from '../../lib/api.js';
import Modal from './Modal.jsx';
import { brandBtn, outlineBtn, profileUrl } from './shared.js';

const FIELDS = [
  ['company_name', 'Nome da empresa', 'text'],
  ['description', 'Descrição', 'textarea'],
  ['phone', 'Telefone', 'text'],
  ['whatsapp', 'WhatsApp (só dígitos)', 'text'],
  ['commercial_email', 'E-mail comercial', 'text'],
  ['address', 'Endereço', 'text'],
  ['business_hours', 'Horário de atendimento', 'text'],
  ['business_type', 'Tipo de negócio', 'text'],
  ['instagram', 'Instagram', 'text'],
  ['facebook', 'Facebook (URL)', 'text'],
  ['google_maps_url', 'Google Maps (URL)', 'text'],
];
const input = 'w-full rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-brand-500';

export default function ProfileEditor({ targetId, domain, initial = {}, onClose, onSaved }) {
  const [form, setForm] = useState(() => {
    const f = {};
    for (const [k] of FIELDS) f[k] = initial[k] || '';
    f.tags = Array.isArray(initial.tags) ? initial.tags.join(', ') : (initial.tags || '');
    return f;
  });
  const [aiFields] = useState(() => new Set(initial.ai_fields || []));  // 🤖 marcadores
  const [visible, setVisible] = useState(initial.public_visible !== false);
  const [seal, setSeal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet(`/account/sites/${targetId}/seal`).then((r) => { if (alive && r.ok) setSeal(r.data); });
    // KL-106 fix: pré-preenche o formulário com o perfil ATUAL (o `initial` do dashboard é parcial).
    apiGet(`/account/sites/${targetId}`).then((r) => {
      if (!alive || !r.ok || !r.data) return;
      const p = r.data.profile || {};
      setForm((prev) => {
        const next = { ...prev };
        for (const [k] of FIELDS) if (p[k] != null) next[k] = p[k];
        if (Array.isArray(p.tags)) next.tags = p.tags.join(', ');
        return next;
      });
      if (typeof (r.data.profile || {}).public_visible === 'boolean') setVisible(r.data.profile.public_visible);
    });
    return () => { alive = false; };
  }, [targetId]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    setBusy(true); setMsg(''); setErr('');
    const body = { ...form, tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean) };
    const r = await apiPut(`/account/sites/${targetId}/profile`, body);
    if (!r.ok) { setErr(r.error || 'Falha ao salvar o perfil.'); setBusy(false); return; }
    await apiPut(`/account/sites/${targetId}/visibility`, { public_visible: visible });
    if (seal) await apiPut(`/account/sites/${targetId}/seal`, { enabled: !!seal.enabled, style: seal.style });
    window.klarimTrack?.('profile_edited', { domain }, '');
    if (seal) window.klarimTrack?.('seal_configured', { enabled: !!seal.enabled, style: seal.style }, '');
    setMsg('Perfil atualizado ✓'); setBusy(false);
    onSaved && onSaved(r.data);
  }

  function copySeal() {
    const code = seal?.variants?.[seal.style || 'badge']?.embed_code || '';
    try { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* noop */ }
  }

  const previewTags = form.tags.split(',').map((t) => t.trim()).filter(Boolean);

  return (
    <Modal title={`Editar perfil · ${domain}`} onClose={onClose} size="xl">
      {err && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</p>}
      <div className="grid gap-5 md:grid-cols-2">
        {/* Formulário */}
        <div className="space-y-3">
          {FIELDS.map(([k, label, type]) => (
            <label key={k} className="block">
              <span className="text-xs text-slate-400">{label} {aiFields.has(k) && <span title="Preenchido automaticamente — edite se precisar">🤖</span>}</span>
              {type === 'textarea'
                ? <textarea rows={3} value={form[k]} onChange={(e) => set(k, e.target.value)} className={`${input} mt-1`} maxLength={1000} />
                : <input value={form[k]} onChange={(e) => set(k, e.target.value)} className={`${input} mt-1`} />}
            </label>
          ))}
          <label className="block">
            <span className="text-xs text-slate-400">Tags (separadas por vírgula, máx 10)</span>
            <input value={form.tags} onChange={(e) => set('tags', e.target.value)} className={`${input} mt-1`} placeholder="loja, roupas, moda" />
          </label>
          <div className="flex items-center gap-4 pt-1">
            <span className="text-xs text-slate-400">Visibilidade:</span>
            <label className="flex items-center gap-1 text-sm text-slate-200"><input type="radio" checked={visible} onChange={() => setVisible(true)} /> Público</label>
            <label className="flex items-center gap-1 text-sm text-slate-200"><input type="radio" checked={!visible} onChange={() => setVisible(false)} /> Oculto</label>
          </div>
        </div>

        {/* Preview + selo */}
        <div className="space-y-4">
          <div>
            <span className="text-xs text-slate-400">Prévia do perfil público</span>
            <div className="mt-1 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-base font-bold text-white">{form.company_name || domain}</p>
              {form.business_type && <p className="text-xs text-brand-400">{form.business_type}</p>}
              {form.description && <p className="mt-2 text-sm text-slate-300">{form.description}</p>}
              <div className="mt-2 space-y-0.5 text-xs text-slate-400">
                {form.phone && <p>📞 {form.phone}</p>}
                {form.address && <p>📍 {form.address}</p>}
                {form.business_hours && <p>🕑 {form.business_hours}</p>}
              </div>
              {previewTags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {previewTags.map((t) => <span key={t} className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{t}</span>)}
                </div>
              )}
              <a href={profileUrl(domain)} target="_blank" rel="noreferrer" className="mt-3 inline-block text-xs text-brand-400 hover:underline">Ver perfil público →</a>
            </div>
          </div>

          {seal && (
            <div className="rounded-xl border border-slate-800 p-4">
              <label className="flex items-center justify-between">
                <span className="text-sm font-semibold text-white">🛡️ Selo Klarim</span>
                <input type="checkbox" checked={!!seal.enabled} onChange={(e) => setSeal((s) => ({ ...s, enabled: e.target.checked }))} />
              </label>
              {seal.enabled && (
                <>
                  <div className="mt-2 flex flex-wrap gap-3 text-sm text-slate-200">
                    {Object.entries(seal.variants || {}).map(([key, v]) => (
                      <label key={key} className="flex items-center gap-1">
                        <input type="radio" checked={(seal.style || 'badge') === key} onChange={() => setSeal((s) => ({ ...s, style: key }))} />
                        {v.name}
                      </label>
                    ))}
                  </div>
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950 p-2 text-[11px] text-slate-400"><code>{seal.variants?.[seal.style || 'badge']?.embed_code}</code></pre>
                  <button type="button" onClick={copySeal} className={`${outlineBtn} mt-2`}>{copied ? 'Copiado ✓' : 'Copiar código'}</button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between gap-3">
        {msg && <span className="text-sm text-green-400">{msg}</span>}
        <div className="ml-auto flex gap-2">
          <button type="button" onClick={onClose} className={outlineBtn}>Fechar</button>
          <button type="button" disabled={busy} onClick={save} className={brandBtn}>{busy ? 'Salvando…' : 'Salvar alterações'}</button>
        </div>
      </div>
    </Modal>
  );
}
