import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '../../lib/api';

// KL-44 P3 — no detalhe do site: técnico responsável (convidar/revogar) + compartilhar
// laudo (link + WhatsApp). O e-mail do técnico nunca é exposto de outro dono.
const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6';

export default function TechnicianSection({ targetId }) {
  const [links, setLinks] = useState(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [share, setShare] = useState(null);

  async function load() {
    const { ok, data } = await apiGet(`/account/technician/links?target_id=${targetId}`);
    setLinks(ok ? (data.links || []) : []);
  }
  useEffect(() => { load(); }, [targetId]);

  async function invite(e) {
    e.preventDefault();
    setBusy(true); setMsg('');
    const { ok, error } = await apiPost('/account/technician/invite', { target_id: targetId, technician_email: email });
    setBusy(false);
    if (!ok) { setMsg(error || 'Não foi possível convidar.'); return; }
    setEmail(''); setMsg('Convite enviado ✓'); load();
  }

  async function revoke(id) {
    await apiPost('/account/technician/revoke', { link_id: id });
    load();
  }

  async function shareLaudo() {
    setBusy(true); setMsg('');
    const { ok, data, error } = await apiPost('/account/shared-report/create', { target_id: targetId });
    setBusy(false);
    if (!ok) { setMsg(error || 'Não foi possível gerar o laudo.'); return; }
    setShare(data);
  }

  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Técnico responsável</h2>
      <p className="mt-1 text-sm text-slate-400">Vincule seu técnico de TI — ele recebe o laudo técnico junto com o boletim.</p>

      {links && links.length > 0 && (
        <ul className="mt-3 space-y-2">
          {links.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
              <span className="text-slate-200">
                {l.technician_email}
                <span className={`ml-2 rounded px-1.5 py-0.5 text-xs ${l.status === 'active' ? 'bg-green-500/15 text-green-300' : 'bg-yellow-500/15 text-yellow-300'}`}>
                  {l.status === 'active' ? 'vinculado' : 'convite enviado'}
                </span>
              </span>
              <button onClick={() => revoke(l.id)} className="text-xs text-red-300 hover:text-red-200">Revogar</button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={invite} className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="tecnico@empresa.com.br"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" />
        <button type="submit" disabled={busy}
          className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-brand-400 disabled:opacity-50">
          Convidar técnico
        </button>
      </form>

      <div className="mt-4 border-t border-slate-800 pt-4">
        <button onClick={shareLaudo} disabled={busy}
          className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50">
          📤 Compartilhar laudo
        </button>
        {share && (
          <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-sm">
            <p className="text-slate-300">Link do laudo (válido 30 dias):</p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <code className="break-all text-xs text-brand-300">{share.url}</code>
              <button onClick={() => navigator.clipboard?.writeText(share.url)} className="text-xs text-slate-400 hover:text-white">Copiar</button>
            </div>
            <a href={share.whatsapp_url} target="_blank" rel="noreferrer"
              className="mt-2 inline-flex rounded-lg bg-green-600/80 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-600">
              Enviar por WhatsApp
            </a>
          </div>
        )}
      </div>
      {msg && <p className="mt-2 text-sm text-slate-300">{msg}</p>}
    </div>
  );
}
