// KL-90 UX (item 2) — adicionar um site ao monitoramento. POST /account/sites {url}.
// Em sucesso, chama onAdded (o dashboard re-fetcha e seleciona o novo site).
import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { brandBtn } from './shared.js';
import Modal from './Modal.jsx';

const inputCls =
  'w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-3 text-base text-white ' +
  'placeholder:text-slate-500 outline-none focus:border-brand-500';

export default function AddSiteModal({ onClose, onAdded }) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [warn, setWarn] = useState('');   // KL-90 reg 6: aviso de outro dono verificado

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(''); setWarn('');
    const { ok, status, data, error: err } = await apiPost('/account/sites', { url });
    setBusy(false);
    if (!ok) {
      setError(status === 403 ? (err || 'Limite do plano atingido.') : (err || 'Não foi possível adicionar. Pesquise o site primeiro.'));
      return;
    }
    // reg 6: site já tem dono verificado (is_owner=false e sem verificação disponível) → avisa,
    // mas o site é adicionado ao acompanhamento mesmo assim.
    if (data && data.is_owner === false && !data.ownership_verification_available) {
      setWarn('Este site já tem um dono verificado. Você o acompanha, mas a propriedade é de outra conta.');
      onAdded();
      setTimeout(onClose, 2600);
      return;
    }
    onAdded(); onClose();
  }

  return (
    <Modal title="+ Adicionar site" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="text-sm text-slate-400">Digite o domínio do site que você quer monitorar.</p>
        <input type="text" required value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="seusite.com.br" className={`${inputCls} mt-3`} autoFocus />
        {warn && <p className="mt-2 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-200">⚠️ {warn}</p>}
        {error && <p className="mt-2 text-sm text-red-300">{error}</p>}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button type="submit" disabled={busy} className={brandBtn}>
            {busy ? 'Adicionando…' : 'Monitorar site →'}
          </button>
          <a href="/scan" className="inline-flex min-h-[44px] items-center justify-center rounded-xl border border-slate-700 px-5 text-sm font-semibold text-slate-200 hover:bg-slate-800">
            Pesquisar antes
          </a>
        </div>
      </form>
    </Modal>
  );
}
